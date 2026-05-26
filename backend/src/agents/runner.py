# =============================================================================
# PH Agent Hub — Agent Runner
# =============================================================================
# Per-request MAF agent assembly and execution.
#
# Primary entry points:
#   ``run_agent()``        — non-streaming (Phase 6)
#   ``run_agent_stream()`` — SSE streaming (Phase 7)
#
# Resolution chain (model):
#   session.selected_model_id → user.default_model_id
#   → skill.default_model_id → first accessible enabled model → ValidationError
#
# System prompt construction:
#   template.system_prompt + memory injection
# =============================================================================

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from sqlalchemy import select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework import (
    CharacterEstimatorTokenizer,
    TokenBudgetComposedStrategy,
    ToolResultCompactionStrategy,
)

from ..core.config import settings
from ..core.exceptions import ValidationError, NotFoundError
from ..core.redis import (
    append_temp_message,
    check_stream_cancel,
    clear_stream_cancel,
    get_temp_messages,
    get_temp_session,
    store_follow_up_questions,
)
from ..db.orm.messages import Message
from ..db.orm.models import Model
from ..db.orm.sessions import Session, SessionActiveTool
from ..db.orm.skills import Skill, SkillAllowedTool
from ..db.orm.templates import Template
from ..db.orm.tools import Tool
from ..db.orm.users import User
from ..models.base import get_chat_client
from ..services.usage_service import write_usage_log

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session config resolution (shared by run_agent / run_agent_stream)
# ---------------------------------------------------------------------------


@dataclass
class SessionConfig:
    """Resolved configuration for a single agent run."""
    model: Model
    model_client: Any
    system_prompt: str
    skill: Skill | None
    active_tool_callables: list
    execution_type: str
    agent_name: str
    thinking_enabled: bool
    reasoning_effort: str | None = None
    temperature: float = 0.7
    tenant_name: str = ""
    cleanup_clients: list = field(default_factory=list)

async def _resolve_session_config(
    db: AsyncSession,
    session_data: dict,
    tenant_id: str,
    user: User | None = None,
    file_ids: list[str] | None = None,
    user_message: str | None = None,
) -> SessionConfig:
    """Resolve model, system prompt, skill, tools, execution type, and agent name.

    Centralises the resolution chain shared by ``run_agent()``,
    ``run_agent_stream()``, and ``run_agent_assistant_only()``.
    """
    # 1. Resolve model
    model = await _resolve_model(db, session_data, user)

    # 2. Build system prompt (includes cross-session memory retrieval if enabled)
    system_prompt = await _build_system_prompt(
        db, session_data, user=user, user_message=user_message,
    )

    # 3. Resolve skill
    skill = await _resolve_skill(db, session_data)

    # 4. Resolve active tools
    context_length = getattr(model, "context_length", None)
    active_tool_callables, cleanup_clients = await _resolve_tool_callables(
        db, session_data, tenant_id, file_ids=file_ids,
        context_length=context_length,
    )

    # 5. Determine execution type and name
    execution_type = skill.execution_type if skill else "agent"
    # Normalize: workflow_based → workflow, prompt_based → agent
    if execution_type == "workflow_based":
        execution_type = "workflow"
    if execution_type == "prompt_based":
        execution_type = "agent"
    agent_name = skill.title if skill else "assistant"

    # 6. Resolve thinking_enabled: session override > model default
    thinking_enabled = session_data.get("thinking_enabled")
    if thinking_enabled is None:
        thinking_enabled = getattr(model, "thinking_enabled", False)

    # 7. Resolve reasoning_effort: session override > model default > None
    #    Only applies when thinking is enabled.
    reasoning_effort = session_data.get("reasoning_effort")
    if reasoning_effort is None:
        reasoning_effort = getattr(model, "reasoning_effort", None)
    # Clear reasoning_effort if thinking is disabled (no effect)
    if not thinking_enabled:
        reasoning_effort = None

    # 8. Resolve temperature: message override > session override > model default
    temperature = session_data.get("_message_temperature")
    if temperature is None:
        temperature = session_data.get("temperature")
    if temperature is None:
        temperature = getattr(model, "temperature", 0.7)

    # Rebuild model_client with resolved thinking_enabled and reasoning_effort
    model_client = get_chat_client(
        model,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
    )

    # Look up tenant name for denormalized usage logging
    tenant_name = ""
    try:
        from ..db.orm.tenants import Tenant as TenantORM
        tenant_result = await db.execute(
            select(TenantORM).where(TenantORM.id == tenant_id)
        )
        tenant_row = tenant_result.scalar_one_or_none()
        if tenant_row:
            tenant_name = tenant_row.name
    except Exception:
        logger.warning("Failed to resolve tenant name for tenant %s", tenant_id)

    return SessionConfig(
        model=model,
        model_client=model_client,
        system_prompt=system_prompt,
        skill=skill,
        active_tool_callables=active_tool_callables,
        execution_type=execution_type,
        agent_name=agent_name,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        tenant_name=tenant_name,
        cleanup_clients=cleanup_clients,
    )


# ---------------------------------------------------------------------------
# Message summarization (Issue #29)
# ---------------------------------------------------------------------------

# Conservative estimate: ~4 characters per token. Works across all
# model families without requiring provider-specific tokenizers.
CHARS_PER_TOKEN_ESTIMATE = 4

# Fraction of context_length at which auto-summarization triggers.
SUMMARIZE_THRESHOLD = 0.75

# Number of most recent user/assistant message *pairs* to keep intact
# when summarizing. Older messages are compressed into a summary.
KEEP_RECENT_PAIRS = 3

# Maximum summary length in characters (to keep the summary itself
# from consuming too much context).
# This is a fallback used when model context_length is unknown.
SUMMARY_CAP_FALLBACK = 2000

# Fallback for tool result truncation when model context_length is unknown.
# Replaced by context-aware caps for models that have context_length set.
TOOL_OUTPUT_CAP_FALLBACK = 100_000

# Maximum fraction of context_length a single tool result may use.
# Prevents any one result from dominating the context window.
# Converted to chars via CHARS_PER_TOKEN_ESTIMATE.
TOOL_OUTPUT_CAP_RATIO = 0.05

# Absolute ceiling in chars for any single tool result (≈125K tokens).
# Prevents even 1M-token models from accepting results that would
# consume more than 12.5% of the window in one call.
TOOL_OUTPUT_CAP_CEILING = 500_000

# Maximum fraction of context_length for conversation summaries.
SUMMARY_CAP_RATIO = 0.005

# Maximum seconds a single tool execution can run before being cancelled.
# Prevents a hung tool (e.g., stalled HTTP request, blocking I/O) from
# freezing the entire agent loop indefinitely.  The agent receives an
# error result and can retry or report the failure.
# SEC filings tools (get_filing_section, get_filing_text) may need up
# to 90s for large documents with section extraction.
TOOL_EXECUTION_TIMEOUT = 90

# Maximum seconds the MAF streaming loop will wait for the next update
# from the model before giving up.  Prevents the agent from hanging
# indefinitely when DeepSeek stops sending data mid-stream (e.g., while
# generating a partial function call).  After this idle period the stream
# is terminated and partial results are persisted.
MAF_STREAM_IDLE_TIMEOUT = 120

# ---------------------------------------------------------------------------
# Model-aware cap computation (Issue #207)
# ---------------------------------------------------------------------------


def _compute_tool_output_cap(context_length: int | None) -> int:
    """Compute a model-aware character cap for tool output truncation.

    Args:
        context_length: The model's total context window in tokens,
            or None if unknown.

    Returns:
        The maximum number of characters a single tool result may
        contribute to the agent context, bounded by the ceiling.
    """
    if not context_length:
        return TOOL_OUTPUT_CAP_FALLBACK
    cap_tokens = max(int(context_length * TOOL_OUTPUT_CAP_RATIO), 25_000)
    cap_chars = cap_tokens * CHARS_PER_TOKEN_ESTIMATE
    return min(cap_chars, TOOL_OUTPUT_CAP_CEILING)


def _compute_summary_cap(context_length: int | None) -> int:
    """Compute a model-aware character cap for conversation summaries.

    Args:
        context_length: The model's total context window in tokens,
            or None if unknown.

    Returns:
        The maximum number of characters a summary may contain.
    """
    if not context_length:
        return SUMMARY_CAP_FALLBACK
    cap = int(context_length * CHARS_PER_TOKEN_ESTIMATE * SUMMARY_CAP_RATIO)
    return max(cap, SUMMARY_CAP_FALLBACK)


# ---------------------------------------------------------------------------
# Platform identity (Issue #83)
# ---------------------------------------------------------------------------
# Static identity text loaded once at startup. Injected as the first block
# of every system prompt so the agent knows what PH Agent Hub is and what
# features it provides.

_AGENT_IDENTITY: str = ""
_IDENTITY_PATH = Path(__file__).parent / "identity.txt"


def load_agent_identity() -> None:
    """Load the platform identity text file into the module-level variable.

    Called once from main.py's lifespan at startup. Gracefully degrades
    if the file is missing or unreadable.
    """
    global _AGENT_IDENTITY
    try:
        if _IDENTITY_PATH.exists():
            _AGENT_IDENTITY = _IDENTITY_PATH.read_text(encoding="utf-8").strip()
            msg = (
                f"[identity] Loaded agent identity from {_IDENTITY_PATH} "
                f"({len(_AGENT_IDENTITY)} chars)"
            )
            print(msg, flush=True)
            logger.info(msg)
        else:
            msg = f"[identity] WARNING: Agent identity file not found at {_IDENTITY_PATH}"
            print(msg, flush=True)
            logger.warning(msg)
    except Exception:
        msg = f"[identity] WARNING: Failed to load agent identity from {_IDENTITY_PATH}"
        print(msg, flush=True)
        logger.warning(msg)
        import traceback
        traceback.print_exc()


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character length.

    Uses a conservative 4 chars/token ratio that slightly over-estimates
    for most models, ensuring we trigger summarization before truly
    running out of context.
    """
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def _extract_message_text(content: list | None) -> str:
    """Extract readable text from a message's JSON content array.

    Handles the structured content format used by _persist_assistant_message.
    """
    if not content or not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")
        if item_type == "text":
            text = item.get("text", "")
            if text:
                parts.append(text)
        elif item_type == "function_call":
            name = item.get("name", "unknown")
            parts.append(f"[Used tool: {name}]")
        elif item_type == "function_result":
            name = item.get("name", "unknown")
            output = item.get("output", "")
            if isinstance(output, str) and output:
                # Truncate tool output in history
                truncated = output[:300] + "..." if len(output) > 300 else output
                parts.append(f"[Tool result from {name}: {truncated}]")

    return "\n".join(parts)


def _msg_get(msg, attr: str, default=None):
    """Read an attribute from an ORM object or a key from a dict.

    Handles both Message ORM objects (DB) and message dicts (Redis).
    """
    if isinstance(msg, dict):
        return msg.get(attr, default)
    return getattr(msg, attr, default)


def _format_conversation_history(messages: list) -> str:
    """Format a list of message dicts/ORMs into a readable history string.

    Used to inject past conversation into the model context.
    Summarized messages are skipped (replaced by the summary system message).
    """
    if not messages:
        return ""

    lines: list[str] = ["[Previous conversation]"]

    for msg in messages:
        sender = _msg_get(msg, "sender", "")
        content = _msg_get(msg, "content")
        summarized = _msg_get(msg, "summarized", False)

        if summarized:
            continue  # Skip messages that have been compressed into a summary

        text = _extract_message_text(content)
        if not text:
            continue

        if sender == "user":
            lines.append(f"User: {text}")
        elif sender == "assistant":
            lines.append(f"Assistant: {text}")
        elif sender == "system":
            lines.append(f"[Summary of earlier conversation]\n{text}")

    if len(lines) == 1:
        return ""  # Only the header, no actual messages

    return "\n\n".join(lines)


def _build_history_string(
    messages: list,
    context_length: int | None,
) -> str:
    """Build conversation history for inclusion in the model context.

    Respects a token budget to avoid the history itself consuming too
    much of the context window. Includes up to 60% of context_length
    for history, leaving room for system prompt, tools, and the new
    user message + response.
    """
    if not messages:
        return ""

    full_history = _format_conversation_history(messages)
    if not full_history:
        return ""

    # Budget: 60% of context_length for history
    if context_length:
        history_budget_tokens = int(context_length * 0.6)
        history_budget_chars = history_budget_tokens * CHARS_PER_TOKEN_ESTIMATE
    else:
        history_budget_chars = 8000 * CHARS_PER_TOKEN_ESTIMATE  # ~32K chars default

    if len(full_history) <= history_budget_chars:
        return full_history

    # Truncate from the beginning (oldest messages first)
    # Keep the last history_budget_chars characters
    truncated = "...\n(earlier conversation omitted)\n\n" + full_history[-history_budget_chars:]
    return truncated


async def _get_messages_for_session(
    db: AsyncSession,
    session_id: str,
    is_temporary: bool,
) -> list:
    """Retrieve ordered, non-deleted messages for a session.

    Returns a list of Message ORM objects (DB) or message dicts (Redis).
    """
    if is_temporary:
        raw = await get_temp_messages(session_id)
        return raw or []
    else:
        result = await db.execute(
            select(Message)
            .where(
                Message.session_id == session_id,
                Message.is_deleted == False,  # noqa: E712
            )
            .order_by(Message.created_at)
        )
        return list(result.scalars().all())


async def _generate_summary(
    model: Model,
    model_client: Any,
    messages_to_summarize: list,
    temperature: float = 0.3,
) -> str | None:
    """Use the configured model to generate a concise summary of past messages.

    Detects existing summary system messages in the input and merges them
    into a single consolidated summary, avoiding summary accumulation over
    very long conversations.

    Creates a fresh non-thinking client for the summarization call.
    Returns None on failure (summarization is best-effort).
    """
    if not messages_to_summarize:
        return None

    # Separate existing summary system messages from regular messages.
    # Existing summaries should be merged into the new one rather than
    # treated as raw conversation, which would bloat the prompt.
    existing_summaries: list[str] = []
    regular_messages: list = []

    for msg in messages_to_summarize:
        sender = _msg_get(msg, "sender", "")
        if sender == "system":
            # This is a previous summary — extract its text
            content = _msg_get(msg, "content")
            text = _extract_message_text(content)
            if text:
                existing_summaries.append(text)
        else:
            regular_messages.append(msg)

    # Build the conversation text for the regular (non-summary) messages
    conversation_text = _format_conversation_history(regular_messages)

    # Build the full prompt: merge existing summaries with new content
    if existing_summaries:
        # There are previous summaries — ask the LLM to merge them
        summaries_block = "\n".join(
            f"- {s}" for s in existing_summaries
        )
        if conversation_text:
            summary_prompt = (
                "You are maintaining a running conversation summary. "
                "Below are one or more EXISTING summaries of earlier parts "
                "of the conversation, followed by newer messages. "
                "Merge everything into a SINGLE concise summary.\n\n"
                "=== EXISTING SUMMARIES (merge these into your response) ===\n"
                f"{summaries_block}\n\n"
                "=== NEWER CONVERSATION ===\n"
                f"{conversation_text}\n\n"
                "Produce ONE consolidated summary. Focus on key topics, "
                "decisions, important facts, and pending questions. "
                "Write in plain English, no more than 200 words. "
                "Do NOT use meta-phrases like 'The conversation covered' — "
                "just state the facts directly as a compact record."
            )
        else:
            # Only summaries, no regular messages — merge them directly
            summary_prompt = (
                "Below are multiple conversation summaries. "
                "Merge them into a SINGLE concise summary.\n\n"
                "=== SUMMARIES TO MERGE ===\n"
                f"{summaries_block}\n\n"
                "Produce ONE consolidated summary. Focus on key topics, "
                "decisions, important facts, and pending questions. "
                "Write in plain English, no more than 200 words. "
                "Do NOT use meta-phrases — just state the facts directly."
            )
    elif conversation_text:
        # No existing summaries — standard summarization
        summary_prompt = (
            "Summarize the following conversation concisely. "
            "Focus on key topics discussed, decisions made, important facts shared, "
            "and any pending questions or action items. "
            "Write in plain English, no more than 200 words. "
            "Do NOT include phrases like 'The conversation covered' or 'The user and assistant discussed' — "
            "just state the facts directly as a compact record.\n\n"
            f"{conversation_text}"
        )
    else:
        return None

    messages_payload = [
        {"role": "user", "content": summary_prompt},
    ]

    try:
        # Create a fresh client (non-thinking mode for simpler output)
        from ..models.base import get_chat_client

        fresh_client = get_chat_client(model, thinking_enabled=False)

        model_max_tokens = getattr(model, "max_tokens", None) or 4096
        summary_max_tokens = max(300, min(model_max_tokens, 2000))

        # Use MAF's standard get_response() instead of accessing .client
        from agent_framework import Message as MafMessage

        maf_messages = [
            MafMessage("user", [summary_prompt]),
        ]
        maf_response = await fresh_client.get_response(
            messages=maf_messages,
            options={"temperature": temperature, "max_tokens": summary_max_tokens},
        )
        summary = maf_response.messages[-1].text if maf_response.messages else ""
        summary = (summary or "").strip()

        model_context_length = getattr(model, "context_length", None)
        summary_cap = _compute_summary_cap(model_context_length)
        if summary and len(summary) > summary_cap:
            summary = summary[:summary_cap] + "..."

        return summary if summary else None

    except Exception:
        logger.exception("Failed to generate conversation summary")
        return None


async def _maybe_summarize_and_build_context(
    db: AsyncSession,
    session_data: dict,
    cfg: "SessionConfig",
    user_message: str,
) -> tuple[str, dict | None]:
    """Check if summarization is needed and build the contextualized message.

    Returns:
        A tuple of (contextualized_message, summary_info_or_None).
        summary_info is a dict with keys: summary_text, summarized_count,
        tokens_saved — used for the SSE summarized event.
    """
    session_id = session_data["id"]
    is_temporary = session_data.get("is_temporary", False)
    context_length = getattr(cfg.model, "context_length", None)

    # Get all messages
    all_messages = await _get_messages_for_session(db, session_id, is_temporary)

    if not all_messages:
        return user_message, None

    # Estimate total tokens of the full context
    system_prompt_tokens = _estimate_tokens(cfg.system_prompt or "")
    user_msg_tokens = _estimate_tokens(user_message)
    history_tokens = sum(
        _estimate_tokens(_extract_message_text(
            _msg_get(m, "content")
        ))
        for m in all_messages
        if not _msg_get(m, "summarized", False)
    )
    total_estimated = system_prompt_tokens + user_msg_tokens + history_tokens

    # Determine threshold
    threshold_tokens = int((context_length or 8192) * SUMMARIZE_THRESHOLD)

    summary_info: dict | None = None

    if total_estimated > threshold_tokens and context_length:
        # Need to summarize. Keep the last KEEP_RECENT_PAIRS pairs intact.
        # Messages are in chronological order; find the cutoff point.
        non_summarized = [
            m for m in all_messages
            if not _msg_get(m, "summarized", False)
        ]

        # Count user+assistant pairs from the end
        pair_count = 0
        cutoff_idx = len(non_summarized)
        for i in range(len(non_summarized) - 1, -1, -1):
            msg = non_summarized[i]
            sender = _msg_get(msg, "sender", "")
            if sender == "user":
                pair_count += 1
                if pair_count >= KEEP_RECENT_PAIRS:
                    cutoff_idx = i
                    break

        messages_to_summarize = non_summarized[:cutoff_idx]

        if messages_to_summarize:
            logger.info(
                "Auto-summarizing %d messages for session %s "
                "(estimated %d tokens, threshold %d)",
                len(messages_to_summarize), session_id,
                total_estimated, threshold_tokens,
            )

            summary_text = await _generate_summary(
                model=cfg.model,
                model_client=cfg.model_client,
                messages_to_summarize=messages_to_summarize,
                temperature=cfg.temperature,
            )

            if summary_text:
                # Mark messages as summarized
                if is_temporary:
                    # For temp sessions, update the Redis messages
                    all_raw = await get_temp_messages(session_id)
                    for raw_msg in all_raw:
                        raw_id = raw_msg.get("id", "")
                        for to_mark in messages_to_summarize:
                            mark_id = _msg_get(to_mark, "id", "")
                            if raw_id == mark_id:
                                raw_msg["summarized"] = True
                    # Re-store updated messages with TTL refresh
                    from ..core.redis import get_redis
                    r = await get_redis()
                    import json as _json
                    msg_key = f"session:tmp:{session_id}:messages"
                    session_key = f"session:tmp:{session_id}"
                    ttl = await r.ttl(session_key)
                    if ttl and ttl > 0:
                        await r.setex(msg_key, ttl, _json.dumps(all_raw))
                    else:
                        await r.setex(msg_key, settings.TEMPORARY_SESSION_TTL_SECONDS, _json.dumps(all_raw))
                else:
                    # For DB sessions, update the summarized flag
                    for msg in messages_to_summarize:
                        if hasattr(msg, "summarized"):
                            msg.summarized = True
                    await db.commit()

                # Store the summary as a system message
                summary_content = [{"type": "text", "text": summary_text}]
                await _persist_system_message(
                    db=db,
                    session_id=session_id,
                    is_temporary=is_temporary,
                    content=summary_content,
                )

                # Calculate tokens saved
                old_tokens = sum(
                    _estimate_tokens(_extract_message_text(
                        _msg_get(m, "content")
                    ))
                    for m in messages_to_summarize
                )
                new_tokens = _estimate_tokens(summary_text)
                tokens_saved = max(0, old_tokens - new_tokens)

                summary_info = {
                    "summary_text": summary_text,
                    "summarized_count": len(messages_to_summarize),
                    "tokens_saved": tokens_saved,
                }

                logger.info(
                    "Summarization complete for session %s: "
                    "%d messages compressed, ~%d tokens saved",
                    session_id, len(messages_to_summarize), tokens_saved,
                )

    # Build the contextualized message with history
    # Re-fetch messages (summarization may have changed the list)
    current_messages = await _get_messages_for_session(db, session_id, is_temporary)
    history_str = _build_history_string(current_messages, context_length)

    if history_str:
        contextualized = f"{history_str}\n\n---\n\nCurrent message: {user_message}"
        return contextualized, summary_info

    return user_message, summary_info


async def _persist_system_message(
    db: AsyncSession,
    session_id: str,
    is_temporary: bool,
    content: list[dict],
) -> str:
    """Persist a system message (used for conversation summaries).

    Returns the message ID.
    """
    msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if is_temporary:
        await append_temp_message(
            session_id,
            {
                "id": msg_id,
                "sender": "system",
                "content": content,
                "parent_message_id": None,
                "branch_index": 0,
                "created_at": now.isoformat(),
            },
        )
    else:
        msg = Message(
            id=msg_id,
            session_id=session_id,
            sender="system",
            content=content,
            parent_message_id=None,
            branch_index=0,
            created_at=now,
        )
        db.add(msg)
        await db.commit()

    return msg_id


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_agent(
    session_data: dict,
    user_message: str,
    db: AsyncSession,
    current_user: User | None = None,
    file_ids: list[str] | None = None,
) -> tuple[str, str]:
    """Assemble and run a MAF agent for a single user message.

    Args:
        session_data: Unified session dict (from DB or Redis).
        user_message: The text the user sent.
        db: Active async DB session.
        current_user: The authenticated user (or None for guest sessions).
        file_ids: Optional list of FileUpload IDs to link to the user message.

    Returns:
        A tuple of (assistant response text, assistant message ID).
    """
    tenant_id = current_user.tenant_id if current_user else session_data.get("tenant_id", "")
    session_id = session_data["id"]
    is_temporary = session_data.get("is_temporary", False)

    # ---- 1-6. Resolve session config ------------------------------------
    cfg = await _resolve_session_config(db, session_data, tenant_id, current_user, file_ids=file_ids, user_message=user_message)

    # ---- Build contextualized message (history + auto-summarization) ----
    contextualized_message, _ = await _maybe_summarize_and_build_context(
        db=db,
        session_data=session_data,
        cfg=cfg,
        user_message=user_message,
    )

    # ---- 7. Run agent or workflow ----------------------------------------
    raw_response: str

    try:
        try:
            if cfg.execution_type == "workflow":
                raw_response, tokens_in, tokens_out = await _run_workflow(
                    model=cfg.model,
                    skill=cfg.skill,
                    model_client=cfg.model_client,
                    system_prompt=cfg.system_prompt,
                    tools=cfg.active_tool_callables,
                    user_message=contextualized_message,
                    agent_name=cfg.agent_name,
                    temperature=cfg.temperature,
                    reasoning_effort=cfg.reasoning_effort,
                )
                cache_hit_tokens = 0
            else:
                raw_response, tokens_in, tokens_out, cache_hit_tokens = await _run_agent(
                    model=cfg.model,
                    model_client=cfg.model_client,
                    system_prompt=cfg.system_prompt,
                    tools=cfg.active_tool_callables,
                    user_message=contextualized_message,
                    agent_name=cfg.agent_name,
                    temperature=cfg.temperature,
                    reasoning_effort=cfg.reasoning_effort,
                )
        except Exception as exc:
            logger.error("Agent run failed: %s", exc)
            raise ValidationError(
                f"Agent execution failed: {exc}"
            ) from exc

        # ---- 8. Persist messages --------------------------------------------
        _user_msg_id, assistant_msg_id = await _persist_messages(
            db=db,
            session_id=session_id,
            is_temporary=is_temporary,
            user_message=user_message,
            assistant_response=raw_response,
            model_id=cfg.model.id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )

        # ---- Background: embed user message for cross-session retrieval -----
        if not is_temporary:
            try:
                from ..services.embedding_service import embed_message as _embed_msg

                asyncio.ensure_future(_embed_msg(
                    db=await _fresh_db_session(),
                    message_id=_user_msg_id,
                    user_id=current_user.id,
                    tenant_id=current_user.tenant_id,
                    text=user_message,
                ))
            except Exception:
                logger.warning("Failed to schedule embedding for message %s", _user_msg_id, exc_info=True)

        # ---- 9. Link file uploads to the user message ------------------------
        if file_ids and not is_temporary:
            from ..services import upload_service as _upload_svc

            await _upload_svc.link_uploads_to_message(
                db=db,
                file_ids=file_ids,
                message_id=_user_msg_id,
                user_id=current_user.id,
            )

        # ---- 10. Write usage log (non-streaming) -----------------------------
        if current_user is not None:
            try:
                await write_usage_log(
                    db,
                    tenant_id=current_user.tenant_id,
                    tenant_name=getattr(cfg, "tenant_name", "") or "",
                    user_id=current_user.id,
                    user_email=current_user.email,
                    user_full_name=current_user.display_name,
                    model_id=cfg.model.id,
                    model_name=cfg.model.name,
                    provider=cfg.model.provider,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cache_hit_tokens=cache_hit_tokens,
                    input_price=getattr(cfg.model, "input_price_per_1m", None),
                    output_price=getattr(cfg.model, "output_price_per_1m", None),
                    cache_hit_price=getattr(cfg.model, "cache_hit_price_per_1m", None),
                )
            except Exception:
                logger.exception("Failed to write usage log (non-streaming)")

        return raw_response, assistant_msg_id

    finally:
        # ---- Close ERPNext httpx clients & MCPTool connections -----------
        if cfg is not None and cfg.cleanup_clients:
            for client in cfg.cleanup_clients:
                try:
                    if hasattr(client, "aclose"):
                        await client.aclose()
                    else:
                        await client.close()
                except Exception:
                    logger.debug("Failed to close client %s", type(client).__name__, exc_info=True)


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


async def _resolve_model(
    db: AsyncSession, session_data: dict, user: User | None = None
) -> Model:
    """Resolve the model client following the fallback chain.

    Resolution order:
    1. session.selected_model_id
    2. user.default_model_id
    3. skill.default_model_id
    4. first accessible enabled model for the user
    5. ValidationError
    """
    tenant_id = session_data.get("tenant_id", "")
    user_id = session_data.get("user_id", "")

    # 1. session.selected_model_id
    model_id = session_data.get("selected_model_id")
    if model_id:
        result = await db.execute(select(Model).where(Model.id == model_id))
        model = result.scalar_one_or_none()
        if model:
            return model

    # 2. user.default_model_id
    if user and user.default_model_id:
        result = await db.execute(
            select(Model).where(Model.id == user.default_model_id)
        )
        model = result.scalar_one_or_none()
        if model:
            return model

    # 3. skill.default_model_id
    skill_id = session_data.get("selected_skill_id")
    if skill_id:
        result = await db.execute(select(Skill).where(Skill.id == skill_id))
        skill = result.scalar_one_or_none()
        if skill and skill.default_model_id:
            result = await db.execute(
                select(Model).where(Model.id == skill.default_model_id)
            )
            model = result.scalar_one_or_none()
            if model:
                return model

    # 4. first accessible enabled model
    from ..services.model_service import list_models as _svc_list_models
    models, _ = await _svc_list_models(
        db, tenant_id=tenant_id, user_id=user_id
    )
    enabled = [m for m in models if m.enabled]
    if enabled:
        return enabled[0]

    raise ValidationError(
        "No model configured. Please select a model for this session, "
        "or configure a default model on your profile or skill."
    )


async def _build_system_prompt(
    db: AsyncSession,
    session_data: dict,
    user: User | None = None,
    user_message: str | None = None,
) -> str:
    """Build the system prompt from template + agent memory + cross-session retrieval.

    Resolution chain:
    1. session.selected_template_id (manual override in chat)
    2. skill.template_id (from the selected skill's config)
    3. "You are a helpful assistant." (hardcoded fallback)

    If ``user_message`` is provided and cross-session retrieval is enabled,
    past conversations semantically similar to the user message are injected
    as an additional context block (Issue #229).
    """
    template_id = session_data.get("selected_template_id")

    # Fall back to the skill's template if no session-level template is set
    if not template_id:
        skill_id = session_data.get("selected_skill_id")
        if skill_id:
            result = await db.execute(select(Skill).where(Skill.id == skill_id))
            skill = result.scalar_one_or_none()
            if skill and skill.template_id:
                template_id = skill.template_id

    parts: list[str] = []

    # ---- Platform identity (Issue #83) -----------------------------------
    # Always inject the platform identity as the first block so the agent
    # knows what PH Agent Hub is and can answer questions about itself.
    if _AGENT_IDENTITY:
        parts.append(_AGENT_IDENTITY)

    # Template system prompt
    if template_id:
        result = await db.execute(
            select(Template).where(Template.id == template_id)
        )
        template = result.scalar_one_or_none()
        if template:
            parts.append(template.system_prompt)

    # ---- Agent memory injection -------------------------------------------
    # Query global memory entries (session_id IS NULL) for the current user
    # and append them as a persistent context block.
    if user:
        try:
            from ..db.orm.memory import Memory as MemoryORM

            result = await db.execute(
                select(MemoryORM).where(
                    MemoryORM.user_id == user.id,
                    MemoryORM.tenant_id == user.tenant_id,
                    MemoryORM.session_id.is_(None),
                ).order_by(MemoryORM.created_at.desc())
            )
            memories = result.scalars().all()

            if memories:
                memory_lines: list[str] = [
                    "## Persistent User Memory",
                    "",
                    "The following information has been saved about the user "
                    "across conversations.  Use this context to personalise "
                    "your responses, but do NOT mention these entries unless "
                    "they are relevant to the current conversation:",
                    "",
                ]
                for m in memories:
                    source_tag = "[auto]" if m.source == "automatic" else "[user]"
                    memory_lines.append(f"- {source_tag} **{m.key}**: {m.value}")

                parts.append("\n".join(memory_lines))
        except DatabaseError as e:
            logger.error(
                "Database error loading agent memory for user=%s, "
                "tenant=%s: %s",
                user.id, user.tenant_id, e,
            )
            parts.append(
                "Note: Your saved memories could not be loaded due to a "
                "temporary database error."
            )
        except Exception:
            logger.error(
                "Failed to load agent memory for user=%s, tenant=%s",
                user.id, user.tenant_id, exc_info=True,
            )
            parts.append(
                "Note: Your saved memories could not be loaded due to an "
                "unexpected error."
            )

    # ---- Cross-session memory retrieval (Issue #229) -----------------------
    # Inject relevant past conversation snippets via semantic search.
    if user and user_message:
        try:
            from ..services.embedding_service import (
                embed_query as _embed_query,
                retrieve_similar as _retrieve_similar,
            )

            # Resolve whether retrieval is enabled:
            #   session override (tri-state) → skill default → off
            retrieval_enabled = session_data.get("cross_session_retrieval_enabled")
            if retrieval_enabled is None:
                # Fall back to skill config
                skill_id = session_data.get("selected_skill_id")
                if skill_id:
                    result = await db.execute(
                        select(Skill).where(Skill.id == skill_id)
                    )
                    skill_row = result.scalar_one_or_none()
                    if skill_row:
                        retrieval_enabled = skill_row.cross_session_retrieval_enabled
                        max_snippets = skill_row.cross_session_max_snippets
                        min_score = skill_row.cross_session_min_score
                    else:
                        retrieval_enabled = False
                        max_snippets = 3
                        min_score = 0.70
                else:
                    retrieval_enabled = False
                    max_snippets = 3
                    min_score = 0.30
            else:
                # Use session-level defaults when skill doesn't apply
                max_snippets = 3
                min_score = 0.30

            if retrieval_enabled:
                query_emb = await _embed_query(user_message)
                if query_emb:
                    similar = await _retrieve_similar(
                        db=db,
                        user_id=user.id,
                        tenant_id=user.tenant_id,
                        query_embedding=query_emb,
                        session_id=session_data.get("id"),
                        top_k=max_snippets,
                        min_score=min_score,
                    )

                    if similar:
                        snippet_lines: list[str] = [
                            "## Relevant Past Conversations",
                            "",
                            "The following excerpts from your past conversations "
                            "may be relevant to the current message. "
                            "Use this context if helpful, but do NOT mention "
                            "these entries unless they are directly relevant:",
                            "",
                        ]
                        for s in similar:
                            session_label = s.get("session_title") or "Unknown session"
                            snippet_lines.append(
                                f"- [Session: {session_label} | "
                                f"Relevance: {s['score']:.2f}]"
                            )
                            # Truncate user message
                            user_txt = s.get("user_text", "")
                            if len(user_txt) > 300:
                                user_txt = user_txt[:300] + "..."
                            snippet_lines.append(f"  User: {user_txt}")

                            asst_txt = s.get("assistant_text")
                            if asst_txt:
                                if len(asst_txt) > 500:
                                    asst_txt = asst_txt[:500] + "..."
                                snippet_lines.append(f"  Assistant: {asst_txt}")

                            snippet_lines.append("")

                        parts.append("\n".join(snippet_lines))
                        logger.debug(
                            "Injected %d cross-session snippets for user=%s",
                            len(similar), user.id,
                        )
        except Exception:
            logger.warning(
                "Failed to retrieve cross-session memory for user=%s",
                user.id, exc_info=True,
            )

    # ---- RAG document context injection (Issue #250) -----------------------
    # When the session has uploaded files that have been RAG-indexed,
    # retrieve relevant chunks and inject them as context.
    if user and user_message:
        try:
            from ..services.rag_service import search_documents as _rag_search

            # Determine which file IDs are relevant to this session
            rag_file_ids = None

            # Check if there are uploaded files in this session
            session_id = session_data.get("id")
            if session_id:
                from ..db.orm.file_uploads import FileUpload as FileUploadORM

                result = await db.execute(
                    select(FileUploadORM).where(
                        FileUploadORM.session_id == session_id,
                        FileUploadORM.tenant_id == user.tenant_id,
                    )
                )
                session_uploads = list(result.scalars().all())
                if session_uploads:
                    rag_file_ids = [u.id for u in session_uploads]

            if rag_file_ids:
                rag_results = await _rag_search(
                    db=db,
                    query=user_message,
                    tenant_id=user.tenant_id,
                    top_k=5,
                    file_ids=rag_file_ids,
                )

                if rag_results:
                    rag_lines: list[str] = [
                        "## Referenced Documents",
                        "",
                        "The following excerpts from your uploaded documents "
                        "are relevant to the current message. "
                        "Use this information to answer the user's question "
                        "when applicable:",
                        "",
                    ]
                    seen_files: set[str] = set()
                    for r in rag_results:
                        fname = r.get("filename", "Unknown")
                        if fname not in seen_files:
                            seen_files.add(fname)
                            rag_lines.append(f"**File: {fname}**")
                        rag_lines.append(
                            f"- (relevance: {r['score']:.2f}) {r['text'][:600]}"
                        )
                        if len(r['text']) > 600:
                            rag_lines[-1] += "..."
                        rag_lines.append("")

                    parts.append("\n".join(rag_lines))
                    logger.debug(
                        "Injected %d RAG chunks for user=%s, tenant=%s",
                        len(rag_results), user.id, user.tenant_id,
                    )
        except Exception:
            logger.warning(
                "Failed to retrieve RAG context for user=%s",
                user.id, exc_info=True,
            )

    if parts:
        return "\n\n---\n\n".join(parts)

    return "You are a helpful assistant."


async def _resolve_skill(
    db: AsyncSession, session_data: dict
) -> Skill | None:
    """Load the selected skill, if any."""
    skill_id = session_data.get("selected_skill_id")
    if not skill_id:
        return None

    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    return result.scalar_one_or_none()


async def _resolve_tool_callables(
    db: AsyncSession,
    session_data: dict,
    tenant_id: str,
    file_ids: list[str] | None = None,
    context_length: int | None = None,
) -> tuple[list, list]:
    """Resolve active tools into MAF tool callables.

    Returns:
        A tuple of (callables, cleanup_clients) where cleanup_clients is a
        list of httpx.AsyncClient instances that must be closed after the
        agent run completes.

    Resolution chain:
    1. session_active_tools (manually activated by user in chat)
    2. skill.tool_ids (from the selected skill's config, if no session tools)
    """
    session_id = session_data["id"]
    cleanup_clients: list = []
    is_temporary = session_data.get("is_temporary", False)

    # Load Tool ORM records
    tools: list[Tool] = []

    if is_temporary:
        # Temp session: use active_tool_ids from the Redis blob
        tool_ids = session_data.get("active_tool_ids", [])
        if tool_ids:
            result = await db.execute(
                select(Tool).where(
                    Tool.id.in_(tool_ids),
                    Tool.tenant_id == tenant_id,
                    Tool.enabled == True,  # noqa: E712
                )
            )
            tools = list(result.scalars().all())
    else:
        # Permanent session: query the join table
        result = await db.execute(
            select(Tool)
            .join(SessionActiveTool, SessionActiveTool.tool_id == Tool.id)
            .where(
                SessionActiveTool.session_id == session_id,
                Tool.tenant_id == tenant_id,
                Tool.enabled == True,  # noqa: E712
            )
        )
        tools = list(result.scalars().all())

    # ---- Fall back to skill's tools if no session tools are active ----
    if not tools:
        skill_id = session_data.get("selected_skill_id")
        if skill_id:
            skill = await db.execute(select(Skill).where(Skill.id == skill_id))
            skill = skill.scalar_one_or_none()
            if skill:
                # Load tools from skill_allowed_tools join table
                skill_tool_result = await db.execute(
                    select(Tool)
                    .join(SkillAllowedTool, SkillAllowedTool.tool_id == Tool.id)
                    .where(
                        SkillAllowedTool.skill_id == skill_id,
                        Tool.tenant_id == tenant_id,
                        Tool.enabled == True,  # noqa: E712
                    )
                )
                tools = list(skill_tool_result.scalars().all())
                if tools:
                    logger.info(
                        "Using %d tool(s) from skill '%s' (no session tools active)",
                        len(tools), skill.title,
                    )

    # Build callables for each tool
    callables: list = []
    for tool in tools:
        tool_callables = await _build_tool_callables(
            db, tool, tenant_id, session_id=session_id, cleanup_clients=cleanup_clients
        )
        callables.extend(tool_callables)

    # ---- Built-in tool: list_uploaded_files (always available) ----------
    # This discovery tool lets the agent see what files are attached to
    # the session without polluting the user message with injected text.
    try:
        from ..tools.file_list import build_file_list_tool
        file_list_tools = build_file_list_tool(
            db, session_id, tenant_id,
            is_temporary=is_temporary,
            uploaded_file_ids=session_data.get("uploaded_file_ids"),
        )
        callables.extend(file_list_tools)
    except Exception:
        logger.warning(
            "Failed to build file_list tool for session %s", session_id, exc_info=True
        )

    # ---- Built-in tool: memory (always available, except temp) ----------
    # Gives the agent persistent memory: save, delete, and list entries.
    # Skipped for temporary sessions — they should leave no DB trace.
    user_id = session_data.get("user_id", "")
    if user_id and not is_temporary:
        try:
            from ..tools.memory import build_memory_tools
            memory_tools = build_memory_tools(db, user_id, tenant_id)
            callables.extend(memory_tools)
        except Exception:
            logger.warning(
                "Failed to build memory tools for user=%s", user_id, exc_info=True
            )

    # ---- Cap tool output size to prevent context overflow (Issue #206) ----
    # Wrap every FunctionTool with a truncation wrapper that caps
    # oversized results before they enter the MAF agent context.
    callables = [_wrap_tool_with_output_cap(t, context_length=context_length) for t in callables]

    # ---- Connect MCPTool instances -----------------------------------------
    # MCP tools (from MCP servers) need an active connection before the
    # agent can invoke them.  Connect now and register them for cleanup
    # so they are disconnected after the agent run completes.
    from agent_framework import MCPStreamableHTTPTool, MCPStdioTool, MCPWebsocketTool

    _MCP_TYPES = (MCPStreamableHTTPTool, MCPStdioTool, MCPWebsocketTool)

    for item in callables[:]:  # iterate over a copy so we can remove items
        if isinstance(item, _MCP_TYPES):
            try:
                await item.connect()
                cleanup_clients.append(item)
                logger.info("Connected MCP tool '%s'", getattr(item, "name", "unknown"))
            except Exception:
                logger.warning(
                    "Failed to connect MCP tool '%s'; removing from agent tools",
                    getattr(item, "name", "unknown"),
                    exc_info=True,
                )
                callables.remove(item)

    return callables, cleanup_clients


async def _build_tool_callables(
    db: AsyncSession,
    tool: Tool,
    tenant_id: str,
    session_id: str = "",
    cleanup_clients: list | None = None,
) -> list:
    """Dispatch on tool.type to the appropriate factory."""
    if tool.type == "erpnext":
        return await _build_erpnext_callables(db, tool, tenant_id, session_id=session_id, cleanup_clients=cleanup_clients)
    elif tool.type == "membrane":
        from ..tools.membrane import build_membrane_tools
        return build_membrane_tools(tool.config or {})
    elif tool.type == "custom":
        from ..tools.custom_tool_executor import build_custom_tool_from_code
        if tool.code:
            return build_custom_tool_from_code(tool.code, tool.config or {})
        logger.warning("Custom tool %s has no code", tool.id)
        return []
    elif tool.type == "datetime":
        from ..tools.datetime import build_datetime_tools
        return build_datetime_tools(tool.config or {})
    elif tool.type == "web_search":
        from ..tools.web_search import build_web_search_tools
        return build_web_search_tools(tool.config or {})
    elif tool.type == "fetch_url":
        from ..tools.fetch_url import build_fetch_url_tools
        return build_fetch_url_tools(tool.config or {})
    elif tool.type == "weather":
        from ..tools.weather import build_weather_tools
        return build_weather_tools(tool.config or {})
    elif tool.type == "calculator":
        from ..tools.calculator import build_calculator_tools
        return build_calculator_tools(tool.config or {})
    elif tool.type == "wikipedia":
        from ..tools.wikipedia import build_wikipedia_tools
        return build_wikipedia_tools(tool.config or {})
    elif tool.type == "rss_feed":
        from ..tools.rss_feed import build_rss_feed_tools
        return build_rss_feed_tools(tool.config or {})
    elif tool.type == "currency_exchange":
        from ..tools.currency_exchange import build_currency_exchange_tools
        return build_currency_exchange_tools(tool.config or {})
    elif tool.type == "market_overview":
        from ..tools.market_overview import build_market_overview_tools
        return build_market_overview_tools(tool.config or {})
    elif tool.type == "etf_data":
        from ..tools.etf_data import build_etf_data_tools
        return build_etf_data_tools(tool.config or {})
    elif tool.type == "stock_data":
        from ..tools.stock_data import build_stock_data_tools
        return build_stock_data_tools(tool.config or {})
    elif tool.type == "portfolio":
        from ..tools.portfolio import build_portfolio_tools
        return build_portfolio_tools(tool.config or {})
    elif tool.type == "sec_filings":
        from ..tools.sec_filings import build_sec_filings_tools
        return build_sec_filings_tools(tool.config or {})
    elif tool.type == "code_interpreter":
        from ..tools.code_interpreter import build_code_interpreter_tools
        return build_code_interpreter_tools(tool.config or {})
    elif tool.type == "sql_query":
        from ..tools.sql_query import build_sql_query_tools
        return build_sql_query_tools(tool.config or {})
    elif tool.type == "document_generation":
        from ..tools.document_generation import build_document_generation_tools
        return build_document_generation_tools(
            tool.config or {}, tenant_id=tenant_id
        )
    elif tool.type == "browser":
        from ..tools.browser import build_browser_tools
        return build_browser_tools(
            tool.config or {}, tenant_id=tenant_id
        )
    elif tool.type == "pdf_extractor":
        from ..tools.pdf_extractor import build_pdf_extractor_tools
        return build_pdf_extractor_tools(tool.config or {})
    elif tool.type == "rag_search":
        from ..tools.rag_search import build_rag_search_tools
        return build_rag_search_tools(
            tool.config or {}, db=db, tenant_id=tenant_id
        )
    elif tool.type == "github":
        from ..tools.github import build_github_tools
        return build_github_tools(tool.config or {})
    elif tool.type == "calendar":
        from ..tools.calendar import build_calendar_tools
        return build_calendar_tools(tool.config or {})
    elif tool.type == "image_generation":
        from ..tools.image_generation import build_image_generation_tools
        return build_image_generation_tools(
            tool.config or {}, tenant_id=tenant_id
        )
    elif tool.type == "slack":
        from ..tools.slack import build_slack_tools
        return build_slack_tools(tool.config or {})
    elif tool.type == "email":
        from ..tools.email import build_email_tools
        return build_email_tools(tool.config or {})
    elif tool.type == "mcp":
        from ..tools.mcp import build_mcp_tool_callables
        return await build_mcp_tool_callables(
            db, tool, tenant_id,
            session_id=session_id,
            cleanup_clients=cleanup_clients,
        )
    else:
        logger.warning("Unknown tool type '%s' for tool %s", tool.type, tool.id)
        return []


async def _build_erpnext_callables(
    db: AsyncSession,
    tool: Tool,
    tenant_id: str,
    session_id: str = "",
    cleanup_clients: list | None = None,
) -> list:
    """Build ERPNext tool callables for a given Tool record.

    Reads credentials directly from ``tool.config`` JSON.
    Queries ALL FileUpload records for the session so the
    ``upload_file`` tool can access files uploaded in any
    message — the built-in ``list_uploaded_files`` tool
    acts as the discovery guard against misuse.

    Creates a shared ``httpx.AsyncClient`` that is registered
    on *cleanup_clients* so the caller can close it after the
    agent run completes.
    """
    import httpx
    from ..tools.erpnext import build_erpnext_tools

    config = tool.config or {}
    base_url = config.get("base_url")
    api_key = config.get("api_key")
    api_secret = config.get("api_secret")

    if not all([base_url, api_key, api_secret]):
        raise NotFoundError(
            f"ERPNext tool '{tool.name}' is missing required config fields. "
            "Set base_url, api_key, and api_secret in the tool config."
        )

    # Build the shared httpx client — caller is responsible for closing it
    auth_header = {"Authorization": f"token {api_key}:{api_secret}"}
    erpnext_client = httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=auth_header,
        timeout=30.0,
    )
    if cleanup_clients is not None:
        cleanup_clients.append(erpnext_client)

    # Resolve file infos from ALL session file uploads
    file_infos: list[dict] | None = None
    if session_id:
        from ..db.orm.file_uploads import FileUpload
        result = await db.execute(
            select(FileUpload).where(
                FileUpload.session_id == session_id,
                FileUpload.tenant_id == tenant_id,
            )
        )
        uploads = list(result.scalars().all())
        if uploads:
            file_infos = [
                {
                    "id": u.id,
                    "storage_key": u.storage_key,
                    "bucket": u.bucket,
                    "original_filename": u.original_filename,
                    "content_type": u.content_type,
                }
                for u in uploads
            ]

    return build_erpnext_tools(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        httpx_client=erpnext_client,
        file_infos=file_infos,
    )


# ---------------------------------------------------------------------------
# Mid-run compaction (Issue #199 — prevents context overflow on tool-heavy runs)
# ---------------------------------------------------------------------------


def _wrap_tool_with_output_cap(tool_obj: Any, context_length: int | None = None) -> Any:
    """Wrap a MAF FunctionTool to cap its output size before it enters the agent context.

    Patches ``FunctionTool.func`` with an async wrapper that truncates
    oversized results via ``_truncate_tool_output``.  Preserves original
    function metadata so MAF tool detection, schema generation, and
    logging continue to work correctly.

    The cap is derived from the model's ``context_length`` when available,
    falling back to ``TOOL_OUTPUT_CAP_FALLBACK`` when unknown.

    Args:
        tool_obj: The MAF FunctionTool to wrap.
        context_length: The model's context window in tokens, or None.

    Returns:
        The same ``FunctionTool`` object (mutated).
    """
    if not hasattr(tool_obj, "func") or not callable(tool_obj.func):
        return tool_obj  # Declaration-only or non-FunctionTool, skip

    original_func = tool_obj.func
    max_chars = _compute_tool_output_cap(context_length)

    async def capped_func(*args: Any, **kwargs: Any) -> Any:
        tool_name = getattr(original_func, '__name__', 'unknown')
        try:
            result = await asyncio.wait_for(
                original_func(*args, **kwargs),
                timeout=TOOL_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return {"error": f"Tool '{tool_name}' timed out after {TOOL_EXECUTION_TIMEOUT}s"}
        truncated = _truncate_tool_output(result, max_chars=max_chars)
        return truncated

    # Preserve function identity so MAF introspection (name, schema, docs)
    # still works after we replace ``func``.
    capped_func.__name__ = getattr(original_func, "__name__", "unknown")
    capped_func.__doc__ = getattr(original_func, "__doc__", None)
    capped_func.__module__ = getattr(original_func, "__module__", "__main__")
    capped_func.__qualname__ = getattr(original_func, "__qualname__", capped_func.__name__)

    tool_obj.func = capped_func
    return tool_obj


def _build_compaction_strategy(
    model: Model,
    model_client: Any,
    tools: list,
) -> tuple[Any, Any] | tuple[None, None]:
    """Build a compaction strategy and tokenizer for tool-heavy agent runs.

    Only applies when tools are active (tool-less runs don't need compaction).
    Uses a two-layer defence:

    1. **ToolResultCompactionStrategy** — collapses older tool-call groups
       into compact summary messages, keeping the 3 most recent tool groups
       intact.  Signal is preserved (tool names + results), structural
       overhead is reclaimed.

    2. **TokenBudgetComposedStrategy** — enforces a hard token budget at
       70 % of the model's configured context length, with a 32K floor.
       If the budget is exceeded after step 1, the built-in fallback
       excludes the oldest message groups.

    Returns:
        A tuple of ``(compaction_strategy, tokenizer)``, or ``(None, None)``
        when no tools are active.
    """
    if not tools:
        return None, None

    try:
        tokenizer = CharacterEstimatorTokenizer()

        # Budget: 70 % of model context length, floor 32K
        context_length = getattr(model, "context_length", None) or 128_000
        token_budget = max(32_000, int(context_length * 0.7))

        strategies = [
            # Step 1: collapse old tool results into compact summaries
            ToolResultCompactionStrategy(keep_last_tool_call_groups=3),
        ]

        compaction = TokenBudgetComposedStrategy(
            token_budget=token_budget,
            tokenizer=tokenizer,
            strategies=strategies,
            early_stop=True,
        )

        logger.debug(
            "Compaction enabled: budget=%d tokens, context=%d, tools=%d",
            token_budget,
            context_length,
            len(tools),
        )
        return compaction, tokenizer

    except Exception:
        logger.warning(
            "Failed to build compaction strategy — running without compaction",
            exc_info=True,
        )
        return None, None


# ---------------------------------------------------------------------------
# Agent / Workflow execution
# ---------------------------------------------------------------------------


async def _run_agent(
    model: Model,
    model_client: Any,
    system_prompt: str,
    tools: list,
    user_message: str,
    agent_name: str,
    temperature: float = 0.7,
    reasoning_effort: str | None = None,
) -> tuple[str, int, int, int]:
    """Run a simple MAF Agent.

    Returns:
        A tuple of (response_text, tokens_in, tokens_out, cache_hit_tokens).
    """
    from agent_framework import Agent

    default_options: dict = {"temperature": temperature}
    if reasoning_effort:
        default_options["reasoning_effort"] = reasoning_effort
    if getattr(model, "max_tokens", 0) and model.max_tokens > 0:
        default_options["max_tokens"] = model.max_tokens

    compaction_strategy, tokenizer = _build_compaction_strategy(
        model, model_client, tools,
    )

    agent = Agent(
        client=model_client,
        name=agent_name,
        instructions=system_prompt,
        tools=tools,
        default_options=default_options,
        compaction_strategy=compaction_strategy,
        tokenizer=tokenizer,
    )

    result = await agent.run(user_message)

    # Extract token counts (best-effort)
    tokens_in, tokens_out, cache_hit = _extract_token_counts(result)

    # result could be a string or a structured object
    if isinstance(result, str):
        return result, tokens_in, tokens_out, cache_hit

    # MAF may return an object with a .final_output or .content attribute
    if hasattr(result, "final_output"):
        return str(result.final_output), tokens_in, tokens_out, cache_hit
    if hasattr(result, "content"):
        return str(result.content), tokens_in, tokens_out, 0

    return str(result), tokens_in, tokens_out, 0


async def _run_workflow(
    model: Model,
    skill: Skill,
    model_client: Any,
    system_prompt: str,
    tools: list,
    user_message: str,
    agent_name: str,
    temperature: float = 0.7,
    reasoning_effort: str | None = None,
) -> tuple[str, int, int]:
    """Run a MAF Workflow via the registry.

    Returns:
        A tuple of (response_text, tokens_in, tokens_out).
    """
    from .registry import get_registered

    if skill is None or not skill.maf_target_key:
        raise ValidationError("Workflow execution requires a skill with a maf_target_key")

    target = get_registered(skill.maf_target_key)
    if target is None:
        raise NotFoundError(
            f"No registered workflow for key '{skill.maf_target_key}'. "
            "Register a workflow module in src/agents/workflows/."
        )

    # Stub: fall back to agent execution if workflow runner not available
    logger.warning(
        "Workflow execution not fully implemented for key '%s'; falling back to agent.",
        skill.maf_target_key,
    )
    return await _run_agent(
        model=model,
        model_client=model_client,
        system_prompt=system_prompt,
        tools=tools,
        user_message=user_message,
        agent_name=agent_name,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    )


# ---------------------------------------------------------------------------
# Fresh DB session helper (for background fire-and-forget tasks)
# ---------------------------------------------------------------------------


async def _fresh_db_session() -> AsyncSession:
    """Create a new async DB session independent of the current request.

    Used by ``asyncio.ensure_future`` background tasks (e.g. embedding)
    that outlive the request-response cycle and therefore can't reuse
    the request-scoped ``db`` session.
    """
    from ..db.base import AsyncSessionLocal
    return AsyncSessionLocal()


# ---------------------------------------------------------------------------
# Message persistence
# ---------------------------------------------------------------------------


async def _persist_user_message(
    db: AsyncSession,
    session_id: str,
    is_temporary: bool,
    user_message: str,
) -> str:
    """Persist just the user message, returning its ID.

    Used by the streaming path so the user message is visible even if
    the agent run fails."""
    content = [{"type": "text", "text": user_message}]
    user_msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if is_temporary:
        await append_temp_message(
            session_id,
            {
                "id": user_msg_id,
                "sender": "user",
                "content": content,
                "created_at": now.isoformat(),
            },
        )
    else:
        msg = Message(
            id=user_msg_id,
            session_id=session_id,
            sender="user",
            content=content,
            created_at=now,
        )
        db.add(msg)
        await db.commit()

    return user_msg_id


async def _persist_assistant_message(
    db: AsyncSession,
    session_id: str,
    is_temporary: bool,
    assistant_response: str,
    model_id: str,
    tool_events: list[dict] | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    reasoning: str = "",
) -> str:
    """Persist just the assistant message, returning its ID."""
    content: list[dict] = []

    # Prepend reasoning content (always first, before tool events)
    if reasoning.strip():
        content.append({"type": "reasoning", "text": reasoning})

    # Build content array: tool events interleaved before the final text
    for evt in (tool_events or []):
        content.append(evt)

    # Always append the final text response, stripped of any leaked
    # <function_calls> XML (DeepSeek thinking-mode artifact)
    clean_text = _strip_raw_tool_xml(assistant_response)
    if clean_text.strip():
        content.append({"type": "text", "text": clean_text})
    assistant_msg_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    if is_temporary:
        await append_temp_message(
            session_id,
            {
                "id": assistant_msg_id,
                "sender": "assistant",
                "content": content,
                "model_id": model_id,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "created_at": now.isoformat(),
            },
        )
    else:
        msg = Message(
            id=assistant_msg_id,
            session_id=session_id,
            sender="assistant",
            content=content,
            model_id=model_id,
            tokens_in=tokens_in if tokens_in > 0 else None,
            tokens_out=tokens_out if tokens_out > 0 else None,
            created_at=now,
        )
        db.add(msg)
        await db.commit()

    return assistant_msg_id


async def _persist_messages(
    db: AsyncSession,
    session_id: str,
    is_temporary: bool,
    user_message: str,
    assistant_response: str,
    model_id: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> tuple[str, str]:
    """Persist the user message and assistant response.

    Returns:
        A tuple of (user_message_id, assistant_message_id).
    """
    user_msg_content = [{"type": "text", "text": user_message}]
    clean_response = _strip_raw_tool_xml(assistant_response)
    assistant_msg_content = [{"type": "text", "text": clean_response}]

    if is_temporary:
        # Store in Redis
        user_msg_id = str(uuid.uuid4())
        assistant_msg_id = str(uuid.uuid4())
        await append_temp_message(
            session_id,
            {
                "id": user_msg_id,
                "sender": "user",
                "content": user_msg_content,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await append_temp_message(
            session_id,
            {
                "id": assistant_msg_id,
                "sender": "assistant",
                "content": assistant_msg_content,
                "model_id": model_id,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return user_msg_id, assistant_msg_id
    else:
        # Store in MariaDB
        now = datetime.now(timezone.utc)
        user_msg_id = str(uuid.uuid4())
        user_msg = Message(
            id=user_msg_id,
            session_id=session_id,
            sender="user",
            content=user_msg_content,
            created_at=now,
        )
        db.add(user_msg)
        await db.flush()

        assistant_msg_id = str(uuid.uuid4())
        assistant_msg = Message(
            id=assistant_msg_id,
            session_id=session_id,
            sender="assistant",
            content=assistant_msg_content,
            model_id=model_id,
            tokens_in=tokens_in if tokens_in > 0 else None,
            tokens_out=tokens_out if tokens_out > 0 else None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(assistant_msg)
        await db.commit()

        return user_msg_id, assistant_msg_id


# =============================================================================
# Phase 7 — Streaming Agent Runner
# =============================================================================


async def run_agent_stream(
    session_data: dict,
    user_message: str,
    db: AsyncSession,
    current_user: User | None = None,
    message_id: str = "",
    file_ids: list[str] | None = None,
) -> AsyncIterator[dict]:
    """Assemble and run a MAF agent, yielding typed SSE event dicts.

    Each yielded dict has the shape ``{"event": str, "data": str}``
    where ``data`` is a JSON-encoded string matching the schema in
    ``docs/streaming-protocol.md §5``.

    Args:
        session_data: Unified session dict (from DB or Redis).
        user_message: The text the user sent.
        db: Active async DB session.
        current_user: The authenticated user (or None for guest sessions).
        message_id: Pre-generated UUID for the assistant message (used
            for client-side correlation across all events).
        file_ids: Optional list of FileUpload IDs to link to the user message.

    Yields:
        Dicts with ``event`` and ``data`` keys suitable for
        ``EventSourceResponse``.
    """
    tenant_id = current_user.tenant_id if current_user else session_data.get("tenant_id", "")
    session_id = session_data["id"]
    is_temporary = session_data.get("is_temporary", False)

    accumulated_text: str = ""
    accumulated_reasoning: str = ""
    accumulated_tool_events: list[dict] = []
    step_index: int = 0
    total_tokens: int = 0
    _stream_token_info: dict = {}  # mutated by _run_agent_stream to propagate token counts
    cfg = None

    # Persist the user message immediately so it's always visible,
    # even if the agent run fails.
    user_msg_id = await _persist_user_message(
        db=db,
        session_id=session_id,
        is_temporary=is_temporary,
        user_message=user_message,
    )

    # Link file uploads to the user message
    if file_ids and not is_temporary and current_user is not None:
        from ..services import upload_service as _upload_svc

        await _upload_svc.link_uploads_to_message(
            db=db,
            file_ids=file_ids,
            message_id=user_msg_id,
            user_id=current_user.id,
        )

    # ---- Background: embed user message for cross-session retrieval -----
    if not is_temporary and current_user is not None:
        try:
            from ..services.embedding_service import embed_message as _embed_msg

            asyncio.ensure_future(_embed_msg(
                db=await _fresh_db_session(),
                message_id=user_msg_id,
                user_id=current_user.id,
                tenant_id=current_user.tenant_id,
                text=user_message,
            ))
        except Exception:
            logger.warning("Failed to schedule embedding for message %s", user_msg_id, exc_info=True)

    try:
        # ---- 1-6. Resolve session config --------------------------------
        cfg = await _resolve_session_config(db, session_data, tenant_id, current_user, file_ids=file_ids, user_message=user_message)

        # ---- Build contextualized message (history + auto-summarization) --
        contextualized_message, summary_info = await _maybe_summarize_and_build_context(
            db=db,
            session_data=session_data,
            cfg=cfg,
            user_message=user_message,
        )

        # Emit summarized event if auto-summarization happened
        if summary_info:
            yield {
                "event": "summarized",
                "data": json.dumps({
                    "session_id": session_id,
                    "message_id": message_id,
                    "summary": summary_info["summary_text"],
                    "summarized_message_count": summary_info["summarized_count"],
                    "tokens_saved": summary_info["tokens_saved"],
                }),
            }

        # ---- 7. Run agent or workflow (streaming) ------------------------
        if cfg.execution_type == "workflow":
            stream = _run_workflow_stream(
                model=cfg.model,
                skill=cfg.skill,
                model_client=cfg.model_client,
                system_prompt=cfg.system_prompt,
                tools=cfg.active_tool_callables,
                user_message=contextualized_message,
                agent_name=cfg.agent_name,
                session_id=session_id,
                message_id=message_id,
                token_counts=_stream_token_info,
                temperature=cfg.temperature,
                reasoning_effort=cfg.reasoning_effort,
            )
        else:
            stream = _run_agent_stream(
                model=cfg.model,
                model_client=cfg.model_client,
                system_prompt=cfg.system_prompt,
                tools=cfg.active_tool_callables,
                user_message=contextualized_message,
                agent_name=cfg.agent_name,
                session_id=session_id,
                message_id=message_id,
                token_counts=_stream_token_info,
                temperature=cfg.temperature,
                reasoning_effort=cfg.reasoning_effort,
            )
        event_count = 0
        async for event_dict in stream:
            event_count += 1
            # Check for cancellation before each yield
            if await check_stream_cancel(session_id):
                await clear_stream_cancel(session_id)
                logger.warning(
                    "Stream cancelled for session %s — partial content will be persisted",
                    session_id,
                )
                # Persist whatever we have so far
                accumulated_text = _maybe_accumulate_text(
                    event_dict, accumulated_text
                )
                accumulated_reasoning = _maybe_accumulate_reasoning(
                    event_dict, accumulated_reasoning
                )
                break

            # Accumulate text from token events
            accumulated_text = _maybe_accumulate_text(
                event_dict, accumulated_text
            )
            # Accumulate reasoning from reasoning_token events
            accumulated_reasoning = _maybe_accumulate_reasoning(
                event_dict, accumulated_reasoning
            )
            # Accumulate tool events for persistence
            accumulated_tool_events = _maybe_accumulate_tool_events(
                event_dict, accumulated_tool_events
            )

            yield event_dict

    except Exception as exc:
        # Persist whatever we accumulated before the error
        if accumulated_text or accumulated_reasoning or accumulated_tool_events:
            try:
                await _persist_assistant_message(
                    db=db,
                    session_id=session_id,
                    is_temporary=is_temporary,
                    assistant_response=accumulated_text,
                    model_id=getattr(cfg, "model", Model(id="unknown", name="unknown", provider="unknown")).id if cfg else "unknown",
                    tool_events=accumulated_tool_events,
                    tokens_in=0,
                    tokens_out=0,
                    reasoning=accumulated_reasoning,
                )
            except Exception:
                logger.exception("Failed to persist partial message after stream error")
        yield {
            "event": "error",
            "data": json.dumps({
                "session_id": session_id,
                "message_id": message_id,
                "code": _exc_to_error_code(exc),
                "message": str(exc),
            }),
        }
        # Roll back DB session to ensure the connection is returned to the pool
        try:
            await db.rollback()
        except Exception:
            logger.debug("Failed to rollback DB session after stream error", exc_info=True)
        return  # Don't proceed to message_complete on error

    finally:
        # ---- Ensure partial results are ALWAYS persisted, even when the
        #      client disconnects (GeneratorExit skips post-finally code).
        #      We must NOT yield during GeneratorExit — it raises
        #      RuntimeError and causes "ASGI callable returned without
        #      completing response".  Persistence-only here. (Issue #206) --
        try:
            tokens_in = _stream_token_info.get("in", 0) or 0
            tokens_out = _stream_token_info.get("out", 0) or 0
            cache_hit_tokens_stream = _stream_token_info.get("cache_hit", 0) or 0

            if accumulated_text or accumulated_reasoning or accumulated_tool_events:
                await _persist_assistant_message(
                    db=db,
                    session_id=session_id,
                    is_temporary=is_temporary,
                    assistant_response=accumulated_text,
                    model_id=cfg.model.id if cfg else "unknown",
                    tool_events=accumulated_tool_events,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    reasoning=accumulated_reasoning,
                )

            if cfg is not None and current_user is not None:
                try:
                    await write_usage_log(
                        db,
                        tenant_id=current_user.tenant_id,
                        tenant_name=getattr(cfg, "tenant_name", "") or "",
                        user_id=current_user.id,
                        user_email=current_user.email,
                        user_full_name=current_user.display_name,
                        model_id=cfg.model.id,
                        model_name=cfg.model.name,
                        provider=cfg.model.provider,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        cache_hit_tokens=cache_hit_tokens_stream,
                        input_price=getattr(cfg.model, "input_price_per_1m", None),
                        output_price=getattr(cfg.model, "output_price_per_1m", None),
                        cache_hit_price=getattr(cfg.model, "cache_hit_price_per_1m", None),
                    )
                except Exception:
                    logger.exception("Failed to write usage log (streaming)")
        except Exception:
            logger.exception("Failed to persist partial message on stream close")

        # ---- Close ERPNext httpx clients & MCPTool connections -----------
        if cfg is not None and cfg.cleanup_clients:
            for client in cfg.cleanup_clients:
                try:
                    if hasattr(client, "aclose"):
                        await client.aclose()
                    else:
                        await client.close()
                except Exception:
                    logger.debug("Failed to close client %s", type(client).__name__, exc_info=True)

    # ---- Only reached on normal completion (no GeneratorExit) ----------
    if accumulated_text or accumulated_reasoning or accumulated_tool_events:
        yield {
            "event": "message_complete",
            "data": json.dumps({
                "session_id": session_id,
                "message_id": message_id,
                "total_tokens": total_tokens,
                "tokens_in": _stream_token_info.get("in", 0) or 0,
                "tokens_out": _stream_token_info.get("out", 0) or 0,
                "model_id": cfg.model.id if cfg else "unknown",
                "model_name": cfg.model.name if cfg else None,
                "model_provider": cfg.model.provider if cfg else None,
            }),
        }

    if cfg is not None:
        _schedule_post_response_tasks(
            model=cfg.model,
            system_prompt=cfg.system_prompt,
            user_message=user_message,
            assistant_response=accumulated_text,
            session_id=session_id,
            tenant_id=tenant_id,
            is_temporary=is_temporary,
            temperature=cfg.temperature,
        )


# ---------------------------------------------------------------------------
# Post-response background tasks (Issue #126)
# ---------------------------------------------------------------------------


def _schedule_post_response_tasks(
    model: Model,
    system_prompt: str,
    user_message: str,
    assistant_response: str,
    session_id: str,
    tenant_id: str,
    is_temporary: bool,
    temperature: float = 0.7,
) -> None:
    """Launch follow-up generation and auto-tagging as background tasks.

    These used to block the SSE stream; now they run independently so the
    stream can close immediately after ``message_complete``, freeing the
    user to send their next message right away.
    """

    async def _run() -> None:
        # -- Follow-up questions -------------------------------------------
        if getattr(model, "follow_up_questions_enabled", False):
            logger.info(
                "Scheduling follow-up question generation for session %s (model=%s)",
                session_id,
                model.name,
            )
            try:
                questions = await _generate_follow_up_questions(
                    model=model,
                    system_prompt=system_prompt,
                    user_message=user_message,
                    assistant_response=assistant_response,
                    temperature=temperature,
                )
                if questions:
                    await store_follow_up_questions(session_id, questions)
            except Exception:
                logger.exception(
                    "Failed to generate follow-up questions for session %s",
                    session_id,
                )

        # -- Auto-tagging --------------------------------------------------
        if not is_temporary and assistant_response:
            try:
                from ..db.base import AsyncSessionLocal

                async with AsyncSessionLocal() as tag_db:
                    tag_count_result = await tag_db.execute(
                        select(Message).where(
                            Message.session_id == session_id,
                            Message.sender == "assistant",
                        )
                    )
                    assistant_count = len(
                        list(tag_count_result.scalars().all())
                    )

                    if assistant_count == 1:
                        tag_names = await _auto_tag_session(
                            model=model,
                            system_prompt=system_prompt,
                            user_message=user_message,
                            assistant_response=assistant_response,
                            session_id=session_id,
                            tenant_id=tenant_id,
                            temperature=temperature,
                        )
                        if tag_names:
                            from ..services import session_service as _tag_svc

                            for tag_name in tag_names:
                                try:
                                    tag = await _tag_svc.get_or_create_tag(
                                        tag_db, tenant_id, tag_name
                                    )
                                    await _tag_svc.add_tag_to_session(
                                        tag_db, session_id, tag.id
                                    )
                                except Exception:
                                    logger.warning(
                                        "Failed to persist tag '%s' for session %s",
                                        tag_name,
                                        session_id,
                                        exc_info=True,
                                    )
            except Exception:
                logger.exception(
                    "Failed to auto-tag session %s", session_id
                )

    asyncio.create_task(_run())


# ---------------------------------------------------------------------------
# Streaming agent / workflow execution helpers
# ---------------------------------------------------------------------------


async def _run_agent_stream(
    model: Model,
    model_client: Any,
    system_prompt: str,
    tools: list,
    user_message: str,
    agent_name: str,
    session_id: str,
    message_id: str,
    token_counts: dict | None = None,
    temperature: float = 0.7,
    reasoning_effort: str | None = None,
) -> AsyncIterator[dict]:
    """Run a MAF Agent in streaming mode, yielding SSE event dicts.

    .. note::

        MAF streams tool-call arguments incrementally (character by
        character).  We aggregate partial *function_call* updates by
        ``call_id`` and only emit a single ``tool_start`` SSE event
        once the call is complete (when *function_result* arrives).
    """
    from agent_framework import Agent

    default_options: dict = {"temperature": temperature}
    if reasoning_effort:
        default_options["reasoning_effort"] = reasoning_effort
    if getattr(model, "max_tokens", 0) and model.max_tokens > 0:
        default_options["max_tokens"] = model.max_tokens

    compaction_strategy, tokenizer = _build_compaction_strategy(
        model, model_client, tools,
    )

    agent = Agent(
        client=model_client,
        name=agent_name,
        instructions=system_prompt,
        tools=tools,
        default_options=default_options,
        compaction_strategy=compaction_strategy,
        tokenizer=tokenizer,
    )

    response_stream = agent.run(user_message, stream=True)

    step_index = 0
    # Aggregate streaming tool calls: call_id -> {name, args_str}
    pending_calls: dict[str, dict] = {}

    inner_event_count = 0
    response_iter = response_stream.__aiter__()
    while True:
        try:
            update = await asyncio.wait_for(
                response_iter.__anext__(),
                timeout=MAF_STREAM_IDLE_TIMEOUT,
            )
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            break
        inner_event_count += 1
        if inner_event_count <= 5 or inner_event_count % 20 == 0:
            content_types = [getattr(c, "type", "?") for c in (update.contents if hasattr(update, 'contents') else [])]
        # Check each content item in the update
        for content in update.contents:
            content_type = getattr(content, "type", None)

            if content_type == "text":
                delta = getattr(content, "text", "")
                if delta:
                    yield _sse_event("token", {
                        "delta": delta,
                    }, session_id=session_id, message_id=message_id)
            elif content_type == "text_reasoning":
                delta = getattr(content, "text", "")
                if delta:
                    yield _sse_event("reasoning_token", {
                        "delta": delta,
                    }, session_id=session_id, message_id=message_id)
            elif content_type in ("function_call", "tool_call"):
                _handle_streaming_function_call(content, pending_calls)
            elif content_type in ("function_result", "tool_result"):
                tool_call_id = getattr(content, "call_id", None) or ""
                tool_name = getattr(content, "name", "unknown")
                output = getattr(content, "output", None) or getattr(content, "result", None)
                success = not _is_tool_error(output)
                result_summary = _summarise_tool_result(output)

                # Resolve the final tool name and arguments from pending calls
                resolved_args = None
                if tool_call_id and tool_call_id in pending_calls:
                    pending = pending_calls.pop(tool_call_id)
                    if pending.get("name"):
                        tool_name = pending["name"]
                    resolved_args = _resolve_tool_arguments(
                        pending.get("args_str", ""), output
                    )

                # Yield a single consolidated tool_start event
                yield _sse_event("tool_start", {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": resolved_args,
                }, session_id=session_id, message_id=message_id)

                # Yield the tool_result event
                yield _sse_event("tool_result", {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "success": success,
                    "result_summary": result_summary,
                    "output": output,
                }, session_id=session_id, message_id=message_id)

                step_index += 1
                yield _sse_event("step_complete", {
                    "step_index": step_index,
                    "total_steps_so_far": step_index,
                }, session_id=session_id, message_id=message_id)
    # After stream is exhausted, get final response for token counts
    try:
        final = await response_stream.get_final_response()
        if token_counts is not None:
            usage = getattr(final, "usage_details", None)
            if usage and isinstance(usage, dict):
                token_counts["in"] = usage.get("input_token_count", 0) or 0
                token_counts["out"] = usage.get("output_token_count", 0) or 0
                # MAF stores cached_tokens as "prompt/cached_tokens" (slash-separated)
                cache_hit = usage.get("prompt/cached_tokens", 0) or 0
                if cache_hit == 0:
                    cache_hit = usage.get("cache_read_input_tokens", 0) or 0
                if cache_hit == 0:
                    details = usage.get("prompt_tokens_details", {})
                    if isinstance(details, dict):
                        cache_hit = details.get("cached_tokens", 0) or 0
                if cache_hit == 0:
                    cache_hit = usage.get("cached_tokens", 0) or 0
                token_counts["cache_hit"] = cache_hit
    except Exception:
        pass  # Token count is best-effort for Phase 7


async def _run_workflow_stream(
    model: Model,
    skill: Any,
    model_client: Any,
    system_prompt: str,
    tools: list,
    user_message: str,
    agent_name: str,
    session_id: str,
    message_id: str,
    token_counts: dict | None = None,
    temperature: float = 0.7,
    reasoning_effort: str | None = None,
) -> AsyncIterator[dict]:
    """Run a MAF Workflow in streaming mode.

    Stub for Phase 7 — falls back to agent streaming.  Will be upgraded
    when workflow streaming is fully implemented.
    """
    from .registry import get_registered

    if skill is None or not skill.maf_target_key:
        raise ValidationError(
            "Workflow execution requires a skill with a maf_target_key"
        )

    target = get_registered(skill.maf_target_key)
    if target is None:
        raise NotFoundError(
            f"No registered workflow for key '{skill.maf_target_key}'. "
            "Register a workflow module in src/agents/workflows/."
        )

    # Stub: attempt workflow.run_stream() if available, else fall back
    if hasattr(target, "run_stream"):
        logger.info(
            "Using workflow.run_stream() for key '%s'", skill.maf_target_key
        )
        # TODO: map workflow stream events to SSE events (Phase 7+)
        # For now, fall back to agent streaming
    else:
        logger.warning(
            "Workflow '%s' has no run_stream(); falling back to agent stream.",
            skill.maf_target_key,
        )

    async for event_dict in _run_agent_stream(
        model=model,
        model_client=model_client,
        system_prompt=system_prompt,
        tools=tools,
        user_message=user_message,
        agent_name=agent_name,
        session_id=session_id,
        message_id=message_id,
        token_counts=token_counts,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
    ):
        yield event_dict


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


def _sse_event(
    event: str,
    payload: dict,
    session_id: str | None = None,
    message_id: str | None = None,
) -> dict:
    """Build a standard SSE event dict.

    *session_id* and *message_id* are injected into *payload* if not
    already present (they are set by the caller in most cases).
    """
    if session_id is not None and "session_id" not in payload:
        payload["session_id"] = session_id
    if message_id is not None and "message_id" not in payload:
        payload["message_id"] = message_id
    return {"event": event, "data": json.dumps(payload)}


def _maybe_accumulate_text(event_dict: dict, current: str) -> str:
    """If *event_dict* is a ``token`` event, append its delta to *current*."""
    if event_dict.get("event") != "token":
        return current
    try:
        payload = json.loads(event_dict["data"])
        delta = payload.get("delta", "")
        return current + delta
    except (json.JSONDecodeError, KeyError):
        return current


def _maybe_accumulate_reasoning(event_dict: dict, current: str) -> str:
    """If *event_dict* is a ``reasoning_token`` event, append its delta to *current*."""
    if event_dict.get("event") != "reasoning_token":
        return current
    try:
        payload = json.loads(event_dict["data"])
        delta = payload.get("delta", "")
        return current + delta
    except (json.JSONDecodeError, KeyError):
        return current


def _strip_raw_tool_xml(text: str) -> str:
    """Strip raw tool-call XML from text.

    DeepSeek thinking mode occasionally leaks tool-call XML into the
    regular text output when the consecutive-error limit is hit.  This
    strips both ``<function_calls>`` (MAF) and ``<*tool_calls*>`` (DSML)
    patterns so they don't render as visible garbage in the UI.
    """
    import re
    return re.sub(
        r"<[\w_-]*tool_calls[\w_-]*>.*?</[\w_-]*tool_calls[\w_-]*>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()


def _maybe_accumulate_tool_events(
    event_dict: dict, current: list[dict]
) -> list[dict]:
    """If *event_dict* is a tool_start or tool_result event,
    append it to *current* for later persistence."""
    event_name = event_dict.get("event", "")
    if event_name not in ("tool_start", "tool_result"):
        return current
    try:
        payload = json.loads(event_dict["data"])
    except (json.JSONDecodeError, KeyError):
        return current

    if event_name == "tool_start":
        return current + [{
            "type": "function_call",
            "name": payload.get("tool_name", "unknown"),
            "arguments": payload.get("arguments", {}),
            "id": payload.get("tool_call_id", ""),
        }]
    else:
        # tool_result
        return current + [{
            "type": "function_result",
            "name": payload.get("tool_name", "unknown"),
            "output": _format_tool_output_for_storage(payload.get("output")),
            "is_error": not payload.get("success", True),
        }]


def _format_tool_output_for_storage(output: Any) -> str | None:
    """Format a tool output value for JSON storage in message content.

    Returns a string representation suitable for display.  Limits the
    size of very large outputs to avoid bloated message content.
    """
    if output is None:
        return None
    if isinstance(output, str):
        if len(output) > 4000:
            return output[:4000] + "\n...(truncated)"
        return output
    try:
        serialized = json.dumps(output, default=str)
    except (TypeError, ValueError):
        serialized = str(output)
    if len(serialized) > 4000:
        serialized = serialized[:4000] + "\n...(truncated)"
    return serialized


# ---------------------------------------------------------------------------
# Streaming-specific: think-filter wrapper
# ---------------------------------------------------------------------------


def _handle_streaming_function_call(
    content: Any,
    pending_calls: dict[str, dict],
) -> None:
    """Accumulate a streaming *function_call* content into *pending_calls*.

    MAF may yield multiple partial function_call updates for the same
    call_id — the first carries the tool name, subsequent ones carry
    fragments of the JSON arguments string.
    """
    call_id = getattr(content, "call_id", None) or str(uuid.uuid4())
    tool_name = getattr(content, "name", None) or ""
    arguments = getattr(content, "arguments", None)

    if call_id not in pending_calls:
        pending_calls[call_id] = {"name": "", "args_str": ""}

    pending = pending_calls[call_id]

    # Update name if we got a real one
    if tool_name and tool_name != "unknown":
        pending["name"] = tool_name

    # Accumulate argument fragments
    if arguments is not None:
        if isinstance(arguments, str):
            pending["args_str"] = (pending["args_str"] or "") + arguments
        else:
            # Already a dict/list — store directly
            pending["args_str"] = arguments


def _resolve_tool_arguments(
    args_str: Any,
    output: Any,
) -> dict | None:
    """Try to parse accumulated streaming arguments into a dict.

    Returns the parsed arguments dict, or None if parsing fails.
    """
    if args_str is None:
        return None
    if isinstance(args_str, dict):
        return args_str
    if isinstance(args_str, str) and args_str.strip():
        try:
            parsed = json.loads(args_str)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _is_tool_error(output: Any) -> bool:
    """Detect whether a tool output indicates an error.

    MAF surfaces errors via separate content types, but DeepSeek's DSML
    format sometimes returns errors as plain output text.  This inspects
    the output content to infer whether the tool call actually succeeded.
    """
    if output is None:
        return False
    if isinstance(output, dict):
        # e.g. {"error": "Permission denied"} or {"exc_type": "..."}
        if "error" in output or "exc_type" in output:
            return True
        # ERPNext sometimes returns {"exc": "..."} in the message field
        if output.get("exc"):
            return True
        return False
    if isinstance(output, str):
        lower = output.strip().lower()
        if lower.startswith("error:") or lower.startswith("argument parsing failed"):
            return True
        return False
    return False


def _summarise_tool_result(output: Any) -> str:
    """Create a short human-readable summary of a tool result for SSE events.

    This only affects the event sent to the frontend; it does NOT limit
    what enters the agent context.  Context-level truncation for oversized
    results is handled by ``_truncate_tool_output`` (applied earlier in
    ``_resolve_tool_callables``).
    """
    if output is None:
        return "(no output)"
    text = str(output)
    if len(text) > 200:
        return text[:197] + "..."
    return text


def _truncate_tool_output(
    output: Any,
    max_chars: int = TOOL_OUTPUT_CAP_FALLBACK,
) -> Any:
    """Truncate oversized tool outputs before they enter the agent context.

    For dict results (the common case), truncates each string value
    exceeding ``max_chars``.  For plain strings, truncates directly.
    Non-string values pass through unchanged.

    Truncation keeps the first half + last half of the text, replacing
    the middle with a notice showing how many characters were dropped.
    This preserves important context from both the beginning (titles,
    headers, summaries) and end (financial figures, conclusions).
    """
    if isinstance(output, dict):
        result = {}
        for key, value in output.items():
            if isinstance(value, str) and len(value) > max_chars:
                half = max_chars // 2
                result[key] = (
                    value[:half]
                    + f"\n\n[... {len(value) - max_chars} characters truncated ...]\n\n"
                    + value[-half:]
                )
            else:
                result[key] = value
        return result
    if isinstance(output, str) and len(output) > max_chars:
        half = max_chars // 2
        truncated = (
            output[:half]
            + f"\n\n[... {len(output) - max_chars} characters truncated ...]\n\n"
            + output[-half:]
        )
        return truncated
    return output


def _exc_to_error_code(exc: Exception) -> str:
    """Map an exception to an SSE error code string."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    if "timeout" in name or "timeout" in msg:
        return "model_timeout"
    if "auth" in name or "jwt" in name or "unauthorized" in msg:
        return "auth_error"
    if "tool" in name:
        return "tool_error"
    if "max" in name and "step" in name:
        return "max_steps_exceeded"
    if "invalid" in name or "output" in name or "parse" in name:
        return "invalid_output"
    return "internal_error"


# ---------------------------------------------------------------------------
# Follow-up questions generation (Phase 10+)
# ---------------------------------------------------------------------------


async def _generate_follow_up_questions(
    model: Model,
    system_prompt: str,
    user_message: str,
    assistant_response: str,
    temperature: float = 0.7,
) -> list[str]:
    """Generate 3 follow-up questions based on the conversation context.

    Creates a fresh non-thinking model client and makes a quick
    non-streaming call. Returns an empty list on failure.
    """
    prompt = (
        "Based on the conversation above, generate exactly 3 concise follow-up "
        "questions that the user might want to ask next. The questions should be "
        "relevant to the topic discussed and help the user explore further.\n\n"
        "Rules:\n"
        "- Return ONLY a JSON array of 3 strings, nothing else.\n"
        "- Each question should be a single sentence, no more than 100 characters.\n"
        "- Do not number the questions.\n"
        '- Example format: ["Question one?", "Question two?", "Question three?"]'
    )

    text = ""
    try:
        # Create a fresh client (non-thinking mode for simpler output)
        from ..models.base import get_chat_client

        fresh_client = get_chat_client(model, thinking_enabled=False)

        logger.info("Follow-up: created %s client", type(fresh_client).__name__)

        # Use MAF's standard get_response() instead of accessing .client
        from agent_framework import Message as MafMessage

        maf_messages = [
            MafMessage("system", [system_prompt]),
            MafMessage("user", [user_message]),
            MafMessage("assistant", [assistant_response]),
            MafMessage("user", [prompt]),
        ]
        response = await fresh_client.get_response(
            messages=maf_messages,
            options={"temperature": temperature, "max_tokens": 200},
        )
        text = response.messages[-1].text if response.messages else ""

        logger.info(
            "Follow-up raw response (first 200 chars): %s",
            (text or "")[:200],
        )

        # Parse the JSON array from the response
        text = (text or "").strip()
        # Remove markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        questions = json.loads(text)
        if isinstance(questions, list) and all(isinstance(q, str) for q in questions):
            logger.info(
                "Generated %d follow-up questions for session",
                len(questions[:3]),
            )
            return questions[:3]

    except (json.JSONDecodeError, Exception) as exc:
        logger.exception(
            "Failed to generate follow-up questions (type=%s): %s",
            type(exc).__name__,
            exc,
        )

    return []


async def _auto_tag_session(
    model: Model,
    system_prompt: str,
    user_message: str,
    assistant_response: str,
    session_id: str,
    tenant_id: str,
    temperature: float = 0.5,
) -> list[str]:
    """Generate 3–5 concise lowercase tags based on the conversation.

    Creates a fresh non-thinking model client and makes a quick
    non-streaming call. Returns a list of tag name strings (or []).
    Never raises — failures are logged and swallowed.
    """
    prompt = (
        "Based on the conversation above, generate 3-5 concise tags "
        "that describe the main topics, tools used, or domain areas.\n\n"
        "Rules:\n"
        "- Return ONLY a JSON array of strings, nothing else.\n"
        "- Each tag should be 1-3 words, lowercase, no special characters.\n"
        "- Tags should capture the essence: e.g. programming, invoices, data analysis.\n"
        '- Example format: ["invoice", "erpnext", "payment reconciliation"]'
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": assistant_response},
        {"role": "user", "content": prompt},
    ]

    try:
        from ..models.base import get_chat_client

        fresh_client = get_chat_client(model, thinking_enabled=False)
        raw_client = getattr(fresh_client, "client", None)
        if raw_client is None:
            logger.warning("No client attribute on fresh model_client, skipping auto-tagging")
            return []

        model_name = getattr(fresh_client, "model", model.model_id)
        response = await raw_client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=300,
            temperature=temperature,
        )
        text = response.choices[0].message.content if response.choices else ""
        text = (text or "").strip()

        # Remove markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]) if len(lines) > 1 else text
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        # Try standard JSON parse first
        try:
            tags = json.loads(text)
            if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
                cleaned = []
                seen = set()
                for t in tags:
                    t = t.strip().lower()
                    if t and t not in seen and len(t) <= 50:
                        cleaned.append(t)
                        seen.add(t)
                return cleaned[:5]
        except json.JSONDecodeError:
            pass

        # Fallback: try to salvage truncated JSON arrays
        # e.g. ["tag1", "tag2", "tag3 — missing closing bracket
        if not text.endswith("]") and "[" in text:
            # Append "]" if it looks like a truncated array
            text = text.rstrip().rstrip(",") + "]"

        tags = json.loads(text)
        if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
            cleaned = []
            seen = set()
            for t in tags:
                t = t.strip().lower()
                if t and t not in seen and len(t) <= 50:
                    cleaned.append(t)
                    seen.add(t)
            return cleaned[:5]

    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("Failed to auto-tag session %s: %s", session_id, exc)

    return []


# =============================================================================
# Token-count helpers
# =============================================================================


# ---------------------------------------------------------------------------
# Phase 9 — Token extraction helper
# ---------------------------------------------------------------------------


def _extract_token_counts(result: Any) -> tuple[int, int, int]:
    """Best-effort extraction of token counts from a MAF agent response.

    Returns:
        A tuple of (tokens_in, tokens_out, cache_hit_tokens), defaulting to (0, 0, 0).
    """
    try:
        usage = getattr(result, "usage_details", None)
        if usage and isinstance(usage, dict):
            tokens_in = usage.get("input_token_count", 0) or 0
            tokens_out = usage.get("output_token_count", 0) or 0
            # MAF stores cached_tokens as "prompt/cached_tokens" (slash-separated)
            cache_hit = usage.get("prompt/cached_tokens", 0) or 0
            if cache_hit == 0:
                cache_hit = usage.get("cache_read_input_tokens", 0) or 0
            if cache_hit == 0:
                details = usage.get("prompt_tokens_details", {})
                if isinstance(details, dict):
                    cache_hit = details.get("cached_tokens", 0) or 0
            if cache_hit == 0:
                cache_hit = usage.get("cached_tokens", 0) or 0
            return tokens_in, tokens_out, cache_hit
    except Exception:
        pass
    return 0, 0, 0
