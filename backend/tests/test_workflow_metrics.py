# =============================================================================
# PH Agent Hub — Workflow Metrics Tests (Issues #627, #661)
# =============================================================================

import hashlib

import pytest

from src.agents.workflows.engine import _emit_workflow_metrics

MESSAGE = (
    "Execute, in full, the prompt for the company below.\n"
    "\n"
    "Name:     Bank of America Corp\n"
    "Ticker:   BAC:xnys\n"
    "Currency: USD\n"
    "ISIN:     US0605051046"
)


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

    def test_turn_input_digest_carried(self):
        """Issue #661 — workflow turns record what they handed the workflow."""
        counts = self._counts()
        _emit_workflow_metrics(
            counts, 1, 0.0, None, 5000, 100, 0,
            message=MESSAGE,
            history_messages=2,
            contextualized=True,
        )

        metrics = counts["metrics"]
        assert metrics["user_message_chars"] == len(MESSAGE)
        assert metrics["user_message_sha256"] == hashlib.sha256(
            MESSAGE.encode("utf-8")
        ).hexdigest()
        assert metrics["user_message_tail"].endswith("US0605051046")
        assert metrics["history_messages"] == 2
        assert metrics["contextualized"] is True

    def test_resume_without_a_message_has_null_digest(self):
        """A checkpoint resume passes ``message=None``; the keys still exist."""
        counts = self._counts()
        _emit_workflow_metrics(counts, 1, 0.0, None, 0, 0, 0)

        metrics = counts["metrics"]
        assert metrics["user_message_chars"] is None
        assert metrics["user_message_sha256"] is None
        assert metrics["user_message_head"] is None
        assert metrics["user_message_tail"] is None
        assert metrics["history_messages"] == 0
        assert metrics["contextualized"] is False
