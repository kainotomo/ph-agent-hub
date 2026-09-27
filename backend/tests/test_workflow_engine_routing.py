# =============================================================================
# PH Agent Hub — Workflow Engine Routing Tests
# =============================================================================
# Tests that build_workflow emits MAF graph conditional edges from authored
# WorkflowDefinition.branches, using only WorkflowBuilder.add_edge().
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class _StubAgent:
    """Minimal agent stub so no model client is constructed."""

    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = "stub"

    def create_session(self):
        from agent_framework import AgentSession

        return AgentSession()


@pytest.mark.unit
class TestEngineRouting:
    """Tests for conditional edge emission from build_workflow()."""

    async def test_no_branches_regression(self):
        """A two-step definition with no branches produces a
        SingleEdgeGroup chaining ``a`` → ``b`` (no SwitchCaseEdgeGroup)."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="no-branch",
            name="NoBranch",
            steps=[
                {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
                {"id": "b", "name": "B", "type": "inline", "instructions": "Do b"},
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _StubAgent(step.id),
        ):
            workflow = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        edge_groups = workflow.graph_signature["edge_groups"]

        # There should be a SingleEdgeGroup with a → b.
        # MAF may also emit InternalEdgeGroup entries from its own bookkeeping;
        # we verify the presence of the chain group and the absence of
        # SwitchCaseEdgeGroup.
        chain_group = None
        for g in edge_groups:
            if g["group_type"] == "SingleEdgeGroup" and g["sources"] == ["a"] and g["targets"] == ["b"]:
                chain_group = g
                break

        assert chain_group is not None, f"No chain group found. Groups: {edge_groups}"
        assert chain_group["edges"][0]["condition"] is None

        # No switch-case group should ever appear
        group_types = [g["group_type"] for g in edge_groups]
        assert "SwitchCaseEdgeGroup" not in group_types

    async def test_conditional_edge_emitted(self):
        """A 3-step definition with branches produces a SingleEdgeGroup for
        the conditional edge with the correct condition name (MAF records
        the function ``__name__``, i.e. ``_always`` from the predicate
        in ``conditions.py``)."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="branch",
            name="Branch",
            steps=[
                {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
                {"id": "b", "name": "B", "type": "inline", "instructions": "Do b"},
                {"id": "c", "name": "C", "type": "inline", "instructions": "Do c"},
            ],
            branches=[
                {"source": "a", "condition": "@always", "target": "b"},
                {"source": "a", "condition": "default", "target": "c"},
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _StubAgent(step.id),
        ):
            workflow = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        edge_groups = workflow.graph_signature["edge_groups"]

        # Find the conditional group (a → b via @always)
        cond_group = None
        default_group = None
        for g in edge_groups:
            if g["sources"] == ["a"] and g["targets"] == ["b"]:
                cond_group = g
            if g["sources"] == ["a"] and g["targets"] == ["c"]:
                default_group = g

        assert cond_group is not None
        assert cond_group["group_type"] == "SingleEdgeGroup"
        assert cond_group["sources"] == ["a"]
        assert cond_group["targets"] == ["b"]
        # MAF records the function __name__ from conditions.py which is _always
        assert cond_group["edges"][0]["condition"] == "_always"

    async def test_default_edge_is_unconditional(self):
        """The ``default`` branch edge carries no condition (``None``)."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="branch",
            name="Branch",
            steps=[
                {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
                {"id": "b", "name": "B", "type": "inline", "instructions": "Do b"},
                {"id": "c", "name": "C", "type": "inline", "instructions": "Do c"},
            ],
            branches=[
                {"source": "a", "condition": "@always", "target": "b"},
                {"source": "a", "condition": "default", "target": "c"},
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _StubAgent(step.id),
        ):
            workflow = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        edge_groups = workflow.graph_signature["edge_groups"]

        default_group = None
        for g in edge_groups:
            if g["sources"] == ["a"] and g["targets"] == ["c"]:
                default_group = g

        assert default_group is not None
        assert default_group["edges"][0]["condition"] is None

    async def test_no_switch_case_group(self):
        """``SwitchCaseEdgeGroup`` must never appear in the graph signature.
        MAF does not fingerprint switch-case conditions, so those shapes
        are forbidden by construction."""
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        defn = WorkflowDefinition(
            key="branch",
            name="Branch",
            steps=[
                {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
                {"id": "b", "name": "B", "type": "inline", "instructions": "Do b"},
                {"id": "c", "name": "C", "type": "inline", "instructions": "Do c"},
            ],
            branches=[
                {"source": "a", "condition": "@always", "target": "b"},
                {"source": "a", "condition": "default", "target": "c"},
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _StubAgent(step.id),
        ):
            workflow = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        group_types = [g["group_type"] for g in workflow.graph_signature["edge_groups"]]
        assert "SwitchCaseEdgeGroup" not in group_types

    async def test_signature_changes_with_condition(self):
        """Building the same 3-step definition with ``@always`` versus
        ``@never`` yields different ``graph_signature_hash`` values.

        This is the whole reason switch-case is forbidden: MAF does not
        fingerprint switch-case conditions, so two switch-case graphs
        whose conditions differ would produce the same hash, making
        a routing change classified as CONFIG_ONLY and stranding paused runs.
        """
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.definition import WorkflowDefinition

        branches_always = [
            {"source": "a", "condition": "@always", "target": "b"},
            {"source": "a", "condition": "default", "target": "c"},
        ]
        branches_never = [
            {"source": "a", "condition": "@never", "target": "b"},
            {"source": "a", "condition": "default", "target": "c"},
        ]

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _StubAgent(step.id),
        ):
            defn_always = WorkflowDefinition(
                key="hash-test",
                name="HashTest",
                steps=[
                    {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
                    {"id": "b", "name": "B", "type": "inline", "instructions": "Do b"},
                    {"id": "c", "name": "C", "type": "inline", "instructions": "Do c"},
                ],
                branches=branches_always,
            )
            workflow_always = await build_workflow(
                defn=defn_always, db=MagicMock(), tenant_id="T1"
            )

            defn_never = WorkflowDefinition(
                key="hash-test",
                name="HashTest",
                steps=[
                    {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
                    {"id": "b", "name": "B", "type": "inline", "instructions": "Do b"},
                    {"id": "c", "name": "C", "type": "inline", "instructions": "Do c"},
                ],
                branches=branches_never,
            )
            workflow_never = await build_workflow(
                defn=defn_never, db=MagicMock(), tenant_id="T1"
            )

        assert workflow_always.graph_signature_hash != workflow_never.graph_signature_hash

    async def test_execute_time_rejection_unknown_step_id(self):
        """A definition whose branch references an unknown step id raises
        ``ValidationError`` at execute time in the engine.

        Route used: the schema validator (definition.py) already rejects
        unknown step ids in branches, so we test the engine-level guard
        by mutating a validated definition's ``branches`` list and calling
        ``add_definition_topology`` directly with a hand-built builder and
        executor list. The builder and executors are patched at
        ``src.agents.workflows.engine.WorkflowBuilder`` so we avoid the
        WorkflowBuilder constructor requiring a valid start_executor."""
        from src.core.exceptions import ValidationError
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.engine import add_definition_topology

        # Build a valid definition first
        defn = WorkflowDefinition(
            key="reject-test",
            name="RejectTest",
            steps=[
                {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
            ],
        )

        # Mutate the validated definition to include an unknown step id
        from pydantic import BaseModel

        class _FakeBranch(BaseModel):
            source: str
            condition: str
            target: str

        defn.branches.append(_FakeBranch(source="a", condition="default", target="nonexistent"))

        # Build a hand-built mock builder and a real executor list
        mock_builder = MagicMock()
        fake_agent = MagicMock()
        fake_agent.id = "a"
        from agent_framework import AgentExecutor
        executors = [AgentExecutor(agent=fake_agent, id="a")]

        with pytest.raises(ValidationError) as exc_info:
            add_definition_topology(mock_builder, executors, defn)

        error_msg = str(exc_info.value)
        assert "reject-test" in error_msg
        assert "nonexistent" in error_msg

    async def test_execute_time_rejection_unknown_condition(self):
        """A definition with an unknown condition reference raises
        ``ValidationError`` whose message names the condition and lists
        the known ones.

        Route used: the schema validator (definition.py) already rejects
        unknown condition references, so we test the engine-level guard
        by mutating a validated definition's ``branches`` list and calling
        ``add_definition_topology`` directly with a hand-built builder."""
        from src.core.exceptions import ValidationError
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.engine import add_definition_topology

        # Build a valid definition first
        defn = WorkflowDefinition(
            key="reject-test",
            name="RejectTest",
            steps=[
                {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
                {"id": "b", "name": "B", "type": "inline", "instructions": "Do b"},
            ],
            branches=[
                {"source": "a", "condition": "@always", "target": "b"},
                {"source": "a", "condition": "default", "target": "b"},
            ],
        )

        # Mutate to include an unknown condition reference
        from pydantic import BaseModel

        class _FakeBranch(BaseModel):
            source: str
            condition: str
            target: str

        defn.branches.append(_FakeBranch(source="a", condition="@nope", target="b"))

        # Build a hand-built mock builder and a real executor list (with both a and b)
        mock_builder = MagicMock()
        fake_agent_a = MagicMock()
        fake_agent_a.id = "a"
        fake_agent_b = MagicMock()
        fake_agent_b.id = "b"
        from agent_framework import AgentExecutor
        executors = [AgentExecutor(agent=fake_agent_a, id="a"), AgentExecutor(agent=fake_agent_b, id="b")]

        with pytest.raises(ValidationError) as exc_info:
            add_definition_topology(mock_builder, executors, defn)

        error_msg = str(exc_info.value)
        assert "reject-test" in error_msg
        assert "@nope" in error_msg
        assert "@always" in error_msg
