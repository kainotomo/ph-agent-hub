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
