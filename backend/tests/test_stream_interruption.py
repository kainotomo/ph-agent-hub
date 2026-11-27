# =============================================================================
# PH Agent Hub — Interrupted streams must never end without an answer
# (Issue #645)
# =============================================================================
# Issue #642 introduced a "never end a turn with zero answer text" guard, but
# it keyed on ``turn_text_chars == 0``.  A single narration sentence streamed
# before the tool calls ("I'll start by reading the prompt…") defeated it, so
# a turn that died mid-tool was still reported as completed with no answer.
#
# Issue #642 also left two holes around the stream watchdog:
#   * one fixed 120s inactivity budget covered both model silence and tool
#     execution, so a healthy-but-slow tool killed the turn mid-flight; and
#   * MCP tools never received a per-request timeout (they are not wrapped by
#     the 90s FunctionTool execution timeout), so a hung MCP server held the
#     stream silent until that watchdog fired.
#
# These tests pin the new behaviour without a provider or a database.
# =============================================================================

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

import src.agents.runner as runner_module
from src.agents.runner import (
    CONTINUE_FOOTER,
    TOOL_EXECUTION_TIMEOUT,
    _generate_stop_wrapup,
    _round_produced_answer,
    _run_agent_stream,
    _tool_phase_idle_timeout,
)
from src.core.config import settings

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


def _content(content_type: str, *, text: str = "", call_id: str = "", name: str = ""):
    content = MagicMock()
    content.type = content_type
    content.text = text
    content.call_id = call_id
    content.name = name
    content.output = None
    content.result = None
    return content


def _update(*contents, finish_reason=None):
    update = MagicMock()
    update.contents = list(contents)
    # Explicit so the MagicMock does not invent a truthy attribute; the runner
    # only accepts string finish reasons anyway.
    update.finish_reason = finish_reason
    return update


def _text(text: str, finish_reason=None):
    return _update(_content("text", text=text), finish_reason=finish_reason)


class _FakeStream:
    """Minimal stand-in for MAF's ``ResponseStream``.

    ``hold_after`` makes the stream go silent after the last update, which is
    how a stalled provider/tool looks from the runner's point of view.
    """

    def __init__(self, updates, *, hold_after: float = 0.0):
        self._updates = list(updates)
        self._hold_after = hold_after

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._updates:
            if self._hold_after:
                await asyncio.sleep(self._hold_after)
            raise StopAsyncIteration
        return self._updates.pop(0)


def _model():
    model = MagicMock()
    model.provider = "openai"
    model.model_id = "deepseek-chat"
    model.name = "DeepSeek Chat"
    model.max_tokens = 8192
    model.context_length = 128000
    return model


async def _run(streams, *, token_counts=None, model_client=None):
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
    return events


def _deltas(events):
    return [
        json.loads(e["data"])["delta"]
        for e in events
        if e.get("event") == "token"
    ]


def _fast_timeouts(monkeypatch, *, tool_phase: float = 0.15) -> None:
    """Shrink the phase watchdogs so the tests stay instantaneous.

    The phase helpers enforce a floor above the real per-tool timeouts, so the
    helpers themselves are patched rather than only the settings.
    """
    monkeypatch.setattr(settings, "AGENT_WRAPUP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(runner_module, "_model_phase_idle_timeout", lambda: 0.05)
    monkeypatch.setattr(
        runner_module, "_tool_phase_idle_timeout", lambda: tool_phase,
    )


# ---------------------------------------------------------------------------
# Issue #645 — a preamble must not defeat the no-answer guard
# ---------------------------------------------------------------------------


class TestInterruptedTurnStillAnswers:
    @pytest.mark.asyncio
    async def test_preamble_then_silent_tool_phase_concludes_with_wrapup(
        self, monkeypatch,
    ):
        """The exact production shape: narration, tool_calls, then silence.

        ``stop_reason`` used to stay ``None`` because ``turn_text_chars`` was
        non-zero, so the turn was persisted as ``completed`` with only the
        preamble and no answer.  It must now be concluded.
        """
        _fast_timeouts(monkeypatch)
        token_counts: dict = {}
        stream = _FakeStream(
            [
                _text("I'll start by reading the prompt."),
                _update(
                    _content("function_call", call_id="c1", name="PowerShell"),
                    finish_reason="tool_calls",
                ),
            ],
            hold_after=5.0,
        )
        # The wrap-up model call fails so the canned message is deterministic.
        client = MagicMock()
        client.get_response = MagicMock(side_effect=RuntimeError("provider down"))

        events = await _run([stream], token_counts=token_counts, model_client=client)

        joined = "".join(_deltas(events))
        assert joined.startswith("I'll start by reading the prompt.")
        assert "My response was interrupted before it finished." in joined

        metrics = token_counts["metrics"]
        assert metrics["stream_end"] == "idle_timeout"
        assert metrics["stop_reason"] == "stream_interrupted"
        # The tool call was in flight: this is what used to be dropped silently.
        assert metrics["pending_tool_calls_at_end"] == 1

    @pytest.mark.asyncio
    async def test_clean_answer_with_stop_reason_is_not_concluded_again(self):
        """A round that rendered an answer and stopped normally is untouched."""
        token_counts: dict = {}
        stream = _FakeStream(
            [_text("Here is the report.", finish_reason="stop")],
        )

        events = await _run([stream], token_counts=token_counts)

        assert _deltas(events) == ["Here is the report."]
        metrics = token_counts["metrics"]
        assert metrics["stream_end"] == "clean"
        assert metrics["stop_reason"] is None


# ---------------------------------------------------------------------------
# Issue #645 — tool-phase budget vs model-phase budget
# ---------------------------------------------------------------------------


class TestToolPhaseBudget:
    def test_tool_phase_covers_the_largest_tool_timeout(self):
        assert _tool_phase_idle_timeout() > TOOL_EXECUTION_TIMEOUT
        assert _tool_phase_idle_timeout() > settings.MCP_TOOL_TIMEOUT_SECONDS
        assert _tool_phase_idle_timeout() > settings.SUBAGENT_TIMEOUT

    @pytest.mark.asyncio
    async def test_slow_tool_does_not_trigger_the_model_watchdog(self, monkeypatch):
        """A silent tool phase may exceed the model idle budget safely."""
        # Model phase is 0.05s; the tool phase is 0.3s and the tool "takes"
        # 0.15s.  Under the old single watchdog this turn died at 0.05s.
        _fast_timeouts(monkeypatch, tool_phase=0.3)

        async def _slow_tool_result():
            await asyncio.sleep(0.15)
            return _update(_content("function_result", call_id="c1", name="t"))

        class _Stream:
            def __init__(self):
                self._step = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                self._step += 1
                if self._step == 1:
                    return _update(
                        _content("function_call", call_id="c1", name="t"),
                        finish_reason="tool_calls",
                    )
                if self._step == 2:
                    return await _slow_tool_result()
                if self._step == 3:
                    return _text("Done.", finish_reason="stop")
                raise StopAsyncIteration

        token_counts: dict = {}
        events = await _run([_Stream()], token_counts=token_counts)

        assert _deltas(events) == ["Done."]
        assert token_counts["metrics"]["stop_reason"] is None
        assert token_counts["metrics"]["stream_end"] == "clean"


# ---------------------------------------------------------------------------
# Issue #645 — absolute turn deadline
# ---------------------------------------------------------------------------


class TestTurnDeadline:
    @pytest.mark.asyncio
    async def test_turn_beyond_deadline_is_concluded(self, monkeypatch):
        # A 1s cap with a stream that never ends: deterministic and quick.
        monkeypatch.setattr(settings, "AGENT_WRAPUP_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(settings, "AGENT_MAX_TURN_SECONDS", 1)
        stream = _FakeStream([_text("working")], hold_after=30.0)

        client = MagicMock()
        client.get_response = MagicMock(side_effect=RuntimeError("provider down"))
        token_counts: dict = {}
        events = await _run([stream], token_counts=token_counts, model_client=client)

        joined = "".join(_deltas(events))
        assert "reached its time limit" in joined
        metrics = token_counts["metrics"]
        assert metrics["stream_end"] == "turn_deadline"
        assert metrics["stop_reason"] == "turn_deadline"


# ---------------------------------------------------------------------------
# Issue #645 — bounded wrap-up
# ---------------------------------------------------------------------------


class TestWrapUpTimeout:
    @pytest.mark.asyncio
    async def test_wrapup_call_is_bounded_by_its_own_timeout(self, monkeypatch):
        monkeypatch.setattr(settings, "AGENT_WRAPUP_TIMEOUT_SECONDS", 0.05)

        async def _never_returns(**_kwargs):
            await asyncio.sleep(30)

        client = MagicMock()
        client.get_response = _never_returns

        wrapped = await asyncio.wait_for(
            _generate_stop_wrapup(
                model_client=client,
                system_prompt="s",
                user_message="u",
                turn_notes=[],
                stop_reason="stream_interrupted",
                steps=1,
            ),
            timeout=2.0,
        )

        assert "interrupted" in wrapped.lower()


# ---------------------------------------------------------------------------
# Follow-up to #579 — the interrupted conclusion must not send the user back
# to the start, and must always end with the same actionable next step
# ---------------------------------------------------------------------------


class TestInterruptedWrapupContract:
    @pytest.mark.asyncio
    async def test_wrapup_appends_the_continue_footer(self):
        captured: dict = {}
        response = MagicMock()
        response.messages = [
            MagicMock(text="The response was interrupted. The work so far is saved."),
        ]
        client = MagicMock()

        async def _get_response(**kwargs):
            captured["messages"] = kwargs["messages"]
            return response

        client.get_response = _get_response

        out = await _generate_stop_wrapup(
            model_client=client,
            system_prompt="s",
            user_message="u",
            turn_notes=[],
            stop_reason="stream_interrupted",
            steps=1,
        )

        assert out.endswith(CONTINUE_FOOTER)
        instruction = captured["messages"][-1].text
        assert "never tell them to re-run the prompt or start over" in instruction
        assert "Do not claim that no work was done" in instruction

    @pytest.mark.asyncio
    async def test_canned_fallback_also_offers_continuation(self):
        client = MagicMock()

        async def _get_response(**_kwargs):
            raise RuntimeError("provider down")

        client.get_response = _get_response

        out = await _generate_stop_wrapup(
            model_client=client,
            system_prompt="s",
            user_message="u",
            turn_notes=[],
            stop_reason="stream_interrupted",
            steps=1,
        )

        assert "Send another message" in out
        assert "continue" in out.lower()


# ---------------------------------------------------------------------------
# Issue #645 — MCP calls are bounded, so a hung server becomes a tool error
# ---------------------------------------------------------------------------


class TestMcpRequestTimeout:
    def _server(self):
        server = MagicMock()
        server.name = "powershell"
        server.transport = "streamable_http"
        server.url = "http://127.0.0.1:9999/mcp"
        server.allowed_tools = None
        return server

    def _build(self, monkeypatch):
        from src.services import mcp_service

        monkeypatch.setattr(mcp_service, "decrypt_env_vars", lambda _s: {})
        monkeypatch.setattr(mcp_service, "decrypt_headers", lambda _s: {})

        captured: dict = {}

        class _FakeMcpTool:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(
            "agent_framework.MCPStreamableHTTPTool", _FakeMcpTool,
        )
        mcp_service._build_mcp_tool_instance(self._server())
        return captured

    def test_request_timeout_is_passed_to_the_tool(self, monkeypatch):
        captured = self._build(monkeypatch)
        assert captured["request_timeout"] == int(settings.MCP_TOOL_TIMEOUT_SECONDS)

    def test_zero_disables_the_mcp_timeout(self, monkeypatch):
        monkeypatch.setattr(settings, "MCP_TOOL_TIMEOUT_SECONDS", 0)
        captured = self._build(monkeypatch)
        assert "request_timeout" not in captured


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


class TestRoundProducedAnswer:
    def test_none_round_is_not_an_answer(self):
        assert _round_produced_answer(None) is False

    def test_text_with_tool_calls_is_not_an_answer(self):
        assert _round_produced_answer(
            {"text_chars": 68, "finish_reason": "tool_calls"}
        ) is False

    def test_text_with_stop_is_an_answer(self):
        assert _round_produced_answer(
            {"text_chars": 68, "finish_reason": "stop"}
        ) is True

    def test_whitespace_only_text_is_not_an_answer(self):
        """Follow-up to #579 — stripped DSML can leave only whitespace."""
        assert _round_produced_answer(
            {
                "text_chars": 21,
                "visible_text_chars": 0,
                "finish_reason": "stop",
            }
        ) is False
