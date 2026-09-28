# =============================================================================
# PH Agent Hub — Workflow Outcome Tests
# =============================================================================
# Tests for the ``workflow_outcome`` helper that classifies a workflow run as
# "completed" or "paused" based on MAF's ``WorkflowRunState``.
# =============================================================================

import pytest

from agent_framework import WorkflowRunState
from src.agents.workflows.engine import workflow_outcome


class _Result:
    """Minimal stub mimicking a ``WorkflowRunResult``."""

    def __init__(self, state=None, raises=False):
        self._state = state
        self._raises = raises

    def get_final_state(self):
        if self._raises:
            raise AttributeError("no status events")
        return self._state


class TestWorkflowOutcome:

    def test_idle_is_completed(self):
        assert workflow_outcome(_Result(WorkflowRunState.IDLE)) == "completed"

    def test_idle_with_pending_requests_is_paused(self):
        assert workflow_outcome(_Result(WorkflowRunState.IDLE_WITH_PENDING_REQUESTS)) == "paused"

    def test_missing_state_is_completed(self):
        assert workflow_outcome(_Result(raises=True)) == "completed"
