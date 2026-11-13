# =============================================================================
# PH Agent Hub — Composed session runtime view
# =============================================================================
# One answer per session: the live lease wins, then the durable row, then the
# short-lived completion marker.  The revision must move forward when a run
# finishes so a client can apply "highest revision wins".
# =============================================================================

import uuid

import pytest

from src.core.redis import get_redis
from src.services import run_record, session_runtime
from src.services.run_registry import (
    OUTCOME_CANCELLED,
    acquire_run,
    release_run,
)

pytestmark = pytest.mark.integration


def _sid() -> str:
    return str(uuid.uuid4())


async def _cleanup(session_id: str, user_id: str) -> None:
    r = await get_redis()
    await r.delete(
        f"run:{session_id}",
        f"run:last:{session_id}",
        f"stream:cancel:{session_id}",
        f"delete:{session_id}",
    )
    await r.srem(f"runs:user:{user_id}", session_id)


class TestResolveRuntime:
    async def test_idle_when_nothing_ran(self, db_session, test_user, test_session):
        view = await session_runtime.resolve_one(
            db_session, test_session.id, user_id=test_user.id,
        )
        assert view.state == "idle"
        assert view.revision == 0
        assert view.run_id is None

    async def test_live_lease_wins_over_terminal_row(
        self, db_session, test_user, test_session,
    ):
        # A finished run in the durable table…
        finished_id = _sid()
        await run_record.start_run(
            run_id=finished_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            db=db_session,
        )
        await run_record.finalize_run(
            run_id=finished_id,
            session_id=test_session.id,
            outcome=OUTCOME_CANCELLED,
            db=db_session,
        )

        # …and a new lease that has not written its start row yet.
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        try:
            view = await session_runtime.resolve_one(
                db_session, test_session.id, user_id=test_user.id,
            )
            assert view.state == "running"
            assert view.run_id == lease.run_id
            assert view.outcome is None
        finally:
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(test_session.id, test_user.id)

    async def test_unfinished_row_without_lease_is_lost(
        self, db_session, test_user, test_session,
    ):
        run_id = _sid()
        await run_record.start_run(
            run_id=run_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            db=db_session,
        )
        view = await session_runtime.resolve_one(
            db_session, test_session.id, user_id=test_user.id,
        )
        assert view.state == "lost"
        assert view.outcome == "lost"
        assert view.run_id == run_id

    async def test_revision_moves_forward_on_finish(
        self, db_session, test_user, test_session,
    ):
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        running = await session_runtime.resolve_one(
            db_session, test_session.id, user_id=test_user.id,
        )
        assert running.running
        running_revision = running.revision

        await run_record.start_run(
            run_id=lease.run_id,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            db=db_session,
        )
        await run_record.finalize_run(
            run_id=lease.run_id,
            session_id=test_session.id,
            outcome=OUTCOME_CANCELLED,
            db=db_session,
        )
        await release_run(lease, OUTCOME_CANCELLED)
        try:
            finished = await session_runtime.resolve_one(
                db_session, test_session.id, user_id=test_user.id,
            )
            assert finished.state == OUTCOME_CANCELLED
            assert finished.revision > running_revision
        finally:
            await _cleanup(test_session.id, test_user.id)

    async def test_marker_is_not_visible_to_another_user(
        self, db_session, test_user, test_session,
    ):
        sid = _sid()
        other_user = _sid()
        lease = await acquire_run(sid, other_user, test_user.tenant_id)
        assert lease is not None
        await release_run(lease, OUTCOME_CANCELLED)
        try:
            view = await session_runtime.resolve_one(
                db_session, sid, user_id=test_user.id,
            )
            # No durable row and another user's marker: idle, not cancelled.
            assert view.state == "idle"
        finally:
            await _cleanup(sid, other_user)

    async def test_marker_falls_back_when_row_missing(
        self, db_session, test_user, test_session,
    ):
        sid = _sid()
        lease = await acquire_run(sid, test_user.id, test_user.tenant_id)
        assert lease is not None
        await release_run(lease, OUTCOME_CANCELLED)
        try:
            view = await session_runtime.resolve_one(
                db_session, sid, user_id=test_user.id,
            )
            assert view.state == OUTCOME_CANCELLED
            assert view.outcome == OUTCOME_CANCELLED
            assert view.revision > 0
        finally:
            await _cleanup(sid, test_user.id)

    async def test_batch_resolution_returns_each_session(
        self, db_session, test_user, test_session,
    ):
        missing = _sid()
        view = await session_runtime.resolve_runtime(
            db_session,
            [test_session.id, missing, test_session.id],
            user_id=test_user.id,
        )
        assert set(view) == {test_session.id, missing}
        assert view[missing].state == "idle"
