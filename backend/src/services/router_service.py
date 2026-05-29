# =============================================================================
# PH Agent Hub — Router Service (Intelligent Model Routing)
# =============================================================================
# Single-step per-query model selection:
#
#   1. Gather all eligible models for the tenant.
#   2. Pick the cheapest eligible model as the classifier.
#   3. Send the user message + list of available models to the classifier.
#   4. The classifier LLM returns the best model_id (e.g. "deepseek-v4-pro").
#   5. Validate and resolve to the DB UUID.
#   6. Chat endpoint locks the model on the session.
# =============================================================================

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Maximum tokens for the classifier response (just a model ID).
ROUTER_MAX_TOKENS = 32

ROUTER_SYSTEM_PROMPT_TEMPLATE = """\
You are a model router. Given a user message and a list of available \
AI models, select the single best model for responding to that message.

Consider each model's:
- Provider (the company that made it — DeepSeek, Anthropic, OpenAI, etc.)
- Model ID (the specific version — pro models are better at reasoning, \
flash models are faster and cheaper)
- Price (if shown — higher price usually means more capable)

Available models:
{model_list}

Respond with ONLY the model_id of the best model. Nothing else."""


# ---------------------------------------------------------------------------
# Single-step routing
# ---------------------------------------------------------------------------


async def route_message(
    db: AsyncSession,
    message: str,
    tenant_id: str,
    user_id: str,
) -> str | None:
    """Select the best model for a user message in one LLM call.

    1. Fetches all eligible models for the tenant.
    2. Picks the cheapest one as the classifier.
    3. Sends a prompt listing available models + the user message.
    4. The classifier LLM returns the best ``model_id``.
    5. Validates and resolves to the DB primary key.

    Returns:
        The chosen model's database ID, or None if no eligible models.
    """
    from ..db.orm.models import Model
    from ..models.base import get_chat_client
    from agent_framework import Message as MafMessage

    # 1. Fetch all eligible models
    result = await db.execute(
        select(Model).where(
            Model.tenant_id == tenant_id,
            Model.enabled == True,  # noqa: E712
            Model.auto_route_eligible == True,  # noqa: E712
        )
    )
    eligible = list(result.scalars().all())

    if not eligible:
        logger.warning("No eligible models found for tenant %s", tenant_id)
        return None

    # 2. Pick the cheapest model as the classifier (exclude from candidates)
    eligible.sort(key=lambda m: (m.input_price_per_1m or 999999))
    classifier_model = eligible[0]
    # Candidates: all eligible models EXCEPT the classifier itself
    candidates = [m for m in eligible if m.id != classifier_model.id]
    if not candidates:
        candidates = eligible  # fallback: use all if only one model exists

    # Build model list string (model_id · name · provider · price)
    model_lines = []
    for m in candidates:
        price_str = f"${m.input_price_per_1m}/1M" if m.input_price_per_1m else "free"
        model_lines.append(
            f"- {m.model_id} ({m.name}, {m.provider}, {price_str})"
        )

    system_prompt = ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
        model_list="\n".join(model_lines),
    )

    logger.debug(
        "Router prompt for session (tenant=%s):\n%s\n\nUser message: %.100s",
        tenant_id, system_prompt, message,
    )

    # 3. Call the classifier LLM
    try:
        client = get_chat_client(classifier_model, thinking_enabled=False)

        maf_messages = [
            MafMessage("system", [system_prompt]),
            MafMessage("user", [message]),
        ]
        response = await client.get_response(
            messages=maf_messages,
            options={"temperature": 0.0, "max_tokens": ROUTER_MAX_TOKENS},
        )
        raw = response.messages[-1].text if response.messages else ""
        chosen_model_id = (raw or "").strip().lower()

        logger.info(
            "🧠 Router selected model_id='%s' (classifier=%s, candidates=%d)",
            chosen_model_id, classifier_model.model_id, len(candidates),
        )

        # 4. Validate: find the matching model by model_id
        for m in candidates:
            if m.model_id.lower() == chosen_model_id:
                return m.id  # DB UUID

        logger.warning(
            "Classifier returned unknown model_id '%s' — falling back to %s",
            chosen_model_id, candidates[0].model_id,
        )
        return candidates[0].id

    except Exception:
        logger.exception("Model routing failed — falling back to cheapest candidate")
        return candidates[0].id
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
