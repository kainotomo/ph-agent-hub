# =============================================================================
# PH Agent Hub — Workflow Checkpoint Service Tests
# =============================================================================
# Tenant-scoped and session-scoped tests for the checkpoint lookup service.
# =============================================================================

import uuid

import pytest
from sqlalchemy import select

from agent_framework import WorkflowCheckpoint
from src.agents.workflows.definition import WorkflowDefinition, WorkflowStep
from src.agents.workflows.identity import graph_signature_hash
from src.core.exceptions import NotFoundError, ValidationError
from src.db.orm.workflow_checkpoints import WorkflowCheckpointRecord
from src.services.workflow_checkpoint_service import (
    RUN_STATE_COMPLETED,
    assert_resumable,
    checkpoint_namespace,
    extract_state_snapshot,
    latest_resumable_checkpoint,
    list_session_checkpoints,
    mark_checkpoint_state,
)


pytestmark = [pytest.mark.integration]


def _make_checkpoint(
    checkpoint_id: str | None = None,
    tenant_id: str | None = None,
    workflow_name: str = "test:workflow",
    session_id: str | None = None,
    run_state: str | None = None,
    payload: str = "test-payload",
) -> WorkflowCheckpointRecord:
    """Build an unsaved checkpoint row."""
    return WorkflowCheckpointRecord(
        id=checkpoint_id or str(uuid.uuid4()),
        tenant_id=tenant_id,
        workflow_name=workflow_name,
        graph_signature_hash="abc123",
        session_id=session_id,
        payload=payload,
        run_state=run_state,
    )


# ---------------------------------------------------------------------------
# Test 1: checkpoint_namespace_is_tenant_prefixed
# ---------------------------------------------------------------------------

async def test_checkpoint_namespace_is_tenant_prefixed():
    """checkpoint_namespace returns the tenant-prefixed key."""
    result = checkpoint_namespace("T1", "research")
    assert result == "T1:research"


# ---------------------------------------------------------------------------
# Test 2: latest_resumable_is_tenant_scoped
# ---------------------------------------------------------------------------

async def test_latest_resumable_is_tenant_scoped(db_session, test_tenant, second_tenant):
    """A tenant's lookup returns only its own checkpoint rows."""
    cp_a = _make_checkpoint(
        tenant_id=test_tenant.id, session_id="S1", workflow_name="t:research"
    )
    cp_b = _make_checkpoint(
        tenant_id=second_tenant.id, session_id="S1", workflow_name="t:research"
    )
    db_session.add(cp_a)
    db_session.add(cp_b)
    await db_session.flush()

    result = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    assert result is not None
    assert result.id == cp_a.id


# ---------------------------------------------------------------------------
# Test 3: latest_resumable_is_session_scoped
# ---------------------------------------------------------------------------

async def test_latest_resumable_is_session_scoped(db_session, test_tenant):
    """A session_id filter isolates rows per session."""
    cp_s1 = _make_checkpoint(
        tenant_id=test_tenant.id, session_id="S1", workflow_name="t:research"
    )
    cp_s2 = _make_checkpoint(
        tenant_id=test_tenant.id, session_id="S2", workflow_name="t:research"
    )
    db_session.add(cp_s1)
    db_session.add(cp_s2)
    await db_session.flush()

    result_s1 = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    result_s2 = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S2"
    )

    assert result_s1 is not None
    assert result_s1.id == cp_s1.id
    assert result_s2 is not None
    assert result_s2.id == cp_s2.id


# ---------------------------------------------------------------------------
# Test 4: latest_resumable_skips_completed
# ---------------------------------------------------------------------------

async def test_latest_resumable_skips_completed(db_session, test_tenant):
    """The lookup skips COMPLETED checkpoints and returns the older one."""
    cp_older = _make_checkpoint(
        checkpoint_id="cp-older",
        tenant_id=test_tenant.id,
        session_id="S1",
        workflow_name="t:research",
    )
    cp_newer = _make_checkpoint(
        checkpoint_id="cp-newer",
        tenant_id=test_tenant.id,
        session_id="S1",
        workflow_name="t:research",
    )
    db_session.add(cp_older)
    db_session.add(cp_newer)
    await db_session.flush()

    # Mark the newer checkpoint as completed
    await mark_checkpoint_state(
        db_session, test_tenant.id, "cp-newer", RUN_STATE_COMPLETED
    )

    result = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    assert result is not None
    assert result.id == "cp-older"


# ---------------------------------------------------------------------------
# Test 5: latest_resumable_returns_none_when_absent
# ---------------------------------------------------------------------------

async def test_latest_resumable_returns_none_when_absent(db_session, test_tenant):
    """Lookup for an unknown session returns None."""
    result = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "nonexistent"
    )
    assert result is None


# ---------------------------------------------------------------------------
# Test 6: list_session_checkpoints_ordered
# ---------------------------------------------------------------------------

async def test_list_session_checkpoints_ordered(db_session, test_tenant, second_tenant):
    """Returns all rows for a session, ordered by created_at, excluding other tenants.

    The created_at values deliberately contradict id order so this proves that
    created_at — not id — is the primary sort key.  (MySQL ``created_at`` is a
    ``DATETIME`` with no fractional seconds, so rows saved in the same second
    would tie and fall back to id order.)
    """
    from datetime import datetime, timedelta, timezone

    cp_1 = _make_checkpoint(
        checkpoint_id="cp-1",
        tenant_id=test_tenant.id,
        session_id="S1",
        workflow_name="t:research",
    )
    cp_2 = _make_checkpoint(
        checkpoint_id="cp-2",
        tenant_id=test_tenant.id,
        session_id="S1",
        workflow_name="t:research",
    )
    cp_foreign = _make_checkpoint(
        checkpoint_id="cp-foreign",
        tenant_id=second_tenant.id,
        session_id="S1",
        workflow_name="t:research",
    )

    # Give cp_2 an OLDER created_at than cp_1 so the order contradicts id order.
    # cp-2 is 2 seconds ago (older), cp-1 is 1 second ago (newer).
    # ASC ordering puts older first → cp-2 before cp-1.
    now = datetime.now(timezone.utc)
    cp_1.created_at = now - timedelta(seconds=1)
    cp_2.created_at = now - timedelta(seconds=2)

    db_session.add(cp_1)
    db_session.add(cp_2)
    db_session.add(cp_foreign)
    await db_session.flush()

    rows = await list_session_checkpoints(
        db_session, test_tenant.id, "S1"
    )

    # Must not include the foreign tenant's row
    ids = [r.id for r in rows]
    assert "cp-foreign" not in ids
    assert len(rows) == 2

    # The older row (cp-2) must come first, even though "cp-2" > "cp-1" lexicographically.
    assert rows[0].id == "cp-2"
    assert rows[1].id == "cp-1"


# ---------------------------------------------------------------------------
# Test 7: mark_checkpoint_state
# ---------------------------------------------------------------------------

async def test_mark_checkpoint_state(db_session, test_tenant, second_tenant):
    """Marking an own checkpoint sets run_state; marking another tenant's raises NotFoundError."""
    cp_own = _make_checkpoint(
        checkpoint_id="cp-own",
        tenant_id=test_tenant.id,
        session_id="S1",
        workflow_name="t:research",
    )
    cp_foreign = _make_checkpoint(
        checkpoint_id="cp-foreign",
        tenant_id=second_tenant.id,
        session_id="S1",
        workflow_name="t:research",
    )
    db_session.add(cp_own)
    db_session.add(cp_foreign)
    await db_session.flush()

    # Mark own checkpoint
    await mark_checkpoint_state(
        db_session, test_tenant.id, "cp-own", RUN_STATE_COMPLETED
    )

    # Re-read to prove the change
    result = await db_session.execute(
        select(WorkflowCheckpointRecord).where(
            WorkflowCheckpointRecord.id == "cp-own"
        )
    )
    row = result.scalar_one()
    assert row.run_state == RUN_STATE_COMPLETED

    # Marking another tenant's checkpoint raises NotFoundError
    with pytest.raises(NotFoundError, match=r"Checkpoint 'cp-foreign' not found"):
        await mark_checkpoint_state(
            db_session, test_tenant.id, "cp-foreign", RUN_STATE_COMPLETED
        )

    # The foreign row must remain unchanged
    result = await db_session.execute(
        select(WorkflowCheckpointRecord).where(
            WorkflowCheckpointRecord.id == "cp-foreign"
        )
    )
    foreign_row = result.scalar_one()
    assert foreign_row.run_state is None


# ---------------------------------------------------------------------------
# Test 8: extract_state_snapshot_reads_dsh_state
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_extract_state_snapshot_reads_dsh_state():
    """Recover the run-wide state from a MAF WorkflowCheckpoint."""
    cp = WorkflowCheckpoint(
        workflow_name="t:wf",
        graph_signature_hash="abc",
        state={
            "_executor_state": {
                "a": {
                    "agent_session": {
                        "state": {
                            "outputs": {"a": "X"},
                            "user_message": "HI",
                        }
                    }
                }
            }
        },
    )
    result = extract_state_snapshot(cp)
    assert result == {"outputs": {"a": "X"}, "user_message": "HI"}


# ---------------------------------------------------------------------------
# Test 9: extract_state_snapshot_returns_empty_when_absent
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_extract_state_snapshot_returns_empty_when_absent():
    """Return {} when the checkpoint has no state, or when no executor has outputs."""
    cp_empty = WorkflowCheckpoint(
        workflow_name="t:wf",
        graph_signature_hash="abc",
        state={},
    )
    assert extract_state_snapshot(cp_empty) == {}

    cp_no_outputs = WorkflowCheckpoint(
        workflow_name="t:wf",
        graph_signature_hash="abc",
        state={
            "_executor_state": {
                "a": {
                    "agent_session": {
                        "state": {"user_message": "HI"}
                    }
                }
            }
        },
    )
    assert extract_state_snapshot(cp_no_outputs) == {}


# ---------------------------------------------------------------------------
# Test 10: assert_resumable_accepts_matching_signature
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assert_resumable_accepts_matching_signature(db_session, test_tenant):
    """No error when the checkpoint signature matches the definition."""
    step = WorkflowStep(
        id="s1",
        name="Step 1",
        type="inline",
        instructions="do it",
    )
    defn = WorkflowDefinition(
        key="t:wf",
        name="Test Workflow",
        steps=[step],
    )
    expected_hash = graph_signature_hash(defn)
    record = WorkflowCheckpointRecord(
        id="c1",
        tenant_id=test_tenant.id,
        workflow_name="t:wf",
        graph_signature_hash=expected_hash,
        payload="{}",
    )
    # Should not raise
    assert_resumable(test_tenant.id, record, defn)


# ---------------------------------------------------------------------------
# Test 11: assert_resumable_rejects_signature_mismatch
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assert_resumable_rejects_signature_mismatch(db_session, test_tenant):
    """Raise when the stored signature differs from the definition."""
    step = WorkflowStep(
        id="s1",
        name="Step 1",
        type="inline",
        instructions="do it",
    )
    defn = WorkflowDefinition(
        key="t:wf",
        name="Test Workflow",
        steps=[step],
    )
    record = WorkflowCheckpointRecord(
        id="c1",
        tenant_id=test_tenant.id,
        workflow_name="t:wf",
        graph_signature_hash="deadbeef",
        payload="{}",
    )
    with pytest.raises(ValidationError):
        assert_resumable(test_tenant.id, record, defn)


# ---------------------------------------------------------------------------
# Test 12: assert_resumable_rejects_foreign_tenant
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_assert_resumable_rejects_foreign_tenant(db_session, test_tenant, second_tenant):
    """Raise when the checkpoint belongs to a different tenant."""
    step = WorkflowStep(
        id="s1",
        name="Step 1",
        type="inline",
        instructions="do it",
    )
    defn = WorkflowDefinition(
        key="t:wf",
        name="Test Workflow",
        steps=[step],
    )
    expected_hash = graph_signature_hash(defn)
    record = WorkflowCheckpointRecord(
        id="c1",
        tenant_id=second_tenant.id,
        workflow_name="t:wf",
        graph_signature_hash=expected_hash,
        payload="{}",
    )
    with pytest.raises(ValidationError):
        assert_resumable(test_tenant.id, record, defn)


# ---------------------------------------------------------------------------
# Test 13: latest_resumable_breaks_created_at_ties_by_iteration_count
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_latest_resumable_breaks_created_at_ties_by_iteration_count(
    db_session, test_tenant
):
    """When created_at ties at whole-second precision, iteration_count wins."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    # id="zzz-low", iteration=1 would be picked by id DESC alone
    cp_low = WorkflowCheckpointRecord(
        id="zzz-low",
        tenant_id=test_tenant.id,
        workflow_name="t:research",
        graph_signature_hash="abc123",
        session_id="S1",
        iteration_count=1,
        created_at=now,
        payload="{}",
    )
    # id="aaa-high", iteration=5 is the correct choice
    cp_high = WorkflowCheckpointRecord(
        id="aaa-high",
        tenant_id=test_tenant.id,
        workflow_name="t:research",
        graph_signature_hash="abc123",
        session_id="S1",
        iteration_count=5,
        created_at=now,
        payload="{}",
    )
    db_session.add(cp_low)
    db_session.add(cp_high)
    await db_session.flush()

    result = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    assert result.id == "aaa-high"
