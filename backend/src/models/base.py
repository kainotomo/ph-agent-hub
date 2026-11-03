# =============================================================================
# PH Agent Hub — Provider Client Factory
# =============================================================================
# Single entry point for obtaining a MAF ChatClient from a Model record.
# Dispatches on model.provider to the appropriate builder.
# =============================================================================

import logging

from agent_framework import BaseChatClient

from ..core.config import settings
from ..db.orm.models import Model

logger = logging.getLogger(__name__)
from .anthropic import build_anthropic_client
from .customendpoint import build_custom_endpoint_client
from .deepseek import build_deepseek_client
from .ollama import build_ollama_client
from .openai import build_openai_client


def get_chat_client(
    model: Model,
    thinking_enabled: bool = False,
    reasoning_effort: str | None = None,
    max_steps: int | None = None,
) -> BaseChatClient:
    """Return the appropriate MAF ChatClient for the given Model.

    Dispatches on model.provider:
      - "openai"        → OpenAIChatClient
      - "deepseek"      → DeepSeekThinkingClient with custom base_url
      - "anthropic"     → AnthropicChatClient
      - "ollama"        → OpenAIChatClient (OpenAI-compatible local API)
      - "customendpoint" → OpenAIChatCompletionClient for full endpoint URLs

    *max_steps* is the effective per-run step budget (Issue #595).  When
    omitted, the global ``AGENT_MAX_STEPS`` is used.

    Raises NotImplementedError for unsupported providers.
    """
    provider = model.provider.lower()

    if provider == "openai":
        client = build_openai_client(model)
    elif provider == "deepseek":
        client = build_deepseek_client(
            model,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        )
    elif provider == "anthropic":
        client = build_anthropic_client(model)
    elif provider == "ollama":
        client = build_ollama_client(model)
    elif provider in {"customendpoint", "custom_endpoint", "custom-endpoint"}:
        client = build_custom_endpoint_client(model)
    else:
        raise NotImplementedError(
            f"Provider '{model.provider}' is not supported"
        )

    _apply_function_invocation_limits(client, max_steps=max_steps)
    return client


def _apply_function_invocation_limits(
    client: BaseChatClient,
    *,
    max_steps: int | None = None,
) -> None:
    """Bind MAF's tool-invocation loop to the app agent step budget.

    MAF's ``FunctionInvokingChatClient`` defaults to ``max_iterations=40``.
    Left unchecked this outlives ``AGENT_MAX_STEPS``: the underlying run keeps
    going after our guard fires and MAF eventually issues a *final request with
    tools disabled*.  DeepSeek answers that request by emitting native DSML
    tool-call markup as plain text, which leaks into the response and ends the
    turn abruptly (Issue #579).

    Aligning MAF's cap to ``AGENT_MAX_STEPS + 1`` makes the app limit
    authoritative.  The ``+1`` covers MAF's extra final wrap-up round-trip,
    since MAF counts model round-trips while our step counter counts tool-result
    rounds.

    Issue #595 — ``max_steps`` is the effective per-skill/template budget; it
    replaces the global value when provided so MAF can never cut a run short
    of (or outlive) the app guard.
    """
    config = getattr(client, "function_invocation_configuration", None)
    if not isinstance(config, dict):
        logger.debug(
            "Client %s has no function_invocation_configuration; skipping limit binding",
            type(client).__name__,
        )
        return

    try:
        try:
            effective_max_steps = (
                int(max_steps)
                if max_steps is not None and int(max_steps) > 0
                else settings.AGENT_MAX_STEPS
            )
        except (TypeError, ValueError):
            # A malformed budget must never leave the MAF loop unbounded.
            effective_max_steps = settings.AGENT_MAX_STEPS
        config["max_iterations"] = effective_max_steps + 1
        config["max_consecutive_errors_per_request"] = (
            settings.AGENT_MAX_CONSECUTIVE_ERRORS_PER_REQUEST
        )
    except (TypeError, KeyError, ValueError):
        logger.warning(
            "Could not bind function-invocation limits on client %s",
            type(client).__name__,
            exc_info=True,
        )
