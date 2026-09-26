# =============================================================================
# PH Agent Hub — Workflow Checkpoint Storage Tests (MariaDB)
# =============================================================================
# Integration tests for ``MariaDBCheckpointStorage`` using the real
# ``workflow_checkpoints`` table via the ``db_session`` fixture.
# =============================================================================

from datetime import datetime, timedelta, timezone

import pytest

from agent_framework import WorkflowCheckpoint, WorkflowCheckpointException
from src.agents.workflows.checkpoint_storage import (
    MariaDBCheckpointStorage,
    delete_expired_checkpoints,
    enforce_checkpoint_retention,
)


# ---------------------------------------------------------------------------
# Fixture wiring helper — prevents enforcement functions from closing the
# fixture's session (they call ``session.close()`` after committing).
# Without this wrapper the enforcement function would close the fixture
# session, causing a hang or error when the fixture tries to roll back.
# ---------------------------------------------------------------------------

class _NoCloseSession:
    """Async-context-manager shim that forwards all calls but never closes."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        # Do NOT close the underlying session — the fixture owns it.
        pass

    def __getattr__(self, name):
        return getattr(self._session, name)


@pytest.fixture
def storage(db_session, test_tenant):
    """Build checkpoint storage wired to the test session and tenant."""
    return MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_factory=lambda: db_session,
    )


@pytest.mark.integration
async def test_save_then_load_round_trips(storage):
    """A saved checkpoint round-trips: save → load returns matching data."""
    cp = WorkflowCheckpoint(
        workflow_name="t:research",
        graph_signature_hash="abc123",
        iteration_count=2,
    )

    saved_id = await storage.save(cp)
    loaded = await storage.load(saved_id)

    assert loaded.workflow_name == "t:research"
    assert loaded.graph_signature_hash == "abc123"
    assert loaded.iteration_count == 2
    assert loaded.checkpoint_id == saved_id


@pytest.mark.integration
async def test_load_unknown_id_raises(storage):
    """Loading a non-existent checkpoint id raises WorkflowCheckpointException."""
    with pytest.raises(WorkflowCheckpointException, match=r"No checkpoint found with ID does-not-exist"):
        await storage.load("does-not-exist")


@pytest.mark.integration
async def test_cross_tenant_load_is_denied(db_session, test_tenant, second_tenant):
    """A checkpoint saved under one tenant is invisible to another tenant's storage."""
    storage_a = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_factory=lambda: db_session,
    )
    storage_b = MariaDBCheckpointStorage(
        tenant_id=second_tenant.id,
        session_factory=lambda: db_session,
    )

    cp = WorkflowCheckpoint(
        workflow_name="shared:research",
        graph_signature_hash="abc123",
        iteration_count=2,
    )

    saved_id = await storage_a.save(cp)

    # storage_b (second tenant) must NOT be able to load this checkpoint
    with pytest.raises(WorkflowCheckpointException, match=r"No checkpoint found with ID"):
        await storage_b.load(saved_id)


@pytest.mark.integration
async def test_save_is_idempotent_for_same_checkpoint_id(storage):
    """Calling save twice with the same checkpoint object succeeds and load
    still returns it (proves the upsert, not a duplicate-key crash)."""
    cp = WorkflowCheckpoint(
        workflow_name="t:research",
        graph_signature_hash="def456",
        iteration_count=5,
    )

    id1 = await storage.save(cp)
    id2 = await storage.save(cp)

    assert id1 == id2

    loaded = await storage.load(id1)
    assert loaded.workflow_name == "t:research"
    assert loaded.graph_signature_hash == "def456"
    assert loaded.iteration_count == 5


# ---------------------------------------------------------------------------
# New tests for list_checkpoints, list_checkpoint_ids, get_latest, delete
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_checkpoints_returns_saved_checkpoints(db_session, storage, test_tenant):
    """list_checkpoints returns all saved checkpoints for the workflow, ordered
    by created_at ASC, id ASC.  The ids are deliberately reverse-sorted so the
    test would FAIL if ordering ever fell back to ``id`` — it proves that
    ``created_at`` is the primary sort key.

    MySQL ``created_at`` is a ``DATETIME`` with no fractional seconds, so two
    rows saved in the same second would tie on ``created_at`` and fall through
    to ``id ASC``.  We use distinct explicit timestamps to avoid that tie.
    """
    from src.db.orm.workflow_checkpoints import WorkflowCheckpointRecord
    from src.agents.workflows.checkpoint_codec import encode_checkpoint

    now = datetime.now(timezone.utc)

    # "zzzz-old" sorts AFTER "aaaa-new" by id, but its created_at is older,
    # so if the query ever falls back to id-order only, the assertion fails.
    cp1 = WorkflowCheckpoint(
        workflow_name="t:list", graph_signature_hash="aaa", iteration_count=1,
        checkpoint_id="zzzz-old",
    )
    cp2 = WorkflowCheckpoint(
        workflow_name="t:list", graph_signature_hash="bbb", iteration_count=2,
        checkpoint_id="aaaa-new",
    )

    db_session.add(WorkflowCheckpointRecord(
        id="zzzz-old",
        tenant_id=test_tenant.id,
        workflow_name="t:list",
        graph_signature_hash="aaa",
        payload=encode_checkpoint(cp1),
        created_at=now - timedelta(seconds=2),
    ))
    db_session.add(WorkflowCheckpointRecord(
        id="aaaa-new",
        tenant_id=test_tenant.id,
        workflow_name="t:list",
        graph_signature_hash="bbb",
        payload=encode_checkpoint(cp2),
        created_at=now - timedelta(seconds=1),
    ))
    await db_session.flush()

    items = await storage.list_checkpoints(workflow_name="t:list")

    assert len(items) == 2
    # older row must come first (proves created_at is primary sort key)
    assert [c.checkpoint_id for c in items] == ["zzzz-old", "aaaa-new"]


@pytest.mark.integration
async def test_list_checkpoint_ids_returns_only_ids(storage):
    """list_checkpoint_ids returns checkpoint id strings without decoding."""
    cp = WorkflowCheckpoint(
        workflow_name="t:ids",
        graph_signature_hash="xyz",
        iteration_count=3,
    )
    saved_id = await storage.save(cp)

    ids = await storage.list_checkpoint_ids(workflow_name="t:ids")

    assert len(ids) == 1
    assert ids[0] == saved_id


@pytest.mark.integration
async def test_get_latest_returns_most_recent(storage):
    """get_latest returns the most recent checkpoint based on lineage."""
    cp1 = WorkflowCheckpoint(
        workflow_name="t:latest",
        graph_signature_hash="init",
        iteration_count=0,
    )
    cp2 = WorkflowCheckpoint(
        workflow_name="t:latest",
        graph_signature_hash="next",
        iteration_count=1,
    )
    cp3 = WorkflowCheckpoint(
        workflow_name="t:latest",
        graph_signature_hash="final",
        iteration_count=2,
    )

    await storage.save(cp1)
    await storage.save(cp2)
    await storage.save(cp3)

    latest = await storage.get_latest(workflow_name="t:latest")

    assert latest is not None
    assert latest.iteration_count == 2
    assert latest.graph_signature_hash == "final"


@pytest.mark.integration
async def test_get_latest_empty_returns_none(storage):
    """get_latest returns None when no checkpoints exist."""
    result = await storage.get_latest(workflow_name="t:nonexistent")
    assert result is None


@pytest.mark.integration
async def test_delete_removes_checkpoint(storage):
    """Deleting a checkpoint removes it and returns True."""
    cp = WorkflowCheckpoint(
        workflow_name="t:delete",
        graph_signature_hash="delme",
        iteration_count=1,
    )
    cid = await storage.save(cp)

    assert await storage.delete(cid) is True

    # A following load must fail
    with pytest.raises(WorkflowCheckpointException, match=r"No checkpoint found with ID"):
        await storage.load(cid)


@pytest.mark.integration
async def test_delete_foreign_tenant_returns_false(db_session, test_tenant, second_tenant):
    """Deleting a checkpoint under another tenant must return False."""
    storage_a = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_factory=lambda: db_session,
    )
    storage_b = MariaDBCheckpointStorage(
        tenant_id=second_tenant.id,
        session_factory=lambda: db_session,
    )

    cp = WorkflowCheckpoint(
        workflow_name="t:foreign",
        graph_signature_hash="secret",
        iteration_count=1,
    )
    cid = await storage_a.save(cp)

    assert await storage_b.delete(cid) is False

    # The checkpoint must still exist under storage_a
    loaded = await storage_a.load(cid)
    assert loaded.graph_signature_hash == "secret"


# ---------------------------------------------------------------------------
# New tests for checkpoint retention (A11)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_delete_expired_checkpoints_removes_only_past_expiry(db_session, test_tenant):
    """delete_expired_checkpoints removes only rows whose expires_at is in the past."""
    import uuid

    from src.db.orm.workflow_checkpoints import WorkflowCheckpointRecord
    from sqlalchemy import select

    past_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
    future_expiry = datetime.now(timezone.utc) + timedelta(days=7)

    past_id = uuid.uuid4().hex
    future_id = uuid.uuid4().hex

    # Insert rows via the fixture session (commit is monkey-patched to flush).
    db_session.add(WorkflowCheckpointRecord(
        id=past_id,
        tenant_id=test_tenant.id,
        workflow_name="t:retention",
        graph_signature_hash="abc123",
        payload="{}",
        expires_at=past_expiry,
    ))
    db_session.add(WorkflowCheckpointRecord(
        id=future_id,
        tenant_id=test_tenant.id,
        workflow_name="t:retention",
        graph_signature_hash="def456",
        payload="{}",
        expires_at=future_expiry,
    ))
    await db_session.commit()

    # The enforcement function uses its own real session (via session_factory)
    # but the factory returns the fixture session wrapped to prevent closing.
    factory = lambda: _NoCloseSession(db_session)

    count = await delete_expired_checkpoints(session_factory=factory)
    assert count == 1

    # Verify via the fixture session — past row gone, future row present
    result = await db_session.execute(
        select(WorkflowCheckpointRecord).where(
            WorkflowCheckpointRecord.id == past_id,
            WorkflowCheckpointRecord.tenant_id == test_tenant.id,
        )
    )
    assert result.scalar_one_or_none() is None

    result = await db_session.execute(
        select(WorkflowCheckpointRecord).where(
            WorkflowCheckpointRecord.id == future_id,
            WorkflowCheckpointRecord.tenant_id == test_tenant.id,
        )
    )
    assert result.scalar_one_or_none() is not None


@pytest.mark.integration
async def test_set_expiry_is_tenant_scoped(db_session, test_tenant, second_tenant):
    """set_expiry is tenant-scoped: another tenant's checkpoint is untouched."""
    from datetime import timezone, timedelta

    storage_a = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_factory=lambda: db_session,
    )

    cp = WorkflowCheckpoint(
        workflow_name="t:retention",
        graph_signature_hash="abc123",
        iteration_count=2,
    )
    saved_id = await storage_a.save(cp)

    future_dt = datetime.now(timezone.utc) + timedelta(days=7)
    assert await storage_a.set_expiry(saved_id, future_dt) is True

    # Re-reading the row shows the value
    from src.db.orm.workflow_checkpoints import WorkflowCheckpointRecord
    from sqlalchemy import select
    result = await db_session.execute(
        select(WorkflowCheckpointRecord).where(
            WorkflowCheckpointRecord.id == saved_id,
            WorkflowCheckpointRecord.tenant_id == test_tenant.id,
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert row.expires_at is not None

    # Another tenant cannot modify this checkpoint
    foreign_storage = MariaDBCheckpointStorage(
        tenant_id=second_tenant.id,
        session_factory=lambda: db_session,
    )
    assert await foreign_storage.set_expiry(saved_id, future_dt) is False


@pytest.mark.integration
async def test_enforce_checkpoint_retention_expires_old_rows(db_session, test_tenant):
    """enforce_checkpoint_retention stamps expires_at on old NULL rows, then deletes."""
    from src.db.orm.workflow_checkpoints import WorkflowCheckpointRecord
    from sqlalchemy import select

    # Insert a row with created_at 30 days in the past and expires_at=None.
    old_created = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.add(WorkflowCheckpointRecord(
        id="enforce-old-0000-0000-000000000003",
        tenant_id=test_tenant.id,
        workflow_name="t:retention",
        graph_signature_hash="ghi789",
        payload="{}",
        created_at=old_created,
        expires_at=None,
    ))
    await db_session.commit()

    # The enforcement function needs its own session to commit.
    # Use _NoCloseSession to prevent the fixture session from being closed.
    factory = lambda: _NoCloseSession(db_session)

    count = await enforce_checkpoint_retention(604800, session_factory=factory)  # 7 days TTL
    assert count == 1

    # Row should be gone — verify via the fixture session
    result = await db_session.execute(
        select(WorkflowCheckpointRecord).where(
            WorkflowCheckpointRecord.id == "enforce-old-0000-0000-000000000003",
            WorkflowCheckpointRecord.tenant_id == test_tenant.id,
        )
    )
    assert result.scalar_one_or_none() is None

    # TTL of 0 should disable the sweep
    count_zero = await enforce_checkpoint_retention(0)
    assert count_zero == 0
