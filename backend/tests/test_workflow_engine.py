# =============================================================================
# PH Agent Hub — Workflow Engine Tests
# =============================================================================
# Comprehensive tests for the MAF 1.19.0 workflow engine.
# =============================================================================

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


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
                    "instructions": "Search the web",
                    "model_ref": "gpt-4",
                },
                {
                    "id": "summarize",
                    "instructions": "Summarize results",
                    "model_ref": "claude",
                },
            ],
        )
        assert defn.key == "research"
        assert defn.name == "Web Research"
        assert len(defn.steps) == 2

    def test_step_id_uniqueness(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="duplicate"):
            WorkflowDefinition(
                key="dup",
                steps=[
                    {"id": "step1", "instructions": "First", "model_ref": "gpt-4"},
                    {"id": "step1", "instructions": "Second", "model_ref": "gpt-4"},
                ],
            )

    def test_invalid_temperature(self):
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError, match="temperature"):
            WorkflowDefinition(
                key="bad_temp",
                steps=[
                    {
                        "id": "s1",
                        "instructions": "Test",
                        "model_ref": "gpt-4",
                        "temperature": 2.0,
                    }
                ],
            )

    def test_valid_on_error(self):
        from src.agents.workflows.definition import WorkflowDefinition

        for on_error in ["stop", "continue"]:
            defn = WorkflowDefinition(
                key="ok",
                steps=[
                    {"id": "s1", "instructions": "Test", "model_ref": "gpt-4"},
                ],
                on_error=on_error,
            )
            assert defn.on_error == on_error


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
                {"id": "s1", "instructions": "Do it", "model_ref": "gpt-4"},
            ],
        }

        defn = load_workflow_definition(module)
        assert isinstance(defn, WorkflowDefinition)
        assert defn.key == "test"

    def test_from_workflow_definition_instance(self):
        from src.agents.workflows.engine import load_workflow_definition
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="test", name="Test", steps=[]
        )
        module = MagicMock(WORKFLOW_DEFINITION=defn)

        result = load_workflow_definition(module)
        assert result is defn

    def test_from_steps_auto_wrap(self):
        from src.agents.workflows.engine import load_workflow_definition

        module = MagicMock()
        module.MAF_KEY = "auto_key"
        module.NAME = "Auto Name"
        module.STEPS = [
            {"id": "s1", "instructions": "Do it", "model_ref": "gpt-4"},
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

    @patch("sqlalchemy.select")
    async def test_resolves_by_id(self, mock_select):
        from src.agents.workflows.engine import resolve_model

        mock_query = MagicMock()
        mock_query.scalar_one_or_none.return_value = MagicMock(id="m1", model_id="gpt-4")
        mock_select.return_value.where.return_value = mock_query

        # Need AsyncSession mock
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_query)

        model = await resolve_model(mock_db, "m1")
        assert model.id == "m1"

    @patch("sqlalchemy.select")
    async def test_raises_not_found(self, mock_select):
        from src.agents.workflows.engine import resolve_model

        mock_query = MagicMock()
        mock_query.scalar_one_or_none.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_query)

        with pytest.raises(Exception, match="not found"):
            await resolve_model(mock_db, "nonexistent")


# =============================================================================
# build_workflow tests
# =============================================================================


class TestBuildWorkflow:
    """Tests for build_workflow()."""

    def test_builds_workflow(self):
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        # Mock all dependencies
        with patch("src.agents.workflows.engine.resolve_model") as mock_resolve, \
             patch("src.agents.workflows.engine.WorkflowBuilder") as mock_builder:

            mock_model = MagicMock(model_client="mock_client", max_tokens=4096)
            mock_resolve.return_value = mock_model

            mock_agent = MagicMock()
            mock_executor = MagicMock()

            defn = WorkflowDefinition(
                key="test",
                steps=[
                    {"id": "s1", "instructions": "Step 1", "model_ref": "m1"},
                    {"id": "s2", "instructions": "Step 2", "model_ref": "m2"},
                ],
            )
            mock_db = MagicMock()
            mock_builder_instance = MagicMock()
            mock_builder.return_value = mock_builder_instance
            mock_builder_instance.build.return_value = MagicMock()

            import asyncio

            async def _run():
                workflow = await build_workflow(
                    defn=defn, db=mock_db, extra_tools=None
                )
                return workflow

            workflow = asyncio.get_event_loop().run_until_complete(_run())

            # Verify builder was called with correct params
            mock_builder.assert_called_once()
            assert mock_builder_instance.add_chain.called

    def test_single_step_no_chain(self):
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        with patch("src.agents.workflows.engine.resolve_model") as mock_resolve, \
             patch("src.agents.workflows.engine.WorkflowBuilder") as mock_builder:

            mock_model = MagicMock(model_client="mock_client", max_tokens=4096)
            mock_resolve.return_value = mock_model

            mock_builder_instance = MagicMock()
            mock_builder.return_value = mock_builder_instance

            defn = WorkflowDefinition(
                key="single",
                steps=[
                    {"id": "s1", "instructions": "One step", "model_ref": "m1"},
                ],
            )
            mock_db = MagicMock()

            import asyncio

            async def _run():
                return await build_workflow(defn=defn, db=mock_db)

            asyncio.get_event_loop().run_until_complete(_run())

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

    def test_runs_and_returns_output(self):
        from src.agents.workflows.engine import run_workflow

        mock_workflow = MagicMock()
        mock_result = MagicMock()
        mock_result.get_outputs.return_value = ["Output from step 1"]

        mock_workflow.run = AsyncMock(return_value=mock_result)

        import asyncio

        async def _run():
            output, result = await run_workflow(
                workflow=mock_workflow, message="Test message"
            )
            return output, result

        output, result = asyncio.get_event_loop().run_until_complete(_run())
        assert output == "Output from step 1"

    def test_no_outputs(self):
        from src.agents.workflows.engine import run_workflow

        mock_workflow = MagicMock()
        mock_result = MagicMock()
        mock_result.get_outputs.return_value = []

        mock_workflow.run = AsyncMock(return_value=mock_result)

        import asyncio

        async def _run():
            output, result = await run_workflow(
                workflow=mock_workflow, message="Test message"
            )
            return output

        output = asyncio.get_event_loop().run_until_complete(_run())
        assert output is not None  # str(result) fallback


# =============================================================================
# iter_workflow_sse tests
# =============================================================================


class TestIterWorkflowSSE:
    """Tests for iter_workflow_sse()."""

    def test_yields_workflow_step_events(self):
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

        events = asyncio.get_event_loop().run_until_complete(_collect())

        # Should have workflow_step started/completed events for each step
        workflow_events = [e for e in events if e.get("event") == "workflow_step"]
        assert len(workflow_events) >= 2  # started + completed for at least 1 step

    def test_yields_token_events_from_output(self):
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

        events = asyncio.get_event_loop().run_until_complete(_collect())

        token_events = [e for e in events if e.get("event") == "token"]
        assert len(token_events) >= 1

    def test_yields_message_complete(self):
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

        (events, tc) = asyncio.get_event_loop().run_until_complete(_collect())

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
# Integration: _run_workflow and _run_workflow_stream
# =============================================================================


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
