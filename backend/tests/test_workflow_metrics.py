# =============================================================================
# PH Agent Hub — Workflow Metrics Tests (Issue #627)
# =============================================================================

import pytest

from src.agents.workflows.engine import _emit_workflow_metrics


@pytest.mark.unit
class TestEmitWorkflowMetrics:
    """``_emit_workflow_metrics`` reports the peak step prompt, not the sum."""

    def _counts(self, **overrides):
        counts = {
            "in": 5000,
            "last_in": 1200,
            "_system_prompt": "x" * 400,  # ~100 tokens
            "_tools": [],
        }
        counts.update(overrides)
        return counts

    def test_last_input_tokens_recorded(self):
        counts = self._counts()
        _emit_workflow_metrics(counts, 3, 0.0, None, 5000, 100, 0)

        metrics = counts["metrics"]
        assert metrics["last_input_tokens"] == 1200
        assert metrics["messages_tokens"] == 1100

    def test_missing_last_in_falls_back_to_cumulative(self):
        counts = self._counts(last_in=0)
        _emit_workflow_metrics(counts, 1, 0.0, None, 5000, 100, 0)

        metrics = counts["metrics"]
        assert metrics["last_input_tokens"] is None
        assert metrics["messages_tokens"] == 5000 - 100
