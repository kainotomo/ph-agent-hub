# =============================================================================
# PH Agent Hub — Workflow Edit Policy Tests
# =============================================================================
# Tests for ``EDIT_POLICY`` and ``render_edit_report`` in
# ``src.agents.workflows.identity``.
# =============================================================================

from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.identity import (
    EDIT_POLICY,
    EditClassification,
    EditKind,
    classify_edit,
    render_edit_report,
)


# =============================================================================
# Helpers
# =============================================================================


def _step(step_id: str, name: str | None = None, **overrides):
    """Build a minimal valid inline step dict."""
    step = {
        "id": step_id,
        "name": name or step_id.title(),
        "type": "inline",
        "instructions": f"Do {step_id}",
    }
    step.update(overrides)
    return step


def _defn(steps, key: str = "wf_key", **overrides):
    """Build a minimal valid workflow definition."""
    payload = {"key": key, "name": "Workflow", "steps": steps}
    payload.update(overrides)
    return WorkflowDefinition(**payload)


def _classification(**overrides) -> EditClassification:
    """Build a synthetic classification for report wording tests."""
    fields = {
        "kind": EditKind.CONFIG_ONLY,
        "current_signature_hash": "aaa",
        "proposed_signature_hash": "aaa",
        "added_step_ids": (),
        "removed_step_ids": (),
    }
    fields.update(overrides)
    return EditClassification(**fields)


# =============================================================================
# Policy text
# =============================================================================


class TestEditPolicy:
    """The policy constant states the operative rules."""

    def test_mentions_the_three_classes(self):
        assert "config-only" in EDIT_POLICY
        assert "topology" in EDIT_POLICY

    def test_mentions_rename_is_a_create(self):
        assert "create, not an edit" in EDIT_POLICY


# =============================================================================
# Unchanged report
# =============================================================================


class TestUnchangedReport:
    """An unchanged definition reports no change."""

    def test_contains_no_change(self):
        report = render_edit_report(_classification(kind=EditKind.UNCHANGED))

        assert "no change" in report.lower()

    def test_from_real_classification(self):
        current = _defn([_step("a")])
        report = render_edit_report(classify_edit(current, current))

        assert "no change" in report.lower()


# =============================================================================
# Config-only report
# =============================================================================


class TestConfigOnlyReport:
    """A config-only edit leaves paused runs resumable."""

    def test_says_config_only_and_promises_resumability(self):
        report = render_edit_report(_classification(kind=EditKind.CONFIG_ONLY))

        assert "config-only" in report
        assert "cannot be resumed" not in report

    def test_from_real_classification(self):
        current = _defn([_step("a", instructions="First")])
        proposed = _defn([_step("a", instructions="Second")])

        report = render_edit_report(classify_edit(current, proposed))

        assert "config-only" in report
        assert "cannot be resumed" not in report


# =============================================================================
# Topology report
# =============================================================================


class TestTopologyReport:
    """A topology edit invalidates paused runs of the previous topology."""

    def test_says_topology_and_invalidates_resume(self):
        report = render_edit_report(_classification(kind=EditKind.TOPOLOGY))

        assert "topology" in report
        assert "cannot be resumed" in report

    def test_names_added_and_removed_step_ids(self):
        report = render_edit_report(
            _classification(
                kind=EditKind.TOPOLOGY,
                added_step_ids=("c",),
                removed_step_ids=("b",),
            )
        )

        assert "c" in report
        assert "b" in report

    def test_renders_empty_tuples_as_none(self):
        report = render_edit_report(_classification(kind=EditKind.TOPOLOGY))

        assert "none" in report

    def test_known_paused_run_count(self):
        report = render_edit_report(
            _classification(kind=EditKind.TOPOLOGY),
            paused_run_count=3,
        )

        assert "3" in report
        assert "paused run(s)" in report

    def test_unknown_paused_run_count_mentions_checkpoint_storage(self):
        report = render_edit_report(
            _classification(kind=EditKind.TOPOLOGY),
            paused_run_count=None,
        )

        assert "checkpoint storage" in report
        assert "paused run(s)" not in report

    def test_from_real_classification(self):
        current = _defn([_step("a"), _step("b")])
        proposed = _defn([_step("a"), _step("c")])

        report = render_edit_report(
            classify_edit(current, proposed),
            paused_run_count=2,
        )

        assert "topology" in report
        assert "cannot be resumed" in report
        assert "c" in report
        assert "b" in report
        assert "2" in report
