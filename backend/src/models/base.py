# =============================================================================
# PH Agent Hub — Provider Client Factory
# =============================================================================
# Single entry point for obtaining a MAF ChatClient from a Model record.
# Dispatches on model.provider to the appropriate builder.
# =============================================================================

from agent_framework import BaseChatClient

from ..db.orm.models import Model
from .anthropic import build_anthropic_client
from .deepseek import build_deepseek_client
from .ollama import build_ollama_client
from .openai import build_openai_client


def get_chat_client(
    model: Model,
    thinking_enabled: bool = False,
    reasoning_effort: str | None = None,
) -> BaseChatClient:
    """Return the appropriate MAF ChatClient for the given Model.

    Dispatches on model.provider:
      - "openai"    → OpenAIChatClient
      - "deepseek"  → DeepSeekThinkingClient with custom base_url
      - "anthropic" → AnthropicChatClient
      - "ollama"    → OpenAIChatClient (OpenAI-compatible local API)

    Raises NotImplementedError for unsupported providers.
    """
    provider = model.provider.lower()

    if provider == "openai":
        return build_openai_client(model)
    elif provider == "deepseek":
        return build_deepseek_client(
            model,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        )
    elif provider == "anthropic":
        return build_anthropic_client(model)
    elif provider == "ollama":
        return build_ollama_client(model)
    else:
        raise NotImplementedError(
            f"Provider '{model.provider}' is not supported"
        )
