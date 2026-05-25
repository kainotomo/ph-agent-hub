# =============================================================================
# PH Agent Hub — Ollama Provider Client
# =============================================================================
# Ollama exposes an OpenAI-compatible chat API at /v1/chat/completions,
# so we reuse OpenAIChatClient from agent_framework.
# No API key is required — a dummy "ollama" placeholder is used.
# =============================================================================

from agent_framework.openai import OpenAIChatClient

from ..db.orm.models import Model


def build_ollama_client(model: Model) -> OpenAIChatClient:
    """Build an OpenAI-compatible chat client for an Ollama-hosted model.

    The model.api_key is already decrypted by the EncryptedString ORM type.
    For Ollama, the key is a dummy placeholder ("ollama") since the local
    server does not require authentication.

    The admin must set model.base_url to point at their Ollama instance,
    e.g. ``http://localhost:11434/v1``.
    """
    import openai

    openai_client_args: dict = {
        "api_key": model.api_key or "ollama",
        "max_retries": 0,  # Local — no need to retry on transient network errors
        "timeout": 900.0,
    }
    if model.base_url:
        openai_client_args["base_url"] = model.base_url

    return OpenAIChatClient(
        model=model.model_id or model.name,
        async_client=openai.AsyncOpenAI(**openai_client_args),
    )
