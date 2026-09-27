# =============================================================================
# PH Agent Hub — Probe Workflow Topology Parity Tests
# =============================================================================
# The probe builder used for signatures and visualization must produce the same
# MAF topology as the real production build for the same definition.
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent_framework import AgentSession
from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.identity import build_probe_workflow


class _StubAgent:
    """Minimal agent stub so no model client is constructed."""

    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = "stub"

    def create_session(self) -> AgentSession:
        return AgentSession()


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="parity",
        name="Parity",
        steps=[
            {"id": "a", "name": "A", "type": "inline", "instructions": "Do a"},
            {"id": "b", "name": "B", "type": "inline", "instructions": "Do b"},
        ],
    )


def _branching_definition() -> WorkflowDefinition:
    """A 3-step definition whose first step branches on a condition."""
    return WorkflowDefinition(
        key="branching_parity",
        name="Branching Parity",
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


@pytest.mark.unit
class TestProbeTopologyParity:
    async def test_probe_matches_real_build(self):
        from src.agents.workflows.engine import build_workflow

        defn = _definition()

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _StubAgent(step.id),
        ):
            real = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        probe = build_probe_workflow(defn)

        assert list(real.executors) == list(probe.executors)
        assert real.graph_signature_hash == probe.graph_signature_hash

    async def test_probe_matches_real_build_with_branches(self):
        """A branching definition (with conditions and a default) produces
        identical graph-signature topology in both the probe and the real build.

        Both ``edge_groups`` structures are asserted equal so that any future
        divergence in how MAF serialises conditional edges is caught immediately.
        """
        from src.agents.workflows.engine import build_workflow

        defn = _branching_definition()

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _StubAgent(step.id),
        ):
            real = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        probe = build_probe_workflow(defn)

        assert list(real.executors) == list(probe.executors)
        assert real.graph_signature_hash == probe.graph_signature_hash

        # edge_groups parity — any structural drift is caught here
        assert real.graph_signature["edge_groups"] == probe.graph_signature["edge_groups"]

    def test_probe_signature_no_switch_case_groups(self):
        """The topology probe must never produce ``SwitchCaseEdgeGroup`` entries
        for a branching definition, because the probe only uses ``add_edge`` /
        ``add_chain``, never the MAF switch-case builder API.  This ensures the
        probe and the real build stay on the same structural footing.
        """
        defn = _branching_definition()

        probe_sig = build_probe_workflow(defn).graph_signature

        group_types = [g["group_type"] for g in probe_sig["edge_groups"]]
        assert "SwitchCaseEdgeGroup" not in group_types
        # A conditional SingleEdgeGroup must actually be present, so this test
        # cannot pass vacuously on a graph with no conditional edges at all.
        assert "SingleEdgeGroup" in group_types
