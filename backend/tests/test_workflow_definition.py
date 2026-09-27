# =============================================================================
# PH Agent Hub — Workflow Definition Schema Tests
# =============================================================================
# Tests for ``WorkflowBranch``, ``DEFAULT_BRANCH``, and the ``branches``
# field on ``WorkflowDefinition``.
# =============================================================================

import pytest

from src.agents.workflows.conditions import condition_names
from src.agents.workflows.definition import (
    DEFAULT_BRANCH,
    WorkflowBranch,
    WorkflowDefinition,
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


def _branch(source, condition, target):
    """Build a minimal valid branch dict."""
    return {"source": source, "condition": condition, "target": target}


# =============================================================================
# WorkflowBranch — field validators
# =============================================================================


class TestWorkflowBranchFieldValidators:
    """Tests for WorkflowBranch field-level validation."""

    def test_empty_source_raises(self):
        with pytest.raises(ValueError, match="source.*non-empty"):
            WorkflowBranch(source="", condition="default", target="b")

    def test_empty_target_raises(self):
        with pytest.raises(ValueError, match="target.*non-empty"):
            WorkflowBranch(source="a", condition="default", target="")

    def test_empty_condition_raises(self):
        with pytest.raises(ValueError, match="condition.*non-empty"):
            WorkflowBranch(source="a", condition="", target="b")

    def test_source_whitespace_raises(self):
        with pytest.raises(ValueError, match="source.*whitespace"):
            WorkflowBranch(source="a b", condition="default", target="b")

    def test_target_whitespace_raises(self):
        with pytest.raises(ValueError, match="target.*whitespace"):
            WorkflowBranch(source="a", condition="default", target="b c")

    def test_condition_whitespace_raises(self):
        with pytest.raises(ValueError, match="condition.*whitespace"):
            WorkflowBranch(source="a", condition="@has space", target="b")

    def test_valid_branch_constructs(self):
        b = WorkflowBranch(source="a", condition="@always", target="b")
        assert b.source == "a"
        assert b.condition == "@always"
        assert b.target == "b"

    def test_default_branch_constructs(self):
        b = WorkflowBranch(source="a", condition=DEFAULT_BRANCH, target="b")
        assert b.condition == DEFAULT_BRANCH


# =============================================================================
# WorkflowDefinition — branches field defaults
# =============================================================================


class TestBranchesDefault:
    """Tests that branches defaults to [] and doesn't break existing defs."""

    def test_branches_defaults_to_empty_list(self):
        steps = [
            _step("a"),
            _step("b"),
        ]
        defn = _defn(steps)
        assert defn.branches == []

    def test_two_step_definition_without_branches_constructs(self):
        steps = [
            _step("a"),
            _step("b"),
        ]
        defn = _defn(steps)
        assert defn.key == "wf_key"
        assert len(defn.steps) == 2
        assert defn.branches == []


# =============================================================================
# WorkflowDefinition — valid branching definition
# =============================================================================


class TestValidBranching:
    """Tests that a valid branching definition constructs."""

    def test_branching_definition_constructs(self):
        steps = [
            _step("a"),
            _step("b"),
            _step("c"),
        ]
        branches = [
            _branch("a", "@always", "b"),
            _branch("a", DEFAULT_BRANCH, "c"),
        ]
        defn = _defn(steps, branches=branches)
        assert len(defn.branches) == 2
        assert defn.branches[0].source == "a"
        assert defn.branches[0].condition == "@always"
        assert defn.branches[0].target == "b"
        assert defn.branches[1].condition == DEFAULT_BRANCH


# =============================================================================
# WorkflowDefinition — structural validation errors
# =============================================================================


class TestUnknownSource:
    """Unknown branch source raises ValueError naming the missing id."""

    def test_unknown_source_raises(self):
        steps = [
            _step("a"),
            _step("b"),
        ]
        branches = [
            _branch("z", "@always", "b"),
            _branch("a", DEFAULT_BRANCH, "b"),
        ]
        with pytest.raises(ValueError, match="source 'z' is not a step id"):
            _defn(steps, branches=branches)


class TestUnknownTarget:
    """Unknown branch target raises ValueError naming the missing id."""

    def test_unknown_target_raises(self):
        steps = [
            _step("a"),
            _step("b"),
        ]
        branches = [
            _branch("a", "@always", "z"),
            _branch("a", DEFAULT_BRANCH, "b"),
        ]
        with pytest.raises(ValueError, match="target 'z' is not a step id"):
            _defn(steps, branches=branches)


class TestSourceEqualsTarget:
    """source == target raises ValueError."""

    def test_source_equals_target_raises(self):
        steps = [
            _step("a"),
            _step("b"),
        ]
        branches = [
            _branch("a", "@always", "a"),
            _branch("a", DEFAULT_BRANCH, "b"),
        ]
        with pytest.raises(ValueError, match="source and target must differ"):
            _defn(steps, branches=branches)


class TestDoubleDefault:
    """Two default branches from the same source raise ValueError."""

    def test_two_defaults_same_source_raises(self):
        steps = [
            _step("a"),
            _step("b"),
            _step("c"),
        ]
        branches = [
            _branch("a", DEFAULT_BRANCH, "b"),
            _branch("a", DEFAULT_BRANCH, "c"),
        ]
        with pytest.raises(ValueError, match="Source 'a' has 2 default branches"):
            _defn(steps, branches=branches)


class TestDuplicatePair:
    """Duplicate (source, condition) pair raises ValueError."""

    def test_duplicate_source_condition_raises(self):
        steps = [
            _step("a"),
            _step("b"),
            _step("c"),
        ]
        branches = [
            _branch("a", "@always", "b"),
            _branch("a", "@always", "c"),
        ]
        with pytest.raises(ValueError, match="Duplicate branch"):
            _defn(steps, branches=branches)


class TestSingleOutgoingBranch:
    """A single branch from a source raises ValueError (needs a fallback)."""

    def test_single_outgoing_branch_raises(self):
        steps = [
            _step("a"),
            _step("b"),
        ]
        branches = [
            _branch("a", "@always", "b"),
        ]
        with pytest.raises(ValueError, match="only one outgoing branch"):
            _defn(steps, branches=branches)


# =============================================================================
# WorkflowDefinition — condition field errors
# =============================================================================


class TestConditionErrors:
    """Condition validation errors."""

    def test_empty_condition_raises(self):
        steps = [
            _step("a"),
            _step("b"),
        ]
        branches = [
            _branch("a", "", "b"),
            _branch("a", DEFAULT_BRANCH, "b"),
        ]
        with pytest.raises(ValueError, match="condition.*non-empty"):
            _defn(steps, branches=branches)

    def test_condition_with_space_raises(self):
        steps = [
            _step("a"),
            _step("b"),
        ]
        branches = [
            _branch("a", "@has space", "b"),
            _branch("a", DEFAULT_BRANCH, "b"),
        ]
        with pytest.raises(ValueError, match="condition.*whitespace"):
            _defn(steps, branches=branches)


# =============================================================================
# B1 — Reference validation against the closed condition vocabulary
# =============================================================================


class TestReferenceValidation:
    """Tests added by chunk B1: validate branch conditions against the
    closed condition vocabulary.
    """

    def test_valid_with_at_always_and_default(self):
        """Regression: a definition using @always and default constructs."""
        steps = [_step("a"), _step("b"), _step("c")]
        branches = [
            _branch("a", "@always", "b"),
            _branch("a", DEFAULT_BRANCH, "c"),
        ]
        defn = _defn(steps, branches=branches)
        assert len(defn.branches) == 2

    def test_unknown_condition_raises(self):
        """condition='@nope' raises ValueError naming @nope and listing @always."""
        steps = [_step("a"), _step("b")]
        branches = [
            _branch("a", "@nope", "b"),
            _branch("a", DEFAULT_BRANCH, "b"),
        ]
        with pytest.raises(ValueError, match="@nope") as exc_info:
            _defn(steps, branches=branches)
        # Must also list a known condition (e.g. @always)
        assert "@always" in str(exc_info.value)

    def test_unprefixed_condition_raises(self):
        """condition='always' (unprefixed, not 'default') raises ValueError."""
        steps = [_step("a"), _step("b")]
        branches = [
            _branch("a", "always", "b"),
            _branch("a", DEFAULT_BRANCH, "b"),
        ]
        with pytest.raises(ValueError) as exc_info:
            _defn(steps, branches=branches)
        err = str(exc_info.value)
        # Must mention @-prefixed or default requirement
        assert "@" in err and "default" in err

    def test_branching_source_without_default_raises(self):
        """A branching source with multiple conditional branches but no
        default raises ValueError naming the source.

        The existing 'only one outgoing branch' check fires first; this test
        uses *two* conditional branches from source 'a' so that structural
        validation passes, leaving only the 'missing default' check.
        """
        steps = [_step("a"), _step("b"), _step("c")]
        branches = [
            _branch("a", "@always", "b"),  # a→b conditional
            _branch("a", "@never", "c"),   # a→c conditional (a has 2 outgoing, no default)
            _branch("b", "@always", "a"),  # b→a conditional (b has 1 outgoing, needs more)
            _branch("b", DEFAULT_BRANCH, "c"),  # b→c default (b has 2 outgoing)
        ]
        with pytest.raises(ValueError, match="no default branch"):
            _defn(steps, branches=branches)

    def test_vocabulary_members_all_accepted(self):
        """Every key returned by condition_names() is accepted as a condition
        when paired with a default branch from the same source.
        """
        for cond in condition_names():
            steps = [_step("a"), _step("b"), _step("c")]
            branches = [
                _branch("a", cond, "b"),
                _branch("a", DEFAULT_BRANCH, "c"),
            ]
            defn = _defn(steps, branches=branches)
            assert defn.branches[0].condition == cond

    def test_default_literal_accepted(self):
        """The 'default' literal constructs and is NOT treated as a condition
        reference.  This documents that 'default' is the absence of a predicate.
        """
        steps = [_step("a"), _step("b"), _step("c")]
        branches = [
            _branch("a", "@always", "b"),  # a→b conditional (a has 2 outgoing)
            _branch("a", DEFAULT_BRANCH, "c"),  # a→c default (default literal used)
        ]
        # Should succeed — default is the unconditional fallback.
        defn = _defn(steps, branches=branches)
        assert defn.branches[0].condition == "@always"
        assert defn.branches[1].condition == DEFAULT_BRANCH


# =============================================================================
# C — Directed-cycle rejection in the branch graph
# =============================================================================


class TestBranchCycles:
    """Tests that cycles in the authored branch graph are rejected."""

    def test_two_step_cycle_rejected(self):
        steps = [
            _step("a"),
            _step("b"),
            _step("c"),
        ]
        branches = [
            _branch("a", "@always", "b"),
            _branch("a", DEFAULT_BRANCH, "c"),
            _branch("b", "@always", "a"),
            _branch("b", DEFAULT_BRANCH, "c"),
        ]
        with pytest.raises(ValueError, match="cycle") as exc_info:
            _defn(steps, branches=branches)
        err = str(exc_info.value)
        assert "a" in err
        assert "b" in err

    def test_three_step_cycle_rejected(self):
        steps = [
            _step("a"),
            _step("b"),
            _step("c"),
            _step("d"),
        ]
        branches = [
            _branch("a", "@always", "b"),
            _branch("a", DEFAULT_BRANCH, "d"),
            _branch("b", "@always", "c"),
            _branch("b", DEFAULT_BRANCH, "d"),
            _branch("c", "@always", "a"),
            _branch("c", DEFAULT_BRANCH, "d"),
        ]
        with pytest.raises(ValueError, match="cycle") as exc_info:
            _defn(steps, branches=branches)
        err = str(exc_info.value)
        assert "a" in err
        assert "b" in err
        assert "c" in err

    def test_diamond_dag_accepted(self):
        """A DAG with no cycle must construct without raising — false-positive guard."""
        steps = [
            _step("a"),
            _step("b"),
            _step("c"),
            _step("d"),
        ]
        branches = [
            _branch("a", "@always", "b"),
            _branch("a", DEFAULT_BRANCH, "d"),
            _branch("b", "@always", "c"),
            _branch("b", DEFAULT_BRANCH, "d"),
        ]
        defn = _defn(steps, branches=branches)
        assert len(defn.branches) == 4

    def test_existing_valid_branching_defn_still_accepted(self):
        """The existing valid 3-step example still constructs."""
        steps = [
            _step("a"),
            _step("b"),
            _step("c"),
        ]
        branches = [
            _branch("a", "@always", "b"),
            _branch("a", DEFAULT_BRANCH, "c"),
        ]
        defn = _defn(steps, branches=branches)
        assert len(defn.branches) == 2
        assert defn.branches[0].source == "a"
        assert defn.branches[0].condition == "@always"
        assert defn.branches[0].target == "b"
        assert defn.branches[1].condition == DEFAULT_BRANCH
