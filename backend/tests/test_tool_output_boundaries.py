# =============================================================================
# PH Agent Hub — Tool-result boundary and context-pressure tests
# =============================================================================
# Covers:
#   * Issue #651 — the model-context output cap reaches MCP / non-FunctionTool
#     results (and the storage cap still applies to them)
#   * Issue #652 — the per-result cap and the compaction budget have absolute
#     ceilings independent of the declared context window
#   * Issue #653 — cumulative context-pressure telemetry and metrics
#   * Issue #654 — a single-string-key {"result": ...} envelope is unwrapped
#     before it reaches the model and is persisted unescaped
# =============================================================================

import json
from unittest.mock import MagicMock, patch

import pytest
from agent_framework import Content, FunctionTool

from src.agents import runner as runner_mod
from src.agents.runner import (
    _compute_tool_output_cap,
    _compute_turn_metrics,
    _format_tool_output_for_storage,
    _normalise_tool_result,
    _truncate_tool_output,
    _unwrap_single_value_result,
    _wrap_mcp_tool_output,
    _wrap_tool_with_output_cap,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool(name: str, func, *, description: str = "test tool") -> FunctionTool:
    return FunctionTool(
        name=name,
        description=description,
        params_schema={"type": "object", "properties": {}},
        func=func,
    )


def _fake_mcp_tool(functions: list) -> MagicMock:
    """A stand-in for a connected MCPTool (no ``.func``, has ``_functions``)."""
    tool = MagicMock()
    tool.name = "fake-mcp"
    tool._functions = functions
    return tool


def _usage_content(input_tokens: int, output_tokens: int = 10) -> MagicMock:
    content = MagicMock()
    content.type = "usage"
    content.usage_details = {
        "input_token_count": input_tokens,
        "output_token_count": output_tokens,
        "cache_read_input_token_count": 0,
    }
    return content


def _func_result_content(call_id: str = "c1", name: str = "tool") -> MagicMock:
    content = MagicMock()
    content.type = "function_result"
    content.call_id = call_id
    content.name = name
    content.output = "ok"
    content.result = "ok"
    return content


def _update(contents: list) -> MagicMock:
    update = MagicMock()
    update.contents = contents
    return update


def _stream_from(updates: list):
    async def _gen():
        for update in updates:
            yield update

    return _gen()


async def _collect_stream_events(updates: list, model: MagicMock):
    from src.agents.budget import RunBudget

    mock_instance = MagicMock()
    mock_instance.run.return_value = _stream_from(updates)
    with patch("agent_framework.Agent", return_value=mock_instance):
        events = []
        async for event in runner_mod._run_agent_stream(
            model=model,
            model_client=MagicMock(),
            system_prompt="test",
            tools=[MagicMock()],
            user_message="test",
            agent_name="test",
            session_id="s1",
            message_id="m1",
            budget=RunBudget(max_steps=50),
        ):
            events.append(event)
    return events


def _events_of(events: list, name: str) -> list:
    return [e for e in events if isinstance(e, dict) and e.get("event") == name]


def _pressure_messages(events: list) -> list[dict]:
    return [json.loads(e["data"]) for e in _events_of(events, "context_pressure")]


# ---------------------------------------------------------------------------
# Issue #654 — single-value result envelopes
# ---------------------------------------------------------------------------


class TestSingleValueEnvelope:
    def test_single_string_value_is_unwrapped(self):
        assert _unwrap_single_value_result({"result": "x"}) == "x"

    def test_multi_key_dict_is_unchanged(self):
        payload = {"result": "x", "status": 0}
        assert _unwrap_single_value_result(payload) is payload

    def test_error_shaped_dict_is_kept(self):
        """Error keys carry semantics ``_is_tool_error`` relies on."""
        for key in ("error", "exc", "exc_type", "exception"):
            payload = {key: "boom"}
            assert _unwrap_single_value_result(payload) is payload

    def test_non_string_value_is_unchanged(self):
        payload = {"count": 3}
        assert _unwrap_single_value_result(payload) is payload

    @pytest.mark.asyncio
    async def test_custom_tool_envelope_reaches_model_as_text(self):
        """A custom tool returning {"result": "x"} must expose just ``x``."""

        async def execute() -> dict:
            return {"result": "line1\r\nline2", "status": 0}

        wrapped = _wrap_tool_with_output_cap(_tool("custom", execute))
        # Multi-key stays a dict, but a true envelope is a bare string.
        assert await wrapped.func() == {"result": "line1\r\nline2", "status": 0}

        async def envelope() -> dict:
            return {"result": "line1\r\nline2"}

        wrapped = _wrap_tool_with_output_cap(_tool("custom2", envelope))
        result = await wrapped.func()
        assert result == "line1\r\nline2"
        assert isinstance(result, str)

        # The model receives MAF's parsed Content, so verify that boundary too.
        parsed = await wrapped.invoke()
        assert [item.text for item in parsed] == ["line1\r\nline2"]

    @pytest.mark.asyncio
    async def test_envelope_is_persisted_unescaped(self):
        """Issue #654 — no JSON wrapper or ``\\r\\n`` escapes in storage."""

        async def envelope() -> dict:
            return {"result": "row1\r\nrow2"}

        wrapped = _wrap_tool_with_output_cap(_tool("custom", envelope))
        stored = _format_tool_output_for_storage(await wrapped.func())

        assert stored == "row1\r\nrow2"
        assert "\\r\\n" not in stored
        assert '"result"' not in stored


# ---------------------------------------------------------------------------
# Issue #651 — the cap reaches MCP / non-FunctionTool results
# ---------------------------------------------------------------------------


class TestMCPOutputCap:
    @pytest.mark.asyncio
    async def test_mcp_text_result_is_truncated(self):
        big = "x" * 126_684

        async def remote_tool() -> str:
            return big

        mcp_tool = _fake_mcp_tool([_tool("PowerShell", remote_tool)])
        capped = _wrap_mcp_tool_output(mcp_tool, context_length=1_000_000)

        assert capped == 1
        result = await mcp_tool._functions[0].func()
        assert isinstance(result, str)
        assert "characters truncated" in result
        assert len(result) <= _compute_tool_output_cap(1_000_000) + 200

    @pytest.mark.asyncio
    async def test_mcp_content_list_is_truncated(self):
        """MAF MCP tools return list[Content], not a raw string."""

        async def remote_tool() -> list:
            return [Content.from_text("y" * 126_684)]

        mcp_tool = _fake_mcp_tool([_tool("PowerShell", remote_tool)])
        _wrap_mcp_tool_output(mcp_tool, context_length=1_000_000)

        result = await mcp_tool._functions[0].func()
        assert isinstance(result, list)
        text = result[0].text
        assert "characters truncated" in text
        assert len(text) <= _compute_tool_output_cap(1_000_000) + 200

    def test_wrapping_is_idempotent(self):
        def remote_tool() -> str:
            return "x"

        mcp_tool = _fake_mcp_tool([_tool("PowerShell", remote_tool)])
        assert _wrap_mcp_tool_output(mcp_tool, context_length=8192) == 1
        first = mcp_tool._functions[0].func
        assert _wrap_mcp_tool_output(mcp_tool, context_length=8192) == 0
        assert mcp_tool._functions[0].func is first

    def test_storage_cap_applies_to_mcp_sized_payload(self):
        """Acceptance: STORAGE_TOOL_OUTPUT_MAX_CHARS clips a 126K MCP dump."""
        from src.agents.runner import STORAGE_TOOL_OUTPUT_MAX_CHARS

        stored = _format_tool_output_for_storage("z" * 126_684)
        assert "truncated" in stored
        assert len(stored) < 126_684
        assert stored.startswith("z" * STORAGE_TOOL_OUTPUT_MAX_CHARS)

    def test_non_function_tool_without_cap_still_passes_through(self):
        """A declaration-only tool has nothing to execute or cap."""
        tool = FunctionTool(name="declaration", description="none", func=None)
        assert _wrap_tool_with_output_cap(tool) is tool


# ---------------------------------------------------------------------------
# Issue #652 — absolute ceilings
# ---------------------------------------------------------------------------


class TestAbsoluteCeilings:
    def test_result_cap_has_absolute_ceiling(self):
        from src.agents.runner import TOOL_OUTPUT_CAP_CEILING

        assert TOOL_OUTPUT_CAP_CEILING > 0
        assert _compute_tool_output_cap(1_000_000) == TOOL_OUTPUT_CAP_CEILING

    def test_unknown_window_fallback_is_unchanged(self):
        from src.agents.runner import TOOL_OUTPUT_CAP_FALLBACK

        assert _compute_tool_output_cap(None) == TOOL_OUTPUT_CAP_FALLBACK

    def test_compaction_budget_has_absolute_ceiling(self):
        from src.agents.runner import (
            COMPACTION_TOKEN_CEILING,
            _build_compaction_strategy,
        )

        model = MagicMock()
        model.context_length = 1_000_000
        strategy, _ = _build_compaction_strategy(model, "client", ["tool"])
        assert strategy.token_budget == COMPACTION_TOKEN_CEILING


# ---------------------------------------------------------------------------
# Issue #653 — context pressure and metrics
# ---------------------------------------------------------------------------


class TestContextPressure:
    @pytest.mark.asyncio
    async def test_emits_once_per_turn_over_threshold(self, monkeypatch):
        monkeypatch.setattr(
            runner_mod.settings, "AGENT_CONTEXT_PRESSURE_MULTIPLIER", 1.0
        )
        model = MagicMock()
        model.provider = "openai"
        model.model_id = "m"
        model.max_tokens = 4096
        model.context_length = 1_000

        updates = [
            _update([_usage_content(600), _func_result_content(f"c{i}", f"t{i}")])
            for i in range(3)
        ]
        events = await _collect_stream_events(updates, model)
        pressure = _pressure_messages(events)

        assert len(pressure) == 1, "context pressure must fire once per turn"
        assert pressure[0]["context_length"] == 1_000
        assert pressure[0]["threshold"] == 1_000
        assert pressure[0]["input_tokens"] >= 1_000
        assert pressure[0]["kind"] == "context_pressure"

    @pytest.mark.asyncio
    async def test_default_multiplier_does_not_fire_for_normal_turn(self, monkeypatch):
        """Issue #653 acceptance — no new event where none fired before."""
        monkeypatch.setattr(
            runner_mod.settings, "AGENT_CONTEXT_PRESSURE_MULTIPLIER", 20.0
        )
        model = MagicMock()
        model.provider = "openai"
        model.model_id = "m"
        model.max_tokens = 4096
        model.context_length = 128_000

        updates = [
            _update([_usage_content(10_000), _func_result_content(f"c{i}", f"t{i}")])
            for i in range(3)
        ]
        events = await _collect_stream_events(updates, model)

        assert _pressure_messages(events) == []

    def test_metrics_include_cost_and_growth(self):
        metrics = _compute_turn_metrics(
            turn_start_s=0.0,
            turn_end_s=1.0,
            first_token_at_s=None,
            tool_durations_s=[],
            steps=4,
            cache_hit_tokens=0,
            system_prompt_tokens=0,
            tool_definition_tokens=0,
            tokens_in=1_000_000,
            last_input_tokens=300_000,
            first_input_tokens=100_000,
            context_pressure=True,
            context_pressure_threshold=200_000,
            rounds=4,
        )

        assert metrics["avg_input_tokens_per_step"] == 250_000
        assert metrics["context_growth"] == 200_000
        assert metrics["context_pressure"] is True
        assert metrics["context_pressure_threshold"] == 200_000

    def test_metrics_handle_turn_without_measurements(self):
        metrics = _compute_turn_metrics(
            turn_start_s=0.0,
            turn_end_s=1.0,
            first_token_at_s=None,
            tool_durations_s=[],
            steps=0,
            cache_hit_tokens=0,
            system_prompt_tokens=0,
            tool_definition_tokens=0,
            tokens_in=0,
        )

        assert metrics["avg_input_tokens_per_step"] is None
        assert metrics["context_growth"] is None
        assert metrics["context_pressure"] is False


# ---------------------------------------------------------------------------
# Issue #654 follow-up — MCP text envelopes are JSON strings, not dicts
# ---------------------------------------------------------------------------


class TestTextEnvelope:
    def test_json_result_envelope_is_unwrapped(self):
        from src.agents.runner import _unwrap_json_text_envelope

        raw = json.dumps({"result": "row1\r\nrow2"})
        assert _unwrap_json_text_envelope(raw) == "row1\r\nrow2"

    def test_structured_single_key_json_is_preserved(self):
        from src.agents.runner import _unwrap_json_text_envelope

        raw = json.dumps({"ticker": "BAC"})
        assert _unwrap_json_text_envelope(raw) is raw

    def test_invalid_json_is_unchanged(self):
        from src.agents.runner import _unwrap_json_text_envelope

        raw = '{"result": "cut mid'
        assert _unwrap_json_text_envelope(raw) is raw

    def test_plain_text_is_unchanged(self):
        from src.agents.runner import _unwrap_json_text_envelope

        assert _unwrap_json_text_envelope("just text") == "just text"

    @pytest.mark.asyncio
    async def test_mcp_powershell_envelope_reaches_model_as_text(self):
        """The production PowerShell MCP result shape."""
        payload = json.dumps({"result": "Response: row1\r\nrow2\r\n\nStatus Code: 0"})

        async def remote_tool() -> list:
            return [Content.from_text(payload)]

        mcp_tool = _fake_mcp_tool([_tool("PowerShell", remote_tool)])
        _wrap_mcp_tool_output(mcp_tool, context_length=1_000_000)

        result = await mcp_tool._functions[0].func()
        text = result[0].text
        assert text == "Response: row1\r\nrow2\r\n\nStatus Code: 0"
        assert '"result"' not in text
        assert "\\r\\n" not in text

    @pytest.mark.asyncio
    async def test_large_mcp_envelope_is_unwrapped_then_capped(self):
        big = "x" * 126_684
        payload = json.dumps({"result": big})

        async def remote_tool() -> list:
            return [Content.from_text(payload)]

        mcp_tool = _fake_mcp_tool([_tool("PowerShell", remote_tool)])
        _wrap_mcp_tool_output(mcp_tool, context_length=1_000_000)

        result = await mcp_tool._functions[0].func()
        text = result[0].text
        assert '"result"' not in text
        assert "characters truncated" in text
        assert len(text) <= _compute_tool_output_cap(1_000_000) + 200

    def test_legacy_stored_envelope_renders_as_text(self):
        """Old messages (before the boundary unwrap) display cleanly too."""
        from src.api.chat import _render_tool_output

        raw = json.dumps({"result": "Response: row1\r\nrow2\r\n\nStatus Code: 0"})
        rendered = _render_tool_output(raw)
        assert rendered == "Response: row1\r\nrow2\r\n\nStatus Code: 0"
        assert '"result"' not in rendered
