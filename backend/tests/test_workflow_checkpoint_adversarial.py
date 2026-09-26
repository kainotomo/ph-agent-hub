# =============================================================================
# PH Agent Hub — Workflow Checkpoint Adversarial Tests (Chunk A14)
# =============================================================================
# Adversarial proofs that (a) one tenant cannot load, list, or resume another
# tenant's checkpoints even when both use the same definition key, and (b) a
# tampered or malformed checkpoint surfaces as WorkflowCheckpointException
# rather than crashing.
# =============================================================================

import uuid

import pytest

from agent_framework import WorkflowCheckpoint, WorkflowCheckpointException
from src.agents.workflows.checkpoint_storage import MariaDBCheckpointStorage
from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.identity import graph_signature_hash
from src.core.exceptions import ValidationError
from src.db.orm.workflow_checkpoints import WorkflowCheckpointRecord
from src.services.workflow_checkpoint_service import (
    assert_resumable,
    latest_resumable_checkpoint,
    list_session_checkpoints,
)


# ---------------------------------------------------------------------------
# Module-level helper — a definition whose key is shared by BOTH tenants.
# ---------------------------------------------------------------------------

def _defn():
    return WorkflowDefinition(
        key="research", name="Research",
        steps=[{"id": "s1", "name": "S", "type": "inline", "instructions": "x"}],
    )


# ---------------------------------------------------------------------------
# Test 1 — tenant isolation: cannot load, list, or resume a foreign checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_cross_tenant_cannot_load_list_or_resume(
    db_session, test_tenant, second_tenant,
):
    """A checkpoint saved by tenant A must be invisible to tenant B on every
    resolution path: direct load, resumable-lookup, and listing.

    Additionally the service-level assert_resumable must reject a foreign
    tenant's record even when the graph signature matches.
    """
    # 1. Build storages for both tenants sharing the same session_id/message_id.
    storage_a = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )
    storage_b = MariaDBCheckpointStorage(
        tenant_id=second_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )

    # 2. Save one checkpoint via storage_a whose workflow_name uses the
    #    tenant-prefixed convention so it is unambiguous even though both
    #    tenants share the same definition key "research".
    defn = _defn()
    wf_name = f"{test_tenant.id}:research"
    cp = WorkflowCheckpoint(
        workflow_name=wf_name,
        graph_signature_hash=graph_signature_hash(defn),
        iteration_count=1,
    )
    saved_id = await storage_a.save(cp)

    # 3. storage_b (second tenant) must NOT be able to load this checkpoint.
    #    The error must be the SAME one an entirely absent id produces —
    #    so the error never reveals that another tenant's row exists.
    with pytest.raises(WorkflowCheckpointException, match=r"No checkpoint found with ID"):
        await storage_b.load(saved_id)

    # 4. latest_resumable_checkpoint for the second tenant must return None.
    result = await latest_resumable_checkpoint(db_session, second_tenant.id, "S1")
    assert result is None

    # 5. list_session_checkpoints for the second tenant must return an empty list.
    rows = await list_session_checkpoints(db_session, second_tenant.id, "S1")
    assert rows == []

    # 6. Fetch the record as the owner (test_tenant) — it must exist.
    record = await latest_resumable_checkpoint(db_session, test_tenant.id, "S1")
    assert record is not None
    # But asserting resumable from the OTHER tenant must raise ValidationError.
    with pytest.raises(ValidationError, match=r"different tenant"):
        assert_resumable(second_tenant.id, record, defn)

    # 7. The owner must be able to resume it — no error.
    assert_resumable(test_tenant.id, record, defn)  # should not raise


# ---------------------------------------------------------------------------
# Test 2 — malformed / tampered checkpoint payloads surface WorkflowCheckpointException
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_malformed_checkpoint_raises_workflow_checkpoint_exception(
    db_session, test_tenant,
):
    """Inserting raw corrupt / malformed payloads directly into the DB must
    produce WorkflowCheckpointException on load — never a raw ValueError,
    TypeError, json.JSONDecodeError, or pickle.UnpicklingError.

    Two payloads are tested:
      - "not-json"   — not JSON at all
      - "[1, 2, 3]"  — valid JSON but wrong shape (array, not object with
                        workflow_name + graph_signature_hash)
    """
    storage_a = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )

    defn = _defn()
    expected_hash = graph_signature_hash(defn)

    payloads_to_test = [
        ("not-json", "not JSON at all"),
        ("[1, 2, 3]", "valid JSON but wrong shape"),
    ]

    for payload, description in payloads_to_test:
        cid = str(uuid.uuid4())

        # Insert a row directly into the DB so the payload is corrupt.
        db_session.add(WorkflowCheckpointRecord(
            id=cid,
            tenant_id=test_tenant.id,
            workflow_name="t:malformed",
            graph_signature_hash=expected_hash,
            session_id="S1",
            payload=payload,
        ))
        await db_session.flush()

        # storage_a.load() must raise WorkflowCheckpointException, and it
        # must NOT be one of the raw underlying exception types.
        try:
            await storage_a.load(cid)
            pytest.fail(f"storage_a.load({cid!r}) did not raise for payload={payload!r} ({description})")
        except WorkflowCheckpointException:
            # Good — this is the expected exception type.
            pass
        except Exception as exc:
            # If a raw underlying error escapes, the test must fail.
            pytest.fail(
                f"Expected WorkflowCheckpointException but got {type(exc).__name__}: {exc}"
            )

        # Double-check by asserting isinstance on caught exceptions, so a raw
        # error escaping would fail the test.
        with pytest.raises(WorkflowCheckpointException):
            await storage_a.load(cid)
