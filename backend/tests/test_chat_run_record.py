# =============================================================================
# PH Agent Hub — Durable run record (Issue #608)
# =============================================================================
# The epic invariant is: every run ends in exactly one terminal state
# (completed | failed | cancelled | lost), recorded durably before it is
# announced.  Production evidence in #607 showed `completed` used to mean
# "no exception escaped", not "an answer exists".
#
# These tests pin:
#   * `completed` implies `persisted=true` (a run with no answer is failed);
#   * the startup sweep turns an orphaned `started_at` row into `lost`;
#   * the read model returns the terminal record;
#   * every termination path of the background task records a row.
# =============================================================================

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.agents.stream_bridge import StreamBridge
from src.api import chat as chat_api
from src.db.orm.chat_runs import ChatRun
from src.services import run_record
from src.services.run_registry import (
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_LOST,
    RunLease,
)

pytestmark = pytest.mark.integration


def _lease(session_id: str, user_id: str, kind: str = "chat") -> RunLease:
    now = datetime.now(timezone.utc).isoformat()
    return RunLease(
        session_id=session_id,
        run_id=str(uuid.uuid4()),
        kind=kind,
        user_id=user_id,
        tenant_id="tenant-1",
        owner="test:1",
        started_at=now,
        heartbeat_at=now,
    )


# =============================================================================
# Run record service
# =============================================================================


class TestRunRecord:
    async def test_start_run_is_durable_and_running(
        self, db_session, test_session, test_user
    ):
        run_id = str(uuid.uuid4())
        await run_record.start_run(
            run_id=run_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            kind="chat",
            db=db_session,
        )

        row = await db_session.get(ChatRun, run_id)
        assert row is not None
        assert row.finished_at is None
        assert row.outcome is None

        state = await run_record.get_run_state(db_session, test_session.id)
        assert state is not None
        assert state["run_id"] == run_id
        assert state["state"] == "running"

    async def test_finalize_completed_requires_persisted(
        self, db_session, test_session, test_user
    ):
        run_id = str(uuid.uuid4())
        await run_record.start_run(
            run_id=run_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            db=db_session,
        )
        # A "completed" outcome without a persisted answer must not stand:
        # this is exactly the #607 production record.
        await run_record.finalize_run(
            run_id=run_id,
            session_id=test_session.id,
            outcome=OUTCOME_COMPLETED,
            persisted=False,
            message_id=None,
            db=db_session,
        )

        row = await db_session.get(ChatRun, run_id)
        assert row.outcome == OUTCOME_FAILED
        assert row.error_code == "not_persisted"
        assert row.persisted is False
        assert row.finished_at is not None

    async def test_finalize_completed_with_persisted_answer(
        self, db_session, test_session, test_user
    ):
        run_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        await run_record.start_run(
            run_id=run_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            db=db_session,
        )
        await run_record.finalize_run(
            run_id=run_id,
            session_id=test_session.id,
            outcome=OUTCOME_COMPLETED,
            persisted=True,
            message_id=message_id,
            client_attached=True,
            last_event_seq=42,
            tokens_in=10,
            tokens_out=20,
            db=db_session,
        )

        state = await run_record.get_run_state(db_session, test_session.id)
        assert state["state"] == OUTCOME_COMPLETED
        assert state["persisted"] is True
        assert state["message_id"] == message_id
        assert state["client_attached"] is True
        assert state["last_event_seq"] == 42
        assert state["tokens_in"] == 10
        assert state["tokens_out"] == 20

    async def test_startup_sweep_marks_orphans_lost(
        self, db_session, test_session, test_user, monkeypatch
    ):
        run_id = str(uuid.uuid4())
        await run_record.start_run(
            run_id=run_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            # Pretend the row was written by the killed process a while ago.
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            db=db_session,
        )

        async def _no_lease(_session_id):
            return None

        monkeypatch.setattr(run_record, "get_run", _no_lease)

        marked = await run_record.mark_orphan_runs_lost(db_session)
        assert marked >= 1

        row = await db_session.get(ChatRun, run_id)
        assert row.outcome == OUTCOME_LOST
        assert row.error_code == "orphaned"
        assert row.finished_at is not None

    async def test_startup_sweep_skips_a_live_run(
        self, db_session, test_session, test_user, monkeypatch
    ):
        lease = _lease(test_session.id, test_user.id)
        await run_record.start_run(
            run_id=lease.run_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            db=db_session,
        )

        async def _live(_session_id):
            lease.heartbeat_at = datetime.now(timezone.utc).isoformat()
            return lease

        monkeypatch.setattr(run_record, "get_run", _live)

        await run_record.mark_orphan_runs_lost(db_session)
        row = await db_session.get(ChatRun, lease.run_id)
        assert row.finished_at is None
        assert row.outcome is None


# =============================================================================
# Run-state read endpoint
# =============================================================================


class TestRunStateEndpoint:
    async def test_returns_terminal_record(
        self, async_client, auth_headers, db_session, test_session, test_user
    ):
        run_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        await run_record.start_run(
            run_id=run_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            db=db_session,
        )
        await run_record.finalize_run(
            run_id=run_id,
            session_id=test_session.id,
            outcome=OUTCOME_FAILED,
            error_code="model_timeout",
            error_message="provider timed out",
            persisted=False,
            db=db_session,
        )

        response = await async_client.get(
            f"/api/chat/session/{test_session.id}/run-state",
            headers=auth_headers(test_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"] == run_id
        assert body["state"] == OUTCOME_FAILED
        assert body["error_code"] == "model_timeout"
        assert body["message_id"] is None
        assert body["persisted"] is False

    async def test_no_run_returns_idle(
        self, async_client, auth_headers, test_session, test_user
    ):
        response = await async_client.get(
            f"/api/chat/session/{test_session.id}/run-state",
            headers=auth_headers(test_user),
        )
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "idle"

    async def test_rejects_another_users_session(
        self, async_client, auth_headers, test_session, test_user, db_session
    ):
        from src.db.orm.users import User

        other = User(
            id=str(uuid.uuid4()),
            tenant_id=test_user.tenant_id,
            email=f"other-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="pbkdf2:sha256:600000$salt$hash",
            display_name="Other",
            role="user",
            is_active=True,
        )
        db_session.add(other)
        await db_session.flush()

        response = await async_client.get(
            f"/api/chat/session/{test_session.id}/run-state",
            headers=auth_headers(other),
        )
        assert response.status_code in (403, 404)


# =============================================================================
# Every background termination path records a terminal row
# =============================================================================


class _Recorder:
    def __init__(self):
        self.started: list[dict] = []
        self.finalized: list[dict] = []
        self.order: list[str] = []

    async def start_run(self, **kwargs):
        self.started.append(kwargs)
        self.order.append("record_start")

    async def finalize_run(self, **kwargs):
        self.finalized.append(kwargs)
        self.order.append("record_terminal")


async def _drive_background(
    monkeypatch,
    *,
    session_id: str,
    user_id: str,
    message_id: str,
    stream_factory,
    check_cancel=None,
):
    """Run ``_run_agent_background`` against a fake stream and capture records."""
    recorder = _Recorder()
    monkeypatch.setattr(run_record, "start_run", recorder.start_run)
    monkeypatch.setattr(run_record, "finalize_run", recorder.finalize_run)
    monkeypatch.setattr(chat_api, "run_agent_stream", stream_factory)
    monkeypatch.setattr(
        chat_api, "_release_lease_safely", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(chat_api, "clear_stream_cancel", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chat_api, "is_run_cancel_requested", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        chat_api, "clear_run_cancel_marker", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(chat_api, "remove_bridge", lambda *a, **k: None)
    if check_cancel is not None:
        monkeypatch.setattr(chat_api, "check_stream_cancel", check_cancel)

    bridge = StreamBridge(session_id=session_id, run_id="run-1")
    bridge.put = AsyncMock(
        side_effect=lambda event: recorder.order.append(f"put:{event.get('event')}")
    )
    lease = _lease(session_id, user_id)

    await chat_api._run_agent_background(
        session_id=session_id,
        data={"user_id": user_id, "tenant_id": "tenant-1"},
        user_message="hi",
        current_user=None,
        message_id=message_id,
        bridge=bridge,
        lease=lease,
    )
    return recorder, bridge, lease


def _terminal(outcome: str, *, persisted: bool, message_id: str | None, error_code=None):
    return {
        "event": "run_terminal",
        "data": json.dumps({
            "outcome": outcome,
            "error_code": error_code,
            "message_id": message_id,
            "persisted": persisted,
            "tokens_in": 3,
            "tokens_out": 4,
        }),
    }


class TestBackgroundTerminationPaths:
    async def test_normal_completion_records_completed_before_announcing(
        self, monkeypatch, test_session, test_user
    ):
        message_id = str(uuid.uuid4())

        async def normal(*args, **kwargs):
            yield {"event": "token", "data": json.dumps({"delta": "hi", "message_id": message_id})}
            yield _terminal("completed", persisted=True, message_id=message_id)

        recorder, _bridge, _lease_obj = await _drive_background(
            monkeypatch,
            session_id=test_session.id,
            user_id=test_user.id,
            message_id=message_id,
            stream_factory=normal,
        )

        assert len(recorder.finalized) == 1
        assert recorder.finalized[0]["outcome"] == OUTCOME_COMPLETED
        assert recorder.finalized[0]["persisted"] is True
        assert recorder.finalized[0]["message_id"] == message_id
        # No SSE subscriber attached in this test: an answer produced with no
        # browser ever seeing it is identifiable from the record alone.
        assert recorder.finalized[0]["client_attached"] is False
        # The row is committed before the terminal frame is announced.
        assert recorder.order.index("record_terminal") < recorder.order.index(
            "put:run_terminal"
        )

    async def test_generator_exception_records_failed(
        self, monkeypatch, test_session, test_user
    ):
        async def exploding(*args, **kwargs):
            yield {"event": "token", "data": json.dumps({"delta": "x", "message_id": "m"})}
            raise RuntimeError("provider exploded")

        recorder, _bridge, _lease_obj = await _drive_background(
            monkeypatch,
            session_id=test_session.id,
            user_id=test_user.id,
            message_id=str(uuid.uuid4()),
            stream_factory=exploding,
        )

        assert len(recorder.finalized) == 1
        assert recorder.finalized[0]["outcome"] == OUTCOME_FAILED
        assert recorder.finalized[0]["persisted"] is False
        assert recorder.finalized[0]["error_code"]

    async def test_cancellation_flag_records_cancelled(
        self, monkeypatch, test_session, test_user
    ):
        async def partial(*args, **kwargs):
            yield {"event": "token", "data": json.dumps({"delta": "x", "message_id": "m"})}

        recorder, _bridge, _lease_obj = await _drive_background(
            monkeypatch,
            session_id=test_session.id,
            user_id=test_user.id,
            message_id=str(uuid.uuid4()),
            stream_factory=partial,
            check_cancel=AsyncMock(return_value=True),
        )

        assert len(recorder.finalized) == 1
        assert recorder.finalized[0]["outcome"] == OUTCOME_CANCELLED

    async def test_cancelled_task_records_cancelled(
        self, monkeypatch, test_session, test_user
    ):
        async def cancelled(*args, **kwargs):
            yield {"event": "token", "data": json.dumps({"delta": "x", "message_id": "m"})}
            raise asyncio.CancelledError()

        recorder, _bridge, _lease_obj = await _drive_background(
            monkeypatch,
            session_id=test_session.id,
            user_id=test_user.id,
            message_id=str(uuid.uuid4()),
            stream_factory=cancelled,
        )

        assert len(recorder.finalized) == 1
        assert recorder.finalized[0]["outcome"] == OUTCOME_CANCELLED

    async def test_empty_output_records_failed_not_completed(
        self, monkeypatch, test_session, test_user
    ):
        async def empty(*args, **kwargs):
            yield _terminal("failed", persisted=False, message_id=None, error_code="empty_output")

        recorder, _bridge, _lease_obj = await _drive_background(
            monkeypatch,
            session_id=test_session.id,
            user_id=test_user.id,
            message_id=str(uuid.uuid4()),
            stream_factory=empty,
        )

        assert recorder.finalized[0]["outcome"] == OUTCOME_FAILED
        assert recorder.finalized[0]["error_code"] == "empty_output"

    async def test_missing_terminal_event_is_never_completed(
        self, monkeypatch, test_session, test_user
    ):
        async def no_terminal(*args, **kwargs):
            yield {"event": "token", "data": json.dumps({"delta": "x", "message_id": "m"})}

        recorder, _bridge, _lease_obj = await _drive_background(
            monkeypatch,
            session_id=test_session.id,
            user_id=test_user.id,
            message_id=str(uuid.uuid4()),
            stream_factory=no_terminal,
        )

        assert recorder.finalized[0]["outcome"] == OUTCOME_FAILED
        assert recorder.finalized[0]["error_code"] == "missing_terminal"

    async def test_bridge_closed_early_still_records(
        self, monkeypatch, test_session, test_user
    ):
        message_id = str(uuid.uuid4())

        async def normal(*args, **kwargs):
            yield _terminal("completed", persisted=True, message_id=message_id)

        recorder = _Recorder()
        monkeypatch.setattr(run_record, "start_run", recorder.start_run)
        monkeypatch.setattr(run_record, "finalize_run", recorder.finalize_run)
        monkeypatch.setattr(chat_api, "run_agent_stream", normal)
        monkeypatch.setattr(chat_api, "_release_lease_safely", AsyncMock(return_value=None))
        monkeypatch.setattr(chat_api, "clear_stream_cancel", AsyncMock(return_value=None))
        monkeypatch.setattr(chat_api, "is_run_cancel_requested", AsyncMock(return_value=False))
        monkeypatch.setattr(chat_api, "clear_run_cancel_marker", AsyncMock(return_value=None))
        monkeypatch.setattr(chat_api, "remove_bridge", lambda *a, **k: None)

        bridge = StreamBridge(session_id=test_session.id, run_id="run-1")
        await bridge.close()

        await chat_api._run_agent_background(
            session_id=test_session.id,
            data={"user_id": test_user.id, "tenant_id": "tenant-1"},
            user_message="hi",
            current_user=None,
            message_id=message_id,
            bridge=bridge,
            lease=_lease(test_session.id, test_user.id),
        )

        assert len(recorder.finalized) == 1
        assert recorder.finalized[0]["outcome"] == OUTCOME_COMPLETED


# =============================================================================
# Logging contract
# =============================================================================


class TestRunLogging:
    async def test_start_and_finish_lines_carry_required_fields(
        self, monkeypatch, test_session, test_user, caplog
    ):
        message_id = str(uuid.uuid4())

        async def normal(*args, **kwargs):
            yield _terminal("completed", persisted=True, message_id=message_id)

        caplog.set_level(logging.INFO, logger="src.api.chat")
        await _drive_background(
            monkeypatch,
            session_id=test_session.id,
            user_id=test_user.id,
            message_id=message_id,
            stream_factory=normal,
        )

        text = caplog.text
        assert "PH-RUN-START" in text
        assert "PH-RUN-FINISH" in text
        finish = [
            record.getMessage()
            for record in caplog.records
            if "PH-RUN-FINISH" in record.getMessage()
        ]
        assert finish, "finish line missing"
        line = finish[-1]
        assert "run_id=" in line
        assert "session_id=" in line
        assert "outcome=completed" in line
        assert "duration_ms=" in line
        assert "tokens_in=3" in line
        assert "tokens_out=4" in line


class TestCompletedWithoutPersisted:
    async def test_terminal_completed_without_persisted_is_failed(
        self, monkeypatch, test_session, test_user
    ):
        async def lying(*args, **kwargs):
            yield _terminal("completed", persisted=False, message_id=None)

        recorder, _bridge, _lease_obj = await _drive_background(
            monkeypatch,
            session_id=test_session.id,
            user_id=test_user.id,
            message_id=str(uuid.uuid4()),
            stream_factory=lying,
        )

        assert recorder.finalized[0]["outcome"] == OUTCOME_FAILED
        assert recorder.finalized[0]["error_code"] == "not_persisted"


# =============================================================================
# Admission logging: a rejected send must leave a trace
# =============================================================================


class TestAdmissionLogging:
    @patch("src.api.chat.run_agent")
    async def test_accepted_send_logs_run_id(
        self, mock_run_agent, async_client, auth_headers, test_user,
        test_session, test_model, caplog,
    ):
        import uuid as _uuid

        mock_run_agent.return_value = ("ok", str(_uuid.uuid4()))
        caplog.set_level(logging.INFO, logger="src.main")
        caplog.set_level(logging.INFO, logger="src.api.chat")

        response = await async_client.post(
            f"/api/chat/session/{test_session.id}/message",
            json={"content": "hello"},
            headers=auth_headers(test_user),
        )
        assert response.status_code == 200, response.text
        admissions = [
            r.getMessage() for r in caplog.records
            if "PH-RUN-ADMIT" in r.getMessage()
        ]
        assert admissions, caplog.text
        assert "run_id=" in admissions[-1]
        assert f"session_id={test_session.id}" in admissions[-1]

    async def test_unauthenticated_send_logs_auth_rejection(
        self, async_client, test_session, caplog,
    ):
        caplog.set_level(logging.INFO, logger="src.main")
        response = await async_client.post(
            f"/api/chat/session/{test_session.id}/message",
            json={"content": "hello"},
        )
        assert response.status_code == 401
        rejections = [
            r.getMessage() for r in caplog.records
            if "PH-RUN-REJECT" in r.getMessage()
        ]
        assert rejections, caplog.text
        assert "reason=auth" in rejections[-1]

    @patch("src.api.chat.run_agent")
    async def test_busy_send_logs_busy_rejection(
        self, mock_run_agent, async_client, auth_headers, test_user,
        test_session, test_model, caplog,
    ):
        from src.services.run_registry import acquire_run, release_run

        caplog.set_level(logging.INFO, logger="src.main")
        mock_run_agent.return_value = ("never", str(uuid.uuid4()))
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        assert lease is not None
        try:
            response = await async_client.post(
                f"/api/chat/session/{test_session.id}/message",
                json={"content": "hello"},
                headers=auth_headers(test_user),
            )
            assert response.status_code == 409, response.text
        finally:
            await release_run(lease, OUTCOME_CANCELLED)

        rejections = [
            r.getMessage() for r in caplog.records
            if "PH-RUN-REJECT" in r.getMessage()
        ]
        assert rejections, caplog.text
        assert "reason=busy" in rejections[-1]
