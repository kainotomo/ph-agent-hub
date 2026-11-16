# =============================================================================
# PH Agent Hub — Run Outcome Notification Tests (Issue #619)
# =============================================================================

from datetime import datetime, timedelta, timezone

import pytest

from src.db.orm.notifications import Notification
from src.services.run_notifications import (
    record_run_outcome,
    should_record_outcome,
)

pytestmark = [pytest.mark.integration]

# Five minutes in the past: any record created by this test is newer than
# the run start, which is what the duplicate guard compares against.
STARTED_AT = datetime.now(timezone.utc) - timedelta(minutes=5)

ALL_PREFS = {
    "enabled": True,
    "on_completion": True,
    "on_failure": True,
    "sound": False,
}


async def _set_prefs(db_session, user, **overrides):
    prefs = dict(ALL_PREFS)
    prefs.update(overrides)
    user.notification_prefs = prefs
    await db_session.flush()


class TestOutcomePolicy:
    def test_failed_always_records(self):
        assert should_record_outcome(
            "failed", duration_s=1, attached=True
        ) == Notification.TYPE_CHAT_RUN_FAILED

    def test_completed_attached_does_not_record(self):
        assert should_record_outcome(
            "completed", duration_s=600, attached=True
        ) is None

    def test_completed_unattached_short_does_not_record(self):
        assert should_record_outcome(
            "completed", duration_s=5, attached=False
        ) is None

    def test_completed_unattached_long_records(self):
        assert should_record_outcome(
            "completed", duration_s=600, attached=False
        ) == Notification.TYPE_CHAT_RUN_COMPLETED

    def test_cancelled_and_lost_never_record(self):
        for outcome in ("cancelled", "lost"):
            assert should_record_outcome(
                outcome, duration_s=600, attached=False
            ) is None


class TestRecordRunOutcome:
    async def test_failed_run_creates_session_notification(
        self, db_session, test_user, test_session
    ):
        await _set_prefs(db_session, test_user)
        notification = await record_run_outcome(
            db_session,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            outcome="failed",
            duration_s=3,
            attached=True,
            started_at=STARTED_AT,
        )
        assert notification is not None
        assert notification.type == Notification.TYPE_CHAT_RUN_FAILED
        assert notification.reference_id == test_session.id
        assert notification.reference_type == "session"
        assert "Test Session" in (notification.body or "")

    async def test_completed_run_with_subscriber_is_ignored(
        self, db_session, test_user, test_session
    ):
        await _set_prefs(db_session, test_user)
        notification = await record_run_outcome(
            db_session,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            outcome="completed",
            duration_s=600,
            attached=True,
            started_at=STARTED_AT,
        )
        assert notification is None

    async def test_long_unwatched_run_creates_notification(
        self, db_session, test_user, test_session
    ):
        await _set_prefs(db_session, test_user)
        notification = await record_run_outcome(
            db_session,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            outcome="completed",
            duration_s=600,
            attached=False,
            started_at=STARTED_AT,
        )
        assert notification is not None
        assert notification.type == Notification.TYPE_CHAT_RUN_COMPLETED

    async def test_same_run_is_recorded_once(
        self, db_session, test_user, test_session
    ):
        await _set_prefs(db_session, test_user)
        kwargs = dict(
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            outcome="failed",
            duration_s=3,
            attached=True,
            started_at=STARTED_AT,
        )
        first = await record_run_outcome(db_session, **kwargs)
        second = await record_run_outcome(db_session, **kwargs)
        assert first is not None
        assert second is None


class TestUserPreferences:
    async def test_disabled_master_switch_records_nothing(
        self, db_session, test_user, test_session
    ):
        await _set_prefs(db_session, test_user, enabled=False)
        notification = await record_run_outcome(
            db_session,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            outcome="failed",
            duration_s=3,
            attached=True,
            started_at=STARTED_AT,
        )
        assert notification is None

    async def test_failure_toggle_off_records_nothing(
        self, db_session, test_user, test_session
    ):
        await _set_prefs(db_session, test_user, on_failure=False)
        notification = await record_run_outcome(
            db_session,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            outcome="failed",
            duration_s=3,
            attached=True,
            started_at=STARTED_AT,
        )
        assert notification is None

    async def test_completion_toggle_off_records_nothing(
        self, db_session, test_user, test_session
    ):
        await _set_prefs(db_session, test_user, on_completion=False)
        notification = await record_run_outcome(
            db_session,
            session_id=test_session.id,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            outcome="completed",
            duration_s=600,
            attached=False,
            started_at=STARTED_AT,
        )
        assert notification is None
