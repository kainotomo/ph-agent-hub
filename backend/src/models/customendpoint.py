# =============================================================================
# PH Agent Hub — Custom OpenAI-Compatible Endpoint Provider
# =============================================================================
# Generic model provider for full Chat Completions URLs such as
# ``http://127.0.0.1:1919/v1/chat/completions``. The SDK requires a base URL
# without the trailing ``/chat/completions`` suffix, so we normalize it by
# stripping the path and reusing the parent ``/v1`` base.
# =============================================================================

from typing import Any

from agent_framework.openai import OpenAIChatCompletionClient

from ..db.orm.models import Model


def _normalize_base_url(base_url: str | None) -> str | None:
    """Convert a full Chat Completions URL into the OpenAI SDK base_url."""
    if not base_url:
        return None

    value = base_url.strip().rstrip("/")
    if not value:
        return None

    # Preserve existing /v1 prefix when already configured. Otherwise, if the
    # URL already points at a chat completions endpoint, strip that suffix.
    if value.endswith("/chat/completions"):
        value = value[: -len("/chat/completions")]
    elif value.endswith("/chat"):
        value = value[: -len("/chat")]
    elif value.endswith("/v1/chat/completions"):
        value = value[: -len("/chat/completions")]

    if value.endswith("/v1"):
        return value
    if value.endswith("/v1/"):
        return value.rstrip("/")
    if value.endswith("/chat"):
        return value.rsplit("/chat", 1)[0]

    return value


def build_custom_endpoint_client(model: Model) -> OpenAIChatCompletionClient:
    """Build a generic OpenAI-compatible chat client for a custom endpoint.

    The provider stores the full endpoint URL (e.g.
    ``http://127.0.0.1:1919/v1/chat/completions``), but the OpenAI SDK expects
    the parent base URL (``http://127.0.0.1:1919/v1``) and will append the
    ``/chat/completions`` path itself.
    """
    import openai

    base_url = _normalize_base_url(model.base_url)
    if not base_url:
        raise ValueError(
            "Custom endpoint provider requires a base_url like 'http://127.0.0.1:1919/v1/chat/completions'"
        )

    openai_client_args: dict[str, Any] = {
        "api_key": model.api_key or "customendpoint",
        "max_retries": 2,
        "timeout": 900.0,
    }
    openai_client_args["base_url"] = base_url

    return OpenAIChatCompletionClient(
        model=model.model_id or model.name,
        async_client=openai.AsyncOpenAI(**openai_client_args),
    )
