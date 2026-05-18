# =============================================================================
# PH Agent Hub — OpenAI Provider Client
# =============================================================================

from agent_framework.openai import OpenAIChatClient

from ..db.orm.models import Model


def build_openai_client(model: Model) -> OpenAIChatClient:
    """Build an OpenAI chat client from a Model record.

    The model.api_key is already decrypted by the EncryptedString ORM type.
    """
    # Pre-configure an AsyncOpenAI client with timeout and retry settings
    # to survive transient network errors during long multi-tool streaming runs.
    import openai

    openai_client_args: dict = {
        "api_key": model.api_key,
        "max_retries": 2,
        "timeout": 900.0,
    }
    if model.base_url:
        openai_client_args["base_url"] = model.base_url

    return OpenAIChatClient(
        model=model.model_id or model.name,
        async_client=openai.AsyncOpenAI(**openai_client_args),
    )
