# =============================================================================
# PH Agent Hub — a turn may never end with no answer text at all (Issue #642)
# =============================================================================
# The #639 guard only retried a round that carried reasoning, no text and *no*
# tool activity on a cleanly exhausted stream.  Two shapes of empty turn still
# escaped it:
#
#   1. a round that ended on `finish_reason=tool_calls` and never received a
#      tool result (the execution raised, the stream was aborted, an approval
#      stalled), and
#   2. a stream that ended uncleanly (provider error, idle timeout, client
#      disconnect) and broke the loop before the round was classified at all.
#
# Both left the turn with zero answer characters.  Because the reasoning
# segment is persisted anyway, the run was then reported as `completed`, so the
# user saw an empty bubble with no retry.
#
# These tests drive `_run_agent_stream` with a mocked MAF Agent.  They pin the
# new classification (a tool call with no result is degenerate), the unclean
# stream path (conclude, never re-drive the model), the after-loop safety net
# (`stop_reason="no_answer"`) and the new metrics keys.
# =============================================================================

import json
import contextvars
from unittest.mock import MagicMock, patch

import pytest

from src.agents.runner import (
    REASONING_ONLY_RETRY_NUDGE,
    _round_produced_answer,
    _run_agent_stream,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Mocks (same shape as tests/test_reasoning_only_round.py)
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


def _tool_call(name="PowerShell", call_id="call_1", finish_reason="tool_calls"):
    return _update(
        _content("function_call", call_id=call_id, name=name),
        finish_reason=finish_reason,
    )


def _tool_result(name="PowerShell", call_id="call_1", output="ok"):
    return _update(
        _content("function_result", call_id=call_id, name=name, output=output),
        finish_reason="tool_calls",
    )


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


class _RaisingStream(_FakeStream):
    """A stream that dies mid-round (provider error / client disconnect)."""

    async def __anext__(self):
        if not self._updates:
            raise RuntimeError("connection reset by peer")
        return self._updates.pop(0)


class _NoFinalStream(_FakeStream):
    """A stream whose final response is unavailable, so no nudge can be built."""

    async def get_final_response(self):
        raise RuntimeError("stream was never finalised")


# Mirrors MAF's ``inner_response_telemetry_captured_fields`` ContextVar: the
# token is set in one ``__anext__`` call and reset in a later one.
_CLEANUP_CONTEXT_VAR = contextvars.ContextVar("test_cleanup_context_var", default=None)


class _ContextVarCleanupStream(_FakeStream):
    """Stream that resets a ContextVar token while exhausting itself.

    Agent Framework's observability finalizer does exactly this from its
    cleanup hook.  On Python 3.11 ``asyncio.wait_for`` runs each ``__anext__``
    in a new Task with a copied context, so the reset raises
    ``ValueError: ... was created in a different Context`` and a clean end is
    misreported as an abort (regression from #645).
    """

    def __init__(self, updates):
        super().__init__(updates)
        self._token = None

    async def __anext__(self):
        if self._updates:
            if self._token is None:
                self._token = _CLEANUP_CONTEXT_VAR.set("stream-active")
            return self._updates.pop(0)
        if self._token is not None:
            _CLEANUP_CONTEXT_VAR.reset(self._token)
            self._token = None
        raise StopAsyncIteration


def _final_with_conversation():
    """A final response carrying the user turn and the assistant reply."""
    from agent_framework import Message

    final = MagicMock()
    final.messages = [
        Message("user", ["Do the task."]),
        Message("assistant", ["...reasoning only..."]),
    ]
    final.finish_reason = "tool_calls"
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
# Issue #642 — a tool call that never returns a result is a degenerate round
# ---------------------------------------------------------------------------


class TestDeadToolCall:
    @pytest.mark.asyncio
    async def test_tool_call_without_result_is_retried(self):
        """A round that asked for a tool and got nothing back is nudged once."""
        token_counts: dict = {}
        first = _FakeStream(
            [
                _reasoning("I'll write the file now."),
                _tool_call(),
            ],
            final=_final_with_conversation(),
        )
        second = _FakeStream([_text("Here is the answer.", finish_reason="stop")])

        events, agent = await _run([first, second], token_counts=token_counts)

        assert agent.run.call_count == 2
        retry_input = agent.run.call_args_list[1].args[0]
        assert isinstance(retry_input, list)
        assert retry_input[-1].role == "user"
        assert retry_input[-1].text == REASONING_ONLY_RETRY_NUDGE
        assert _deltas(events, "token") == ["Here is the answer."]
        metrics = token_counts["metrics"]
        assert metrics["reasoning_only_retried"] is True
        assert metrics["attempts"] == 2
        assert metrics["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_tool_round_with_a_result_is_not_degenerate(self, monkeypatch):
        """A completed tool round is normal progress, not a dead end."""
        async def _wrapup(**_kwargs):
            return "\n\nfallback conclusion"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)

        # One round that ran a tool call and received its result, then the
        # stream ended without text (in MAF this only happens when the inner
        # loop is torn down, which is why a conclusion is still warranted).
        events, agent = await _run(
            [_FakeStream([
                _update(
                    _content("function_call", call_id="call_1", name="PowerShell"),
                    _content(
                        "function_result", call_id="call_1", name="PowerShell",
                        output="ok",
                    ),
                ),
            ])]
        )

        assert agent.run.call_count == 1
        assert _deltas(events, "token") == ["\n\nfallback conclusion"]


# ---------------------------------------------------------------------------
# Issue #642 — an unclean stream end is concluded, never re-driven
# ---------------------------------------------------------------------------


class TestStreamInterrupted:
    @pytest.mark.asyncio
    async def test_unclean_stream_end_produces_a_conclusion(self, monkeypatch):
        seen: dict = {}

        async def _wrapup(**kwargs):
            seen.update(kwargs)
            return "\n\nfallback conclusion"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}

        events, agent = await _run(
            [_RaisingStream([_reasoning("thinking")])],
            token_counts=token_counts,
        )

        # No retry: the aborted round may have been mid-write.
        assert agent.run.call_count == 1
        assert seen["stop_reason"] == "stream_interrupted"
        assert _deltas(events, "token") == ["\n\nfallback conclusion"]
        assert token_counts["metrics"]["stream_end"] == "abort"
        assert token_counts["metrics"]["stop_reason"] == "stream_interrupted"

    @pytest.mark.asyncio
    async def test_unclean_stream_end_after_text_keeps_the_text(self, monkeypatch):
        async def _wrapup(**_kwargs):
            raise AssertionError("a turn that produced text needs no wrap-up")

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}

        events, agent = await _run(
            # Issue #645 — text survives only when the round also closed with
            # a normal finish reason; a partial round is concluded below.
            [_RaisingStream([_text("partial answer", finish_reason="stop")])],
            token_counts=token_counts,
        )

        assert agent.run.call_count == 1
        assert _deltas(events, "token") == ["partial answer"]
        assert token_counts["metrics"]["stream_end"] == "abort"
        assert token_counts["metrics"]["stop_reason"] is None

    @pytest.mark.asyncio
    async def test_partial_text_without_finish_is_concluded(self, monkeypatch):
        """Issue #645 — a preamble is not an answer when the round never stopped."""
        seen: dict = {}

        async def _wrapup(**kwargs):
            seen.update(kwargs)
            return "\n\nfallback conclusion"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}

        events, agent = await _run(
            [_RaisingStream([_text("partial answer")])],
            token_counts=token_counts,
        )

        assert agent.run.call_count == 1
        assert _deltas(events, "token") == [
            "partial answer", "\n\nfallback conclusion",
        ]
        assert seen["stop_reason"] == "stream_interrupted"
        assert token_counts["metrics"]["stream_end"] == "abort"
        assert token_counts["metrics"]["stop_reason"] == "stream_interrupted"

    def test_markup_only_round_is_not_an_answer(self):
        """Follow-up to #579 — stripped DSML leaves whitespace, not an answer."""
        assert _round_produced_answer({
            "text_chars": 21,
            "visible_text_chars": 0,
            "finish_reason": "stop",
        }) is False
        assert _round_produced_answer({
            "text_chars": 21,
            "visible_text_chars": 6,
            "finish_reason": "stop",
        }) is True
        # Callers that predate the visible counter keep the old behaviour.
        assert _round_produced_answer({
            "text_chars": 21,
            "finish_reason": "stop",
        }) is True

    @pytest.mark.asyncio
    async def test_leaked_dsml_round_is_concluded_not_persisted_as_answer(
        self, monkeypatch,
    ):
        """The 2026-11-27 shape: the only text is a leaked DSML container."""
        seen: dict = {}

        async def _wrapup(**kwargs):
            seen.update(kwargs)
            return "\n\nfallback conclusion"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}
        leaked = "<｜｜DSML｜｜ calls>\n\n\n\n</｜｜DSML｜｜ calls>"

        events, agent = await _run(
            [_FakeStream([_text(leaked, finish_reason="stop")])],
            token_counts=token_counts,
        )

        assert agent.run.call_count == 1
        # The markup produced no visible delta, so the turn is concluded
        # instead of persisting the residue as the answer.
        assert _deltas(events, "token") == ["\n\nfallback conclusion"]
        assert seen["stop_reason"] == "no_answer"
        assert token_counts["metrics"]["stop_reason"] == "no_answer"


# ---------------------------------------------------------------------------
# Issue #642 — the after-loop safety net: zero answer text is concluded
# ---------------------------------------------------------------------------


class TestNoAnswerSafetyNet:
    @pytest.mark.asyncio
    async def test_turn_with_no_answer_text_gets_a_conclusion(self, monkeypatch):
        """A dead call whose nudge cannot be built still ends with a message."""
        seen: dict = {}

        async def _wrapup(**kwargs):
            seen.update(kwargs)
            return "\n\nfallback conclusion"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}

        events, agent = await _run(
            [_NoFinalStream([_tool_call()])],
            token_counts=token_counts,
        )

        assert agent.run.call_count == 1
        assert seen["stop_reason"] == "no_answer"
        assert _deltas(events, "token") == ["\n\nfallback conclusion"]
        assert token_counts["metrics"]["stop_reason"] == "no_answer"
        assert token_counts["metrics"]["pending_tool_calls_at_end"] == 1

    @pytest.mark.asyncio
    async def test_answer_on_the_retry_needs_no_fallback(self, monkeypatch):
        async def _wrapup(**_kwargs):
            raise AssertionError("the retry produced an answer")

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}
        degenerate = _FakeStream(
            [_reasoning("thinking"), _update(finish_reason="tool_calls")],
            final=_final_with_conversation(),
        )
        answer = _FakeStream([_text("the answer", finish_reason="stop")])

        events, agent = await _run([degenerate, answer], token_counts=token_counts)

        assert agent.run.call_count == 2
        assert _deltas(events, "token") == ["the answer"]
        assert token_counts["metrics"]["stop_reason"] is None


# ---------------------------------------------------------------------------
# Issue #642 — the metrics answer "why is this message empty?" from the export
# ---------------------------------------------------------------------------


class TestMetrics642:
    @pytest.mark.asyncio
    async def test_metrics_record_stream_end_rounds_and_stop_reason(self):
        token_counts: dict = {}

        await _run(
            [_FakeStream([_text("the answer", finish_reason="stop")])],
            token_counts=token_counts,
        )

        metrics = token_counts["metrics"]
        assert metrics["stream_end"] == "clean"
        assert metrics["rounds"] >= 1
        assert metrics["attempts"] == 1
        assert metrics["stop_reason"] is None
        assert metrics["final_round_finish_reason"] == "stop"
        assert metrics["pending_tool_calls_at_end"] == 0

    @pytest.mark.asyncio
    async def test_trailing_partial_round_does_not_reuse_an_earlier_finish_reason(
        self, monkeypatch,
    ):
        """finish_reason belongs to the round that closed, not to the attempt."""
        async def _wrapup(**_kwargs):
            return "\n\nfallback conclusion"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}

        # Round 1 closes with `tool_calls`; round 2 is a trailing partial round
        # that never sent a finish_reason chunk.
        await _run(
            [_NoFinalStream([_tool_call(), _reasoning("still thinking")])],
            token_counts=token_counts,
        )

        metrics = token_counts["metrics"]
        assert metrics["final_round_finish_reason"] is None
        assert metrics["finish_reason"] != "tool_calls"

    @pytest.mark.asyncio
    async def test_pending_tool_calls_are_counted_at_the_end(self, monkeypatch):
        async def _wrapup(**_kwargs):
            return "\n\nfallback conclusion"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}

        await _run([_NoFinalStream([_tool_call()])], token_counts=token_counts)

        assert token_counts["metrics"]["pending_tool_calls_at_end"] == 1

    @pytest.mark.asyncio
    async def test_unclean_stream_end_is_recorded_as_abort(self, monkeypatch):
        async def _wrapup(**_kwargs):
            return "\n\nfallback conclusion"

        monkeypatch.setattr("src.agents.runner._generate_stop_wrapup", _wrapup)
        token_counts: dict = {}

        await _run([_RaisingStream([_reasoning("thinking")])], token_counts=token_counts)

        metrics = token_counts["metrics"]
        assert metrics["stream_end"] == "abort"
        assert metrics["finish_reason"] is None
        # Issue #661 follow-up — the cause is persisted with the turn, because
        # the wrap-up path means no error frame ever reaches the outer producer.
        assert metrics["stream_error"]["type"] == "RuntimeError"
        assert "connection reset by peer" in metrics["stream_error"]["message"]
        assert metrics["stream_error"]["code"] == "internal_error"


# ---------------------------------------------------------------------------
# Regression from #645 — a clean stream end must stay clean
# ---------------------------------------------------------------------------


class TestContextVarCleanup:
    @pytest.mark.asyncio
    async def test_clean_exhaustion_with_contextvar_reset_is_not_an_abort(self):
        """A ContextVar token reset during MAF's cleanup must not abort a turn.

        The observability finalizer stores a token across ``__anext__`` calls
        and resets it in a later one.  ``asyncio.wait_for`` (Python 3.11) runs
        each call in a copied context, so the reset raised
        ``ValueError: ... was created in a different Context`` and a normal
        ``StopAsyncIteration`` was misread as ``stream_end="abort"`` — the run
        then ended with a bogus "interrupted" wrap-up even though the model
        had finished.  The loop must keep the timeout watchdog without moving
        the call to another context.
        """
        async def _wrapup(**_kwargs):
            raise AssertionError("a cleanly finished turn needs no wrap-up")

        with patch("src.agents.runner._generate_stop_wrapup", _wrapup):
            token_counts: dict = {}
            events, agent = await _run(
                [_ContextVarCleanupStream([_text("Here is the answer.", finish_reason="stop")])],
                token_counts=token_counts,
            )

        assert agent.run.call_count == 1
        assert _deltas(events, "token") == ["Here is the answer."]
        metrics = token_counts["metrics"]
        assert metrics["stream_end"] == "clean"
        assert metrics["stop_reason"] is None
