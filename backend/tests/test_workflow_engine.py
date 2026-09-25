# =============================================================================
# PH Agent Hub — Workflow Engine Tests
# =============================================================================
# Comprehensive tests for the MAF 1.19.0 workflow engine.
# =============================================================================

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# =============================================================================
# Recording stub agent (module-level)
# =============================================================================


class RecordingAgent:
    """Minimal agent that records every message list it receives.

    * ``self.seen`` accumulates one entry per call to ``run()``.
    * Uses a plain ``def run(...)`` so MAF's ``async for`` loop in
      ``_run_agent_streaming`` doesn't raise ``TypeError``.
    """

    def __init__(self, agent_id: str, response_text: str) -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = None
        self.response_text = response_text
        self.seen: list[list[tuple[str, str]]] = []

    def create_session(self):
        from agent_framework import AgentSession

        return AgentSession()

    def run(self, messages=None, *, stream=False, session=None, **kwargs):
        """Record messages, then return response.

        * Plain ``def`` — MAF does ``async for update in agent.run(..., stream=True)``.
        * When ``stream=False`` returns a coroutine resolving to ``AgentResponse``.
        * When ``stream=True`` returns an async iterator yielding one ``AgentResponseUpdate``.
        """
        from agent_framework import AgentResponse, AgentResponseUpdate, Content, Message

        rec = [(m.role, m.text) for m in (messages or [])]
        self.seen.append(rec)

        if stream:

            async def _gen():
                yield AgentResponseUpdate(
                    contents=[Content(type="text", text=self.response_text)]
                )

            return _gen()
        else:

            async def _coro():
                return AgentResponse(
                    messages=[Message("assistant", [self.response_text])]
                )

            return _coro()


# =============================================================================
# WorkflowDefinition model tests
# =============================================================================


class TestWorkflowDefinition:
    """Tests for the WorkflowDefinition Pydantic model."""

    def test_valid_definition(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="research",
            name="Web Research",
            description="Research a topic",
            steps=[
                {
                    "id": "search",
                    "name": "Search",
                    "type": "inline",
                    "instructions": "Search the web",
                },
                {
                    "id": "summarize",
                    "name": "Summarize",
                    "type": "inline",
                    "instructions": "Summarize results",
                },
            ],
        )
        assert defn.key == "research"
        assert defn.name == "Web Research"
        assert len(defn.steps) == 2

    def test_step_id_uniqueness(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="unique"):
            WorkflowDefinition(
                key="dup",
                name="Dup",
                steps=[
                    {"id": "step1", "name": "S1", "type": "inline", "instructions": "First"},
                    {"id": "step1", "name": "S1", "type": "inline", "instructions": "Second"},
                ],
            )

    def test_invalid_temperature(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="temperature"):
            WorkflowDefinition(
                key="bad_temp",
                name="Bad Temp",
                steps=[
                    {
                        "id": "s1",
                        "name": "S1",
                        "type": "inline",
                        "instructions": "Test",
                        "temperature": 2.1,
                    }
                ],
            )

    def test_valid_on_error(self):
        from src.agents.workflows.definition import WorkflowDefinition

        for on_error in ["stop", "continue"]:
            defn = WorkflowDefinition(
                key="ok",
                name="OK",
                steps=[
                    {
                        "id": "s1",
                        "name": "S1",
                        "type": "inline",
                        "instructions": "Test",
                        "on_error": on_error,
                    },
                ],
            )
            assert defn.steps[0].on_error == on_error

    def test_agent_step_no_agent_ref_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="type 'agent' requires 'agent_ref'"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "agent"},
                ],
            )

    def test_inline_step_no_instructions_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="type 'inline' requires 'instructions'"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline"},
                ],
            )

    def test_inline_step_with_agent_ref_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="'agent_ref' is only valid for type 'agent'"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "agent_ref": "some_agent_key"},
                ],
            )

    def test_agent_step_with_instructions_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="'instructions' is only valid for type 'inline'"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "agent", "agent_ref": "some_agent_key", "instructions": "Test"},
                ],
            )

    def test_model_ref_unknown_role_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="unknown_role"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "model_ref": "@unknown_role"},
                ],
            )

    def test_input_output_of_nope_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="output_of:nope"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "input": "output_of:nope"},
                ],
            )

    def test_input_output_of_own_id_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="may not reference its own output"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "input": "output_of:s1"},
                ],
            )

    def test_input_user_message_valid(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "input": "user_message"},
            ],
        )
        assert defn.steps[0].input == "user_message"

    def test_input_literal_text_valid(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "input": "literal text"},
            ],
        )
        assert defn.steps[0].input == "literal text"

    def test_input_empty_valid(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "input": ""},
            ],
        )
        assert defn.steps[0].input == ""

    def test_context_mode_default(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test"},
            ],
        )
        assert defn.steps[0].context_mode == "last_agent"

    def test_context_mode_custom_rejected(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="context_mode"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "context_mode": "custom"},
                ],
            )

    def test_model_ref_unprefixed_valid(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "model_ref": "gpt-4o"},
            ],
        )
        assert defn.steps[0].model_ref == "gpt-4o"

    def test_model_ref_known_role_valid(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "model_ref": "@reasoning"},
            ],
        )
        assert defn.steps[0].model_ref == "@reasoning"

    def test_tool_refs_defaults_empty(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test"},
            ],
        )
        assert defn.steps[0].tool_refs == []

    def test_tool_refs_role_valid(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "tool_refs": ["@web_search"]},
            ],
        )
        assert defn.steps[0].tool_refs == ["@web_search"]

    def test_tool_refs_concrete_valid(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "tool_refs": ["web_search"]},
            ],
        )
        assert defn.steps[0].tool_refs == ["web_search"]

    def test_tool_refs_unknown_role_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="Unknown tool role"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "tool_refs": ["@nope"]},
                ],
            )

    def test_tool_refs_empty_string_raises(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="non-empty"):
            WorkflowDefinition(
                key="bad",
                name="Bad",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "Test", "tool_refs": [""]},
                ],
            )

    def test_agent_step_with_valid_agent_ref(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "agent", "agent_ref": "some_agent_key"},
            ],
        )
        assert defn.steps[0].type == "agent"
        assert defn.steps[0].agent_ref == "some_agent_key"

    def test_input_output_of_valid(self):
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="ok",
            name="OK",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "First"},
                {"id": "s2", "name": "S2", "type": "inline", "instructions": "Second", "input": "output_of:s1"},
            ],
        )
        assert defn.steps[1].input == "output_of:s1"

    def test_from_workflow_definition_dict(self):
        from src.agents.workflows.engine import load_workflow_definition
        from src.agents.workflows.definition import WorkflowDefinition

        module = MagicMock()
        module.WORKFLOW_DEFINITION = {
            "key": "test",
            "name": "Test",
            "steps": [
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Do it"},
            ],
        }

        defn = load_workflow_definition(module)
        assert isinstance(defn, WorkflowDefinition)
        assert defn.key == "test"

    def test_from_workflow_definition_instance(self):
        from src.agents.workflows.engine import load_workflow_definition
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="test",
            name="Test",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Do it"},
            ],
        )
        module = MagicMock(WORKFLOW_DEFINITION=defn)

        result = load_workflow_definition(module)
        assert result is defn

    def test_from_steps_auto_wrap(self):
        from src.agents.workflows.engine import load_workflow_definition

        module = MagicMock(WORKFLOW_DEFINITION=None)
        module.MAF_KEY = "auto_key"
        module.NAME = "Auto Name"
        module.DESCRIPTION = ""
        module.STEPS = [
            {"id": "s1", "name": "S1", "type": "inline", "instructions": "Do it"},
        ]

        defn = load_workflow_definition(module)
        assert defn.key == "auto_key"
        assert defn.name == "Auto Name"


class TestLoadWorkflowDefinition:
    """Tests for load_workflow_definition()."""

    def test_from_workflow_definition_dict(self):
        from src.agents.workflows.engine import load_workflow_definition
        from src.agents.workflows.definition import WorkflowDefinition

        module = MagicMock()
        module.WORKFLOW_DEFINITION = {
            "key": "test",
            "name": "Test",
            "steps": [
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Do it"},
            ],
        }

        defn = load_workflow_definition(module)
        assert isinstance(defn, WorkflowDefinition)
        assert defn.key == "test"

    def test_from_workflow_definition_instance(self):
        from src.agents.workflows.engine import load_workflow_definition
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="test",
            name="Test",
            steps=[
                {"id": "s1", "name": "S1", "type": "inline", "instructions": "Do it"},
            ],
        )
        module = MagicMock(WORKFLOW_DEFINITION=defn)

        result = load_workflow_definition(module)
        assert result is defn

    def test_from_steps_auto_wrap(self):
        from src.agents.workflows.engine import load_workflow_definition

        module = MagicMock(WORKFLOW_DEFINITION=None)
        module.MAF_KEY = "auto_key"
        module.NAME = "Auto Name"
        module.DESCRIPTION = ""
        module.STEPS = [
            {"id": "s1", "name": "S1", "type": "inline", "instructions": "Do it"},
        ]

        defn = load_workflow_definition(module)
        assert defn.key == "auto_key"
        assert defn.name == "Auto Name"

    def test_none_module(self):
        from src.agents.workflows.engine import load_workflow_definition
        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="None"):
            load_workflow_definition(None)

    def test_no_definition(self):
        from src.agents.workflows.engine import load_workflow_definition
        from src.core.exceptions import ValidationError

        module = MagicMock()
        # No WORKFLOW_DEFINITION, no STEPS
        del module.WORKFLOW_DEFINITION

        with pytest.raises(ValidationError, match="no WORKFLOW_DEFINITION"):
            load_workflow_definition(module)


# =============================================================================
# resolve_model tests
# =============================================================================


class TestResolveModel:
    """Tests for resolve_model()."""

    async def test_resolves_by_id(self):
        from src.agents.workflows.engine import resolve_model

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = MagicMock(
            id="m1", model_id="gpt-4"
        )

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        model = await resolve_model(mock_db, "m1", "tenant-1")
        assert model.id == "m1"

    async def test_raises_not_found(self):
        from src.agents.workflows.engine import resolve_model

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(Exception, match="not found"):
            await resolve_model(mock_db, "nonexistent", "tenant-1")

    async def test_is_tenant_scoped(self, db_session, test_tenant, second_tenant):
        """A model_id shared by two tenants resolves to the caller's tenant."""
        import uuid as _uuid

        from src.agents.workflows.engine import resolve_model
        from src.db.orm.models import Model

        def _row(tenant_id: str) -> Model:
            return Model(
                id=str(_uuid.uuid4()),
                tenant_id=tenant_id,
                name=f"Shared {tenant_id[:8]}",
                model_id="shared-model",
                provider="openai",
                api_key="test-key",
                enabled=True,
                is_public=True,
                max_tokens=4096,
                temperature=0.7,
            )

        mine = _row(test_tenant.id)
        theirs = _row(second_tenant.id)
        db_session.add_all([mine, theirs])
        await db_session.flush()

        resolved = await resolve_model(db_session, "shared-model", test_tenant.id)
        assert resolved.id == mine.id
        assert resolved.tenant_id == test_tenant.id


# =============================================================================
# build_workflow tests
# =============================================================================


class TestBuildWorkflow:
    """Tests for build_workflow()."""

    async def test_builds_workflow(self):
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        # Mock all dependencies
        with patch("src.agents.workflows.engine.resolve_model", new=AsyncMock()) as mock_resolve, \
             patch("src.agents.workflows.engine._build_agent_for_step") as mock_build_agent, \
             patch("src.agents.workflows.engine.WorkflowBuilder") as mock_builder:

            mock_model = MagicMock(model_client="mock_client", max_tokens=4096)
            mock_resolve.return_value = mock_model

            mock_agent = MagicMock()
            mock_executor = MagicMock()
            mock_build_agent.return_value = mock_agent

            defn = WorkflowDefinition(
                key="test",
                name="Test",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "Step 1"},
                    {"id": "s2", "name": "S2", "type": "inline", "instructions": "Step 2"},
                ],
            )
            mock_db = MagicMock()
            mock_builder_instance = MagicMock()
            mock_builder.return_value = mock_builder_instance
            mock_builder_instance.build.return_value = MagicMock()

            workflow = await build_workflow(
                defn=defn, db=mock_db, tenant_id="test-tenant", extra_tools=None
            )

            # Verify builder was called with correct params
            mock_builder.assert_called_once()
            assert mock_builder_instance.add_chain.called

    async def test_single_step_no_chain(self):
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        with patch("src.agents.workflows.engine.resolve_model", new=AsyncMock()) as mock_resolve, \
             patch("src.agents.workflows.engine._build_agent_for_step") as mock_build_agent, \
             patch("src.agents.workflows.engine.WorkflowBuilder") as mock_builder:

            mock_model = MagicMock(model_client="mock_client", max_tokens=4096)
            mock_resolve.return_value = mock_model
            mock_build_agent.return_value = MagicMock()

            mock_builder_instance = MagicMock()
            mock_builder.return_value = mock_builder_instance

            defn = WorkflowDefinition(
                key="single",
                name="Single",
                steps=[
                    {"id": "s1", "name": "S1", "type": "inline", "instructions": "One step"},
                ],
            )
            mock_db = MagicMock()

            await build_workflow(defn=defn, db=mock_db, tenant_id="test-tenant")

            # Should NOT call add_chain for single step
            assert not mock_builder_instance.add_chain.called


# =============================================================================
# Token extraction tests
# =============================================================================


class TestExtractTokenCountsFromWorkflow:
    """Tests for _extract_token_counts_from_workflow()."""

    def test_empty_result(self):
        from src.agents.workflows.engine import _extract_token_counts_from_workflow

        result = MagicMock()
        result.__iter__ = lambda self: iter([])

        tokens_in, tokens_out, cache_hit = _extract_token_counts_from_workflow(result)
        assert tokens_in == 0
        assert tokens_out == 0
        assert cache_hit == 0

    def test_with_token_counts(self):
        from src.agents.workflows.engine import _extract_token_counts_from_workflow

        # Create mock events with usage_details in data
        event1 = MagicMock()
        event1.type = "output"
        event1.data = {
            "usage_details": {
                "input_token_count": 100,
                "output_token_count": 50,
                "cache_read_input_token_count": 30,
            }
        }

        event2 = MagicMock()
        event2.type = "intermediate"
        event2.data = {
            "usage_details": {
                "input_token_count": 200,
                "output_token_count": 100,
                "cache_read_input_token_count": 50,
            }
        }

        event3 = MagicMock()
        event3.type = "executor_invoked"  # Not output/intermediate
        event3.data = None

        result = [event1, event2, event3]

        tokens_in, tokens_out, cache_hit = _extract_token_counts_from_workflow(result)
        assert tokens_in == 300  # 100 + 200
        assert tokens_out == 150  # 50 + 100
        assert cache_hit == 80  # 30 + 50


# =============================================================================
# run_workflow tests
# =============================================================================


class TestRunWorkflow:
    """Tests for run_workflow()."""

    async def test_runs_and_returns_output(self):
        from src.agents.workflows.engine import run_workflow

        mock_workflow = MagicMock()
        mock_result = MagicMock()
        mock_result.get_outputs.return_value = ["Output from step 1"]

        mock_workflow.run = AsyncMock(return_value=mock_result)

        output, result = await run_workflow(
            workflow=mock_workflow, message="Test message"
        )
        assert output == "Output from step 1"

    async def test_no_outputs(self):
        from src.agents.workflows.engine import run_workflow

        mock_workflow = MagicMock()
        mock_result = MagicMock()
        mock_result.get_outputs.return_value = []

        mock_workflow.run = AsyncMock(return_value=mock_result)

        output, result = await run_workflow(
            workflow=mock_workflow, message="Test message"
        )
        assert output is not None  # str(result) fallback


# =============================================================================
# iter_workflow_sse tests
# =============================================================================


class TestIterWorkflowSSE:
    """Tests for iter_workflow_sse()."""

    async def test_yields_workflow_step_events(self):
        from src.agents.workflows.engine import iter_workflow_sse

        # Create mock workflow
        mock_workflow = MagicMock()
        mock_workflow.get_executors_list.return_value = [MagicMock(), MagicMock()]
        mock_workflow.run.return_value = self._make_mock_stream()

        events = []

        async def _collect():
            async for event in iter_workflow_sse(
                workflow=mock_workflow,
                message="Test",
                session_id="sess1",
                message_id="msg1",
            ):
                events.append(event)
            return events

        events = await _collect()

        # Should have workflow_step started/completed events for each step
        workflow_events = [e for e in events if e.get("event") == "workflow_step"]
        assert len(workflow_events) >= 2  # started + completed for at least 1 step

    async def test_yields_token_events_from_output(self):
        from src.agents.workflows.engine import iter_workflow_sse

        mock_workflow = MagicMock()
        mock_workflow.get_executors_list.return_value = [MagicMock()]
        mock_workflow.run.return_value = self._make_stream_with_output(text="Hello world")

        events = []

        async def _collect():
            async for event in iter_workflow_sse(
                workflow=mock_workflow,
                message="Test",
                session_id="sess1",
                message_id="msg1",
            ):
                events.append(event)
            return events

        events = await _collect()

        token_events = [e for e in events if e.get("event") == "token"]
        assert len(token_events) >= 1

    async def test_yields_message_complete(self):
        from src.agents.workflows.engine import iter_workflow_sse

        mock_workflow = MagicMock()
        mock_workflow.get_executors_list.return_value = [MagicMock()]
        mock_workflow.run.return_value = self._make_mock_stream()

        events = []
        token_counts = {}

        async def _collect():
            async for event in iter_workflow_sse(
                workflow=mock_workflow,
                message="Test",
                session_id="sess1",
                message_id="msg1",
                token_counts=token_counts,
            ):
                events.append(event)
            return events, token_counts

        (events, tc) = await _collect()

        complete_events = [e for e in events if e.get("event") == "message_complete"]
        assert len(complete_events) >= 1

    def _make_mock_stream(self):
        """Create a mock ResponseStream with executor lifecycle events."""
        mock_stream = MagicMock()

        def make_event(event_type, executor_id=None, data=None):
            event = MagicMock()
            event.type = event_type
            event.data = data
            event.source_executor_id = executor_id
            return event

        async def async_gen():
            yield make_event("executor_invoked", "step1")
            yield make_event("output", "step1", "Step 1 output")
            yield make_event("executor_completed", "step1")
            yield make_event("executor_invoked", "step2")
            yield make_event("output", "step2", "Step 2 output")
            yield make_event("executor_completed", "step2")

        mock_stream.__aiter__ = lambda self: async_gen()
        mock_stream.get_final_response = AsyncMock(return_value=MagicMock())
        return mock_stream

    def _make_stream_with_output(self, text=""):
        """Create a mock stream with output data containing text."""
        mock_stream = MagicMock()

        def make_event(event_type, executor_id=None, data=None):
            event = MagicMock()
            event.type = event_type
            event.data = data
            event.source_executor_id = executor_id
            return event

        async def async_gen():
            yield make_event("executor_invoked", "step1")
            yield make_event("output", "step1", MagicMock(text=text))
            yield make_event("executor_completed", "step1")

        mock_stream.__aiter__ = lambda self: async_gen()
        mock_stream.get_final_response = AsyncMock(return_value=MagicMock())
        return mock_stream


# =============================================================================
# Agent-step resolution tests
# =============================================================================


class TestAgentStepResolution:
    """Tests for agent_ref validation and agent-step build path."""

    async def test_agent_step_inherits_inSTRUCTIONS_and_MODEL_ROLE(self):
        """An agent step should inherit instructions from the registered
        agent module and resolve the agent's MODEL_ROLE."""
        import types

        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        agent_mod = types.SimpleNamespace(
            INSTRUCTIONS="AGENT INSTRUCTIONS",
            MODEL_ROLE="@reasoning",
        )

        with patch(
            "src.agents.registry.get_registered_agent", return_value=agent_mod
        ), patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step", new=MagicMock()
        ) as mock_build:

            mock_model = MagicMock()
            mock_resolve.return_value = mock_model

            defn = WorkflowDefinition(
                key="test",
                name="Test",
                steps=[
                    {"id": "a", "name": "A", "type": "agent", "agent_ref": "web_researcher"},
                    {"id": "b", "name": "B", "type": "inline", "instructions": "BI", "model_ref": "@reasoning"},
                ],
            )
            mock_db = MagicMock()

            await build_workflow(defn=defn, db=mock_db, tenant_id="test-tenant")

            # resolve_model should be called with "@reasoning" for agent step (from MODEL_ROLE)
            calls = mock_resolve.call_args_list
            # First call is for agent step (step a), model_ref="@reasoning" from agent module
            assert calls[0][0][1] == "@reasoning"

            # _build_agent_for_step should receive instructions="AGENT INSTRUCTIONS"
            agent_build_calls = mock_build.call_args_list
            assert agent_build_calls[0][1]["instructions"] == "AGENT INSTRUCTIONS"

    async def test_step_level_model_ref_overrides_agent_MODEL_ROLE(self):
        """A step-level model_ref should override the agent module's MODEL_ROLE."""
        import types

        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        agent_mod = types.SimpleNamespace(
            INSTRUCTIONS="AGENT INSTRUCTIONS",
            MODEL_ROLE="@reasoning",
        )

        with patch(
            "src.agents.registry.get_registered_agent", return_value=agent_mod
        ), patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step", new=MagicMock()
        ):

            mock_model = MagicMock()
            mock_resolve.return_value = mock_model

            defn = WorkflowDefinition(
                key="test",
                name="Test",
                steps=[
                    {
                        "id": "a",
                        "name": "A",
                        "type": "agent",
                        "agent_ref": "web_researcher",
                        "model_ref": "@fast",
                    },
                ],
            )
            mock_db = MagicMock()

            await build_workflow(defn=defn, db=mock_db, tenant_id="test-tenant")

            # resolve_model should be called with "@fast" (step-level override)
            calls = mock_resolve.call_args_list
            assert calls[0][0][1] == "@fast"

    async def test_unbound_role_raises(self):
        """An @-prefixed model_ref with no binding raises ValidationError
        naming the role — it must never fall back to the skill default."""
        import types

        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition
        from src.core.exceptions import ValidationError

        agent_mod = types.SimpleNamespace(
            INSTRUCTIONS="AGENT INSTRUCTIONS",
            MODEL_ROLE="@reasoning",
        )

        with patch(
            "src.agents.registry.get_registered_agent", return_value=agent_mod
        ), patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step", new=MagicMock()
        ) as mock_build:

            async def side_effect_fn(db, ref, tenant_id):
                raise ValidationError(
                    f"No model bound to role '{ref}' for tenant '{tenant_id}'"
                )

            mock_resolve.side_effect = side_effect_fn

            defn = WorkflowDefinition(
                key="test",
                name="Test",
                steps=[
                    {"id": "a", "name": "A", "type": "agent", "agent_ref": "web_researcher"},
                ],
            )

            with pytest.raises(ValidationError, match=r"@reasoning"):
                await build_workflow(
                    defn=defn,
                    db=MagicMock(),
                    tenant_id="test-tenant",
                    default_model_id="fallback-id",
                )

            # The unbound role must not be rescued by the fallback model.
            assert not mock_build.called

    async def test_concrete_model_ref_not_found_still_falls_back(self):
        """A concrete reference that does not resolve still falls back to
        default_model_id (existing behaviour preserved)."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition
        from src.core.exceptions import NotFoundError

        fallback_model = MagicMock()

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step", new=MagicMock()
        ) as mock_build:
            calls = 0

            async def side_effect_fn(db, ref, tenant_id):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise NotFoundError("not found")
                return fallback_model

            mock_resolve.side_effect = side_effect_fn

            defn = WorkflowDefinition(
                key="test",
                name="Test",
                steps=[
                    {"id": "a", "name": "A", "type": "inline", "instructions": "I", "model_ref": "ghost-model"},
                ],
            )

            await build_workflow(
                defn=defn,
                db=MagicMock(),
                tenant_id="test-tenant",
                default_model_id="fallback-id",
            )

            assert mock_resolve.call_args_list[1][0][1] == "fallback-id"
            assert mock_build.call_args[1]["model"] is fallback_model

    async def test_concrete_model_ref_in_another_tenant_raises(self):
        """A concrete model_ref owned by another tenant is rejected loudly
        rather than resolving or falling back."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition
        from src.core.exceptions import ValidationError

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step", new=MagicMock()
        ):

            async def side_effect_fn(db, ref, tenant_id):
                raise ValidationError(
                    f"Model '{ref}' belongs to a different tenant"
                )

            mock_resolve.side_effect = side_effect_fn

            defn = WorkflowDefinition(
                key="test",
                name="Test",
                steps=[
                    {"id": "a", "name": "A", "type": "inline", "instructions": "I", "model_ref": "foreign-model"},
                ],
            )

            with pytest.raises(ValidationError, match="different tenant"):
                await build_workflow(
                    defn=defn,
                    db=MagicMock(),
                    tenant_id="test-tenant",
                    default_model_id="fallback-id",
                )

    async def test_resolve_model_role_branch_uses_tenant_bindings(self):
        """resolve_model routes an @-prefixed reference through the tenant's
        role bindings and names the role when unbound."""
        from src.agents.workflows.engine import resolve_model
        from src.core.exceptions import ValidationError

        bound = MagicMock(id="bound-model")

        with patch(
            "src.services.model_role_service.resolve_role_model",
            new=AsyncMock(return_value=bound),
        ) as mock_role:
            resolved = await resolve_model(MagicMock(), "@reasoning", "tenant-1")

        assert resolved is bound
        assert mock_role.call_args[0][1] == "tenant-1"
        assert mock_role.call_args[0][2] == "@reasoning"

        with patch(
            "src.services.model_role_service.resolve_role_model",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(ValidationError, match=r"@reasoning"):
                await resolve_model(MagicMock(), "@reasoning", "tenant-1")

    def test_load_time_validation_raises_for_unknown_agent(self):
        """load_workflow_definition should raise ValidationError when the
        registry returns None for an agent_ref."""
        from src.agents.workflows.engine import load_workflow_definition
        from src.core.exceptions import ValidationError

        module = MagicMock(WORKFLOW_DEFINITION=None)
        module.MAF_KEY = "bad_workflow"
        module.NAME = "Bad"
        module.DESCRIPTION = ""
        module.STEPS = [
            {"id": "a", "name": "A", "type": "agent", "agent_ref": "nonexistent_agent"},
        ]

        with patch("src.agents.registry.get_registered_agent", return_value=None):
            with pytest.raises(ValidationError, match="nonexistent_agent"):
                load_workflow_definition(module)

    async def test_build_time_validation_raises_for_unknown_agent(self):
        """build_workflow should raise ValidationError naming the key when the
        load-time check is bypassed (construct WorkflowDefinition directly)."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition
        from src.core.exceptions import ValidationError

        defn = WorkflowDefinition(
            key="bypass",
            name="Bypass",
            steps=[
                {"id": "a", "name": "A", "type": "agent", "agent_ref": "phantom"},
            ],
        )

        with patch("src.agents.workflows.engine._registered_agent_module", return_value=None):
            with pytest.raises(ValidationError, match="phantom"):
                await build_workflow(defn=defn, db=MagicMock(), tenant_id="test-tenant")

    async def test_end_to_end_real_registered_agent(self):
        """A definition referencing the real 'web_researcher' agent builds
        successfully after calling scan_agent_defs()."""
        import src.agents.registry as reg
        import src.agents.workflows.engine as eng
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.engine import build_workflow

        # Ensure the agent is registered
        reg.scan_agent_defs()

        defn = WorkflowDefinition(
            key="e2e_test",
            name="E2E Test",
            steps=[
                {"id": "a", "name": "A", "type": "agent", "agent_ref": "web_researcher"},
            ],
        )

        with patch.object(eng, "resolve_model", new=AsyncMock()) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step", new=MagicMock()
        ):
            mock_model = MagicMock()
            mock_resolve.return_value = mock_model

            # load_workflow_definition should pass (load-time validation)
            mock_mod = MagicMock()
            mock_mod.WORKFLOW_DEFINITION = defn
            loaded = eng.load_workflow_definition(mock_mod)
            assert loaded.key == "e2e_test"

            # build_workflow should also pass
            await build_workflow(defn=loaded, db=MagicMock(), tenant_id="test-tenant")


class TestRunWorkflowIntegration:
    """Integration tests for _run_workflow and _run_workflow_stream."""

    async def _make_mock_model(self):
        model = MagicMock()
        model.provider = "openai"
        model.model_id = "gpt-4"
        model.max_tokens = 4096
        return model

    def _make_mock_skill(self, maf_target_key=None):
        skill = MagicMock()
        skill.maf_target_key = maf_target_key
        return skill

    async def test_run_workflow_raises_without_maf_key(self):
        from src.agents.runner import _run_workflow

        model = await self._make_mock_model()
        skill = self._make_mock_skill(maf_target_key=None)

        with pytest.raises(Exception, match="maf_target_key"):
            await _run_workflow(
                model=model,
                skill=skill,
                model_client="client",
                system_prompt="prompt",
                tools=[],
                user_message="hi",
                agent_name="assistant",
            )

    async def test_run_workflow_raises_when_not_registered(self):
        from src.agents.runner import _run_workflow

        model = await self._make_mock_model()
        skill = self._make_mock_skill(maf_target_key="missing_key")

        with patch("src.agents.registry.get_registered", return_value=None):
            with pytest.raises(Exception, match="No registered workflow"):
                await _run_workflow(
                    model=model,
                    skill=skill,
                    model_client="client",
                    system_prompt="prompt",
                    tools=[],
                    user_message="hi",
                    agent_name="assistant",
                )

    async def test_run_workflow_stream_raises_without_maf_key(self):
        from src.agents.runner import _run_workflow_stream

        model = await self._make_mock_model()
        skill = self._make_mock_skill(maf_target_key=None)

        with pytest.raises(Exception, match="maf_target_key"):
            async for _ in _run_workflow_stream(
                model=model,
                skill=skill,
                model_client="client",
                system_prompt="prompt",
                tools=[],
                user_message="hi",
                agent_name="assistant",
                session_id="sess1",
                message_id="msg1",
            ):
                pass


# =============================================================================
# context_mode wiring tests (Session 6)
# =============================================================================


class TestContextMode:
    """Tests that ``context_mode`` is passed to every ``AgentExecutor``.

    We use a *real* MAF Workflow (via WorkflowBuilder) so that the
    context_mode wiring actually runs at execution time.  The only mocks
    are model resolution and agent construction.
    """

    @staticmethod
    def _make_steps(context_mode_1="last_agent", context_mode_2="last_agent"):
        step1 = {
            "id": "step1",
            "name": "Step1",
            "type": "inline",
            "instructions": "I1",
            "model_ref": "@reasoning",
        }
        step2 = {
            "id": "step2",
            "name": "Step2",
            "type": "inline",
            "instructions": "I2",
            "model_ref": "@reasoning",
        }
        if context_mode_1 is not None:
            step1["context_mode"] = context_mode_1
        if context_mode_2 is not None:
            step2["context_mode"] = context_mode_2
        return [step1, step2]

    async def _build_and_run(self, context_mode_1="last_agent", context_mode_2="last_agent"):
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        step1 = RecordingAgent("step1", "OUT_1")
        step2 = RecordingAgent("step2", "OUT_2")

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_resolve.return_value = MagicMock(max_tokens=4096)
            mock_build.side_effect = [step1, step2]

            defn = WorkflowDefinition(
                key="ctx_test",
                name="Context Mode Test",
                steps=self._make_steps(context_mode_1, context_mode_2),
            )
            mock_db = MagicMock()

            workflow = await build_workflow(defn=defn, db=mock_db, tenant_id="test-tenant")
            await workflow.run("USER_ORIGINAL")
            return step1, step2

    async def test_last_agent_receives_only_previous_response(self):
        step1, step2 = await self._build_and_run("last_agent", "last_agent")
        assert step2.seen == [[("assistant", "OUT_1")]]

    async def test_full_receives_original_input_too(self):
        step1, step2 = await self._build_and_run("full", "full")
        assert step2.seen == [[("user", "USER_ORIGINAL"), ("assistant", "OUT_1")]]

    async def test_default_context_mode_is_last_agent(self):
        step1, step2 = await self._build_and_run(None, None)  # omit context_mode → default "last_agent"
        assert step2.seen == [[("assistant", "OUT_1")]]


# =============================================================================
# Step input execution tests (Session 7)
# =============================================================================


class TestStepInput:
    """Tests that a step's ``input`` field is honoured at execution time.

    We reuse the same patching approach as ``TestContextMode``: patch
    ``resolve_model`` with an ``AsyncMock`` and ``_build_agent_for_step``
    with a ``side_effect`` list of ``RecordingAgent``s, then exercise the
    real MAF ``Workflow`` via ``WorkflowBuilder``.
    """

    async def _build_and_run_2step(
        self,
        step1_input: str,
        step2_input: str,
        context_mode_1: str = "last_agent",
        context_mode_2: str = "last_agent",
    ):
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        step1 = RecordingAgent("step1", "OUT_1")
        step2 = RecordingAgent("step2", "OUT_2")

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_resolve.return_value = MagicMock(max_tokens=4096)
            mock_build.side_effect = [step1, step2]

            defn = WorkflowDefinition(
                key="input_test",
                name="Input Test",
                steps=[
                    {
                        "id": "step1",
                        "name": "Step1",
                        "type": "inline",
                        "instructions": "I1",
                        "input": step1_input,
                        "context_mode": context_mode_1,
                    },
                    {
                        "id": "step2",
                        "name": "Step2",
                        "type": "inline",
                        "instructions": "I2",
                        "input": step2_input,
                        "context_mode": context_mode_2,
                    },
                ],
            )
            mock_db = MagicMock()
            workflow = await build_workflow(defn=defn, db=mock_db, tenant_id="test-tenant")
            await workflow.run("USER_ORIGINAL")
            return step1, step2

    async def test_empty_inherit(self):
        """Step 2 with ``input: ""`` inherits upstream — no extra message."""
        step1, step2 = await self._build_and_run_2step("", "")
        assert step2.seen == [[("assistant", "OUT_1")]]

    async def test_user_message(self):
        """Step 2 with ``input: "user_message"`` appends the original input."""
        step1, step2 = await self._build_and_run_2step("", "user_message")
        assert step2.seen == [[("assistant", "OUT_1"), ("user", "USER_ORIGINAL")]]

    async def test_literal(self):
        """Step 2 with ``input: "LITERAL_ASK"`` appends the literal text."""
        step1, step2 = await self._build_and_run_2step("", "LITERAL_ASK")
        assert step2.seen == [[("assistant", "OUT_1"), ("user", "LITERAL_ASK")]]

    async def test_output_of_adjacent(self):
        """Step 2 with ``input: "output_of:step1"`` appends step1's output."""
        step1, step2 = await self._build_and_run_2step("", "output_of:step1")
        assert step2.seen == [[("assistant", "OUT_1"), ("user", "OUT_1")]]

    async def test_output_of_non_adjacent(self):
        """A three-step workflow where step 3 references step 1's output."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        step1 = RecordingAgent("step1", "OUT_1")
        step2 = RecordingAgent("step2", "OUT_2")
        step3 = RecordingAgent("step3", "OUT_3")

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_resolve.return_value = MagicMock(max_tokens=4096)
            mock_build.side_effect = [step1, step2, step3]

            defn = WorkflowDefinition(
                key="input_test_3",
                name="Input Test 3 Step",
                steps=[
                    {
                        "id": "step1",
                        "name": "Step1",
                        "type": "inline",
                        "instructions": "I1",
                    },
                    {
                        "id": "step2",
                        "name": "Step2",
                        "type": "inline",
                        "instructions": "I2",
                    },
                    {
                        "id": "step3",
                        "name": "Step3",
                        "type": "inline",
                        "instructions": "I3",
                        "input": "output_of:step1",
                    },
                ],
            )
            mock_db = MagicMock()
            workflow = await build_workflow(defn=defn, db=mock_db, tenant_id="test-tenant")
            await workflow.run("USER_ORIGINAL")

        assert step3.seen == [[("assistant", "OUT_2"), ("user", "OUT_1")]]

    async def test_topology_guard(self):
        """No extra graph nodes are inserted by the input wrapper."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        step1 = RecordingAgent("step1", "OUT_1")
        step2 = RecordingAgent("step2", "OUT_2")
        step3 = RecordingAgent("step3", "OUT_3")

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_resolve.return_value = MagicMock(max_tokens=4096)
            mock_build.side_effect = [step1, step2, step3]

            defn = WorkflowDefinition(
                key="topology_guard",
                name="Topology Guard",
                steps=[
                    {"id": "step1", "name": "S1", "type": "inline", "instructions": "I1"},
                    {"id": "step2", "name": "S2", "type": "inline", "instructions": "I2"},
                    {"id": "step3", "name": "S3", "type": "inline", "instructions": "I3", "input": "output_of:step1"},
                ],
            )
            mock_db = MagicMock()
            workflow = await build_workflow(defn=defn, db=mock_db, tenant_id="test-tenant")
            await workflow.run("USER_ORIGINAL")

        assert [e.id for e in workflow.get_executors_list()] == [
            "step1",
            "step2",
            "step3",
        ]

    async def test_streaming_smoke(self):
        """StepAgent yields each update on the production streaming path via Workflow.run."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        step1 = RecordingAgent("step1", "OUT_1")
        step2 = RecordingAgent("step2", "OUT_2")

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_resolve.return_value = MagicMock(max_tokens=4096)
            mock_build.side_effect = [step1, step2]

            defn = WorkflowDefinition(
                key="streaming_smoke",
                name="Streaming Smoke",
                steps=[
                    {
                        "id": "step1",
                        "name": "Step1",
                        "type": "inline",
                        "instructions": "I1",
                        "input": "",
                        "context_mode": "last_agent",
                    },
                    {
                        "id": "step2",
                        "name": "Step2",
                        "type": "inline",
                        "instructions": "I2",
                        "input": "user_message",
                        "context_mode": "last_agent",
                    },
                ],
            )
            mock_db = MagicMock()
            workflow = await build_workflow(defn=defn, db=mock_db, tenant_id="test-tenant")

            # Drive the PRODUCTION streaming path. Workflow.run(message, stream=True)
            # returns an async-iterable ResponseStream; it is NOT a coroutine.
            stream = workflow.run("USER_ORIGINAL", stream=True)
            async for _event in stream:
                pass

        # The wrapped step 2 should have received: upstream + user_message
        assert step2.seen == [[("assistant", "OUT_1"), ("user", "USER_ORIGINAL")]]


# =============================================================================
# Per-step tool resolution tests
# =============================================================================


class TestResolveStepTools:
    """Tests for ``_resolve_step_tools`` — restricting the run's tool pool."""

    @staticmethod
    def _step(tool_refs):
        import types

        return types.SimpleNamespace(id="s1", tool_refs=tool_refs)

    @staticmethod
    def _tools(*names):
        import types

        return [types.SimpleNamespace(name=n) for n in names]

    def test_empty_refs_inherit_whole_pool(self):
        from src.agents.workflows.engine import _resolve_step_tools

        pool = self._tools("web_search", "calculator")
        result = _resolve_step_tools(self._step([]), pool, "wf")
        assert result == pool
        assert result[0] is pool[0]

    def test_none_pool_behaves_as_empty(self):
        from src.agents.workflows.engine import _resolve_step_tools
        from src.core.exceptions import ValidationError

        # Empty refs inherit an empty pool.
        assert _resolve_step_tools(self._step([]), None, "wf") == []
        # A ref against an empty pool cannot resolve and must fail loudly.
        with pytest.raises(ValidationError, match="web_search"):
            _resolve_step_tools(self._step(["web_search"]), None, "wf")

    def test_role_ref_restricts_to_target_callable(self):
        from src.agents.workflows.engine import _resolve_step_tools

        pool = self._tools("web_search", "calculator")
        result = _resolve_step_tools(self._step(["@web_search"]), pool, "wf")
        assert result == [pool[0]]

    def test_concrete_ref_restricts_to_named_callable(self):
        from src.agents.workflows.engine import _resolve_step_tools

        pool = self._tools("web_search", "calculator")
        result = _resolve_step_tools(self._step(["web_search"]), pool, "wf")
        assert result == [pool[0]]

    def test_multiple_refs_follow_pool_order(self):
        from src.agents.workflows.engine import _resolve_step_tools

        pool = self._tools("web_search", "calculator")
        result = _resolve_step_tools(
            self._step(["calculator", "@web_search"]), pool, "wf"
        )
        assert result == pool

    def test_unresolved_ref_raises_naming_step_and_ref(self):
        from src.agents.workflows.engine import _resolve_step_tools
        from src.core.exceptions import ValidationError

        pool = self._tools("calculator")
        with pytest.raises(ValidationError) as exc:
            _resolve_step_tools(self._step(["@web_search"]), pool, "wf")
        message = str(exc.value)
        assert "s1" in message
        assert "@web_search" in message
        assert "calculator" in message  # available tools are listed

    def test_unresolved_concrete_ref_raises(self):
        from src.agents.workflows.engine import _resolve_step_tools
        from src.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="ghost_tool"):
            _resolve_step_tools(
                self._step(["ghost_tool"]), self._tools("calculator"), "wf"
            )

    def test_duplicate_refs_do_not_duplicate_tools(self):
        from src.agents.workflows.engine import _resolve_step_tools

        pool = self._tools("web_search")
        result = _resolve_step_tools(
            self._step(["@web_search", "web_search"]), pool, "wf"
        )
        assert result == [pool[0]]

    async def test_build_workflow_applies_per_step_tools(self):
        """A restricted step gets only its tools; an unrestricted step inherits."""
        import types

        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        web = types.SimpleNamespace(name="web_search")
        calc = types.SimpleNamespace(name="calculator")

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_resolve.return_value = MagicMock(max_tokens=4096)
            mock_build.return_value = MagicMock()

            defn = WorkflowDefinition(
                key="tool_refs_test",
                name="Tool Refs Test",
                steps=[
                    {
                        "id": "research",
                        "name": "Research",
                        "type": "inline",
                        "instructions": "I1",
                        "tool_refs": ["@web_search"],
                    },
                    {
                        "id": "report",
                        "name": "Report",
                        "type": "inline",
                        "instructions": "I2",
                    },
                ],
            )

            await build_workflow(
                defn=defn,
                db=MagicMock(),
                tenant_id="test-tenant",
                extra_tools=[web, calc],
            )

            assert mock_build.call_args_list[0][1]["tools"] == [web]
            assert mock_build.call_args_list[1][1]["tools"] == [web, calc]
