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
# =============================================================================

import uuid

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
