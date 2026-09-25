# =============================================================================
# PH Agent Hub — Workflow Definition Identity Tests
# =============================================================================
# Tests for the derived graph-signature primitives in
# ``src.agents.workflows.identity``.
# =============================================================================

import pytest

from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.identity import graph_signature, graph_signature_hash


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
# Derived signature: config changes never move the hash
# =============================================================================


class TestGraphSignatureIgnoresConfig:
    """The signature is derived from MAF, so non-structural edits are invisible."""

    def test_instructions_change_leaves_hash_equal(self):
        current = _defn([_step("a", instructions="First")])
        proposed = _defn([_step("a", instructions="Second")])

        assert graph_signature_hash(current) == graph_signature_hash(proposed)

    def test_context_mode_change_leaves_hash_equal(self):
        current = _defn([_step("a", context_mode="last_agent")])
        proposed = _defn([_step("a", context_mode="full")])

        assert graph_signature_hash(current) == graph_signature_hash(proposed)

    def test_temperature_change_leaves_hash_equal(self):
        current = _defn([_step("a", temperature=0.1)])
        proposed = _defn([_step("a", temperature=1.7)])

        assert graph_signature_hash(current) == graph_signature_hash(proposed)

    def test_display_name_change_leaves_hash_equal(self):
        current = _defn([_step("a", name="Research")])
        proposed = _defn([_step("a", name="Totally Different Label")])

        assert graph_signature_hash(current) == graph_signature_hash(proposed)

    def test_description_change_leaves_hash_equal(self):
        current = _defn([_step("a")], description="One")
        proposed = _defn([_step("a")], description="Two")

        assert graph_signature_hash(current) == graph_signature_hash(proposed)

    def test_step_type_change_leaves_hash_equal(self):
        """Documents the derived behaviour: MAF sees the same executor class."""
        current = _defn([_step("a"), _step("b")])
        proposed = _defn(
            [
                _step("a"),
                {
                    "id": "b",
                    "name": "B",
                    "type": "agent",
                    "agent_ref": "some_agent",
                },
            ]
        )

        assert graph_signature_hash(current) == graph_signature_hash(proposed)


# =============================================================================
# Derived signature: topology changes move the hash
# =============================================================================


class TestGraphSignatureDetectsTopology:
    """Identity and structure edits are visible in MAF's signature."""

    def test_step_id_change_changes_hash(self):
        current = _defn([_step("a")])
        proposed = _defn([_step("b")])

        assert graph_signature_hash(current) != graph_signature_hash(proposed)

    def test_step_reorder_changes_hash(self):
        current = _defn([_step("a"), _step("b")])
        proposed = _defn([_step("b"), _step("a")])

        assert graph_signature_hash(current) != graph_signature_hash(proposed)

    def test_added_step_changes_hash(self):
        current = _defn([_step("a")])
        proposed = _defn([_step("a"), _step("b")])

        assert graph_signature_hash(current) != graph_signature_hash(proposed)

    def test_removed_step_changes_hash(self):
        current = _defn([_step("a"), _step("b")])
        proposed = _defn([_step("a")])

        assert graph_signature_hash(current) != graph_signature_hash(proposed)


# =============================================================================
# Shape of the returned signature
# =============================================================================


class TestGraphSignatureShape:
    """The returned object is MAF's own signature dict."""

    def test_single_step_definition_works(self):
        signature = graph_signature(_defn([_step("only")]))

        assert signature["start_executor"] == "only"

    def test_signature_has_maf_keys(self):
        signature = graph_signature(_defn([_step("a"), _step("b")]))

        assert "start_executor" in signature
        assert "executors" in signature
        assert "edge_groups" in signature

    def test_signature_hash_is_stable_across_calls(self):
        defn = _defn([_step("a"), _step("b")])

        assert graph_signature_hash(defn) == graph_signature_hash(defn)

    def test_requires_no_database_or_model_client(self):
        """A signature can be derived with neither a DB session nor a model."""
        signature = graph_signature(_defn([_step("a")], key="standalone"))

        assert signature["executors"]["a"]
