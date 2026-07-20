# =============================================================================
# PH Agent Hub — Parallel Tool Execution Tests (Issue #447)
# =============================================================================

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_db_session():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    async def default_execute(*args, **kwargs):
        result = MagicMock()
        result.scalars.return_value = result
        result.all.return_value = []
        result.first.return_value = None
        result.scalar_one_or_none.return_value = None
        return result
    session.execute = default_execute
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session

def _make_func_result_content(call_id="call_1", name="test_tool", output="ok", success=True):
    content = MagicMock()
    content.type = "function_result"
    content.call_id = call_id
    content.name = name
    content.output = output
    content.result = output
    if not success:
        content.output = {"error": "something went wrong"}
        content.result = {"error": "something went wrong"}
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

# ---------------------------------------------------------------------------
# 1. System prompt injection
# ---------------------------------------------------------------------------

class TestParallelPromptInjection:
    @pytest.fixture(autouse=True)
    def _setup(self):
        from src.agents.runner import _build_system_prompt
        self.fn = _build_system_prompt

    @pytest.mark.asyncio
    async def test_guidance_injected_when_enabled(self, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_PARALLEL_TOOLS_ENABLED", True)
        db = _make_mock_db_session()
        from src.db.orm.templates import Template
        mock_template = MagicMock(spec=Template)
        mock_template.system_prompt = "You are a test agent."
        async def mock_execute(*args, **kwargs):
            result = MagicMock()
            result.scalars.return_value = result
            result.scalar_one_or_none.return_value = mock_template
            return result
        db.execute = mock_execute
        prompt = await self.fn(db, {"selected_template_id": str(uuid.uuid4())})
        assert "Parallel Tool Execution" in prompt
        assert "MULTIPLE tools" in prompt

    @pytest.mark.asyncio
    async def test_guidance_absent_when_disabled(self, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_PARALLEL_TOOLS_ENABLED", False)
        db = _make_mock_db_session()
        from src.db.orm.templates import Template
        mock_template = MagicMock(spec=Template)
        mock_template.system_prompt = "You are a test agent."
        async def mock_execute(*args, **kwargs):
            result = MagicMock()
            result.scalars.return_value = result
            result.scalar_one_or_none.return_value = mock_template
            return result
        db.execute = mock_execute
        prompt = await self.fn(db, {"selected_template_id": str(uuid.uuid4())})
        assert "Parallel Tool Execution" not in prompt

    @pytest.mark.asyncio
    async def test_guidance_absent_no_template(self, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_PARALLEL_TOOLS_ENABLED", True)
        db = _make_mock_db_session()
        prompt = await self.fn(db, {})
        assert "Parallel Tool Execution" not in prompt

# ---------------------------------------------------------------------------
# 2. Step counting
# ---------------------------------------------------------------------------

class TestParallelStepCounting:

    async def _make_mock_model(self):
        model = MagicMock()
        model.provider = "openai"
        model.model_id = "gpt-4"
        model.max_tokens = 4096
        return model

    @patch("agent_framework.Agent")
    async def test_single_tool_one_step(self, mock_agent_class, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 15)
        from src.agents.runner import _run_agent_stream

        mock_instance = MagicMock()
        mock_instance.run.return_value = _stream_from_updates([
            _make_stream_update([
                _make_func_result_content(call_id="c1", name="fetch", output="done"),
            ]),
        ])
        mock_agent_class.return_value = mock_instance

        model = await self._make_mock_model()
        events = []
        async for event in _run_agent_stream(
            model=model, model_client=MagicMock(),
            system_prompt="test", tools=[MagicMock()],
            user_message="test", agent_name="test",
            session_id="s1", message_id="m1",
        ):
            events.append(event)

        event_types = [e.get("event") for e in events if isinstance(e, dict)]
        assert "tool_start" in event_types
        assert "tool_result" in event_types
        assert "step_complete" in event_types
        for e in events:
            if isinstance(e, dict) and e.get("event") == "step_complete":
                data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
                assert "batch_id" not in data

    @patch("agent_framework.Agent")
    async def test_parallel_batch_one_step(self, mock_agent_class, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 15)
        from src.agents.runner import _run_agent_stream

        mock_instance = MagicMock()
        mock_instance.run.return_value = _stream_from_updates([
            _make_stream_update([
                _make_func_result_content(call_id="c1", name="a", output="ok"),
                _make_func_result_content(call_id="c2", name="b", output="ok"),
                _make_func_result_content(call_id="c3", name="c", output="ok"),
            ]),
        ])
        mock_agent_class.return_value = mock_instance

        model = await self._make_mock_model()
        events = []
        async for event in _run_agent_stream(
            model=model, model_client=MagicMock(),
            system_prompt="test", tools=[MagicMock()],
            user_message="test", agent_name="test",
            session_id="s1", message_id="m1",
        ):
            events.append(event)

        event_types = [e.get("event") for e in events if isinstance(e, dict)]
        assert "step_complete" in event_types

        for e in events:
            if isinstance(e, dict) and e.get("event") == "step_complete":
                data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
                assert data.get("batch_id") is not None
                assert data.get("batch_size") == 3

        batch_id = None
        for e in events:
            if isinstance(e, dict) and e.get("event") == "step_complete":
                data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
                batch_id = data.get("batch_id")
                break
        assert batch_id is not None
        for e in events:
            if isinstance(e, dict) and e.get("event") in ("tool_start", "tool_result"):
                data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
                assert data.get("batch_id") == batch_id

    @patch("agent_framework.Agent")
    async def test_sequential_two_steps(self, mock_agent_class, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 15)
        from src.agents.runner import _run_agent_stream

        mock_instance = MagicMock()
        mock_instance.run.return_value = _stream_from_updates([
            _make_stream_update([
                _make_func_result_content(call_id="c1", name="a", output="ok"),
            ]),
            _make_stream_update([
                _make_func_result_content(call_id="c2", name="b", output="ok"),
            ]),
        ])
        mock_agent_class.return_value = mock_instance

        model = await self._make_mock_model()
        events = []
        async for event in _run_agent_stream(
            model=model, model_client=MagicMock(),
            system_prompt="test", tools=[MagicMock()],
            user_message="test", agent_name="test",
            session_id="s1", message_id="m1",
        ):
            events.append(event)

        step_events = [e for e in events if isinstance(e, dict) and e.get("event") == "step_complete"]
        assert len(step_events) == 2
        for se in step_events:
            data = json.loads(se["data"]) if isinstance(se["data"], str) else se["data"]
            assert "batch_id" not in data

    @patch("src.agents.runner.settings")
    @patch("agent_framework.Agent")
    async def test_step_limit_respected(self, mock_agent_class, mock_settings, monkeypatch):
        mock_settings.AGENT_MAX_STEPS = 1
        from src.agents.runner import _run_agent_stream

        mock_instance = MagicMock()
        mock_instance.run.return_value = _stream_from_updates([
            _make_stream_update([
                _make_func_result_content(call_id=f"c{i}", name=f"t{i}", output="ok")
                for i in range(5)
            ]),
        ])
        mock_agent_class.return_value = mock_instance

        model = await self._make_mock_model()
        events = []
        async for event in _run_agent_stream(
            model=model, model_client=MagicMock(),
            system_prompt="test", tools=[MagicMock()],
            user_message="test", agent_name="test",
            session_id="s1", message_id="m1",
        ):
            events.append(event)

        event_types = [e.get("event") for e in events if isinstance(e, dict)]
        assert "step_limit_reached" in event_types

# ---------------------------------------------------------------------------
# 3. Error isolation
# ---------------------------------------------------------------------------

class TestParallelErrorIsolation:

    async def _make_mock_model(self):
        model = MagicMock()
        model.provider = "openai"
        model.model_id = "gpt-4"
        model.max_tokens = 4096
        return model

    @patch("agent_framework.Agent")
    async def test_error_does_not_block_others(self, mock_agent_class, monkeypatch):
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_MAX_STEPS", 15)
        from src.agents.runner import _run_agent_stream

        mock_instance = MagicMock()
        mock_instance.run.return_value = _stream_from_updates([
            _make_stream_update([
                _make_func_result_content(call_id="c1", name="ok1", output="good"),
                _make_func_result_content(call_id="c2", name="fail", output={"error": "boom"}, success=False),
                _make_func_result_content(call_id="c3", name="ok2", output="also good"),
            ]),
        ])
        mock_agent_class.return_value = mock_instance

        model = await self._make_mock_model()
        events = []
        async for event in _run_agent_stream(
            model=model, model_client=MagicMock(),
            system_prompt="test", tools=[MagicMock()],
            user_message="test", agent_name="test",
            session_id="s1", message_id="m1",
        ):
            events.append(event)

        result_events = [e for e in events if isinstance(e, dict) and e.get("event") == "tool_result"]
        assert len(result_events) == 3

        fail = None
        for e in result_events:
            data = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
            if data.get("tool_name") == "fail":
                fail = data
                assert data["success"] is False
            elif data.get("tool_name") in ("ok1", "ok2"):
                assert data["success"] is True
        assert fail is not None

# ---------------------------------------------------------------------------
# 4. allow_multiple_tool_calls in default_options
# ---------------------------------------------------------------------------

class TestAllowMultipleToolCalls:
    @pytest.mark.asyncio
    async def test_set_when_enabled_with_tools(self, monkeypatch):
        from src.agents.runner import _run_agent
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_PARALLEL_TOOLS_ENABLED", True)
        mock_model = MagicMock()
        mock_model.max_tokens = 0
        mock_result = MagicMock()
        mock_result.final_output = "done"
        mock_result.usage_details = {}
        captured = {}
        with patch("agent_framework.Agent") as mc:
            ma = MagicMock()
            ma.run = AsyncMock(return_value=mock_result)
            def cap(*a, **kw):
                nonlocal captured
                captured = kw.get("default_options", {})
                return ma
            mc.side_effect = cap
            await _run_agent(model=mock_model, model_client=MagicMock(),
                             system_prompt="test", tools=[MagicMock()],
                             user_message="test", agent_name="test")
        assert captured.get("allow_multiple_tool_calls") is True

    @pytest.mark.asyncio
    async def test_not_set_when_disabled(self, monkeypatch):
        from src.agents.runner import _run_agent
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_PARALLEL_TOOLS_ENABLED", False)
        mock_model = MagicMock()
        mock_model.max_tokens = 0
        mock_result = MagicMock()
        mock_result.final_output = "done"
        mock_result.usage_details = {}
        captured = {}
        with patch("agent_framework.Agent") as mc:
            ma = MagicMock()
            ma.run = AsyncMock(return_value=mock_result)
            def cap(*a, **kw):
                nonlocal captured
                captured = kw.get("default_options", {})
                return ma
            mc.side_effect = cap
            await _run_agent(model=mock_model, model_client=MagicMock(),
                             system_prompt="test", tools=[MagicMock()],
                             user_message="test", agent_name="test")
        assert "allow_multiple_tool_calls" not in captured

    @pytest.mark.asyncio
    async def test_not_set_without_tools(self, monkeypatch):
        from src.agents.runner import _run_agent
        from src.core import config
        monkeypatch.setattr(config.settings, "AGENT_PARALLEL_TOOLS_ENABLED", True)
        mock_model = MagicMock()
        mock_model.max_tokens = 0
        mock_result = MagicMock()
        mock_result.final_output = "done"
        mock_result.usage_details = {}
        captured = {}
        with patch("agent_framework.Agent") as mc:
            ma = MagicMock()
            ma.run = AsyncMock(return_value=mock_result)
            def cap(*a, **kw):
                nonlocal captured
                captured = kw.get("default_options", {})
                return ma
            mc.side_effect = cap
            await _run_agent(model=mock_model, model_client=MagicMock(),
                             system_prompt="test", tools=[],
                             user_message="test", agent_name="test")
        assert "allow_multiple_tool_calls" not in captured
