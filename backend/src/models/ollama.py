# =============================================================================
# PH Agent Hub — Ollama Provider Client
# =============================================================================
# Ollama exposes an OpenAI-compatible chat API at /v1/chat/completions.
# We use OpenAIChatCompletionClient (Chat Completions API) rather than
# OpenAIChatClient (Responses API) because Ollama does NOT implement the
# /v1/responses endpoint — it returns "404 page not found" (or hangs).
# No API key is required — a dummy "ollama" placeholder is used.
# =============================================================================

from agent_framework.openai import OpenAIChatCompletionClient

from ..db.orm.models import Model


def build_ollama_client(model: Model) -> OpenAIChatCompletionClient:
    """Build an OpenAI-compatible chat client for an Ollama-hosted model.

    The model.api_key is already decrypted by the EncryptedString ORM type.
    For Ollama, the key is a dummy placeholder ("ollama") since the local
    server does not require authentication.

    The admin must set model.base_url to point at their Ollama instance,
    e.g. ``http://localhost:11434/v1``.

    Ollama's OpenAI-compatible API lives under the ``/v1`` prefix.  If the
    configured base_url omits it (e.g. ``http://host:11434``) we append it,
    otherwise the OpenAI SDK would call ``/chat/completions`` instead of
    ``/v1/chat/completions`` and get a 404.
    """
    import openai

    base_url = (model.base_url or "").strip().rstrip("/")
    if base_url and not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    openai_client_args: dict = {
        "api_key": model.api_key or "ollama",
        "max_retries": 0,  # Local — no need to retry on transient network errors
        "timeout": 900.0,
    }
    if base_url:
        openai_client_args["base_url"] = base_url

    return OpenAIChatCompletionClient(
        model=model.model_id or model.name,
        async_client=openai.AsyncOpenAI(**openai_client_args),
    )
