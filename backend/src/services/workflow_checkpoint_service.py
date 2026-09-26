# =============================================================================
# PH Agent Hub — Workflow Checkpoint Service (Session-Scoped)
# =============================================================================
# Tenant-scoped lookup and mutation for workflow checkpoint run state.
#
# MAF keys checkpoints only on ``(workflow_name, checkpoint_id)`` and has
# **no session dimension**, so DSH stores the session/message mapping itself
# in its own table; MAF's ``get_latest(workflow_name=...)`` is grouped only
# by workflow name and therefore must never be used to choose which run to
# resume; this module is the authoritative, session-scoped lookup.
#
# Every function here is tenant-scoped on every query — there is no code path
# that resolves or mutates a checkpoint without a tenant filter.
# =============================================================================

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError, ValidationError
from ..agents.workflows.definition import WorkflowDefinition
from ..agents.workflows.identity import graph_signature_hash
from ..db.orm.workflow_checkpoints import WorkflowCheckpointRecord

logger = logging.getLogger(__name__)

RUN_STATE_COMPLETED: str = "COMPLETED"


def checkpoint_namespace(tenant_id: str, key: str) -> str:
    """Return the tenant-namespaced MAF workflow name for a definition key.

    Returns ``f"{tenant_id}:{key}"`` — the exact string passed as
    ``WorkflowBuilder(name=...)``.  This namespaces MAF's checkpoint
    grouping so two tenants sharing a definition key cannot see each
    other's checkpoints, and it does not affect ``graph_signature_hash``
    (which is derived from topology only).
    """
    return f"{tenant_id}:{key}"


async def latest_resumable_checkpoint(
    db: AsyncSession, tenant_id: str, session_id: str
) -> WorkflowCheckpointRecord | None:
    """Return the most recent non-completed checkpoint for a session.

    Selects rows where ``tenant_id`` matches, ``session_id`` matches, and
    the row is **not** completed — expressed as
    ``run_state`` is NULL *or* ``run_state != "COMPLETED"`` so that
    never-marked checkpoints still qualify.  Ordered by ``created_at``
    descending then ``id`` descending, limited to 1.

    Returns ``None`` when no resumable checkpoint exists.
    """
    result = await db.execute(
        select(WorkflowCheckpointRecord)
        .where(
            WorkflowCheckpointRecord.tenant_id == tenant_id,
            WorkflowCheckpointRecord.session_id == session_id,
            or_(
                WorkflowCheckpointRecord.run_state.is_(None),
                WorkflowCheckpointRecord.run_state != RUN_STATE_COMPLETED,
            ),
        )
        .order_by(
            # `created_at` is a MySQL `DATETIME` without fractional seconds
            # so fast multi-step workflows produce ties; `iteration_count`
            # advances with each superstep and is the correct tie-break
            # (MAF notes it is not unique — human-in-the-loop flows may
            # produce multiple checkpoints at the same iteration — hence
            # the `id` fallback).
            WorkflowCheckpointRecord.created_at.desc(),
            WorkflowCheckpointRecord.iteration_count.desc(),
            WorkflowCheckpointRecord.id.desc(),
        )
        .limit(1)
    )
    return result.scalars().first()


async def list_session_checkpoints(
    db: AsyncSession, tenant_id: str, session_id: str
) -> list[WorkflowCheckpointRecord]:
    """Return every checkpoint for a session, ordered by creation time.

    No run_state filter is applied — all checkpoints (completed and
    incomplete) are returned.  Ordered by ``created_at`` ascending then
    ``id`` ascending.
    """
    result = await db.execute(
        select(WorkflowCheckpointRecord)
        .where(
            WorkflowCheckpointRecord.tenant_id == tenant_id,
            WorkflowCheckpointRecord.session_id == session_id,
        )
        .order_by(
            WorkflowCheckpointRecord.created_at.asc(),
            WorkflowCheckpointRecord.id.asc(),
        )
    )
    return list(result.scalars().all())


async def mark_checkpoint_state(
    db: AsyncSession, tenant_id: str, checkpoint_id: str, run_state: str
) -> None:
    """Mark a checkpoint's run state for a tenant.

    Updates the ``run_state`` column for the row matching both
    ``checkpoint_id`` and ``tenant_id``.  A row belonging to another
    tenant must **not** be updated — if the reported row count is not
    exactly 1, raises ``NotFoundError``.
    """
    result = await db.execute(
        update(WorkflowCheckpointRecord)
        .where(
            WorkflowCheckpointRecord.id == checkpoint_id,
            WorkflowCheckpointRecord.tenant_id == tenant_id,
        )
        .values(run_state=run_state)
    )
    await db.commit()

    if result.rowcount != 1:
        raise NotFoundError(
            f"Checkpoint '{checkpoint_id}' not found for this tenant"
        )


def extract_state_snapshot(checkpoint: Any) -> dict[str, Any]:
    """Recover the run-wide cross-step state from a workflow checkpoint.

    DSH writes the run-wide ``outputs`` / ``user_message`` dict into the MAF
    session state after each step (see
    :func:`~.agents.workflows.executors._sync_state_to_session`), so every
    executor that has already run carries a snapshot of that state in its own
    saved session.  On a resume the next step to run has an empty session of
    its own — so the snapshot must be recovered from whichever producer did
    run.  All executed checkpoints carry the same snapshot, so returning the
    first match is correct.
    """
    state = getattr(checkpoint, "state", None)
    if not isinstance(state, dict):
        return {}

    executor_state = state.get("_executor_state")
    if not isinstance(executor_state, dict):
        return {}

    for _step_id, payload in executor_state.items():
        if not isinstance(payload, dict):
            continue
        session = payload.get("agent_session")
        if not isinstance(session, dict):
            continue
        snapshot = session.get("state")
        if isinstance(snapshot, dict) and snapshot.get("outputs"):
            return dict(snapshot)

    return {}


def assert_resumable(
    tenant_id: str, record: WorkflowCheckpointRecord, defn: WorkflowDefinition
) -> None:
    """Raise ValidationError if this checkpoint cannot be resumed for this tenant.

    Verifies two things: (1) the record belongs to the caller's tenant, and
    (2) the workflow topology (graph signature) has not changed since the
    checkpoint was written.  Does **not** require a database session — it is
    a pure, synchronous validation.
    """
    if record.tenant_id != tenant_id:
        raise ValidationError(
            f"Checkpoint '{record.id}' belongs to a different tenant"
        )

    expected = graph_signature_hash(defn)
    if record.graph_signature_hash != expected:
        raise ValidationError(
            f"Workflow definition '{defn.key}' topology has changed since the "
            "checkpoint was written, so paused runs of the previous topology "
            "cannot be resumed"
        )
