# =============================================================================
# PH Agent Hub — Workflow Edit Classification Tests
# =============================================================================
# Tests for ``classify_edit`` and ``assert_key_immutable`` in
# ``src.agents.workflows.identity``.
# =============================================================================

import pytest

from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.identity import (
    EditKind,
    assert_key_immutable,
    classify_edit,
    graph_signature_hash,
)
from src.core.exceptions import ValidationError


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


# =============================================================================
# Key immutability
# =============================================================================


class TestAssertKeyImmutable:
    """A workflow key may not be renamed in place."""

    def test_same_key_does_not_raise(self):
        assert_key_immutable("a", "a")

    def test_different_key_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            assert_key_immutable("a", "b")

        message = str(excinfo.value)
        assert "a" in message
        assert "b" in message
        assert "immutable" in message


# =============================================================================
# Unchanged
# =============================================================================


class TestClassifyUnchanged:
    """Identical definitions classify as UNCHANGED."""

    def test_identical_definitions(self):
        current = _defn([_step("a"), _step("b")])
        proposed = _defn([_step("a"), _step("b")])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.UNCHANGED
        assert result.added_step_ids == ()
        assert result.removed_step_ids == ()
        assert result.current_signature_hash == result.proposed_signature_hash


# =============================================================================
# Config-only
# =============================================================================


class TestClassifyConfigOnly:
    """Config edits leave the derived signature hash untouched."""

    def test_instructions_change(self):
        current = _defn([_step("a", instructions="First")])
        proposed = _defn([_step("a", instructions="Second")])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.CONFIG_ONLY
        assert result.current_signature_hash == result.proposed_signature_hash

    def test_context_mode_change(self):
        current = _defn([_step("a", context_mode="last_agent")])
        proposed = _defn([_step("a", context_mode="full")])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.CONFIG_ONLY
        assert result.current_signature_hash == result.proposed_signature_hash

    def test_display_name_change(self):
        current = _defn([_step("a", name="Research")])
        proposed = _defn([_step("a", name="Different Label")])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.CONFIG_ONLY
        assert result.added_step_ids == ()
        assert result.removed_step_ids == ()

    def test_temperature_change(self):
        current = _defn([_step("a", temperature=0.2)])
        proposed = _defn([_step("a", temperature=1.9)])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.CONFIG_ONLY
        assert result.current_signature_hash == result.proposed_signature_hash


# =============================================================================
# Topology
# =============================================================================


class TestClassifyTopology:
    """Structural edits are detected from MAF's derived signature."""

    def test_added_step(self):
        current = _defn([_step("a"), _step("b")])
        proposed = _defn([_step("a"), _step("b"), _step("c")])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.TOPOLOGY
        assert result.added_step_ids == ("c",)
        assert result.removed_step_ids == ()

    def test_removed_step(self):
        current = _defn([_step("a"), _step("b"), _step("c")])
        proposed = _defn([_step("a"), _step("b")])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.TOPOLOGY
        assert result.added_step_ids == ()
        assert result.removed_step_ids == ("c",)

    def test_renamed_step_id_is_never_silent(self):
        current = _defn([_step("a"), _step("b")])
        proposed = _defn([_step("a"), _step("c")])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.TOPOLOGY
        assert result.added_step_ids == ("c",)
        assert result.removed_step_ids == ("b",)

    def test_reordered_steps(self):
        current = _defn([_step("a"), _step("b")])
        proposed = _defn([_step("b"), _step("a")])

        result = classify_edit(current, proposed)

        assert result.kind == EditKind.TOPOLOGY
        assert result.added_step_ids == ()
        assert result.removed_step_ids == ()

    def test_topology_hash_actually_differs(self):
        current = _defn([_step("a")])
        proposed = _defn([_step("a"), _step("b")])

        result = classify_edit(current, proposed)

        assert result.current_signature_hash != result.proposed_signature_hash
        assert result.current_signature_hash == graph_signature_hash(current)
        assert result.proposed_signature_hash == graph_signature_hash(proposed)


# =============================================================================
# Key rename rejection
# =============================================================================


class TestClassifyKeyRename:
    """A key rename is rejected before any classification happens."""

    def test_different_keys_raise(self):
        current = _defn([_step("a")], key="first")
        proposed = _defn([_step("a")], key="second")

        with pytest.raises(ValidationError) as excinfo:
            classify_edit(current, proposed)

        assert "immutable" in str(excinfo.value)
