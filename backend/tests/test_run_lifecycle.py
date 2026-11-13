# =============================================================================
# PH Agent Hub — Run / session lifecycle hardening
# =============================================================================
# Covers the failure modes that let a run or a scheduled task get stuck:
#   * the scheduler loop's missing setting and its non-atomic dedup guard,
#   * a stale `running` row whose lease is gone (another replica crashed),
#   * the status snapshot losing an outcome after the Redis marker expires,
#   * deleting a session/message while a run is still writing into it.
# =============================================================================

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.core.config import settings
from src.core.exceptions import SessionBusyError
from src.core.redis import get_redis
from src.services import run_record, session_service
from src.services.run_registry import (
    OUTCOME_CANCELLED,
    OUTCOME_LOST,
    acquire_run,
    begin_session_delete,
    clear_session_run_state,
    end_session_delete,
    get_run,
    release_run,
    request_run_cancel,
)
from src.services.scheduled_task_service import (
    claim_due_task,
    create_scheduled_task,
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
        f"delete:{session_id}",
    )
    if user_id:
        await r.srem(f"runs:user:{user_id}", session_id)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class TestSchedulerClaim:
    def test_loop_setting_exists(self):
        """The loop reads this attribute before its try block; a missing
        setting made the whole scheduler raise AttributeError on tick one."""
        assert settings.SCHEDULER_POLL_INTERVAL_SECONDS > 0
        assert settings.SCHEDULER_CLAIM_TTL_SECONDS > 0

    async def test_claim_is_compare_and_swap(self, db_session, test_user):
        task = await create_scheduled_task(
            db_session,
            tenant_id=test_user.tenant_id,
            user_id=test_user.id,
            goal="daily digest",
            schedule_description="every day",
            cron_expression="0 9 * * *",
            timezone="UTC",
        )
        try:
            due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            task.next_run_at = due_at
            await db_session.flush()

            first = await claim_due_task(
                db_session, task.id, due_at, claim_ttl_seconds=60,
            )
            assert first is True

            # Same expected value again → the row no longer matches.
            second = await claim_due_task(
                db_session, task.id, due_at, claim_ttl_seconds=60,
            )
            assert second is False

            await db_session.refresh(task)
            assert task.next_run_at is not None
            # A crashed executor must leave the task due again, never
            # silently disabled.
            assert task.next_run_at > due_at.replace(tzinfo=None)
        finally:
            await db_session.delete(task)
            await db_session.flush()

    async def test_claim_survives_crash_without_record(self, db_session, test_user):
        """After a claim with no `record_run_result`, the task is only
        deferred by the claim window — never cleared."""
        task = await create_scheduled_task(
            db_session,
            tenant_id=test_user.tenant_id,
            user_id=test_user.id,
            goal="hourly report",
            schedule_description="hourly",
            cron_expression="0 * * * *",
            timezone="UTC",
        )
        try:
            due_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            task.next_run_at = due_at
            await db_session.flush()

            assert await claim_due_task(
                db_session, task.id, due_at, claim_ttl_seconds=900,
            )
            await db_session.refresh(task)
            assert task.next_run_at is not None

            # Once the claim window passes the task is claimable again.
            later = task.next_run_at
            task.next_run_at = due_at
            await db_session.flush()
            assert await claim_due_task(
                db_session, task.id, due_at, claim_ttl_seconds=900,
            )
        finally:
            await db_session.delete(task)
            await db_session.flush()


# ---------------------------------------------------------------------------
# Stale `running` rows
# ---------------------------------------------------------------------------


class TestRunStateRepair:
    async def test_stale_running_row_becomes_lost(
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

        state = await run_record.repair_unfinished_run(db_session, test_session.id)

        assert state is not None
        assert state["state"] == OUTCOME_LOST
        assert state["finished_at"] is not None

    async def test_live_lease_keeps_run_running(
        self, db_session, test_user, test_session,
    ):
        run_id = _sid()
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        try:
            # start_run uses the lease id in production; use it here too.
            await run_record.start_run(
                run_id=lease.run_id,
                session_id=test_session.id,
                user_id=test_user.id,
                tenant_id=test_user.tenant_id,
                db=db_session,
            )
            state = await run_record.repair_unfinished_run(
                db_session, test_session.id,
            )
            assert state is not None
            assert state["state"] == "running"
        finally:
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(test_session.id, test_user.id)

    async def test_outcome_fallback_reads_durable_row(
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
        assert await run_record.get_outcome_from_db(
            db_session,
            session_id=test_session.id,
            run_id=run_id,
            user_id=test_user.id,
        ) is None

        await run_record.finalize_run(
            run_id=run_id,
            session_id=test_session.id,
            outcome=OUTCOME_CANCELLED,
            db=db_session,
        )
        assert await run_record.get_outcome_from_db(
            db_session,
            session_id=test_session.id,
            run_id=run_id,
            user_id=test_user.id,
        ) == OUTCOME_CANCELLED
        # Owner scoping: another user never sees the outcome.
        assert await run_record.get_outcome_from_db(
            db_session,
            session_id=test_session.id,
            run_id=run_id,
            user_id=_sid(),
        ) is None


# ---------------------------------------------------------------------------
# Deletion vs. live run
# ---------------------------------------------------------------------------


class TestDeleteWithLiveRun:
    async def test_delete_session_refuses_live_run(
        self, db_session, test_user, test_session,
    ):
        lease = await acquire_run(
            test_session.id, test_user.id, test_user.tenant_id,
        )
        try:
            with pytest.raises(SessionBusyError):
                await session_service.delete_session(db_session, test_session.id)
            assert await get_run(test_session.id) is not None
        finally:
            await release_run(lease, OUTCOME_CANCELLED)
            await _cleanup(test_session.id, test_user.id)

    async def test_batch_delete_skips_busy_and_cleans_state(
        self, db_session, test_user, test_tenant, test_model,
    ):
        from src.db.orm.sessions import Session

        idle = Session(
            id=_sid(),
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Test idle session",
            is_temporary=False,
            selected_model_id=test_model.id,
        )
        busy = Session(
            id=_sid(),
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Test busy session",
            is_temporary=False,
            selected_model_id=test_model.id,
        )
        db_session.add_all([idle, busy])
        await db_session.flush()

        lease = await acquire_run(busy.id, test_user.id, test_user.tenant_id)
        await request_run_cancel(busy.id, lease.run_id)
        try:
            result = await session_service.delete_sessions_batch(
                db_session, [idle.id, busy.id], test_user.id, test_user.tenant_id,
            )
            assert result["deleted"] == 1
            reasons = {s["id"]: s["reason"] for s in result["skipped"]}
            assert reasons[busy.id] == "Agent is running"
        finally:
            await release_run(lease, OUTCOME_CANCELLED)
            await clear_session_run_state(busy.id, test_user.id)
            await _cleanup(busy.id, test_user.id)

    async def test_delete_lock_blocks_new_runs(
        self, db_session, test_user, test_session,
    ):
        token = await begin_session_delete(test_session.id)
        assert token is not None
        try:
            assert await acquire_run(
                test_session.id, test_user.id, test_user.tenant_id,
            ) is None
            assert await begin_session_delete(test_session.id) is None
        finally:
            await end_session_delete(test_session.id, token)
            await _cleanup(test_session.id, test_user.id)
