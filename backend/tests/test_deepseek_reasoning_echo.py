# =============================================================================
# PH Agent Hub — DeepSeek thinking-mode reasoning round-trip (Issues #661, #664)
# =============================================================================
# With ``tools`` present, DeepSeek thinking mode requires ``reasoning_content``
# to be passed back in *all* subsequent requests, for every assistant message —
# "even for turns where the model did not perform a tool call".  The client
# reconstructed the field from the streamed ``text_reasoning`` contents, but
# only when there was reasoning to reconstruct.  Two shapes therefore went back
# to the API without the key and were rejected on the *next* request with
#
#   400 - The `reasoning_content` in the thinking mode must be passed back to
#         the API.
#
# aborting the turn mid-run through the #642 interrupted wrap-up, after the
# tools had already executed:
#
#   * a thinking round that emitted a tool call with *no* reasoning (the
#     original #664 report), and
#   * a synthetic assistant message inserted by the in-run compaction, which
#     replaces older tool-call rounds with ``[Tool results: …]`` summaries that
#     never had reasoning (the reason #664's first fix, limited to tool-call
#     messages, did not stop the 400 in production).
#
# The empty string is the accepted echo when there is nothing to send; the
# field must NOT be added when thinking is disabled, and non-assistant roles
# are untouched.
# =============================================================================

import json
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

    def test_every_assistant_message_echoes_the_field(self):
        """Issue #664 — the requirement is not limited to tool-call messages."""
        client = _client(thinking=True)
        message = Message("assistant", [Content(type="text", text="just an answer")])

        prepared = client._prepare_message_for_openai(message)

        assert prepared[0]["role"] == "assistant"
        assert "tool_calls" not in prepared[0]
        assert prepared[0]["reasoning_content"] == ""

    def test_compaction_summary_echoes_an_empty_string(self):
        """The production 400: a synthetic assistant summary from compaction."""
        client = _client(thinking=True)
        summary = Message(
            "assistant",
            [Content(type="text", text="[Tool results: PowerShell: ok]")],
            message_id="tool_summary_g3",
        )

        prepared = client._prepare_message_for_openai(summary)

        assert prepared[0]["role"] == "assistant"
        assert prepared[0]["reasoning_content"] == ""

    def test_user_message_is_unaffected(self):
        client = _client(thinking=True)
        message = Message("user", [Content(type="text", text="hello")])

        prepared = client._prepare_message_for_openai(message)

        assert prepared[0]["role"] == "user"
        assert "reasoning_content" not in prepared[0]

    def test_thinking_disabled_adds_nothing_to_a_summary(self):
        client = _client(thinking=False)
        summary = Message("assistant", [Content(type="text", text="[Tool results: ok]")])

        prepared = client._prepare_message_for_openai(summary)

        assert "reasoning_content" not in prepared[0]

    @pytest.mark.asyncio
    async def test_a_compacted_turn_prepares_every_assistant_message(self):
        """Issue #664 — after real MAF compaction, every assistant message
        sent back carries ``reasoning_content`` (real for genuine rounds, "")
        for the synthetic summaries)."""
        from agent_framework import CharacterEstimatorTokenizer, Message
        from agent_framework import apply_compaction

        from src.agents.runner import _build_compaction_strategy

        # Eight genuine tool rounds; the compaction budget floor (32K tokens)
        # is crossed, so the oldest groups become summaries while the newest
        # three are kept verbatim.
        messages = [Message("system", ["system"]), Message("user", ["read the file"])]
        for r in range(8):
            messages.append(Message("assistant", [
                Content.from_text_reasoning(
                    text=f"reasoning {r}", protected_data=json.dumps(f"reasoning {r}")
                ),
                Content(
                    type="function_call",
                    name="PowerShell",
                    call_id=f"c{r}",
                    arguments={"command": "Get-Content x -Raw"},
                ),
            ]))
            messages.append(Message("tool", [
                Content(
                    type="function_result",
                    call_id=f"c{r}",
                    name="PowerShell",
                    result="y" * 20_000,
                )
            ]))

        from unittest.mock import MagicMock

        model = MagicMock()
        model.context_length = 16_000  # 32K floor -> this conversation compacts
        strategy, tokenizer = _build_compaction_strategy(model, MagicMock(), [MagicMock()])
        projected = await apply_compaction(
            messages, strategy=strategy, tokenizer=tokenizer,
        )
        assert strategy.compacted_rounds >= 1

        client = _client(thinking=True)
        prepared: list[dict] = []
        for message in projected:
            prepared.extend(client._prepare_message_for_openai(message))

        assistants = [m for m in prepared if m.get("role") == "assistant"]
        assert assistants, "the compacted request has assistant messages"
        for message in assistants:
            assert "reasoning_content" in message, message
        # The genuine rounds keep their real reasoning; the summaries get "".
        assert any(m["reasoning_content"] == "reasoning 7" for m in assistants)
        assert any(
            m["reasoning_content"] == "" and "tool_calls" not in m
            for m in assistants
        )
