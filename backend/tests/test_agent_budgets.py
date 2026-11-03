# =============================================================================
# PH Agent Hub — Agent Run Budget Tests (Issue #595)
# =============================================================================
# Covers:
#   * streaming step / input-token budget enforcement in ``_run_agent_stream``
#   * warning thresholds, stop events and the deterministic budget footer
#   * streamed usage accumulation and telemetry payloads
#   * non-streaming (``_run_agent``) telemetry and budget reporting
#   * MAF ``max_iterations`` binding to the effective per-skill step budget
# =============================================================================

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.budget import RunBudget

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_model():
    model = MagicMock()
    model.provider = "openai"
    model.model_id = "gpt-4"
    model.max_tokens = 4096
    return model


def _make_func_result_content(call_id="call_1", name="test_tool", output="ok"):
    content = MagicMock()
    content.type = "function_result"
    content.call_id = call_id
    content.name = name
    content.output = output
    content.result = output
    return content


def _make_usage_content(input_tokens, output_tokens=10, cache_hits=0):
    content = MagicMock()
    content.type = "usage"
    content.usage_details = {
        "input_token_count": input_tokens,
        "output_token_count": output_tokens,
        "cache_read_input_token_count": cache_hits,
    }
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


def _event_data(event):
    return json.loads(event["data"])


async def _collect_events(updates, *, budget, token_counts=None):
    from src.agents.runner import _run_agent_stream

    mock_instance = MagicMock()
    mock_instance.run.return_value = _stream_from_updates(updates)
    with patch("agent_framework.Agent", return_value=mock_instance):
        events = []
        async for event in _run_agent_stream(
            model=_make_mock_model(),
            model_client=MagicMock(),
            system_prompt="test",
            tools=[MagicMock()],
            user_message="test",
            agent_name="test",
            session_id="s1",
            message_id="m1",
            token_counts=token_counts,
            budget=budget,
        ):
            events.append(event)
    return events


def _events_of(events, name):
    return [e for e in events if isinstance(e, dict) and e.get("event") == name]


def _joined_token_text(events):
    return "".join(
        _event_data(e).get("delta", "")
        for e in _events_of(events, "token")
    )


def _step_updates(count, *, usage_per_step=None):
    """Build *count* sequential tool-result updates with distinct tool names."""
    updates = []
    for i in range(count):
        contents = [_make_func_result_content(call_id=f"c{i}", name=f"tool_{i}")]
        if usage_per_step is not None:
            contents.insert(0, _make_usage_content(usage_per_step))
        updates.append(_make_stream_update(contents))
    return updates


# ---------------------------------------------------------------------------
# Streaming: step budget
# ---------------------------------------------------------------------------


class TestStreamingStepBudget:
    @pytest.mark.asyncio
    async def test_warns_at_60_percent_and_stops_at_budget(self):
        budget = RunBudget(
            max_steps=70,
            max_steps_source="skill",
        )
        events = await _collect_events(_step_updates(70), budget=budget)

        step_events = _events_of(events, "step_complete")
        assert len(step_events) == 70

        warnings = _events_of(events, "budget_warning")
        assert len(warnings) == 1
        warning = _event_data(warnings[0])
        assert warning["kind"] == "steps"
        assert warning["consumed"] == 42  # ceil(70 * 0.6)
        assert warning["budget"] == 70
        assert warning["percent"] == 60.0

        limit_events = _events_of(events, "step_limit_reached")
        assert len(limit_events) == 1
        limit = _event_data(limit_events[0])
        assert limit["max_steps"] == 70
        assert limit["budget_source"] == "skill"
        assert limit["tool_calls"] == 70

        exceeded = _events_of(events, "budget_exceeded")
        assert len(exceeded) == 1
        assert _event_data(exceeded[0])["kind"] == "steps"

        text = _joined_token_text(events)
        assert "stopped after 70" in text
        assert "Budget: 70 steps" in text
        assert "Used: 70 steps, 70 tool calls" in text

    @pytest.mark.asyncio
    async def test_per_skill_steps_override_global(self):
        """A skill budget of 3 must stop at 3 even when global is 1."""
        budget = RunBudget(max_steps=3, max_steps_source="skill")
        events = await _collect_events(_step_updates(5), budget=budget)
        assert len(_events_of(events, "step_complete")) == 3
        assert len(_events_of(events, "step_limit_reached")) == 1

    @pytest.mark.asyncio
    async def test_parallel_batch_counts_one_step_but_many_calls(self):
        budget = RunBudget(max_steps=2)
        updates = [
            _make_stream_update([
                _make_func_result_content(call_id=f"c{i}", name=f"t{i}")
                for i in range(3)
            ]),
            _make_stream_update([
                _make_func_result_content(call_id=f"d{i}", name=f"u{i}")
                for i in range(4)
            ]),
        ]
        events = await _collect_events(updates, budget=budget)
        assert len(_events_of(events, "step_complete")) == 2
        limit = _event_data(_events_of(events, "step_limit_reached")[0])
        # 2 rounds, 7 executed tool calls.
        assert limit["tool_calls"] == 7
        assert limit["steps"] == 2


# ---------------------------------------------------------------------------
# Streaming: input-token budget
# ---------------------------------------------------------------------------


class TestStreamingTokenBudget:
    @pytest.mark.asyncio
    async def test_warns_and_stops_at_input_token_budget(self):
        budget = RunBudget(
            max_steps=50,
            max_input_tokens=3_000_000,
            max_input_tokens_source="skill",
        )
        token_counts: dict = {}
        events = await _collect_events(
            _step_updates(5, usage_per_step=1_000_000),
            budget=budget,
            token_counts=token_counts,
        )

        warnings = _events_of(events, "budget_warning")
        assert len(warnings) == 1
        warning = _event_data(warnings[0])
        assert warning["kind"] == "input_tokens"
        assert warning["consumed"] == 2_000_000
        assert warning["budget"] == 3_000_000

        exceeded = _events_of(events, "budget_exceeded")
        assert len(exceeded) == 1
        data = _event_data(exceeded[0])
        assert data["kind"] == "input_tokens"
        assert data["consumed"] == 3_000_000
        assert data["input_tokens"] == 3_000_000

        text = _joined_token_text(events)
        assert "3,000,000-input-token budget" in text
        assert "Budget: 50 steps / 3,000,000 input tokens" in text

        assert token_counts["in"] == 3_000_000
        assert token_counts["budget_exceeded"] is True
        assert token_counts["budget_kind"] == "input_tokens"
        metrics = token_counts["metrics"]
        assert metrics["budget_exceeded"] is True
        assert metrics["budget"]["max_input_tokens"] == 3_000_000

    @pytest.mark.asyncio
    async def test_streamed_usage_totals_survive_missing_final_response(self):
        """Issue #595 — totals must come from the stream when final is absent."""
        budget = RunBudget(max_steps=50)
        token_counts: dict = {}
        events = await _collect_events(
            _step_updates(3, usage_per_step=1_500),
            budget=budget,
            token_counts=token_counts,
        )
        # No stop events on a normal run.
        assert not _events_of(events, "budget_exceeded")
        assert token_counts["in"] == 4_500
        assert token_counts["out"] == 30
        assert token_counts["tool_calls"] == 3
        assert token_counts["metrics"]["tool_calls"] == 3
        assert token_counts["metrics"]["budget_exceeded"] is False

    @pytest.mark.asyncio
    async def test_missing_usage_does_not_crash_or_stop(self):
        budget = RunBudget(max_steps=50, max_input_tokens=1_000)
        token_counts: dict = {}
        events = await _collect_events(
            _step_updates(5),
            budget=budget,
            token_counts=token_counts,
        )
        assert not _events_of(events, "budget_exceeded")
        assert token_counts["budget_enforced"] is False
        assert token_counts["budget_exceeded"] is False

    @pytest.mark.asyncio
    async def test_normal_short_run_unchanged(self):
        budget = RunBudget(max_steps=50)
        token_counts: dict = {}
        events = await _collect_events(
            _step_updates(3),
            budget=budget,
            token_counts=token_counts,
        )
        assert not _events_of(events, "budget_warning")
        assert not _events_of(events, "budget_exceeded")
        assert not _events_of(events, "step_limit_reached")
        # No synthetic wrap-up token on a clean finish.
        assert _joined_token_text(events) == ""
        assert token_counts["metrics"]["steps"] == 3
        assert token_counts["metrics"]["budget_exceeded"] is False
        assert token_counts["budget"] == budget.to_dict()


# ---------------------------------------------------------------------------
# message_complete payload
# ---------------------------------------------------------------------------


class TestMessageCompletePayload:
    def test_payload_includes_budget_telemetry(self):
        from src.agents.runner import _build_message_complete_payload

        cfg = MagicMock()
        cfg.run_budget = RunBudget(max_steps=70, max_input_tokens=12_000_000)
        cfg.model.id = "model-1"
        cfg.model.name = "Test Model"
        cfg.model.provider = "deepseek"
        token_info = {
            "in": 12_345_678,
            "out": 90_000,
            "cache_hit": 12_000_000,
            "tool_calls": 78,
            "budget_exceeded": True,
            "budget_kind": "input_tokens",
            "budget": cfg.run_budget.to_dict(),
            "metrics": {"steps": 70, "tool_calls": 78},
        }

        payload = _build_message_complete_payload(
            session_id="s1", message_id="m1", token_info=token_info, cfg=cfg,
        )

        assert payload["input_tokens"] == 12_345_678
        assert payload["output_tokens"] == 90_000
        assert payload["cache_hits"] == 12_000_000
        assert payload["steps"] == 70
        assert payload["tool_calls"] == 78
        assert payload["budget_exceeded"] is True
        assert payload["budget"]["max_input_tokens"] == 12_000_000
        assert payload["tokens_in"] == 12_345_678  # legacy key preserved
        assert payload["model_id"] == "model-1"

    def test_payload_without_budget_metadata(self):
        from src.agents.runner import _build_message_complete_payload

        cfg = MagicMock()
        cfg.run_budget = None
        cfg.model.id = "model-1"
        cfg.model.name = "Test Model"
        cfg.model.provider = "openai"

        payload = _build_message_complete_payload(
            session_id="s1", message_id="m1", token_info={}, cfg=cfg,
        )
        assert payload["budget"] is None
        assert payload["budget_exceeded"] is False
        assert payload["tool_calls"] == 0


# ---------------------------------------------------------------------------
# Non-streaming path
# ---------------------------------------------------------------------------


def _make_agent_result(*, tool_calls=0, tokens_in=0, tokens_out=0):
    result = MagicMock()
    result.final_output = "done"
    result.usage_details = {
        "input_token_count": tokens_in,
        "output_token_count": tokens_out,
    }
    messages = []
    for i in range(tool_calls):
        message = MagicMock()
        content = MagicMock()
        content.type = "function_call"
        message.contents = [content]
        messages.append(message)
    result.messages = messages
    return result


class TestNonStreamingBudget:
    def test_client_binds_effective_step_budget(self):
        from src.models.base import _apply_function_invocation_limits

        client = MagicMock()
        client.function_invocation_configuration = {}
        _apply_function_invocation_limits(client, max_steps=70)
        assert client.function_invocation_configuration["max_iterations"] == 71

    def test_client_defaults_to_global_step_budget(self):
        from src.core.config import settings
        from src.models.base import _apply_function_invocation_limits

        client = MagicMock()
        client.function_invocation_configuration = {}
        _apply_function_invocation_limits(client)
        assert (
            client.function_invocation_configuration["max_iterations"]
            == settings.AGENT_MAX_STEPS + 1
        )

    @pytest.mark.asyncio
    async def test_run_agent_counts_tool_calls_and_reports_budget_overage(self):
        from src.agents.runner import _run_agent

        result = _make_agent_result(
            tool_calls=2, tokens_in=2_000_000, tokens_out=100,
        )
        metrics: dict = {}
        with patch("agent_framework.Agent") as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=result)
            mock_agent_class.return_value = mock_agent

            text, tokens_in, tokens_out, _ = await _run_agent(
                model=_make_mock_model(),
                model_client=MagicMock(),
                system_prompt="test",
                tools=[MagicMock()],
                user_message="hi",
                agent_name="test",
                metrics_out=metrics,
                budget=RunBudget(max_steps=5, max_input_tokens=1_000_000),
            )

        assert tokens_in == 2_000_000
        assert tokens_out == 100
        assert metrics["tool_calls"] == 2
        assert metrics["budget_exceeded"] is True
        assert metrics["budget_kind"] == "input_tokens"
        assert "reached its run budget" in text
        assert "1,000,000 input tokens" in text
        assert "Used: 0 steps, 2 tool calls" in text

    @pytest.mark.asyncio
    async def test_run_agent_normal_run_unchanged(self):
        from src.agents.runner import _run_agent

        result = _make_agent_result(tool_calls=1, tokens_in=500, tokens_out=10)
        metrics: dict = {}
        with patch("agent_framework.Agent") as mock_agent_class:
            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=result)
            mock_agent_class.return_value = mock_agent

            text, _, _, _ = await _run_agent(
                model=_make_mock_model(),
                model_client=MagicMock(),
                system_prompt="test",
                tools=[MagicMock()],
                user_message="hi",
                agent_name="test",
                metrics_out=metrics,
                budget=RunBudget(max_steps=50),
            )

        assert text == "done"
        assert metrics["tool_calls"] == 1
        assert metrics["budget_exceeded"] is False


# ---------------------------------------------------------------------------
# Session-config wiring: skill/template agent_config -> SessionConfig.run_budget
# ---------------------------------------------------------------------------


class TestSessionConfigBudgetWiring:
    @pytest.mark.asyncio
    async def test_skill_and_template_budgets_are_wired_into_session_config(self):
        from src.agents.runner import _resolve_session_config

        model = _make_mock_model()
        skill = MagicMock()
        skill.execution_type = "prompt_based"
        skill.title = "Research Skill"
        skill.template_id = "template-1"
        skill.constraints = None
        skill.agent_config = {"max_steps": 70}
        template = MagicMock()
        template.agent_config = {"max_input_tokens": 12_000_000}

        db = MagicMock()
        db.execute = AsyncMock()
        db_result = MagicMock()
        db_result.scalar_one_or_none.return_value = None
        db.execute.return_value = db_result

        with (
            patch("src.agents.runner._resolve_model", AsyncMock(return_value=model)),
            patch("src.agents.runner._resolve_skill", AsyncMock(return_value=skill)),
            patch(
                "src.agents.runner._build_system_prompt",
                AsyncMock(return_value="prompt"),
            ),
            patch(
                "src.agents.runner._resolve_tool_callables",
                AsyncMock(return_value=([], [])),
            ),
            patch(
                "src.agents.runner._resolve_config_template",
                AsyncMock(return_value=template),
            ),
            patch("src.agents.runner.get_chat_client") as mock_client_factory,
        ):
            cfg = await _resolve_session_config(
                db,
                {
                    "id": "session-1",
                    "auto_select_tools": False,
                    "selected_skill_id": "skill-1",
                },
                "tenant-1",
            )

        assert cfg.run_budget is not None
        assert cfg.run_budget.max_steps == 70
        assert cfg.run_budget.max_steps_source == "skill"
        assert cfg.run_budget.max_input_tokens == 12_000_000
        assert cfg.run_budget.max_input_tokens_source == "template"
        assert (
            mock_client_factory.call_args.kwargs["max_steps"] == 70
        )

    @pytest.mark.asyncio
    async def test_no_skill_or_template_uses_global_fallbacks(self):
        from src.agents.runner import _resolve_session_config

        model = _make_mock_model()
        db = MagicMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        ))

        with (
            patch("src.agents.runner._resolve_model", AsyncMock(return_value=model)),
            patch("src.agents.runner._resolve_skill", AsyncMock(return_value=None)),
            patch(
                "src.agents.runner._build_system_prompt",
                AsyncMock(return_value="prompt"),
            ),
            patch(
                "src.agents.runner._resolve_tool_callables",
                AsyncMock(return_value=([], [])),
            ),
            patch(
                "src.agents.runner._resolve_config_template",
                AsyncMock(return_value=None),
            ),
            patch("src.agents.runner.get_chat_client") as mock_client_factory,
        ):
            cfg = await _resolve_session_config(
                db,
                {"id": "session-2", "auto_select_tools": False},
                "tenant-1",
            )

        from src.core.config import settings

        assert cfg.run_budget is not None
        assert cfg.run_budget.max_steps == settings.AGENT_MAX_STEPS
        assert cfg.run_budget.max_steps_source == "global"
        assert cfg.run_budget.max_input_tokens_source == "global"
        assert (
            mock_client_factory.call_args.kwargs["max_steps"]
            == settings.AGENT_MAX_STEPS
        )

