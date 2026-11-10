# =============================================================================
# PH Agent Hub — Authoritative Session Run State (Issue #604)
# =============================================================================
# Covers the Redis run registry (exclusive leases, owner-checked release,
# lease expiry), the per-session lock on send, run-scoped cancellation,
# snapshot fields on session APIs, and the multiplexed status stream.
# =============================================================================

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from src.api.chat import _iter_session_status_events
from src.agents.stream_bridge import StreamBridge, register_bridge, remove_bridge
from src.core.config import settings
from src.core.redis import (
    check_stream_cancel,
    clear_stream_cancel,
    get_redis,
    set_stream_cancel,
)
from src.db.orm.messages import Message
from src.services.run_registry import (
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    RunLease,
    acquire_run,
    clear_run_cancel_marker,
    get_active_runs_for_user,
    get_run,
    get_run_outcome,
    get_runs,
    heartbeat_run,
    is_run_cancel_requested,
    release_run,
    request_run_cancel,
)

pytestmark = pytest.mark.integration


def _session_id() -> str:
    return str(uuid.uuid4())


async def _cleanup(session_id: str, user_id: str | None = None) -> None:
    """Best-effort removal of this test's Redis keys."""
    r = await get_redis()
    keys = [
        f"run:{session_id}",
        f"run:last:{session_id}",
        f"stream:cancel:{session_id}",
    ]
    await r.delete(*keys)
    if user_id:
        await r.srem(f"runs:user:{user_id}", session_id)


# =============================================================================
# Registry unit/integration tests
# =============================================================================


class TestRunRegistry:
    async def test_acquire_is_exclusive(self, test_user):
        sid = _session_id()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        try:
            assert lease is not None
            second = await acquire_run(sid, test_user.id, test_user.tenant_id)
            assert second is None
            current = await get_run(sid)
            assert current is not None and current.run_id == lease.run_id
            assert await release_run(lease, OUTCOME_COMPLETED) is True
            assert await get_run(sid) is None
        finally:
            await _cleanup(sid, test_user.id)

    async def test_concurrent_acquires_only_one_wins(self, test_user):
        sid = _session_id()
        first, second = await asyncio.gather(
            acquire_run(sid, test_user.id, test_user.tenant_id),
            acquire_run(sid, test_user.id, test_user.tenant_id),
        )
        try:
            winners = [lease for lease in (first, second) if lease is not None]
            assert len(winners) == 1
            current = await get_run(sid)
            assert current is not None
            assert current.run_id == winners[0].run_id
        finally:
            for lease in (first, second):
                if lease is not None:
                    await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(sid, test_user.id)

    async def test_release_by_non_owner_is_noop(self, test_user):
        sid = _session_id()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            impostor = RunLease(
                session_id=lease.session_id,
                run_id=str(uuid.uuid4()),
                kind=lease.kind,
                user_id=lease.user_id,
                tenant_id=lease.tenant_id,
                owner=lease.owner,
                started_at=lease.started_at,
                heartbeat_at=lease.heartbeat_at,
            )
            assert await release_run(impostor, OUTCOME_COMPLETED) is False
            assert await get_run(sid) is not None
        finally:
            await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(sid, test_user.id)

    async def test_heartbeat_checks_owner(self, test_user):
        sid = _session_id()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            assert await heartbeat_run(lease) is True
            impostor = RunLease(
                session_id=lease.session_id,
                run_id=str(uuid.uuid4()),
                kind=lease.kind,
                user_id=lease.user_id,
                tenant_id=lease.tenant_id,
                owner=lease.owner,
                started_at=lease.started_at,
                heartbeat_at=lease.heartbeat_at,
            )
            assert await heartbeat_run(impostor) is False
        finally:
            await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(sid, test_user.id)

    async def test_lease_expires_without_heartbeat(
        self, test_user, monkeypatch
    ):
        monkeypatch.setattr(settings, "RUN_LEASE_TTL_SECONDS", 1)
        sid = _session_id()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            await asyncio.sleep(1.2)
            assert await get_run(sid) is None
            # The per-user index self-heals when read.
            active = await get_active_runs_for_user(test_user.id)
            assert all(run.session_id != sid for run in active)
        finally:
            await _cleanup(sid, test_user.id)

    async def test_completion_marker_sets_outcome(self, test_user):
        sid = _session_id()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            assert await release_run(lease, OUTCOME_COMPLETED) is True
            assert await get_run_outcome(sid, lease.run_id) == OUTCOME_COMPLETED
            # A different (stale) run id is treated as a lost lease.
            assert await get_run_outcome(sid, str(uuid.uuid4())) == "lost"
        finally:
            await _cleanup(sid, test_user.id)

    async def test_batch_lookup_pipeline(self, test_user):
        sid_a = _session_id()
        sid_b = _session_id()
        lease = await acquire_run(sid_a, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            runs = await get_runs([sid_a, sid_b, sid_a])
            assert set(runs) == {sid_a}
            assert runs[sid_a].run_id == lease.run_id
        finally:
            await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(sid_a, test_user.id)

    async def test_cancel_targeting(self, test_user):
        sid = _session_id()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            assert await request_run_cancel(sid, str(uuid.uuid4())) is False
            assert await check_stream_cancel(sid) is False
            assert await is_run_cancel_requested(lease.run_id) is False

            assert await request_run_cancel(sid, lease.run_id) is True
            assert await check_stream_cancel(sid) is True
            # The run-scoped marker survives the runner clearing the
            # session flag, so the background task can record "cancelled".
            assert await is_run_cancel_requested(lease.run_id) is True
        finally:
            await clear_stream_cancel(sid)
            await clear_run_cancel_marker(lease.run_id)
            await release_run(lease, OUTCOME_CANCELLED)
            assert await get_run_outcome(sid, lease.run_id) == OUTCOME_CANCELLED
            await _cleanup(sid, test_user.id)

    async def test_acquire_clears_stale_cancel_flag(self, test_user):
        sid = _session_id()
        await set_stream_cancel(sid, ttl=30)
        assert await check_stream_cancel(sid) is True
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            assert await check_stream_cancel(sid) is False
        finally:
            await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(sid, test_user.id)


# =============================================================================
# HTTP behaviour tests
# =============================================================================


class TestSendLock:
    async def test_second_send_returns_409_with_run_id(
        self,
        async_client,
        auth_headers,
        test_user,
        test_session,
        db_session,
    ):
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        assert lease is not None
        try:
            resp = await async_client.post(
                f"/api/chat/session/{test_session.id}/message",
                headers={
                    **auth_headers(test_user),
                    "Accept": "text/event-stream",
                },
                json={"content": "second client should not run"},
            )
            assert resp.status_code == 409, resp.text
            body = resp.json()
            assert body["code"] == "session_busy"
            assert body["run_id"] == lease.run_id
            assert body["session_id"] == test_session.id

            # The rejected request must not persist a message.
            count = await db_session.scalar(
                select(func.count()).select_from(Message).where(
                    Message.session_id == test_session.id,
                )
            )
            assert count == 0
        finally:
            await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(test_session.id, test_user.id)

    async def test_send_acquires_and_releases_when_handler_returns(
        self, async_client, auth_headers, test_user, test_session, monkeypatch
    ):
        from src.api import chat as chat_api

        observed: dict = {}

        async def fake_handle_streaming_message(
            session_id,
            body,
            data,
            db,
            current_user,
            lease,
            modified_message="",
            valid_file_ids=None,
        ):
            observed["run_id"] = lease.run_id
            observed["active"] = await get_run(session_id) is not None
            from fastapi.responses import JSONResponse

            return JSONResponse({"ok": True})

        monkeypatch.setattr(
            chat_api,
            "_handle_streaming_message",
            fake_handle_streaming_message,
        )

        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/message",
            headers={
                **auth_headers(test_user),
                "Accept": "text/event-stream",
            },
            json={"content": "hello"},
        )
        try:
            assert resp.status_code == 200, resp.text
            assert observed.get("active") is True
            # The request-scoped lease is released once the handler
            # returns without handing ownership to a background task.
            assert await get_run(test_session.id) is None
        finally:
            await _cleanup(test_session.id, test_user.id)


class TestStatusAndSnapshot:
    async def test_stream_status_uses_registry(
        self, async_client, auth_headers, test_user, test_session
    ):
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id, kind="autopilot",
        )
        assert lease is not None
        try:
            resp = await async_client.get(
                f"/api/chat/session/{test_session.id}/stream-status",
                headers=auth_headers(test_user),
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["active"] is True
            assert body["run_id"] == lease.run_id
            assert body["run_kind"] == "autopilot"
            assert body["autopilot"] is True
        finally:
            await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(test_session.id, test_user.id)

        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/stream-status",
            headers=auth_headers(test_user),
        )
        body = resp.json()
        assert body["active"] is False
        assert body["run_id"] is None

    async def test_session_snapshots_include_running_run(
        self, async_client, auth_headers, test_user, test_session
    ):
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        assert lease is not None
        try:
            sidebar = await async_client.get(
                "/api/chat/sessions/sidebar",
                headers=auth_headers(test_user),
            )
            assert sidebar.status_code == 200, sidebar.text
            rows = [
                session
                for group in sidebar.json()["groups"]
                for session in group["sessions"]
                if session["id"] == test_session.id
            ]
            assert rows and rows[0]["running"] is True
            assert rows[0]["run_id"] == lease.run_id

            listing = await async_client.get(
                "/api/chat/sessions", headers=auth_headers(test_user),
            )
            row = next(
                s for s in listing.json() if s["id"] == test_session.id
            )
            assert row["running"] is True
            assert row["run_id"] == lease.run_id

            detail = await async_client.get(
                f"/api/chat/session/{test_session.id}",
                headers=auth_headers(test_user),
            )
            assert detail.json()["running"] is True
            assert detail.json()["run_id"] == lease.run_id
        finally:
            await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(test_session.id, test_user.id)

        after = await async_client.get(
            "/api/chat/sessions/sidebar", headers=auth_headers(test_user),
        )
        rows = [
            session
            for group in after.json()["groups"]
            for session in group["sessions"]
            if session["id"] == test_session.id
        ]
        if rows:
            assert rows[0]["running"] is False
            assert rows[0]["run_id"] is None

    async def test_cancel_with_stale_run_id_is_noop(
        self, async_client, auth_headers, test_user, test_session
    ):
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        assert lease is not None
        try:
            stale = await async_client.delete(
                f"/api/chat/session/{test_session.id}/stream",
                params={"run_id": str(uuid.uuid4())},
                headers=auth_headers(test_user),
            )
            assert stale.status_code == 204
            assert await check_stream_cancel(test_session.id) is False

            current = await async_client.delete(
                f"/api/chat/session/{test_session.id}/stream",
                params={"run_id": lease.run_id},
                headers=auth_headers(test_user),
            )
            assert current.status_code == 204
            assert await check_stream_cancel(test_session.id) is True
        finally:
            await clear_stream_cancel(test_session.id)
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(test_session.id, test_user.id)

    async def test_reconnect_rejects_stale_run_id(
        self, async_client, auth_headers, test_user, test_session
    ):
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        assert lease is not None
        bridge = StreamBridge(
            session_id=test_session.id, run_id=str(uuid.uuid4()),
        )
        register_bridge(test_session.id, bridge)
        try:
            resp = await async_client.get(
                f"/api/chat/session/{test_session.id}/stream",
                params={"run_id": lease.run_id},
                headers=auth_headers(test_user),
            )
            assert resp.status_code == 200
            assert resp.json()["active"] is False
        finally:
            remove_bridge(test_session.id)
            await release_run(lease, OUTCOME_COMPLETED)
            await _cleanup(test_session.id, test_user.id)

    async def test_status_stream_requires_auth(self, async_client):
        resp = await async_client.get("/api/chat/streams")
        assert resp.status_code == 401

    async def test_status_stream_emits_snapshot_then_finish(
        self, test_user
    ):
        sid = _session_id()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None

        disconnected = False

        async def is_disconnected() -> bool:
            return disconnected

        events = _iter_session_status_events(
            user_id=test_user.id,
            is_disconnected=is_disconnected,
            poll_interval=0.01,
        )
        try:
            first = await anext(events)
            assert first["event"] == "status_snapshot"
            assert sid in first["data"]

            await release_run(lease, OUTCOME_COMPLETED)

            second = await anext(events)
            assert second["event"] == "run_status"
            assert '"state": "finished"' in second["data"]
            assert f'"session_id": "{sid}"' in second["data"]
            assert '"outcome": "completed"' in second["data"]
        finally:
            disconnected = True
            await events.aclose()
            await _cleanup(sid, test_user.id)
