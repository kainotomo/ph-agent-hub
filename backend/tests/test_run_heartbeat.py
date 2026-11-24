# =============================================================================
# PH Agent Hub — Run lease heartbeat (Issue #632)
# =============================================================================
# The long-chat bug report showed a live run being declared lost because its
# Redis lease expired while the process kept working.  These tests pin the
# three guarantees that prevent it:
#
#   * the heartbeat starts when the run is admitted (so a slow pre-stream
#     phase cannot outlive the lease),
#   * an expired key is re-acquired with the *same* run id (never a second
#     competing run), and
#   * a superseded run stops heartbeating instead of fighting the new owner.
# =============================================================================

import asyncio
import uuid

import pytest

from src.api.chat import _session_run
from src.core.config import settings
from src.core.redis import get_redis
from src.services import run_heartbeat, run_record
from src.services.run_registry import (
    OUTCOME_CANCELLED,
    acquire_run,
    get_run,
    reacquire_run,
    release_run,
)

pytestmark = pytest.mark.integration


def _sid() -> str:
    return str(uuid.uuid4())


async def _cleanup(session_id: str, user_id: str | None = None) -> None:
    r = await get_redis()
    await r.delete(
        f"run:{session_id}",
        f"run:last:{session_id}",
        f"stream:cancel:{session_id}",
    )
    if user_id:
        await r.srem(f"runs:user:{user_id}", session_id)


def _fast_heartbeat(monkeypatch) -> None:
    """Small TTL/interval so the tests can cross the expiry boundary quickly."""
    monkeypatch.setattr(settings, "RUN_LEASE_TTL_SECONDS", 1)
    monkeypatch.setattr(settings, "RUN_HEARTBEAT_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(settings, "RUN_DB_HEARTBEAT_INTERVAL_SECONDS", 10.0)


# =============================================================================
# Re-acquire (registry level)
# =============================================================================


class TestReacquire:
    async def test_reacquire_restores_the_same_run_id(self, test_user):
        sid = _sid()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            r = await get_redis()
            await r.delete(f"run:{sid}")
            assert await get_run(sid) is None

            assert await reacquire_run(lease) is True

            current = await get_run(sid)
            assert current is not None
            assert current.run_id == lease.run_id
            assert current.session_id == sid
        finally:
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(sid, test_user.id)

    async def test_reacquire_does_not_steal_another_run(self, test_user):
        sid = _sid()
        first = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert first is not None
        second = None
        try:
            # The first run's key expired and a second run took the session.
            r = await get_redis()
            await r.delete(f"run:{sid}")
            second = await acquire_run(sid, test_user.id, test_user.tenant_id)
            assert second is not None

            assert await reacquire_run(first) is False
            current = await get_run(sid)
            assert current is not None
            assert current.run_id == second.run_id
        finally:
            if second is not None:
                await release_run(second, OUTCOME_CANCELLED)
            await _cleanup(sid, test_user.id)


# =============================================================================
# Heartbeat lifecycle
# =============================================================================


class TestHeartbeatLifecycle:
    async def test_lease_survives_a_long_pre_stream_phase(
        self, test_user, monkeypatch,
    ):
        """Issue #632 — admission starts the heartbeat, not the agent task.

        The request handler holds the lease while it does pre-stream work
        (auto-routing, attachment validation); that phase used to outlive the
        6 s lease.  The lease must stay owned for the whole block.
        """
        _fast_heartbeat(monkeypatch)
        sid = _sid()

        async with _session_run(sid, test_user, kind="chat") as lease:
            await asyncio.sleep(1.5)
            current = await get_run(sid)
            assert current is not None
            assert current.run_id == lease.run_id

        # ...and it is still released when the request ends.
        assert await get_run(sid) is None
        assert run_heartbeat.active_heartbeat_count() == 0

    async def test_keeps_lease_alive_past_the_ttl(self, test_user, monkeypatch):
        _fast_heartbeat(monkeypatch)
        sid = _sid()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            await run_heartbeat.start_run_heartbeat(lease)
            await asyncio.sleep(1.6)

            current = await get_run(sid)
            assert current is not None
            assert current.run_id == lease.run_id
        finally:
            await run_heartbeat.stop_run_heartbeat(lease.run_id)
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(sid, test_user.id)

    async def test_reacquires_after_the_key_is_deleted(
        self, test_user, monkeypatch,
    ):
        _fast_heartbeat(monkeypatch)
        sid = _sid()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            await run_heartbeat.start_run_heartbeat(lease)
            r = await get_redis()
            await r.delete(f"run:{sid}")
            assert await get_run(sid) is None

            await asyncio.sleep(0.5)

            current = await get_run(sid)
            assert current is not None
            assert current.run_id == lease.run_id
        finally:
            await run_heartbeat.stop_run_heartbeat(lease.run_id)
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(sid, test_user.id)

    async def test_stops_when_the_lease_cannot_be_reacquired(
        self, test_user, monkeypatch,
    ):
        """Issue #632 — a superseded run must not keep fighting for the session.

        The registry-level guarantee is covered by
        :meth:`TestReacquire.test_reacquire_does_not_steal_another_run`; this
        pins the loop's reaction: a heartbeat that cannot be refreshed or
        re-acquired ends and de-registers instead of retrying forever.
        """
        _fast_heartbeat(monkeypatch)

        async def _lost(_lease) -> bool:
            return False

        monkeypatch.setattr(run_heartbeat, "heartbeat_run", _lost)
        monkeypatch.setattr(run_heartbeat, "reacquire_run", _lost)

        sid = _sid()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            await run_heartbeat.start_run_heartbeat(lease)
            assert run_heartbeat.active_heartbeat_count() == 1

            await asyncio.sleep(0.3)

            assert run_heartbeat.active_heartbeat_count() == 0
        finally:
            await run_heartbeat.stop_run_heartbeat(lease.run_id)
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(sid, test_user.id)

    async def test_schedules_the_durable_heartbeat(self, test_user, monkeypatch):
        """The DB heartbeat is scheduled off the lease path, never awaited."""
        _fast_heartbeat(monkeypatch)
        monkeypatch.setattr(settings, "RUN_DB_HEARTBEAT_INTERVAL_SECONDS", 0.1)
        calls: list[str] = []

        async def _fake_touch(run_id: str, **kwargs):
            calls.append(run_id)
            return True

        monkeypatch.setattr(run_record, "touch_run_heartbeat", _fake_touch)

        sid = _sid()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        try:
            await run_heartbeat.start_run_heartbeat(lease)
            await asyncio.sleep(0.4)
        finally:
            await run_heartbeat.stop_run_heartbeat(lease.run_id)
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(sid, test_user.id)

        assert lease.run_id in calls

    async def test_stop_all_stops_every_heartbeat(self, test_user, monkeypatch):
        _fast_heartbeat(monkeypatch)
        sid_a = _sid()
        sid_b = _sid()
        lease_a = await acquire_run(sid_a, test_user.id, test_user.tenant_id)
        lease_b = await acquire_run(sid_b, test_user.id, test_user.tenant_id)
        assert lease_a is not None
        assert lease_b is not None
        try:
            await run_heartbeat.start_run_heartbeat(lease_a)
            await run_heartbeat.start_run_heartbeat(lease_b)
            assert run_heartbeat.active_heartbeat_count() == 2

            stopped = await run_heartbeat.stop_all_run_heartbeats()

            assert stopped == 2
            assert run_heartbeat.active_heartbeat_count() == 0
        finally:
            await release_run(lease_a, OUTCOME_CANCELLED)
            await release_run(lease_b, OUTCOME_CANCELLED)
            await _cleanup(sid_a, test_user.id)
            await _cleanup(sid_b, test_user.id)
