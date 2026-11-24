# =============================================================================
# PH Agent Hub — Reasoning-only rounds are retried, and reasoning whitespace is
# preserved (Issues #639 and #640)
# =============================================================================
# Issue #639: a run whose final model round carried `reasoning_content` but no
# text and no tool call was accepted as the natural end of the turn.  The
# terminal path then reported `empty_output` ("The run ended without producing
# or persisting an answer") with no retry and no `finish_reason` recorded.
#
# Issue #640: every streamed reasoning delta was passed through
# `strip_dsml_markers`, which unconditionally `.strip()`ed it.  Because
# reasoning arrives one token per delta, every space and newline was deleted
# and the persisted reasoning became one run-on string that was then re-sent
# to the provider as `reasoning_content`.
#
# These tests drive `_run_agent_stream` with a mocked MAF Agent so both
# behaviours are pinned without a provider or a database.
# =============================================================================

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from src.agents.runner import (
    REASONING_ONLY_RETRY_NUDGE,
    _accumulate_stream_state,
    _flush_reasoning_segment,
    _run_agent_stream,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


def _content(
    content_type: str,
    *,
    text: str = "",
    call_id: str = "",
    name: str = "",
    output=None,
):
    content = MagicMock()
    content.type = content_type
    content.text = text
    content.call_id = call_id
    content.name = name
    content.output = output
    content.result = output
    return content


def _update(*contents, finish_reason=None):
    update = MagicMock()
    update.contents = list(contents)
    # Explicitly set so the MagicMock does not invent a truthy attribute;
    # the runner only accepts string finish reasons anyway.
    update.finish_reason = finish_reason
    return update


def _reasoning(text: str, finish_reason=None):
    return _update(_content("text_reasoning", text=text), finish_reason=finish_reason)


def _text(text: str, finish_reason=None):
    return _update(_content("text", text=text), finish_reason=finish_reason)


class _FakeStream:
    """Minimal stand-in for MAF's ``ResponseStream``.

    Supports async iteration and exposes the run's final ``AgentResponse``
    through ``_final_result`` (which the retry path reads).
    """

    def __init__(self, updates, final=None):
        self._updates = list(updates)
        self._final_result = final

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._updates:
            raise StopAsyncIteration
        return self._updates.pop(0)


def _final_with_conversation():
    """A final response carrying the user turn and the reasoning-only reply."""
    from agent_framework import Message

    final = MagicMock()
    final.messages = [
        Message("user", ["Do the task."]),
        Message("assistant", ["...reasoning only..."]),
    ]
    final.finish_reason = "length"
    return final


def _model():
    model = MagicMock()
    model.provider = "openai"
    model.model_id = "deepseek-chat"
    model.name = "DeepSeek Chat"
    model.max_tokens = 8192
    model.context_length = 128000
    return model


async def _run(streams, *, token_counts=None, model_client=None):
    """Yield events for *streams* (one per ``agent.run`` attempt)."""
    mock_instance = MagicMock()
    mock_instance.run.side_effect = list(streams)
    with patch("agent_framework.Agent", return_value=mock_instance):
        events = []
        async for event in _run_agent_stream(
            model=_model(),
            model_client=model_client if model_client is not None else MagicMock(),
            system_prompt="test",
            tools=[],
            user_message="Do the task.",
            agent_name="test",
            session_id="s1",
            message_id="m1",
            token_counts=token_counts,
        ):
            events.append(event)
    return events, mock_instance


def _events(events, name):
    return [e for e in events if e.get("event") == name]


def _deltas(events, name):
    return [json.loads(e["data"])["delta"] for e in _events(events, name)]


# ---------------------------------------------------------------------------
# Issue #640 — reasoning whitespace survives the sanitising path
# ---------------------------------------------------------------------------


class TestReasoningWhitespace:
    @pytest.mark.asyncio
    async def test_streamed_reasoning_keeps_spaces_and_newlines(self):
        """Per-token deltas must not be trimmed before persistence."""
        chunks = ["Let me ", "understand where ", "I am.\n\n", "1. cash and"]
        events, _ = await _run([
            _FakeStream([
                _reasoning(chunk) for chunk in chunks
            ] + [_update(finish_reason="stop")]),
        ])

        joined = "".join(_deltas(events, "reasoning_token"))
        assert joined == "Let me understand where I am.\n\n1. cash and"
        assert " " in joined and "\n" in joined

        # Persistence assembles the ordered segments from these same events;
        # the stored reasoning must keep every space and newline.
        segments: list[dict] = []
        pending = ""
        text = ""
        for event in events:
            segments, pending, text = _accumulate_stream_state(
                event, segments, pending, text,
            )
        segments = _flush_reasoning_segment(segments, pending)
        reasoning_parts = [p["text"] for p in segments if p["type"] == "reasoning"]
        assert reasoning_parts == ["Let me understand where I am.\n\n1. cash and"]

    @pytest.mark.asyncio
    async def test_reasoning_delta_that_is_only_whitespace_is_kept(self):
        """A lone space/newline delta is meaningful and must be emitted."""
        events, _ = await _run([
            _FakeStream([
                _reasoning("word"),
                _reasoning(" "),
                _reasoning("word"),
                _reasoning("\n"),
                _text("done", finish_reason="stop"),
            ]),
        ])

        assert "".join(_deltas(events, "reasoning_token")) == "word word\n"

    @pytest.mark.asyncio
    async def test_leaked_dsml_markup_is_still_stripped_from_reasoning(self):
        """The Issue #579 sanitiser must still fire when markup is present."""
        events, _ = await _run([
            _FakeStream([
                _reasoning("Let me <｜tool_calls｜>x</｜tool_calls｜> continue"),
                _text("done", finish_reason="stop"),
            ]),
        ])

        joined = "".join(_deltas(events, "reasoning_token"))
        assert "tool_calls" not in joined
        assert "DSML" not in joined
        assert "Let me" in joined and "continue" in joined


# ---------------------------------------------------------------------------
# Issue #639 — a reasoning-only final round is retried once
# ---------------------------------------------------------------------------


class TestReasoningOnlyRoundRetry:
    @pytest.mark.asyncio
    async def test_reasoning_only_round_is_retried_with_the_conversation(self):
        """The retry resumes the run (history + nudge), it does not restart it."""
        token_counts: dict = {}
        first = _FakeStream(
            [
                _reasoning("Now write them. Then fetch."),
                _update(finish_reason="length"),
            ],
            final=_final_with_conversation(),
        )
        second = _FakeStream([_text("Here is the answer.", finish_reason="stop")])

        events, agent = await _run([first, second], token_counts=token_counts)

        assert agent.run.call_count == 2
        retry_input = agent.run.call_args_list[1].args[0]
        assert isinstance(retry_input, list)
        # The conversation of the first attempt is carried over...
        assert len(retry_input) == 3
        assert retry_input[-1].role == "user"
        assert retry_input[-1].text == REASONING_ONLY_RETRY_NUDGE
        # ...and the retry's answer is streamed and billed to the same turn.
        assert _deltas(events, "token") == ["Here is the answer."]
        assert token_counts["metrics"]["finish_reason"] == "stop"
        assert token_counts["metrics"]["reasoning_only_retried"] is True

    @pytest.mark.asyncio
    async def test_retry_finish_reason_and_provider_usage_are_in_metrics(self):
        token_counts: dict = {}
        first = _FakeStream(
            [
                _reasoning("thinking"),
                _update(finish_reason="length"),
            ],
            final=_final_with_conversation(),
        )
        second = _FakeStream([_text("answer", finish_reason="stop")])

        await _run([first, second], token_counts=token_counts)

        metrics = token_counts["metrics"]
        assert metrics["finish_reason"] == "stop"
        assert metrics["provider_usage"] == {
            "input_token_count": 0,
            "output_token_count": 0,
            "cache_read_input_token_count": 0,
        }

    @pytest.mark.asyncio
    async def test_reasoning_only_turn_records_length_as_finish_reason(self, monkeypatch):
        """A turn that could not be retried still records why it ended."""
        token_counts: dict = {}

        async def _wrapup(**_kwargs):
            return "\n\nI could not finish; please continue."

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)

        # No final conversation available -> the retry cannot be built, so the
        # #579 wrap-up keeps the turn from ending empty.
        events, agent = await _run(
            [_FakeStream([
                _reasoning("thinking"),
                _update(finish_reason="length"),
            ])],
            token_counts=token_counts,
        )

        assert agent.run.call_count == 1
        assert token_counts["metrics"]["finish_reason"] == "length"
        assert token_counts["metrics"]["reasoning_only_retried"] is False
        assert _deltas(events, "token") == ["\n\nI could not finish; please continue."]

    @pytest.mark.asyncio
    async def test_second_reasoning_only_round_falls_back_to_wrapup(self, monkeypatch):
        """One retry only; a second degenerate round ends with a real message."""
        token_counts: dict = {}

        async def _wrapup(**_kwargs):
            return "\n\nI stopped because my last response contained only reasoning."

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)

        def _degenerate():
            return _FakeStream(
                [
                    _reasoning("thinking"),
                    _update(finish_reason="length"),
                ],
                final=_final_with_conversation(),
            )

        events, agent = await _run(
            [_degenerate(), _degenerate()],
            token_counts=token_counts,
        )

        assert agent.run.call_count == 2
        tokens = _deltas(events, "token")
        assert tokens and "only reasoning" in tokens[-1]
        assert token_counts["metrics"]["reasoning_only_retried"] is True

    @pytest.mark.asyncio
    async def test_normal_answer_is_not_retried(self):
        events, agent = await _run([
            _FakeStream([_text("the answer", finish_reason="stop")]),
        ])

        assert agent.run.call_count == 1
        assert _deltas(events, "token") == ["the answer"]


# ---------------------------------------------------------------------------
# Issue #639 — diagnostic warning for a round with no content and no tool call
# ---------------------------------------------------------------------------


class TestZeroContentRoundWarning:
    @pytest.mark.asyncio
    async def test_reasoning_only_round_logs_model_and_budget(self, caplog):
        with caplog.at_level(logging.WARNING, logger="src.agents.runner"):
            await _run(
                [
                    _FakeStream(
                        [
                            _reasoning("thinking"),
                            _update(finish_reason="length"),
                        ],
                        final=_final_with_conversation(),
                    ),
                    _FakeStream([_text("answer", finish_reason="stop")]),
                ],
            )

        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(
            "deepseek-chat" in message and "8192" in message and "finish_reason=length" in message
            for message in warnings
        ), warnings

    @pytest.mark.asyncio
    async def test_zero_token_round_logs_warning_without_retrying(self, caplog, monkeypatch):
        async def _wrapup(**_kwargs):
            return "\n\nfallback"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}

        with caplog.at_level(logging.WARNING, logger="src.agents.runner"):
            _, agent = await _run([_FakeStream([])], token_counts=token_counts)

        # Nothing was produced, so there is nothing to nudge (Issue #317 is a
        # different failure); the round is still logged for triage.
        assert agent.run.call_count == 1
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(
            "deepseek-chat" in message and "no text and no tool call" in message
            for message in warnings
        ), warnings
        assert token_counts["metrics"]["finish_reason"] is None
