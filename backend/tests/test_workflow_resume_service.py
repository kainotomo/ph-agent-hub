# =============================================================================
# PH Agent Hub — Workflow Resume Service Tests
# =============================================================================
# Unit tests for the durable-resume entry point.
# =============================================================================

import json
import types

import pytest
from agent_framework import WorkflowCheckpoint
from unittest.mock import AsyncMock, MagicMock, patch
from src.agents.workflows.checkpoint_storage import MariaDBCheckpointStorage
from src.agents.workflows.definition import WorkflowDefinition, WorkflowStep
from src.core.exceptions import NotFoundError
from src.services.workflow_resume_service import (
    resume_workflow_stream,
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
# Test 1: resume_seeds_snapshot_and_checkpoint_id
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_resume_seeds_snapshot_and_checkpoint_id():
    """The resume stream builds a fresh workflow seeded with the snapshot,
    passes the checkpoint_id to iter_workflow_sse, and streams events."""
    record = _make_record()
    checkpoint = _make_checkpoint()
    storage = MagicMock()
    storage.load = AsyncMock(return_value=checkpoint)
    storage.set_expiry = AsyncMock()
    defn = _make_defn()
    db = AsyncMock()

    build_workflow_mock = AsyncMock()

    async def _empty_gen(*_args, **_kwargs):
        return
        yield  # turn into async generator

    iter_mock = MagicMock()
    iter_mock.side_effect = _empty_gen

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
    ):
        events = [e async for e in resume_workflow_stream(
            db=db,
            tenant_id="T1",
            session_id="S1",
            message_id="M1",
            defn=defn,
            checkpoint_storage=storage,
        )]

    assert build_workflow_mock.call_args.kwargs["initial_state"] == {
        "outputs": {"a": "X"},
        "user_message": "HI",
    }
    assert build_workflow_mock.call_args.kwargs["checkpoint_storage"] is storage
    assert "workflow_name" not in build_workflow_mock.call_args.kwargs
    assert iter_mock.call_args.kwargs["checkpoint_id"] == "cp-1"
    assert iter_mock.call_args.kwargs["message"] is None


# ---------------------------------------------------------------------------
# Test 2: resume_raises_when_no_checkpoint
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_resume_raises_when_no_checkpoint():
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
            [e async for e in resume_workflow_stream(
                db=db,
                tenant_id="T1",
                session_id="S1",
                message_id="M1",
                defn=_make_defn(),
                checkpoint_storage=MagicMock(),
            )]


# ---------------------------------------------------------------------------
# Test 3: completed_run_is_marked_and_expiring
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_completed_run_is_marked_and_expiring():
    """A completed run is marked COMPLETED and given an expiry for retention."""
    from datetime import datetime, timedelta, timezone
    from src.services.workflow_checkpoint_service import mark_checkpoint_state

    record = _make_record()
    checkpoint = _make_checkpoint()
    storage = MagicMock()
    storage.load = AsyncMock(return_value=checkpoint)
    storage.set_expiry = AsyncMock()
    defn = _make_defn()
    db = AsyncMock()

    build_workflow_mock = AsyncMock()

    mark_mock = AsyncMock()

    async def _message_complete_gen(*_args, **_kwargs):
        yield {"event": "message_complete", "data": json.dumps({"outcome": "completed"})}

    iter_mock = MagicMock()
    iter_mock.side_effect = _message_complete_gen

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
        events = [e async for e in resume_workflow_stream(
            db=db,
            tenant_id="T1",
            session_id="S1",
            message_id="M1",
            defn=defn,
            checkpoint_storage=storage,
        )]

    # Event was streamed through to the caller
    assert any(e["event"] == "message_complete" for e in events)
    # Mark COMPLETED was called once with the correct state
    assert mark_mock.call_count == 1
    assert mark_mock.call_args[0][3] == "COMPLETED"
    # set_expiry was called once
    assert storage.set_expiry.call_count == 1


# ---------------------------------------------------------------------------
# Test 4: paused_run_is_not_marked
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_paused_run_is_not_marked():
    """A paused run is NOT marked COMPLETED and does not set expiry."""
    record = _make_record()
    checkpoint = _make_checkpoint()
    storage = MagicMock()
    storage.load = AsyncMock(return_value=checkpoint)
    storage.set_expiry = AsyncMock()
    defn = _make_defn()
    db = AsyncMock()

    build_workflow_mock = AsyncMock()

    mark_mock = AsyncMock()

    async def _message_complete_gen(*_args, **_kwargs):
        yield {"event": "message_complete", "data": json.dumps({"outcome": "paused"})}

    iter_mock = MagicMock()
    iter_mock.side_effect = _message_complete_gen

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
        events = [e async for e in resume_workflow_stream(
            db=db,
            tenant_id="T1",
            session_id="S1",
            message_id="M1",
            defn=defn,
            checkpoint_storage=storage,
        )]

    # No mark was called
    assert mark_mock.call_count == 0
    # No expiry was set
    assert storage.set_expiry.call_count == 0


# ---------------------------------------------------------------------------
# Test 5: resume_with_responses_forwards_responses_and_checkpoint_id
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_resume_with_responses_forwards_responses_and_checkpoint_id():
    """resume_workflow_with_responses yields a workflow_resumed event carrying
    the workflow_key and checkpoint_id, and passes the responses dict and
    checkpoint_id to iter_workflow_sse."""
    record = _make_record()
    checkpoint = _make_checkpoint()
    storage = MagicMock()
    storage.load = AsyncMock(return_value=checkpoint)
    storage.set_expiry = AsyncMock()
    defn = _make_defn()
    db = AsyncMock()

    build_workflow_mock = AsyncMock()

    async def _empty_gen(*_args, **_kwargs):
        return
        yield  # turn into async generator

    iter_mock = MagicMock()
    iter_mock.side_effect = _empty_gen

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
    ):
        events = [e async for e in resume_workflow_with_responses(
            db=db,
            tenant_id="T1",
            session_id="S1",
            message_id="M1",
            defn=defn,
            checkpoint_storage=storage,
            responses={"req-1": "yes"},
        )]

    # First event is workflow_resumed with the workflow_key equal to defn.key
    first = events[0]
    assert first["event"] == "workflow_resumed"
    data = json.loads(first["data"])
    assert data["workflow_key"] == defn.key
    assert data["checkpoint_id"] == record.id
    assert data["pending_request_ids"] == ["req-1"]

    # iter_workflow_sse received responses and checkpoint_id
    assert iter_mock.call_args.kwargs["responses"] == {"req-1": "yes"}
    assert iter_mock.call_args.kwargs["checkpoint_id"] == record.id


# ---------------------------------------------------------------------------
# Test 6: paused_resume_does_not_mark_completed
# ---------------------------------------------------------------------------

@pytest.mark.unit
async def test_paused_resume_does_not_mark_completed():
    """A resumed workflow that pauses does not mark the checkpoint COMPLETED."""
    record = _make_record()
    checkpoint = _make_checkpoint()
    storage = MagicMock()
    storage.load = AsyncMock(return_value=checkpoint)
    storage.set_expiry = AsyncMock()
    defn = _make_defn()
    db = AsyncMock()

    build_workflow_mock = AsyncMock()

    mark_mock = AsyncMock()

    async def _message_complete_gen(*_args, **_kwargs):
        yield {"event": "message_complete", "data": json.dumps({
            "outcome": "paused",
            "pending_request_ids": ["req-2"],
        })}

    iter_mock = MagicMock()
    iter_mock.side_effect = _message_complete_gen

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

    # message_complete was forwarded
    assert any(e["event"] == "message_complete" for e in events)

    # No mark was called
    assert mark_mock.call_count == 0
    # No expiry was set
    assert storage.set_expiry.call_count == 0
