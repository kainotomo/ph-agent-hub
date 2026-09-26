# =============================================================================
# PH Agent Hub — Workflow Definition Identity
# =============================================================================
# Definition identity and edit classification for workflow definitions.
#
# The topology fingerprint of a definition is derived from MAF's own
# ``Workflow.graph_signature``: the definition is rebuilt into a real MAF
# ``Workflow`` with placeholder probe executors, and MAF's canonical signature
# is read back.  Because the fingerprint comes from MAF itself, it cannot drift
# from MAF's definition of structure — there is no hand-maintained field list
# to keep in sync.
#
# Building a signature requires neither a database session nor a model client:
# every `AgentExecutor` is bound to a placeholder agent that is never run.
#
# ---------------------------------------------------------------------------
# Definition identity and edit policy
# ---------------------------------------------------------------------------
# Topology-ness is derived from MAF's ``graph_signature``, never from a
# hand-written field list: a config-only edit is exactly the complement of a
# topology edit, so a new definition field is classified correctly without any
# change to this module.  Renaming a definition key or a step id is not an edit
# at all — both are validation errors, because the key namespaces checkpoints
# and registry lookups and the step id is MAF executor identity.  This policy is
# stated and reported here now; it must be honoured once checkpoint storage
# lands, at which point a topology edit also reports how many paused runs of the
# previous topology can no longer be resumed.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent_framework import AgentExecutor, AgentSession, WorkflowBuilder

from ...core.exceptions import ValidationError
from .definition import WorkflowDefinition


# ---------------------------------------------------------------------------
# Edit policy
# ---------------------------------------------------------------------------

EDIT_POLICY: str = """Workflow definition edit policy
- A definition is identified by its key (the checkpoint namespace and the registry
  key) and by its step ids (MAF executor identity). Neither may be renamed in place:
  renaming is a create, not an edit, and is rejected by validation.
- A config-only edit changes nothing in MAF's graph signature (instructions,
  model_ref, tool_refs, temperature, reasoning_effort, context_mode, type, agent_ref,
  input, on_error, display name, description). It is allowed and applies to
  subsequent runs; paused runs of the previous definition remain resumable.
- A topology edit changes the graph: a step is added, removed, renumbered or renamed,
  or an edge changes. It is allowed, but paused runs of the previous topology can no
  longer be resumed. The edit must report the added and removed step ids, and, once
  checkpoint storage exists, the number of paused runs affected. Paused runs are
  never silently stranded and the edit is never silently blocked."""


# ---------------------------------------------------------------------------
# Topology signature primitives
# ---------------------------------------------------------------------------


class _TopologyProbeAgent:
    """Placeholder agent used only to construct a graph for signature purposes."""

    def __init__(self, name: str) -> None:
        self.id = name
        self.name = name
        self.description: str | None = None

    def create_session(self) -> AgentSession:  # AgentExecutor calls this in __init__
        return AgentSession()

    def run(self, *args: Any, **kwargs: Any) -> Any:  # never executed
        raise NotImplementedError("topology probe agents are never run")


def build_probe_workflow(defn: WorkflowDefinition) -> Any:
    """Build a real MAF ``Workflow`` from probe executors (one per step).

    Mirrors ``engine.build_workflow``'s builder usage exactly: same name,
    description, start executor, output executor, and sequential chain.  No
    database session and no model client are required, and the probe agents are
    never run.

    Args:
        defn: The workflow definition to instantiate.

    Returns:
        A built MAF ``Workflow``.
    """
    executors = [
        AgentExecutor(
            agent=_TopologyProbeAgent(step.id),
            id=step.id,
            context_mode="last_agent",
        )
        for step in defn.steps
    ]

    builder = WorkflowBuilder(
        name=defn.key,
        description=defn.description,
        start_executor=executors[0],
        output_from=[executors[-1]],
    )
    if len(executors) > 1:
        builder.add_chain(executors)

    return builder.build()


def graph_signature(defn: WorkflowDefinition) -> dict[str, Any]:
    """Return MAF's canonical graph signature for a workflow definition.

    The definition is rebuilt into a real MAF ``Workflow`` (see
    :func:`build_probe_workflow`) and MAF's own ``graph_signature`` dict is
    returned unmodified.

    Args:
        defn: The workflow definition to fingerprint.

    Returns:
        MAF's graph signature dict (``start_executor``, ``executors``,
        ``edge_groups``, and nested sub-workflow signatures, if any).
    """
    return build_probe_workflow(defn).graph_signature


def graph_signature_hash(defn: WorkflowDefinition) -> str:
    """Return MAF's canonical graph signature hash for a workflow definition.

    The graph is built exactly once, inside :func:`build_probe_workflow`.

    Args:
        defn: The workflow definition to fingerprint.

    Returns:
        MAF's ``Workflow.graph_signature_hash`` string.
    """
    return build_probe_workflow(defn).graph_signature_hash


# ---------------------------------------------------------------------------
# Edit classification
# ---------------------------------------------------------------------------


class EditKind(str, Enum):
    """Classification of a proposed edit to a workflow definition."""

    UNCHANGED = "unchanged"
    CONFIG_ONLY = "config_only"
    TOPOLOGY = "topology"


@dataclass(frozen=True)
class EditClassification:
    """Result of comparing a proposed definition against the current one."""

    kind: EditKind
    current_signature_hash: str
    proposed_signature_hash: str
    added_step_ids: tuple[str, ...]
    removed_step_ids: tuple[str, ...]


def assert_key_immutable(current_key: str, proposed_key: str) -> None:
    """Assert that a workflow definition key is not being renamed in place.

    Args:
        current_key: The key of the current definition.
        proposed_key: The key of the proposed definition.

    Raises:
        ValidationError: If the two keys differ.
    """
    if current_key == proposed_key:
        return
    raise ValidationError(
        f"Workflow definition key '{current_key}' may not be renamed to "
        f"'{proposed_key}': the key is the checkpoint namespace and the "
        f"registry key, so it is immutable — renaming it is a create, not an edit"
    )


def classify_edit(
    current: WorkflowDefinition,
    proposed: WorkflowDefinition,
) -> EditClassification:
    """Classify a proposed edit to a workflow definition.

    Topology-ness is decided by MAF's own ``graph_signature_hash``, never by a
    hand-maintained field list; the config-only class is exactly the complement
    of the topology class.  A step rename therefore always surfaces as a
    non-empty ``removed_step_ids`` *and* a non-empty ``added_step_ids``, so it
    can never be reported silently.

    Args:
        current: The definition as it exists today.
        proposed: The proposed replacement definition.

    Returns:
        An :class:`EditClassification` describing the edit.

    Raises:
        ValidationError: If the definition key changed. A key rename is a
            create, not an edit.
    """
    assert_key_immutable(current.key, proposed.key)

    current_hash = graph_signature_hash(current)
    proposed_hash = graph_signature_hash(proposed)

    current_ids = {step.id for step in current.steps}
    proposed_ids = {step.id for step in proposed.steps}
    added_step_ids = tuple(sorted(proposed_ids - current_ids))
    removed_step_ids = tuple(sorted(current_ids - proposed_ids))

    if current_hash != proposed_hash:
        kind = EditKind.TOPOLOGY
    elif current.model_dump() != proposed.model_dump():
        kind = EditKind.CONFIG_ONLY
    else:
        kind = EditKind.UNCHANGED

    return EditClassification(
        kind=kind,
        current_signature_hash=current_hash,
        proposed_signature_hash=proposed_hash,
        added_step_ids=added_step_ids,
        removed_step_ids=removed_step_ids,
    )


# ---------------------------------------------------------------------------
# Edit impact report
# ---------------------------------------------------------------------------


def render_edit_report(
    classification: EditClassification,
    *,
    paused_run_count: int | None = None,
) -> str:
    """Render a human-readable impact report for a classified edit.

    Wording is selected from the classification itself, never from a field
    name: the report states whether paused runs of the previous definition
    remain resumable.

    Args:
        classification: The result of comparing a proposed definition against
            the current one.
        paused_run_count: Number of paused runs of the previous definition, when
            known. ``None`` means checkpoint storage does not exist yet, so the
            affected count cannot be reported.

    Returns:
        A one-line, human-readable report.
    """
    if classification.kind is EditKind.UNCHANGED:
        return "No change detected: the proposed definition matches the current one."

    if classification.kind is EditKind.CONFIG_ONLY:
        return (
            "This is a config-only edit: it applies to subsequent runs, and "
            "paused runs of the current definition remain resumable."
        )

    added = ", ".join(classification.added_step_ids) or "none"
    removed = ", ".join(classification.removed_step_ids) or "none"
    if paused_run_count is None:
        impact = (
            "the affected paused-run count will be reported once checkpoint "
            "storage exists"
        )
    else:
        impact = f"{paused_run_count} paused run(s) are affected"

    return (
        f"This is a topology edit: paused runs of the previous topology cannot "
        f"be resumed. Added step ids: {added}; removed step ids: {removed}. "
        f"Impact: {impact}."
    )
