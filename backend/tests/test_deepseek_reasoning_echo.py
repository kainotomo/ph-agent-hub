# =============================================================================
# PH Agent Hub — DeepSeek thinking-mode reasoning round-trip (Issue #661)
# =============================================================================
# DeepSeek thinking mode requires ``reasoning_content`` on every assistant
# message that carries ``tool_calls``.  The client reconstructed the field from
# the streamed ``text_reasoning`` contents, but only when there was reasoning
# to reconstruct: a thinking round that emitted a tool call with *no* reasoning
# went back to the API without the key, and the next request was rejected with
#
#   400 - The `reasoning_content` in the thinking mode must be passed back to
#         the API.
#
# which aborts the turn mid-run through the #642 interrupted wrap-up (observed
# in a production session: 7 tool rounds, the last one reasoning-free, then the
# 8th request 400'd).  An empty string is the accepted echo when the round has
# no reasoning; it must NOT be added when thinking is disabled.
# =============================================================================

from unittest.mock import MagicMock

import pytest

from agent_framework import Content, Message

from src.models.deepseek import DeepSeekThinkingClient

pytestmark = [pytest.mark.unit]


def _client(thinking: bool = True) -> DeepSeekThinkingClient:
    return DeepSeekThinkingClient(
        model="deepseek-chat",
        async_client=MagicMock(),
        thinking_enabled=thinking,
    )


def _tool_call_message(call_id: str = "c1") -> Message:
    return Message(
        "assistant",
        [
            Content(
                type="function_call",
                name="PowerShell",
                call_id=call_id,
                arguments={"command": "Get-Content x -Raw"},
            )
        ],
    )


class TestToolCallReasoningEcho:
    def test_tool_call_without_reasoning_echoes_an_empty_string(self):
        """The production 400: a tool-call round that produced no reasoning."""
        client = _client(thinking=True)

        prepared = client._prepare_message_for_openai(_tool_call_message())

        assert len(prepared) == 1
        assert prepared[0]["role"] == "assistant"
        assert prepared[0]["tool_calls"]
        assert prepared[0]["reasoning_content"] == ""

    def test_tool_call_with_reasoning_echoes_the_reasoning(self):
        client = _client(thinking=True)
        message = Message(
            "assistant",
            [
                Content.from_text_reasoning(
                    text="thinking hard", protected_data='"thinking hard"'
                ),
                Content(
                    type="function_call",
                    name="PowerShell",
                    call_id="c2",
                    arguments={"command": "y"},
                ),
            ],
        )

        prepared = client._prepare_message_for_openai(message)

        assert prepared[0]["reasoning_content"] == "thinking hard"
        # The framework-level field must not leak alongside it.
        assert "reasoning_details" not in prepared[0]

    def test_non_streaming_reasoning_is_echoed(self):
        client = _client(thinking=True)
        message = Message(
            "assistant",
            [
                Content(
                    type="function_call",
                    name="PowerShell",
                    call_id="c3",
                    arguments={"command": "z"},
                )
            ],
            additional_properties={"deepseek_reasoning": "raw thinking"},
        )

        prepared = client._prepare_message_for_openai(message)

        assert prepared[0]["reasoning_content"] == "raw thinking"

    def test_base_class_reasoning_details_are_the_fallback(self):
        client = _client(thinking=True)
        message = Message(
            "assistant",
            [
                Content(
                    type="function_call",
                    name="PowerShell",
                    call_id="c4",
                    arguments={"command": "z"},
                )
            ],
            additional_properties={"reasoning_details": '"from details"'},
        )

        prepared = client._prepare_message_for_openai(message)

        assert prepared[0]["reasoning_content"] == "from details"
        assert "reasoning_details" not in prepared[0]

    def test_thinking_disabled_adds_nothing(self):
        """DeepSeek rejects reasoning_content when thinking is off."""
        client = _client(thinking=False)

        prepared = client._prepare_message_for_openai(_tool_call_message())

        assert "reasoning_content" not in prepared[0]

    def test_plain_assistant_message_is_not_given_an_empty_echo(self):
        """Only tool-call messages carry the requirement."""
        client = _client(thinking=True)
        message = Message("assistant", [Content(type="text", text="just an answer")])

        prepared = client._prepare_message_for_openai(message)

        assert prepared[0]["role"] == "assistant"
        assert "tool_calls" not in prepared[0]
        assert "reasoning_content" not in prepared[0]

    def test_user_message_is_unaffected(self):
        client = _client(thinking=True)
        message = Message("user", [Content(type="text", text="hello")])

        prepared = client._prepare_message_for_openai(message)

        assert prepared[0]["role"] == "user"
        assert "reasoning_content" not in prepared[0]
