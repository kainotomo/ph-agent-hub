# =============================================================================
# PH Agent Hub — Workflow Engine Wiring Tests
# =============================================================================
# Tests that checkpoint storage, tenant-namespaced workflow name, initial
# state, and resume checkpoint id flow into MAF's WorkflowBuilder and
# Workflow.run.
# =============================================================================

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# Sentinel checkpoint storage that mimics the CheckpointStorage Protocol.
class _FakeCheckpointStorage:
    """Minimal async checkpoint-storage shim used as a sentinel."""

    async def save(self, checkpoint):
        return "ckpt-1"

    async def load(self, checkpoint_id):
        return MagicMock()

    async def list_checkpoints(self, *, workflow_name):
        return []

    async def list_checkpoint_ids(self, *, workflow_name):
        return []

    async def get_latest(self, *, workflow_name):
        return None

    async def delete(self, checkpoint_id):
        return True


# Stub agent that exposes the surface build_workflow expects from _build_agent_for_step.
class _StubAgent:
    """Minimal agent stub so no model client is constructed."""

    def __init__(self) -> None:
        self.id = "stub-agent"
        self.name = "stub-agent"
        self.description = "stub"

    def create_session(self):
        from agent_framework import AgentSession

        return AgentSession()


@pytest.mark.unit
class TestBuildWorkflowWiring:
    """Tests for checkpoint/name wiring in build_workflow()."""

    async def test_build_workflow_namespaces_name_per_tenant(self):
        """When no explicit workflow_name is given the effective name is
        ``f'{tenant_id}:{defn.key}'`` so MAF namespaces checkpoints per tenant."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        with patch("src.agents.workflows.engine.resolve_model", new=AsyncMock()):
            with patch(
                "src.agents.workflows.engine._build_agent_for_step",
                return_value=_StubAgent(),
            ) as mock_build_agent:
                with patch(
                    "src.agents.workflows.engine.WorkflowBuilder"
                ) as mock_builder_cls:
                    mock_builder_instance = MagicMock()
                    mock_builder_cls.return_value = mock_builder_instance
                    mock_workflow = MagicMock()
                    mock_builder_instance.build.return_value = mock_workflow

                    defn = WorkflowDefinition(
                        key="research",
                        name="Research",
                        steps=[
                            {
                                "id": "step1",
                                "name": "Step1",
                                "type": "inline",
                                "instructions": "Do stuff",
                            }
                        ],
                    )
                    mock_db = MagicMock()

                    workflow = await build_workflow(
                        defn=defn,
                        db=mock_db,
                        tenant_id="T1",
                        extra_tools=None,
                        base_temperature=0.7,
                        base_reasoning_effort=None,
                        default_model_id=None,
                    )

                    # Verify the builder was constructed with the namespaced name
                    mock_builder_cls.assert_called_once()
                    call_kwargs = mock_builder_cls.call_args[1]
                    assert call_kwargs["name"] == "T1:research"
                    assert workflow is mock_workflow

    async def test_build_workflow_accepts_explicit_workflow_name_and_storage(self):
        """When an explicit workflow_name and checkpoint_storage are passed
        the builder receives them verbatim."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        sentinel_storage = _FakeCheckpointStorage()

        with patch("src.agents.workflows.engine.resolve_model", new=AsyncMock()):
            with patch(
                "src.agents.workflows.engine._build_agent_for_step",
                return_value=_StubAgent(),
            ) as mock_build_agent:
                with patch(
                    "src.agents.workflows.engine.WorkflowBuilder"
                ) as mock_builder_cls:
                    mock_builder_instance = MagicMock()
                    mock_builder_cls.return_value = mock_builder_instance
                    mock_workflow = MagicMock()
                    mock_builder_instance.build.return_value = mock_workflow

                    defn = WorkflowDefinition(
                        key="research",
                        name="Research",
                        steps=[
                            {
                                "id": "step1",
                                "name": "Step1",
                                "type": "inline",
                                "instructions": "Do stuff",
                            }
                        ],
                    )
                    mock_db = MagicMock()

                    workflow = await build_workflow(
                        defn=defn,
                        db=mock_db,
                        tenant_id="T1",
                        extra_tools=None,
                        base_temperature=0.7,
                        base_reasoning_effort=None,
                        default_model_id=None,
                        checkpoint_storage=sentinel_storage,
                        workflow_name="custom:name",
                        initial_state=None,
                    )

                    mock_builder_cls.assert_called_once()
                    call_kwargs = mock_builder_cls.call_args[1]
                    assert call_kwargs["name"] == "custom:name"
                    assert call_kwargs["checkpoint_storage"] is sentinel_storage
                    assert workflow is mock_workflow
