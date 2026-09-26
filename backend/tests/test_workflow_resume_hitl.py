# =============================================================================
# PH Agent Hub — Workflow Resume HITL Tests (Chunk T2)
# =============================================================================
# Service-level tests for resume-with-rejections, cross-tenant guard, and
# completed-checkpoint marking in the HITL (human-in-the-loop) path.
#
# These tests do NOT open a database connection — every external dependency is
# patched.
# =============================================================================

import json

import pytest
from agent_framework import WorkflowCheckpoint
from unittest.mock import AsyncMock, MagicMock, patch
from src.agents.workflows.definition import WorkflowDefinition, WorkflowStep
from src.core.exceptions import NotFoundError, ValidationError
from src.services.workflow_checkpoint_service import RUN_STATE_COMPLETED
from src.services.workflow_resume_service import (
    resume_workflow_with_responses,
)


pytestmark = [pytest.mark.unit]


def _make_record(
    checkpoint_id: str = "cp-1",
    tenant_id: str = "T1",
    session_id: str = "S1",
    graph_signature_hash: str = "abc",
    run_state: str | None = None,
    workflow_name: str = "T1:research",
):
    """Build a lightweight checkpoint record."""
    import types
    return types.SimpleNamespace(
        id=checkpoint_id,
        tenant_id=tenant_id,
        session_id=session_id,
        graph_signature_hash=graph_signature_hash,
        run_state=run_state,
        workflow_name=workflow_name,
    )


def _make_checkpoint():
    """Build a workflow checkpoint with nested executor state."""
    return WorkflowCheckpoint(
        workflow_name="T1:research",
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


def _make_defn():
    """Build a valid one-step workflow definition."""
    return WorkflowDefinition(
        key="research",
        name="R",
        steps=[
            WorkflowStep(
                id="s1",
                name="S",
                type="inline",
                instructions="x",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: reject_response_is_forwarded
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_resume_reject_is_forwarded():
    """A rejection response ({\"req-1\": False}) is forwarded to
    iter_workflow_sse, and the consumed stream contains a message_complete event.
    """
    record = _make_record()
    checkpoint = _make_checkpoint()
    storage = MagicMock()
    storage.load = AsyncMock(return_value=checkpoint)
    storage.set_expiry = AsyncMock()
    defn = _make_defn()
    db = AsyncMock()

    build_workflow_mock = AsyncMock()
    mark_mock = AsyncMock()

    # The patched stream yields one synthetic message_complete so that the
    # service completes normally.
    async def _stream(*_args, **_kwargs):
        yield {"event": "message_complete", "data": json.dumps({
            "outcome": "completed",
            "pending_request_ids": [],
        })}

    iter_mock = MagicMock()
    iter_mock.side_effect = _stream

    with patch(
        "src.services.workflow_resume_service.latest_resumable_checkpoint",
        AsyncMock(return_value=record),
    ), patch(
        "src.services.workflow_resume_service.assert_resumable",
        MagicMock(),
    ), patch(
        "src.services.workflow_resume_service.build_workflow",
        build_workflow_mock,
    ), patch(
        "src.services.workflow_resume_service.iter_workflow_sse",
        iter_mock,
    ), patch(
        "src.services.workflow_resume_service.mark_checkpoint_state",
        mark_mock,
    ):
        events = [e async for e in resume_workflow_with_responses(
            db=db,
            tenant_id="T1",
            session_id="S1",
            message_id="M1",
            defn=defn,
            checkpoint_storage=storage,
            responses={"req-1": False},
        )]

    # --- Verify stream contents ------------------------------------------------
    # workflow_resumed event was emitted first
    assert events[0]["event"] == "workflow_resumed"
    data = json.loads(events[0]["data"])
    assert data["workflow_key"] == defn.key
    assert data["checkpoint_id"] == record.id
    assert data["pending_request_ids"] == ["req-1"]
    assert data["session_id"] == "S1"
    assert data["message_id"] == "M1"

    # message_complete was forwarded through
    assert any(e["event"] == "message_complete" for e in events)

    # --- Verify iter_workflow_sse received the rejection -------------------------
    assert iter_mock.call_args.kwargs["responses"] == {"req-1": False}
    assert iter_mock.call_args.kwargs["checkpoint_id"] == record.id

    # --- Verify build_workflow got the recovered snapshot ------------------------
    assert build_workflow_mock.call_args.kwargs["initial_state"] == {
        "outputs": {"a": "X"},
        "user_message": "HI",
    }
    assert "workflow_name" not in build_workflow_mock.call_args.kwargs

    # --- Verify checkpoint was marked completed (outcome + empty prids) ----------
    assert mark_mock.call_count == 1
    assert mark_mock.call_args[0][3] == RUN_STATE_COMPLETED


# ---------------------------------------------------------------------------
# Test 2: no_checkpoint_raises_not_found
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_no_checkpoint_raises_not_found():
    """Resuming with no resumable checkpoint raises NotFoundError."""
    db = AsyncMock()

    with patch(
        "src.services.workflow_resume_service.latest_resumable_checkpoint",
        AsyncMock(return_value=None),
    ), patch(
        "src.services.workflow_resume_service.assert_resumable",
        MagicMock(),
    ):
        with pytest.raises(NotFoundError):
            [e async for e in resume_workflow_with_responses(
                db=db,
                tenant_id="T1",
                session_id="S1",
                message_id="M1",
                defn=_make_defn(),
                checkpoint_storage=MagicMock(),
                responses={},
            )]


# ---------------------------------------------------------------------------
# Test 3: cross_tenant_resume_raises_validation_error
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_cross_tenant_resume_raises_validation_error():
    """A checkpoint belonging to a different tenant raises ValidationError."""
    record = _make_record(tenant_id="T_OTHER")

    with patch(
        "src.services.workflow_resume_service.latest_resumable_checkpoint",
        AsyncMock(return_value=record),
    ), patch(
        "src.services.workflow_resume_service.assert_resumable",
        MagicMock(side_effect=ValidationError(
            "Checkpoint 'cp-1' belongs to a different tenant"
        )),
    ):
        with pytest.raises(ValidationError) as exc_info:
            [e async for e in resume_workflow_with_responses(
                db=AsyncMock(),
                tenant_id="T1",
                session_id="S1",
                message_id="M1",
                defn=_make_defn(),
                checkpoint_storage=MagicMock(),
                responses={},
            )]
        assert "different tenant" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 4: completed_resume_marks_checkpoint
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_completed_resume_marks_checkpoint():
    """A resumed workflow that completes (outcome == 'completed',
    pending_request_ids == []) marks the checkpoint COMPLETED.
    """
    record = _make_record()
    checkpoint = _make_checkpoint()
    storage = MagicMock()
    storage.load = AsyncMock(return_value=checkpoint)
    storage.set_expiry = AsyncMock()
    defn = _make_defn()
    db = AsyncMock()

    build_workflow_mock = AsyncMock()
    mark_mock = AsyncMock()

    async def _stream(*_args, **_kwargs):
        yield {"event": "message_complete", "data": json.dumps({
            "outcome": "completed",
            "pending_request_ids": [],
        })}

    iter_mock = MagicMock()
    iter_mock.side_effect = _stream

    with patch(
        "src.services.workflow_resume_service.latest_resumable_checkpoint",
        AsyncMock(return_value=record),
    ), patch(
        "src.services.workflow_resume_service.assert_resumable",
        MagicMock(),
    ), patch(
        "src.services.workflow_resume_service.build_workflow",
        build_workflow_mock,
    ), patch(
        "src.services.workflow_resume_service.iter_workflow_sse",
        iter_mock,
    ), patch(
        "src.services.workflow_resume_service.mark_checkpoint_state",
        mark_mock,
    ):
        events = [e async for e in resume_workflow_with_responses(
            db=db,
            tenant_id="T1",
            session_id="S1",
            message_id="M1",
            defn=defn,
            checkpoint_storage=storage,
            responses={},
        )]

    # workflow_resumed event was emitted first
    assert events[0]["event"] == "workflow_resumed"
    data = json.loads(events[0]["data"])
    assert data["workflow_key"] == defn.key
    assert data["pending_request_ids"] == []

    # message_complete was forwarded
    assert any(e["event"] == "message_complete" for e in events)

    # mark_checkpoint_state was called with COMPLETED state
    assert mark_mock.call_count == 1
    assert mark_mock.call_args[0][3] == RUN_STATE_COMPLETED

    # set_expiry was called once (TTL > 0 default)
    assert storage.set_expiry.call_count == 1
