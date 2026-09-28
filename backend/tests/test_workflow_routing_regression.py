# =============================================================================
# PH Agent Hub — Workflow Routing Regression Tests
# =============================================================================
# Regression suite for the conditional routing classification path.
#
# Verifies the end-to-end chain:
#   - WorkflowDefinition.branches → MAF graph edges
#   - routing_fingerprint() ↔ graph_signature_hash() correlation
#   - classify_edit() routing_changed flag
#   - No SwitchCaseEdgeGroup leaks
#
# Pure unit tests — no database, no tenant fixture, no network.
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def _stub_execute_time_reference_validation(monkeypatch):
    """Stub execute-time reference validation.

    These tests build workflow graphs with a mocked DB session and do not
    exercise tenant reference validation (which needs real rows).  Since
    ``build_workflow`` revalidates references on every build, that seam is
    stubbed here so the tests keep testing what they are about.
    """
    monkeypatch.setattr(
        "src.services.workflow_reference_service.assert_definition_references",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "src.services.workflow_definition_resolver.ensure_definition_enabled",
        AsyncMock(return_value=None),
    )


class _ProbeAgent:
    """Minimal agent stub so no model client is constructed."""

    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = "probe"

    def create_session(self):
        from agent_framework import AgentSession
        return AgentSession()


# ---------------------------------------------------------------------------
# Helpers — mirror the pattern used in existing test files
# ---------------------------------------------------------------------------

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
    return _defn_with_branches(steps, key=key, branches=overrides.get("branches", []))


def _defn_with_branches(steps, key: str = "wf_key", branches=None):
    """Build a workflow definition with explicit branches list."""
    payload = {"key": key, "name": "Workflow", "steps": steps}
    if branches is not None:
        payload["branches"] = branches
    return _defn_with_branches_2(payload)


def _defn_with_branches_2(payload):
    """Internal helper to finalise a WorkflowDefinition dict."""
    from src.agents.workflows.definition import WorkflowDefinition
    return WorkflowDefinition(**payload)


def _branch(source, condition, target):
    """Build a minimal valid branch dict."""
    return {"source": source, "condition": condition, "target": target}


# ---------------------------------------------------------------------------
# Test 1: routing_fingerprint — stable, deterministic (T1)
# ---------------------------------------------------------------------------

class TestRoutingFingerprintStable:
    """The routing fingerprint must be a pure function of branches."""

    def test_fingerprint_is_stable_across_builds(self):
        """A definition is parsed twice and fingerprinted both times:
        the result is identical."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import routing_fingerprint

        defn_data = {
            "key": "stable",
            "name": "Stable",
            "steps": [
                _step("a"), _step("b"), _step("c"),
            ],
            "branches": [
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        }
        defn_a = WorkflowDefinition(**defn_data)
        defn_b = WorkflowDefinition(**defn_data)

        fp_a = routing_fingerprint(defn_a)
        fp_b = routing_fingerprint(defn_b)

        assert fp_a == fp_b == (("a", "@always", "b"), ("a", "default", "c"))


# ---------------------------------------------------------------------------
# Test 2: No SwitchCaseEdgeGroup (T2)
# ---------------------------------------------------------------------------

class TestNoSwitchCaseGroup:
    """SwitchCaseEdgeGroup must never appear in built graph signatures."""

    async def test_build_workflow_no_switch_case(self):
        """A definition with branches must not emit SwitchCaseEdgeGroup."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.engine import build_workflow

        defn = WorkflowDefinition(
            key="no-swc",
            name="NoSwitchCase",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _ProbeAgent(step.id),
        ):
            workflow = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        group_types = [g["group_type"] for g in workflow.graph_signature["edge_groups"]]
        assert "SwitchCaseEdgeGroup" not in group_types


# ---------------------------------------------------------------------------
# Test 3: Routing fingerprint matches engine topology (T3)
# ---------------------------------------------------------------------------

class TestRoutingFingerprintMatchesEngine:
    """The fingerprint (source, condition, target) must match engine edge_groups."""

    async def test_fingerprint_matches_engine_edges(self):
        """Each (source, condition_ref, target) triple must appear as an
        edge_group in the built MAF graph.  This ties the data (fingerprint)
        to the code (add_definition_topology)."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.identity import routing_fingerprint

        defn = WorkflowDefinition(
            key="match",
            name="Match",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _ProbeAgent(step.id),
        ):
            workflow = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        # Build lookup: (source, target) → edge_group
        edge_map = {}
        for g in workflow.graph_signature["edge_groups"]:
            for src, tgt in zip(g["sources"], g["targets"]):
                edge_map[(src, tgt)] = g

        fingerprint = routing_fingerprint(defn)
        for source, condition_ref, target in fingerprint:
            assert (source, target) in edge_map, (
                f"Branch ({source}, {condition_ref}, {target}) has no corresponding "
                f"edge_group. Found groups: {list(edge_map.keys())}"
            )


# ---------------------------------------------------------------------------
# Test 4: classify_edit detects routing_changed (T4)
# ---------------------------------------------------------------------------

class TestClassifyRoutingChanged:
    """classify_edit must set routing_changed=True when branches differ."""

    def test_routing_change_classified_as_topology(self):
        """Changing a branch condition must be classified as TOPOLOGY
        with routing_changed=True."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import classify_edit, EditKind

        current = WorkflowDefinition(
            key="rc",
            name="RoutingChanged",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        )
        proposed = WorkflowDefinition(
            key="rc",
            name="RoutingChanged",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@never", "b"),  # changed from @always
                _branch("a", "default", "c"),
            ],
        )

        result = classify_edit(current, proposed)

        assert result.kind is EditKind.TOPOLOGY
        assert result.routing_changed is True


# ---------------------------------------------------------------------------
# Test 5: routing_changed=False when unchanged (T5)
# ---------------------------------------------------------------------------

class TestRoutingChangedFalse:
    """routing_changed must be False when branches are identical."""

    def test_unchanged_branchy_classification(self):
        """An identical branchy definition must have routing_changed=False."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import classify_edit, EditKind

        branches = [
            _branch("a", "@always", "b"),
            _branch("a", "default", "c"),
        ]
        current = WorkflowDefinition(
            key="unch",
            name="Unchanged",
            steps=[_step("a"), _step("b"), _step("c")],
            branches=branches,
        )
        proposed = WorkflowDefinition(
            key="unch",
            name="Unchanged",
            steps=[_step("a"), _step("b"), _step("c")],
            branches=branches,
        )

        result = classify_edit(current, proposed)

        assert result.routing_changed is False


# ---------------------------------------------------------------------------
# Test 6: Fingerprint correlation for sequential chain (T6)
# ---------------------------------------------------------------------------

class TestSequentialChainFingerprint:
    """A definition with no branches must have an empty fingerprint
    and a graph signature that contains only a chain group."""

    def test_no_branches_fingerprint_empty(self):
        """Empty branches → empty fingerprint tuple."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import routing_fingerprint

        defn = WorkflowDefinition(
            key="seq",
            name="Sequential",
            steps=[
                _step("a"), _step("b"),
            ],
        )

        fp = routing_fingerprint(defn)
        assert fp == ()

    async def test_no_branches_chain_group_only(self):
        """No branches → graph signature has only a SingleEdgeGroup chain."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.engine import build_workflow

        defn = WorkflowDefinition(
            key="chain",
            name="Chain",
            steps=[
                _step("a"), _step("b"),
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _ProbeAgent(step.id),
        ):
            workflow = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        edge_groups = workflow.graph_signature["edge_groups"]
        # Should have at least one chain group
        chain_groups = [
            g for g in edge_groups
            if g["group_type"] == "SingleEdgeGroup"
        ]
        assert len(chain_groups) >= 1

        group_types = [g["group_type"] for g in edge_groups]
        assert "SwitchCaseEdgeGroup" not in group_types


# ---------------------------------------------------------------------------
# Test 7: Multi-source branching fingerprint (T7)
# ---------------------------------------------------------------------------

class TestMultiSourceBranching:
    """A definition with multiple branching sources must produce
    a fingerprint that reflects every branch triple."""

    def test_two_branching_sources_fingerprint(self):
        """Two branching sources must produce four fingerprint triples."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import routing_fingerprint

        defn = WorkflowDefinition(
            key="multi",
            name="MultiSource",
            steps=[
                _step("a"), _step("b"), _step("c"), _step("d"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
                _branch("b", "@always", "c"),
                _branch("b", "default", "d"),
            ],
        )

        fp = routing_fingerprint(defn)
        assert len(fp) == 4
        assert fp[0] == ("a", "@always", "b")
        assert fp[1] == ("a", "default", "c")
        assert fp[2] == ("b", "@always", "c")
        assert fp[3] == ("b", "default", "d")

    async def test_two_branching_sources_engine_edges(self):
        """Two branching sources must produce edge_groups that match
        the fingerprint triples."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.identity import routing_fingerprint

        defn = WorkflowDefinition(
            key="multi-eng",
            name="MultiSourceEng",
            steps=[
                _step("a"), _step("b"), _step("c"), _step("d"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
                _branch("b", "@always", "c"),
                _branch("b", "default", "d"),
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _ProbeAgent(step.id),
        ):
            workflow = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        # Build lookup: (source, target) → edge_group
        edge_map = {}
        for g in workflow.graph_signature["edge_groups"]:
            for src, tgt in zip(g["sources"], g["targets"]):
                edge_map[(src, tgt)] = g

        fingerprint = routing_fingerprint(defn)
        for source, condition_ref, target in fingerprint:
            assert (source, target) in edge_map, (
                f"Branch ({source}, {condition_ref}, {target}) missing from "
                f"edge_groups. Found: {list(edge_map.keys())}"
            )


# ---------------------------------------------------------------------------
# Test 8: Authored routing is fingerprinted by MAF (D1.1)
# ---------------------------------------------------------------------------

class TestRoutingFingerprintedByMAF:
    """Different conditions on the same branching edges must produce
    different graph_signature_hash values.

    This matters because SwitchCaseEdgeGroup fails to do exactly this —
    it does not record condition payloads in its fingerprint.  By contrast,
    the graph() API used here calls ``add_edge(condition=fn)`` which MAF
    hashes via the function's ``__name__``, so ``_always`` and ``_never``
    produce distinct hashes.
    """

    def test_different_conditions_different_hashes(self):
        """A 3-step definition with [{source:'a',condition:'@always',target:'b'},
        {source:'a',condition:'default',target:'c'}] must have a different
        graph_signature_hash from the same definition with condition:'@never'
        on the first branch."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import graph_signature_hash

        defn_always = WorkflowDefinition(
            key="fingerprint-test",
            name="FingerprintTest",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        )
        defn_never = WorkflowDefinition(
            key="fingerprint-test",
            name="FingerprintTest",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@never", "b"),  # different condition
                _branch("a", "default", "c"),
            ],
        )

        hash_always = graph_signature_hash(defn_always)
        hash_never = graph_signature_hash(defn_never)

        assert hash_always != hash_never


# ---------------------------------------------------------------------------
# Test 9: Loop over all condition vocabulary (D1.2)
# ---------------------------------------------------------------------------

class TestConditionVocabularyLoop:
    """Every condition in the vocabulary must cause a TOPOLOGY classification
    when swapped for another member on a branchy definition.

    This is the mandated loop: import condition_names from
    src.agents.workflows.conditions; for every name in condition_names()
    paired with a default branch, swapping it for a different member must
    yield classify_edit(...).kind is EditKind.TOPOLOGY, is not
    EditKind.CONFIG_ONLY, and routing_changed is True.
    """

    def test_swap_all_conditions_yields_topology(self):
        """Loop over condition_names(): swap each condition for a different
        member of the set; verify TOPOLOGY kind, not CONFIG_ONLY, and
        routing_changed=True."""
        from src.agents.workflows.conditions import condition_names
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import classify_edit, EditKind

        conditions = condition_names()
        assert len(conditions) >= 2, "Need at least 2 conditions for swap test"

        # Build a base definition with @always as the routing condition
        base_defn = WorkflowDefinition(
            key="loop-test",
            name="LoopTest",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", conditions[0], "b"),
                _branch("a", "default", "c"),
            ],
        )

        for target_cond in conditions:
            if target_cond == conditions[0]:
                continue  # skip self-swap

            swap_defn = WorkflowDefinition(
                key="loop-test",
                name="LoopTest",
                steps=[
                    _step("a"), _step("b"), _step("c"),
                ],
                branches=[
                    _branch("a", target_cond, "b"),
                    _branch("a", "default", "c"),
                ],
            )

            result = classify_edit(base_defn, swap_defn)

            assert result.kind is EditKind.TOPOLOGY, (
                f"Swapping {conditions[0]!r} → {target_cond!r} should be "
                f"TOPOLOGY, got {result.kind!r}"
            )
            assert result.kind is not EditKind.CONFIG_ONLY, (
                f"Swapping {conditions[0]!r} → {target_cond!r} should not be "
                f"CONFIG_ONLY"
            )
            assert result.routing_changed is True, (
                f"Swapping {conditions[0]!r} → {target_cond!r} should set "
                f"routing_changed=True"
            )


# ---------------------------------------------------------------------------
# Test 10: Definition-boundary assertions — no switch/selection fields (D1.3)
# ---------------------------------------------------------------------------

class TestNoSwitchSelectionFields:
    """Definition models must not contain any field whose name contains
    'switch' or 'selection'.

    MAF cannot fingerprint a ``SwitchCaseEdgeGroup``'s condition payload,
    which is documented in the identity module.  Therefore the forbidden
    shape is rejected by construction rather than by validation: neither
    ``WorkflowStep`` nor ``WorkflowBranch`` models contain fields related
    to switch-case selection, and the graph signature for any branching
    definition contains no ``SwitchCaseEdgeGroup``.
    """

    def test_no_switch_or_selection_in_models(self):
        """WorkflowStep.model_fields and WorkflowBranch.model_fields must not
        contain any field name containing 'switch' or 'selection'."""
        from src.agents.workflows.definition import WorkflowStep, WorkflowBranch

        for model_cls in (WorkflowStep, WorkflowBranch):
            field_names = {
                name for name in model_cls.model_fields.keys()
            }
            for name in field_names:
                name_lower = name.lower()
                assert "switch" not in name_lower, (
                    f"{model_cls.__name__} has field {name!r} containing "
                    "'switch'"
                )
                assert "selection" not in name_lower, (
                    f"{model_cls.__name__} has field {name!r} containing "
                    "'selection'"
                )

    def test_no_switch_case_edge_group_in_signature(self):
        """A branching definition's graph signature must not contain any
        edge_group with group_type == 'SwitchCaseEdgeGroup'."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import graph_signature

        defn = WorkflowDefinition(
            key="no-swc-check",
            name="NoSwitchCaseCheck",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        )

        sig = graph_signature(defn)
        group_types = [g["group_type"] for g in sig["edge_groups"]]

        assert "SwitchCaseEdgeGroup" not in group_types, (
            f"Found SwitchCaseEdgeGroup in edge_groups: {group_types}"
        )


# ---------------------------------------------------------------------------
# Test 11: Config-only edit on branchy definition (D1.4)
# ---------------------------------------------------------------------------

class TestConfigOnlyOnBranchyDefinition:
    """Changing only a step's instructions on a branchy definition must
    be classified as CONFIG_ONLY, not TOPOLOGY, and must not mention
    'routing conditions changed' in the report."""

    def test_instruction_change_on_branchy_def(self):
        """Change only a step's instructions on a definition with branches;
        assert CONFIG_ONLY, routing_changed=False, and the report does not
        contain 'routing conditions changed'."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import (
            classify_edit,
            render_edit_report,
            EditKind,
        )

        current = WorkflowDefinition(
            key="config-test",
            name="ConfigTest",
            steps=[
                _step("a", instructions="First"),
                _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        )
        proposed = WorkflowDefinition(
            key="config-test",
            name="ConfigTest",
            steps=[
                _step("a", instructions="Second"),  # only change
                _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        )

        result = classify_edit(current, proposed)

        assert result.kind is EditKind.CONFIG_ONLY, (
            f"Instruction-only change should be CONFIG_ONLY, got {result.kind!r}"
        )
        assert result.routing_changed is False, (
            "routing_changed must be False for instruction-only change"
        )

        report = render_edit_report(result)

        assert "routing conditions changed" not in report, (
            "CONFIG_ONLY report must not mention 'routing conditions changed'"
        )


# ---------------------------------------------------------------------------
# Test 12: Probe/real parity for a branching definition (D1.5)
# ---------------------------------------------------------------------------

class TestProbeRealParityBranching:
    """This deliberately duplicates
    tests/test_workflow_probe_parity.py so a change to either file is
    caught.  For a branching definition, build_workflow and
    build_probe_workflow must produce identical executors, hashes, and
    edge_groups.

    The test patches ``resolve_model`` and ``_build_agent_for_step`` as
    tests/test_workflow_engine_routing.py does, so build_workflow works
    without a real database or model client.
    """

    async def test_probe_matches_real_build_branching(self):
        """Build a branching definition via both build_probe_workflow and
        build_workflow (with mocked model/agent); executors, hash, and
        edge_groups must be identical."""
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.engine import build_workflow
        from src.agents.workflows.identity import build_probe_workflow

        defn = WorkflowDefinition(
            key="parity-branch",
            name="ParityBranch",
            steps=[
                _step("a"), _step("b"), _step("c"),
            ],
            branches=[
                _branch("a", "@always", "b"),
                _branch("a", "default", "c"),
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model",
            new=AsyncMock(return_value=MagicMock(id="model-1")),
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=lambda step, **kwargs: _ProbeAgent(step.id),
        ):
            real = await build_workflow(defn=defn, db=MagicMock(), tenant_id="T1")

        probe = build_probe_workflow(defn)

        assert list(real.executors) == list(probe.executors), (
            f"Executors differ: real={real.executors}, probe={probe.executors}"
        )
        assert real.graph_signature_hash == probe.graph_signature_hash, (
            f"Hashes differ: real={real.graph_signature_hash}, "
            f"probe={probe.graph_signature_hash}"
        )
        assert real.graph_signature["edge_groups"] == probe.graph_signature[
            "edge_groups"
        ], (
            f"edge_groups differ: real={real.graph_signature['edge_groups']}, "
            f"probe={probe.graph_signature['edge_groups']}"
        )


# ---------------------------------------------------------------------------
# Test 13: Unknown condition rejected at definition boundary (D1.6)
# ---------------------------------------------------------------------------

class TestUnknownConditionRejected:
    """An unknown condition reference must be rejected when constructing
    a WorkflowDefinition."""

    def test_unknown_condition_raises(self):
        """A WorkflowDefinition with condition='@nope' (paired with a
        'default' branch) must raise ValueError; the message must contain
        '@nope' and '@always'."""
        from src.agents.workflows.definition import WorkflowDefinition

        with pytest.raises(ValueError) as exc_info:
            WorkflowDefinition(
                key="bad-condition",
                name="BadCondition",
                steps=[
                    _step("a"), _step("b"),
                ],
                branches=[
                    _branch("a", "@nope", "b"),
                    _branch("a", "default", "b"),
                ],
            )

        error_msg = str(exc_info.value)
        assert "@nope" in error_msg, (
            f"Error must mention '@nope': {error_msg}"
        )
        assert "@always" in error_msg, (
            f"Error must list known conditions including '@always': {error_msg}"
        )


# ---------------------------------------------------------------------------
# Test 14: Every vocabulary member round-trips (D1.7)
# ---------------------------------------------------------------------------

class TestVocabularyRoundTrip:
    """Every condition in condition_names() must round-trip: a definition
    using it plus a default branch constructs, has a non-empty
    routing_fingerprint, and classifies as TOPOLOGY when swapped to
    @never."""

    def test_all_conditions_round_trip(self):
        """For each condition name in condition_names(): construct a
        definition using it with a default branch, verify non-empty
        routing_fingerprint, and verify that swapping to @never is
        classified as TOPOLOGY."""
        from src.agents.workflows.conditions import condition_names
        from src.agents.workflows.definition import WorkflowDefinition
        from src.agents.workflows.identity import (
            classify_edit,
            EditKind,
            routing_fingerprint,
        )

        for cond in condition_names():
            if cond == "@never":
                continue  # swapping @never → @never is self-swap (UNCHANGED)
            defn_orig = WorkflowDefinition(
                key=f"rt-{cond}",
                name="RoundTrip",
                steps=[
                    _step("a"), _step("b"), _step("c"),
                ],
                branches=[
                    _branch("a", cond, "b"),
                    _branch("a", "default", "c"),
                ],
            )

            fp = routing_fingerprint(defn_orig)
            assert len(fp) > 0, (
                f"Condition {cond!r} should produce non-empty routing_fingerprint"
            )

            defn_swapped = WorkflowDefinition(
                key=f"rt-{cond}",
                name="RoundTrip",
                steps=[
                    _step("a"), _step("b"), _step("c"),
                ],
                branches=[
                    _branch("a", "@never", "b"),  # swapped to @never
                    _branch("a", "default", "c"),
                ],
            )

            result = classify_edit(defn_orig, defn_swapped)

            assert result.kind is EditKind.TOPOLOGY, (
                f"Swapping {cond!r} → @never should be TOPOLOGY, "
                f"got {result.kind!r}"
            )
