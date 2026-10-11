# =============================================================================
# PH Agent Hub — DeepSeek DSML Sanitizer + Agent Guard Tests (Issue #579)
# =============================================================================
# Covers:
#   * deepseek_patch.strip_dsml_markers / contains_dsml
#   * the fixed AGENT_MAX_STEPS guard in _run_agent_stream
#   * runaway tool-loop detection
# =============================================================================

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.deepseek_patch import contains_dsml, strip_dsml_markers

pytestmark = [pytest.mark.unit]


# ===========================================================================
# Sanitizer unit tests
# ===========================================================================


class TestContainsDsml:
    def test_detects_bare_sentinel(self):
        assert contains_dsml("text ｜｜DSML｜｜ more") is True

    def test_detects_fullwidth_tag(self):
        assert contains_dsml("<｜tool_calls｜>") is True

    def test_detects_ascii_tag(self):
        assert contains_dsml("<function_calls>") is True

    def test_plain_text_is_false(self):
        assert contains_dsml("just text with no markup") is False

    def test_empty_is_false(self):
        assert contains_dsml("") is False


class TestStripDsmlMarkers:
    def test_strips_fullwidth_container(self):
        assert strip_dsml_markers("A<｜tool_calls｜>X</｜tool_calls｜>B") == "AB"

    def test_strips_bare_sentinel(self):
        out = strip_dsml_markers("calls ｜｜DSML｜｜ done")
        assert "DSML" not in out
        assert "calls" in out and "done" in out

    def test_strips_ascii_function_calls(self):
        assert strip_dsml_markers("Hi<function_calls>x</function_calls>!") == "Hi!"

    def test_strips_invoke_with_attributes(self):
        text = (
            '<tool_calls><invoke name="get">'
            '<parameter name="c">X</parameter></invoke></tool_calls>'
        )
        out = strip_dsml_markers(text)
        assert "invoke" not in out
        assert "tool_calls" not in out

    def test_strips_unclosed_block(self):
        assert strip_dsml_markers("Keep<tool_calls>garbage") == "Keep"

    def test_plain_text_unchanged(self):
        text = "Just a normal answer."
        assert strip_dsml_markers(text) == text

    def test_empty_unchanged(self):
        assert strip_dsml_markers("") == ""


# ===========================================================================
# Stream guard / loop-detection tests (mocked MAF Agent)
# ===========================================================================


def _make_func_result_content(call_id, name, output="ok"):
    content = MagicMock()
    content.type = "function_result"
    content.call_id = call_id
    content.name = name
    content.output = output
    content.result = output
    return content


def _make_stream_update(contents):
    update = MagicMock()
    update.contents = contents
    return update


def _stream_from_updates(updates):
    async def _gen():
        for u in updates:
            yield u
    return _gen()


def _make_mock_model():
    model = MagicMock()
    model.provider = "openai"
    model.model_id = "gpt-4"
    model.max_tokens = 4096
    return model


async def _collect_events(model, updates, model_client=None):
    from src.agents.runner import _run_agent_stream

    mock_instance = MagicMock()
    mock_instance.run.return_value = _stream_from_updates(updates)
    with patch("agent_framework.Agent", return_value=mock_instance):
        events = []
        async for event in _run_agent_stream(
            model=model,
            model_client=model_client if model_client is not None else MagicMock(),
            system_prompt="test",
            tools=[MagicMock()],
            user_message="test",
            agent_name="test",
            session_id="s1",
            message_id="m1",
        ):
            events.append(event)
    return events


def _event_types(events):
    return [e.get("event") for e in events if isinstance(e, dict)]


class TestStepLimitGuard:
    @pytest.mark.asyncio
    async def test_guard_terminates_stream_and_counts_once(self, monkeypatch):
        """The guard must stop the stream at the cap and count each result once."""
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 3)
        monkeypatch.setattr(config.settings, "AGENT_LOOP_DETECTION_ENABLED", False)

        updates = [
            _make_stream_update(
                [_make_func_result_content(call_id=f"c{i}", name=f"t{i}")]
            )
            for i in range(10)
        ]
        events = await _collect_events(_make_mock_model(), updates)
        types = _event_types(events)

        # Guard fires exactly once, at the cap — not per content item.
        assert types.count("step_limit_reached") == 1
        assert types.count("step_complete") == 3

    @pytest.mark.asyncio
    async def test_cap_emits_non_empty_final_message(self, monkeypatch):
        """Issue #579 — the turn must not end on an empty bubble."""
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 2)
        monkeypatch.setattr(config.settings, "AGENT_LOOP_DETECTION_ENABLED", False)

        updates = [
            _make_stream_update(
                [_make_func_result_content(call_id=f"c{i}", name=f"t{i}")]
            )
            for i in range(5)
        ]
        events = await _collect_events(_make_mock_model(), updates)

        token_text = "".join(
            json.loads(e["data"])["delta"]
            for e in events
            if isinstance(e, dict) and e.get("event") == "token"
        )
        assert "stopped after 2" in token_text

    @pytest.mark.asyncio
    async def test_cap_uses_model_wrapup_when_available(self, monkeypatch):
        """Issue #579 — the model should author the conclusion, not a canned line."""
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 2)
        monkeypatch.setattr(config.settings, "AGENT_LOOP_DETECTION_ENABLED", False)

        updates = [
            _make_stream_update(
                [_make_func_result_content(call_id=f"c{i}", name=f"t{i}")]
            )
            for i in range(5)
        ]

        response = MagicMock()
        response.messages = [MagicMock()]
        response.messages[-1].text = "Here is a wrap-up of the work so far."
        client = MagicMock()
        client.get_response = AsyncMock(return_value=response)

        events = await _collect_events(
            _make_mock_model(), updates, model_client=client
        )
        token_text = "".join(
            json.loads(e["data"])["delta"]
            for e in events
            if isinstance(e, dict) and e.get("event") == "token"
        )
        assert "Here is a wrap-up" in token_text
        assert "stopped after" not in token_text


class TestLoopDetection:
    @pytest.mark.asyncio
    async def test_repeated_identical_call_terminates_early(self, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 25)
        monkeypatch.setattr(config.settings, "AGENT_LOOP_DETECTION_ENABLED", True)
        monkeypatch.setattr(config.settings, "AGENT_LOOP_REPEAT_THRESHOLD", 3)

        updates = [
            _make_stream_update(
                [_make_func_result_content(call_id=f"c{i}", name="PowerShell")]
            )
            for i in range(10)
        ]
        events = await _collect_events(_make_mock_model(), updates)
        types = _event_types(events)

        assert types.count("loop_detected") == 1
        assert "step_limit_reached" not in types
        assert types.count("step_complete") == 3

        token_text = "".join(
            json.loads(e["data"])["delta"]
            for e in events
            if isinstance(e, dict) and e.get("event") == "token"
        )
        assert "repeating" in token_text

    @pytest.mark.asyncio
    async def test_distinct_calls_do_not_trigger_loop_detection(self, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 25)
        monkeypatch.setattr(config.settings, "AGENT_LOOP_DETECTION_ENABLED", True)
        monkeypatch.setattr(config.settings, "AGENT_LOOP_REPEAT_THRESHOLD", 3)

        updates = [
            _make_stream_update(
                [_make_func_result_content(call_id=f"c{i}", name=f"tool_{i}")]
            )
            for i in range(6)
        ]
        events = await _collect_events(_make_mock_model(), updates)
        types = _event_types(events)

        assert "loop_detected" not in types
        assert types.count("step_complete") == 6

    @pytest.mark.asyncio
    async def test_leaked_markup_stripped_from_live_delta(self, monkeypatch):
        """A leaked DSML delta must not reach the token stream verbatim."""
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 25)

        bad = MagicMock()
        bad.type = "text"
        bad.text = "answer ｜｜DSML｜｜ here"

        events = await _collect_events(
            _make_mock_model(), [_make_stream_update([bad])]
        )
        token_text = "".join(
            json.loads(e["data"])["delta"]
            for e in events
            if isinstance(e, dict) and e.get("event") == "token"
        )
        assert "DSML" not in token_text
        assert "answer" in token_text


# ===========================================================================
# MAF function-invocation limit binding
# ===========================================================================


class TestFunctionInvocationLimits:
    def test_binds_limits_when_config_present(self):
        from src.models.base import _apply_function_invocation_limits
        from src.core.config import settings

        client = MagicMock()
        client.function_invocation_configuration = {}
        _apply_function_invocation_limits(client)

        cfg = client.function_invocation_configuration
        assert cfg["max_iterations"] == settings.AGENT_MAX_STEPS + 1
        assert (
            cfg["max_consecutive_errors_per_request"]
            == settings.AGENT_MAX_CONSECUTIVE_ERRORS_PER_REQUEST
        )

    def test_missing_config_is_safe(self):
        from src.models.base import _apply_function_invocation_limits

        client = MagicMock()
        client.function_invocation_configuration = None
        _apply_function_invocation_limits(client)  # must not raise
