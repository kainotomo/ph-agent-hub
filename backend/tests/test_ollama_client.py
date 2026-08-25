# =============================================================================
# PH Agent Hub — Tests: Ollama Provider Client (build_ollama_client)
# =============================================================================
"""Unit tests for ``src.models.ollama.build_ollama_client``.

Verifies that Ollama models are wired to the Chat Completions API client
(``OpenAIChatCompletionClient``) — NOT the Responses API client
(``OpenAIChatClient``), which Ollama does not implement (it returns
"404 page not found") — and that ``base_url`` is normalized to include
the required ``/v1`` prefix.
"""

import uuid
from unittest.mock import patch

import pytest

from src.db.orm.models import Model
from src.models.customendpoint import build_custom_endpoint_client
from src.models.ollama import build_ollama_client


def _make_model(**overrides) -> Model:
    defaults: dict = dict(
        id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        name="Test Ollama",
        model_id="llama3",
        provider="ollama",
        api_key="ollama",
        enabled=True,
        is_public=False,
        max_tokens=4096,
        temperature=0.7,
    )
    defaults.update(overrides)
    return Model(**defaults)


@pytest.mark.unit
def test_build_ollama_client_uses_chat_completions_client():
    """Ollama must use OpenAIChatCompletionClient, not OpenAIChatClient."""
    from agent_framework.openai import OpenAIChatCompletionClient, OpenAIChatClient

    model = _make_model(base_url="http://192.168.0.3:11434")
    with patch("openai.AsyncOpenAI") as mock_async_openai:
        client = build_ollama_client(model)

    # Correct client family (Chat Completions API, not Responses API)
    assert isinstance(client, OpenAIChatCompletionClient)
    assert not isinstance(client, OpenAIChatClient)

    # The OpenAI SDK client must point at the /v1 base_url with a dummy key
    mock_async_openai.assert_called_once()
    kwargs = mock_async_openai.call_args.kwargs
    assert kwargs["base_url"] == "http://192.168.0.3:11434/v1"
    assert kwargs["api_key"] == "ollama"
    assert kwargs["max_retries"] == 0


@pytest.mark.unit
def test_build_ollama_client_normalizes_base_url():
    """Missing /v1 is appended; existing /v1 is preserved; empty stays empty."""
    cases = [
        ("http://192.168.0.3:11434", "http://192.168.0.3:11434/v1"),
        ("http://192.168.0.3:11434/", "http://192.168.0.3:11434/v1"),
        ("http://192.168.0.3:11434/v1", "http://192.168.0.3:11434/v1"),
        ("http://192.168.0.3:11434/v1/", "http://192.168.0.3:11434/v1"),
        (None, None),
        ("", None),
    ]
    for given, expected in cases:
        model = _make_model(base_url=given)
        with patch("openai.AsyncOpenAI") as mock_async_openai:
            build_ollama_client(model)
        kwargs = mock_async_openai.call_args.kwargs
        if expected is None:
            assert "base_url" not in kwargs, f"expected no base_url for {given!r}"
        else:
            assert kwargs["base_url"] == expected, (
                f"for {given!r} expected {expected!r}, got {kwargs.get('base_url')!r}"
            )


@pytest.mark.unit
def test_build_custom_endpoint_client_uses_full_chat_completion_url():
    """Custom endpoint URLs can point directly at /v1/chat/completions without double-appending."""
    model = _make_model(
        provider="customendpoint",
        api_key="freetoken",
        base_url="http://127.0.0.1:1919/v1/chat/completions",
    )

    with patch("openai.AsyncOpenAI") as mock_async_openai:
        client = build_custom_endpoint_client(model)

    assert client is not None
    kwargs = mock_async_openai.call_args.kwargs
    assert kwargs["base_url"] == "http://127.0.0.1:1919/v1"
    assert kwargs["api_key"] == "freetoken"
    assert kwargs["max_retries"] == 2


@pytest.mark.unit
def test_build_custom_endpoint_client_defaults_placeholders_and_requires_url():
    """The custom endpoint provider must keep a non-empty placeholder key and reject empty URLs."""
    with patch("openai.AsyncOpenAI") as mock_async_openai:
        model = _make_model(provider="customendpoint", api_key="", base_url="http://127.0.0.1:1919/v1/chat/completions")
        build_custom_endpoint_client(model)
    assert mock_async_openai.call_args.kwargs["api_key"] == "customendpoint"

    bad = _make_model(provider="customendpoint", api_key="freetoken", base_url="")
    with pytest.raises(ValueError, match="base_url.*customendpoint|customendpoint.*base_url"):
        build_custom_endpoint_client(bad)
