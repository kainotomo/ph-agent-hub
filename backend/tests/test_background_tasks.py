# =============================================================================
# PH Agent Hub — Background Tasks & Notifications API Tests
# =============================================================================
# Tests for:
#   - GET /api/background-tasks
#   - GET /api/background-tasks/{task_id}
#   - DELETE /api/background-tasks/{task_id}
#   - GET /api/notifications
#   - GET /api/notifications/unread-count
#   - POST /api/notifications/{id}/read
#   - POST /api/notifications/read-all
#   - DELETE /api/notifications/read
#   - POST /api/notifications/run-finished
# =============================================================================

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.autopilot_runs import AutopilotRun
from src.db.orm.notifications import Notification
from src.db.orm.sessions import Session
from src.main import app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.background_tasks,
]


# =============================================================================
# Fixtures
# =============================================================================


@pytest_asyncio.fixture
async def test_session(db_session: AsyncSession, test_user, test_tenant) -> Session:
    """Create a test session owned by test_user."""
    session = Session(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Test Session",
        auto_route_enabled=False,
        auto_select_tools=True,
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest_asyncio.fixture
async def background_task(db_session: AsyncSession, test_session) -> AutopilotRun:
    """Create a completed background task owned by test_user."""
    task = AutopilotRun(
        id=str(uuid.uuid4()),
        session_id=test_session.id,
        goal="Test background task",
        state=AutopilotRun.STATE_COMPLETED,
        current_turn=3,
        max_turns=10,
        background_task=True,
        progress_message="Completed",
        result_summary="Test result summary",
        notification_sent=True,
    )
    db_session.add(task)
    await db_session.flush()
    return task


@pytest_asyncio.fixture
async def running_task(db_session: AsyncSession, test_session) -> AutopilotRun:
    """Create a currently running background task."""
    task = AutopilotRun(
        id=str(uuid.uuid4()),
        session_id=test_session.id,
        goal="Running task",
        state=AutopilotRun.STATE_EXECUTING,
        current_turn=1,
        max_turns=10,
        background_task=True,
        progress_message="Working…",
    )
    db_session.add(task)
    await db_session.flush()
    return task


@pytest_asyncio.fixture
async def test_notification(
    db_session: AsyncSession,
    test_user,
    test_tenant,
    background_task,
) -> Notification:
    """Create a test notification."""
    notification = Notification(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        tenant_id=test_tenant.id,
        type=Notification.TYPE_TASK_COMPLETED,
        title="Task completed",
        body="Test result summary",
        reference_id=background_task.id,
        reference_type="autopilot_run",
        is_read=False,
    )
    db_session.add(notification)
    await db_session.flush()
    return notification


@pytest_asyncio.fixture
async def read_notification(
    db_session: AsyncSession,
    test_user,
    test_tenant,
    background_task,
) -> Notification:
    """Create an already-read notification."""
    notification = Notification(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        tenant_id=test_tenant.id,
        type=Notification.TYPE_TASK_COMPLETED,
        title="Old notification",
        body="Already seen",
        reference_id=background_task.id,
        reference_type="autopilot_run",
        is_read=True,
    )
    db_session.add(notification)
    await db_session.flush()
    return notification


# =============================================================================
# Background Tasks — Tests
# =============================================================================


class TestBackgroundTasksList:
    async def test_list_empty(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
    ) -> None:
        """GET /api/background-tasks returns empty list for user with no tasks."""
        headers = auth_headers(test_user)
        response = await async_client.get("/api/background-tasks", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    async def test_list_with_tasks(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        background_task,
        running_task,
    ) -> None:
        """GET /api/background-tasks returns all tasks for the user."""
        headers = auth_headers(test_user)
        response = await async_client.get("/api/background-tasks", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        # Tasks are ordered newest first
        task_ids = [t["id"] for t in data["items"]]
        assert background_task.id in task_ids
        assert running_task.id in task_ids

    async def test_list_filter_by_state(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        background_task,
        running_task,
    ) -> None:
        """GET /api/background-tasks?state=EXECUTING filters correctly."""
        headers = auth_headers(test_user)
        response = await async_client.get(
            "/api/background-tasks?state=EXECUTING",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == running_task.id
        assert data["items"][0]["state"] == "EXECUTING"

    async def test_list_other_user_not_visible(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        db_session: AsyncSession,
        test_tenant,
        background_task,
    ) -> None:
        """A different user cannot see another user's background tasks."""
        # Create another user
        from src.db.orm.users import User as UserORM

        other_user = UserORM(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            email=f"other-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="fakehash",
            display_name="Other User",
            role="user",
            is_active=True,
        )
        db_session.add(other_user)
        await db_session.flush()

        headers = auth_headers(other_user)
        response = await async_client.get("/api/background-tasks", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestBackgroundTaskDetail:
    async def test_get_detail(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        background_task,
    ) -> None:
        """GET /api/background-tasks/{id} returns task details."""
        headers = auth_headers(test_user)
        response = await async_client.get(
            f"/api/background-tasks/{background_task.id}",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == background_task.id
        assert data["goal"] == "Test background task"
        assert data["state"] == "COMPLETED"
        assert data["current_turn"] == 3
        assert data["max_turns"] == 10
        assert data["progress_message"] == "Completed"
        assert data["result_summary"] == "Test result summary"

    async def test_get_not_found(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
    ) -> None:
        """GET /api/background-tasks/{id} returns 404 for nonexistent task."""
        headers = auth_headers(test_user)
        response = await async_client.get(
            f"/api/background-tasks/{uuid.uuid4()}",
            headers=headers,
        )
        assert response.status_code == 404


class TestBackgroundTaskCancel:
    async def test_cancel_running(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        running_task,
    ) -> None:
        """DELETE /api/background-tasks/{id} cancels a running task."""
        headers = auth_headers(test_user)
        response = await async_client.delete(
            f"/api/background-tasks/{running_task.id}",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == running_task.id

    async def test_cancel_completed_fails(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        background_task,
    ) -> None:
        """DELETE /api/background-tasks/{id} returns 422 for completed tasks."""
        headers = auth_headers(test_user)
        response = await async_client.delete(
            f"/api/background-tasks/{background_task.id}",
            headers=headers,
        )
        assert response.status_code == 422  # ValidationError

    async def test_cancel_not_found(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
    ) -> None:
        """DELETE /api/background-tasks/{id} returns 404 for nonexistent task."""
        headers = auth_headers(test_user)
        response = await async_client.delete(
            f"/api/background-tasks/{uuid.uuid4()}",
            headers=headers,
        )
        assert response.status_code == 404


# =============================================================================
# Notifications — Tests
# =============================================================================


class TestNotificationsList:
    async def test_list_empty(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
    ) -> None:
        """GET /api/notifications returns empty list."""
        headers = auth_headers(test_user)
        response = await async_client.get("/api/notifications", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    async def test_list_with_notifications(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_notification,
        read_notification,
    ) -> None:
        """GET /api/notifications returns all notifications."""
        headers = auth_headers(test_user)
        response = await async_client.get("/api/notifications", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    async def test_list_unread_only(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_notification,
        read_notification,
    ) -> None:
        """GET /api/notifications?unread_only=true returns only unread."""
        headers = auth_headers(test_user)
        response = await async_client.get(
            "/api/notifications?unread_only=true",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["is_read"] is False


class TestUnreadCount:
    async def test_unread_count(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_notification,
        read_notification,
    ) -> None:
        """GET /api/notifications/unread-count returns the correct count."""
        headers = auth_headers(test_user)
        response = await async_client.get(
            "/api/notifications/unread-count",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

    async def test_unread_count_zero(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
    ) -> None:
        """GET /api/notifications/unread-count returns 0 when no notifications."""
        headers = auth_headers(test_user)
        response = await async_client.get(
            "/api/notifications/unread-count",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0


class TestMarkRead:
    async def test_mark_read(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_notification,
    ) -> None:
        """POST /api/notifications/{id}/read marks a notification as read."""
        headers = auth_headers(test_user)
        response = await async_client.post(
            f"/api/notifications/{test_notification.id}/read",
            headers=headers,
        )
        assert response.status_code == 200
        # Verify unread count decreased
        count_resp = await async_client.get(
            "/api/notifications/unread-count",
            headers=headers,
        )
        assert count_resp.json()["count"] == 0

    async def test_mark_read_not_found(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
    ) -> None:
        """POST /api/notifications/{id}/read returns 404 for unknown id."""
        headers = auth_headers(test_user)
        response = await async_client.post(
            f"/api/notifications/{uuid.uuid4()}/read",
            headers=headers,
        )
        assert response.status_code == 404

    async def test_mark_read_other_user(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        db_session: AsyncSession,
        test_tenant,
        test_notification,
    ) -> None:
        """A different user cannot mark another user's notification as read."""
        from src.db.orm.users import User as UserORM

        other_user = UserORM(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            email=f"other-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="fakehash",
            display_name="Other User",
            role="user",
            is_active=True,
        )
        db_session.add(other_user)
        await db_session.flush()

        headers = auth_headers(other_user)
        response = await async_client.post(
            f"/api/notifications/{test_notification.id}/read",
            headers=headers,
        )
        assert response.status_code == 404


class TestMarkAllRead:
    async def test_mark_all_read(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_notification,
        read_notification,
    ) -> None:
        """POST /api/notifications/read-all marks all as read."""
        headers = auth_headers(test_user)
        response = await async_client.post(
            "/api/notifications/read-all",
            headers=headers,
        )
        assert response.status_code == 200
        count_resp = await async_client.get(
            "/api/notifications/unread-count",
            headers=headers,
        )
        assert count_resp.json()["count"] == 0


class TestDeleteRead:
    async def test_delete_read_removes_only_read(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_notification,
        read_notification,
    ) -> None:
        """DELETE /api/notifications/read removes read rows and keeps unread."""
        headers = auth_headers(test_user)
        response = await async_client.delete(
            "/api/notifications/read",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "deleted": 1}

        list_resp = await async_client.get("/api/notifications", headers=headers)
        data = list_resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == test_notification.id
        assert data["items"][0]["is_read"] is False

        count_resp = await async_client.get(
            "/api/notifications/unread-count",
            headers=headers,
        )
        assert count_resp.json()["count"] == 1

    async def test_delete_read_removes_all_when_all_read(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        read_notification,
    ) -> None:
        """DELETE /api/notifications/read empties the list when all rows are read."""
        headers = auth_headers(test_user)
        response = await async_client.delete(
            "/api/notifications/read",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "deleted": 1}

        list_resp = await async_client.get("/api/notifications", headers=headers)
        assert list_resp.json()["total"] == 0

    async def test_delete_read_nothing_to_delete(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_notification,
    ) -> None:
        """DELETE /api/notifications/read reports zero when only unread rows exist."""
        headers = auth_headers(test_user)
        response = await async_client.delete(
            "/api/notifications/read",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "deleted": 0}

        list_resp = await async_client.get("/api/notifications", headers=headers)
        assert list_resp.json()["total"] == 1

    async def test_delete_read_other_user_not_affected(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        db_session: AsyncSession,
        test_tenant,
    ) -> None:
        """A user's delete-read must not touch another user's notifications."""
        from src.db.orm.users import User as UserORM

        other_user = UserORM(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            email=f"other-delete-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="fakehash",
            display_name="Other Delete User",
            role="user",
            is_active=True,
        )
        db_session.add(other_user)
        await db_session.flush()

        other_notification = Notification(
            id=str(uuid.uuid4()),
            user_id=other_user.id,
            tenant_id=test_tenant.id,
            type=Notification.TYPE_TASK_COMPLETED,
            title="Other user read notification",
            body=None,
            reference_id=None,
            reference_type=None,
            is_read=True,
        )
        db_session.add(other_notification)
        await db_session.flush()

        headers = auth_headers(test_user)
        response = await async_client.delete(
            "/api/notifications/read",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["deleted"] == 0

        other_headers = auth_headers(other_user)
        list_resp = await async_client.get(
            "/api/notifications",
            headers=other_headers,
        )
        assert list_resp.json()["total"] == 1


class TestRecordRunNotification:
    """POST /api/notifications/run-finished — browser-notified runs (#669)."""

    @staticmethod
    def _run_started_a_minute_ago() -> str:
        return (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()

    @staticmethod
    async def _enable_notifications(db_session: AsyncSession, test_user) -> None:
        test_user.notification_prefs = {
            "enabled": True,
            "on_completion": True,
            "on_failure": True,
            "sound": False,
        }
        await db_session.flush()

    async def test_records_a_notified_completion(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_session,
        db_session: AsyncSession,
    ) -> None:
        """A browser-notified completion becomes an unread bell record."""
        await self._enable_notifications(db_session, test_user)
        headers = auth_headers(test_user)
        response = await async_client.post(
            "/api/notifications/run-finished",
            headers=headers,
            json={
                "session_id": test_session.id,
                "outcome": "completed",
                "started_at": self._run_started_a_minute_ago(),
            },
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "created": True}

        list_resp = await async_client.get("/api/notifications", headers=headers)
        data = list_resp.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["type"] == Notification.TYPE_CHAT_RUN_COMPLETED
        assert item["title"] == "Response ready"
        assert item["is_read"] is False
        assert item["reference_id"] == test_session.id
        assert item["reference_type"] == "session"

        count_resp = await async_client.get(
            "/api/notifications/unread-count",
            headers=headers,
        )
        assert count_resp.json()["count"] == 1

    async def test_is_idempotent_for_the_same_run(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_session,
        db_session: AsyncSession,
    ) -> None:
        """Calling twice for one run writes a single record."""
        await self._enable_notifications(db_session, test_user)
        headers = auth_headers(test_user)
        payload = {
            "session_id": test_session.id,
            "outcome": "completed",
            "started_at": self._run_started_a_minute_ago(),
        }

        first = await async_client.post(
            "/api/notifications/run-finished", headers=headers, json=payload
        )
        second = await async_client.post(
            "/api/notifications/run-finished", headers=headers, json=payload
        )
        assert first.json()["created"] is True
        assert second.json()["created"] is False

        list_resp = await async_client.get("/api/notifications", headers=headers)
        assert list_resp.json()["total"] == 1

    async def test_respects_notification_preferences(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_session,
    ) -> None:
        """Nothing is stored when browser notifications are disabled."""
        headers = auth_headers(test_user)
        response = await async_client.post(
            "/api/notifications/run-finished",
            headers=headers,
            json={
                "session_id": test_session.id,
                "outcome": "completed",
                "started_at": self._run_started_a_minute_ago(),
            },
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "created": False}

        list_resp = await async_client.get("/api/notifications", headers=headers)
        assert list_resp.json()["total"] == 0

    async def test_records_a_notified_failure(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        test_session,
        db_session: AsyncSession,
    ) -> None:
        """A failed run is recorded with the failure copy and type."""
        await self._enable_notifications(db_session, test_user)
        headers = auth_headers(test_user)
        response = await async_client.post(
            "/api/notifications/run-finished",
            headers=headers,
            json={
                "session_id": test_session.id,
                "outcome": "failed",
                "started_at": self._run_started_a_minute_ago(),
            },
        )
        assert response.json()["created"] is True

        list_resp = await async_client.get("/api/notifications", headers=headers)
        item = list_resp.json()["items"][0]
        assert item["type"] == Notification.TYPE_CHAT_RUN_FAILED
        assert item["title"] == "Run failed"

    async def test_rejects_another_users_session(
        self,
        override_get_db,
        async_client: httpx.AsyncClient,
        auth_headers,
        test_user,
        db_session: AsyncSession,
        test_tenant,
    ) -> None:
        """A user cannot record a notification for someone else's session."""
        from src.db.orm.users import User as UserORM

        await self._enable_notifications(db_session, test_user)
        other_user = UserORM(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            email=f"other-run-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="fakehash",
            display_name="Other Run User",
            role="user",
            is_active=True,
        )
        db_session.add(other_user)
        await db_session.flush()
        other_session = Session(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            user_id=other_user.id,
            title="Other user's session",
            auto_route_enabled=False,
            auto_select_tools=True,
        )
        db_session.add(other_session)
        await db_session.flush()

        headers = auth_headers(test_user)
        response = await async_client.post(
            "/api/notifications/run-finished",
            headers=headers,
            json={
                "session_id": other_session.id,
                "outcome": "completed",
                "started_at": self._run_started_a_minute_ago(),
            },
        )
        assert response.status_code == 404

        list_resp = await async_client.get("/api/notifications", headers=headers)
        assert list_resp.json()["total"] == 0
