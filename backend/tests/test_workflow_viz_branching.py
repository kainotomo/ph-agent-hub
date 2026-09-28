# =============================================================================
# PH Agent Hub — Workflow Visualization: Branching / Conditional Edge Tests
# =============================================================================
# Characterises how MAF's Mermaid renderer handles conditional and switch-case
# edges when small graphs are built directly with WorkflowBuilder.  This chunk
# is test-only: it adds no product code or schema.
# =============================================================================

import pytest
from agent_framework import (
    AgentExecutor,
    AgentSession,
    Case,
    Default,
    WorkflowBuilder,
    WorkflowViz,
)


# ---------------------------------------------------------------------------
# Local stub agent — no model client is constructed
# ---------------------------------------------------------------------------

class _ProbeAgent:
    """Minimal agent surface so no model client is constructed."""

    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = "probe"

    def create_session(self) -> AgentSession:
        return AgentSession()

    def run(self, *args, **kwargs):
        """Dummy run surface so AgentExecutor can inspect its signature."""
        pass


def _executor(executor_id: str) -> AgentExecutor:
    return AgentExecutor(
        agent=_ProbeAgent(executor_id), id=executor_id, context_mode="last_agent"
    )


# ---------------------------------------------------------------------------
# Module-level condition callables
# ---------------------------------------------------------------------------

def _is_high(data) -> bool:
    """Return True for 'high' branches."""
    return True


def _always(_data):
    return True


def _never(_data):
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestConditionalEdgeRendering:
    """Characterise how MAF's WorkflowViz renders conditional edges in Mermaid."""

    def test_conditional_edge_is_marked_conditional(self):
        """Regular edges with a ``condition`` callable render as
        ``-. conditional .->`` while plain edges render as ``-->``."""

        route = _executor("route")
        high = _executor("high")
        low = _executor("low")

        builder = WorkflowBuilder(name="branch_conditional", start_executor=route, output_from=[high, low])
        builder.add_edge(route, high, condition=_is_high)
        builder.add_edge(route, low)

        mermaid = WorkflowViz(builder.build()).to_mermaid()

        assert mermaid.startswith("flowchart TD")
        assert "route -. conditional .-> high;" in mermaid
        assert "route --> low;" in mermaid

    def test_switch_case_group_keeps_nodes_but_not_conditional_marking(self):
        """SwitchCaseEdgeGroup nodes appear but edges lack the conditional
        styling because MAF's ``WorkflowViz`` decides conditional rendering from
        ``edge._condition``, which is ``None`` for the edges inside a
        ``SwitchCaseEdgeGroup`` (the condition metadata lives on
        ``SwitchCaseEdgeGroupCase`` objects, not on ``Edge`` instances)."""

        route = _executor("route")
        high = _executor("high")
        low = _executor("low")

        builder = WorkflowBuilder(name="branch_switch", start_executor=route, output_from=[high, low])
        builder.add_switch_case_edge_group(route, [Case(condition=_is_high, target=high), Default(target=low)])

        mermaid = WorkflowViz(builder.build()).to_mermaid()

        assert 'route["route (Start)"]' in mermaid
        assert 'high["high"]' in mermaid
        assert 'low["low"]' in mermaid
        assert "route --> high;" in mermaid
        assert "route --> low;" in mermaid
        # MAF does not render conditional styling for switch-case edges.
        assert "-." not in mermaid

    def test_graph_signature_ignores_switch_case_conditions(self):
        """MAF's ``_compute_graph_signature`` serialises ``condition_name`` on
        edges; for a regular ``add_edge`` the edge has no ``condition_name``
        attribute, and for a switch-case group the condition metadata is stored
        inside ``SwitchCaseEdgeGroupCase`` objects (not on the ``Edge`` level),
        so the graph signature cannot detect a change to a switch-case
        condition.  An edit-classification path built on that hash would treat
        a routing change as a non-structural edit."""

        def _switch_signature(condition):
            route = _executor("route")
            high = _executor("high")
            low = _executor("low")
            builder = WorkflowBuilder(name="switch_signature", start_executor=route, output_from=[high, low])
            builder.add_switch_case_edge_group(route, [Case(condition=condition, target=high), Default(target=low)])
            return builder.build().graph_signature_hash

        sig_always = _switch_signature(_always)
        sig_never = _switch_signature(_never)

        assert sig_always == sig_never
