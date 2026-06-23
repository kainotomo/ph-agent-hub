# =============================================================================
# PH Agent Hub — A2A Call Logs Admin API Tests
# =============================================================================
# Tests for GET /admin/a2a-call-logs endpoint: listing, filtering,
# pagination, tenant scoping, and auth guards.
# =============================================================================

import uuid
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.jwt import create_access_token
from src.db.orm.a2a_call_logs import A2aCallLog
from src.main import app

pytestmark = [
    pytest.mark.integration,
]


# ---------------------------------------------------------------------------
# Fixture: local async_client with test DB override
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Helper: create a call log row directly in the test DB
# ---------------------------------------------------------------------------
async def create_call_log(
    db_session: AsyncSession,
    *,
    tenant_id: str,
    a2a_server_id: str = "server-1",
    a2a_server_name: str = "Test Server",
    skill_id: str | None = "skill-1",
    session_id: str | None = "session-1",
    trace_id: str | None = None,
    status: str = "success",
    latency_ms: int | None = 100,
    retry_count: int = 0,
    error_message: str | None = None,
    created_at: datetime | None = None,
) -> A2aCallLog:
    log = A2aCallLog(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        a2a_server_id=a2a_server_id,
        a2a_server_name=a2a_server_name,
        skill_id=skill_id,
        session_id=session_id,
        trace_id=trace_id or str(uuid.uuid4()),
        status=status,
        latency_ms=latency_ms,
        retry_count=retry_count,
        error_message=error_message,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db_session.add(log)
    await db_session.flush()
    return log


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestListA2aCallLogs:
    """Tests for GET /api/admin/a2a-call-logs."""

    async def test_list_empty(
        self, async_client: httpx.AsyncClient, admin_user, auth_headers,
    ):
        """Returns empty list when no call logs exist."""
        headers = auth_headers(admin_user)
        response = await async_client.get("/api/admin/a2a-call-logs", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["items"] == []
        assert body["page"] == 1

    async def test_list_with_data(
        self, async_client: httpx.AsyncClient, db_session: AsyncSession,
        test_tenant, admin_user, auth_headers,
    ):
        """Returns call logs when data exists."""
        log = await create_call_log(db_session, tenant_id=test_tenant.id)

        headers = auth_headers(admin_user)
        response = await async_client.get("/api/admin/a2a-call-logs", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == log.id
        assert body["items"][0]["status"] == "success"
        assert body["items"][0]["a2a_server_name"] == "Test Server"
        assert body["items"][0]["latency_ms"] == 100
        assert body["items"][0]["retry_count"] == 0

    async def test_filter_by_server_id(
        self, async_client: httpx.AsyncClient, db_session: AsyncSession,
        test_tenant, admin_user, auth_headers,
    ):
        """Filters results by a2a_server_id."""
        await create_call_log(db_session, tenant_id=test_tenant.id, a2a_server_id="server-a")
        await create_call_log(db_session, tenant_id=test_tenant.id, a2a_server_id="server-b")

        headers = auth_headers(admin_user)
        response = await async_client.get(
            "/api/admin/a2a-call-logs?a2a_server_id=server-a",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["a2a_server_id"] == "server-a"

    async def test_filter_by_status(
        self, async_client: httpx.AsyncClient, db_session: AsyncSession,
        test_tenant, admin_user, auth_headers,
    ):
        """Filters results by status."""
        await create_call_log(db_session, tenant_id=test_tenant.id, status="success")
        await create_call_log(db_session, tenant_id=test_tenant.id, status="error")

        headers = auth_headers(admin_user)
        response = await async_client.get(
            "/api/admin/a2a-call-logs?status=error",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "error"

    async def test_filter_by_date_range(
        self, async_client: httpx.AsyncClient, db_session: AsyncSession,
        test_tenant, admin_user, auth_headers,
    ):
        """Filters results by date range."""
        await create_call_log(
            db_session, tenant_id=test_tenant.id,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        await create_call_log(
            db_session, tenant_id=test_tenant.id,
            created_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        )

        headers = auth_headers(admin_user)
        response = await async_client.get(
            "/api/admin/a2a-call-logs?date_from=2026-06-10&date_to=2026-06-20",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1

    async def test_pagination(
        self, async_client: httpx.AsyncClient, db_session: AsyncSession,
        test_tenant, admin_user, auth_headers,
    ):
        """Pagination returns correct slice of results."""
        for i in range(5):
            await create_call_log(
                db_session, tenant_id=test_tenant.id,
                created_at=datetime(2026, 6, 1 + i, tzinfo=timezone.utc),
            )

        headers = auth_headers(admin_user)
        response = await async_client.get(
            "/api/admin/a2a-call-logs?page=1&page_size=2",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["page"] == 1
        assert body["total_pages"] == 3

    async def test_default_ordering_newest_first(
        self, async_client: httpx.AsyncClient, db_session: AsyncSession,
        test_tenant, admin_user, auth_headers,
    ):
        """Results are ordered by created_at descending by default."""
        old = await create_call_log(
            db_session, tenant_id=test_tenant.id,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        new = await create_call_log(
            db_session, tenant_id=test_tenant.id,
            created_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
        )

        headers = auth_headers(admin_user)
        response = await async_client.get("/api/admin/a2a-call-logs", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["items"][0]["id"] == new.id
        assert body["items"][1]["id"] == old.id


class TestA2aCallLogsAuth:
    """Auth guards for GET /api/admin/a2a-call-logs."""

    async def test_requires_auth(self, async_client: httpx.AsyncClient):
        """Request without auth token returns 401."""
        response = await async_client.get("/api/admin/a2a-call-logs")
        assert response.status_code == 401

    async def test_regular_user_forbidden(
        self, async_client: httpx.AsyncClient, test_user, auth_headers,
    ):
        """Regular user (non-admin/non-manager) gets 403."""
        headers = auth_headers(test_user)
        response = await async_client.get("/api/admin/a2a-call-logs", headers=headers)
        assert response.status_code == 403


class TestA2aCallLogsTenantScoping:
    """Manager role scopes call logs to own tenant."""

    async def test_manager_sees_only_own_tenant(
        self, async_client: httpx.AsyncClient, db_session: AsyncSession,
        test_tenant, second_tenant, admin_user, manager_user, auth_headers,
    ):
        """Manager sees only call logs from their own tenant."""
        await create_call_log(db_session, tenant_id=test_tenant.id, a2a_server_name="Tenant-A Log")
        await create_call_log(db_session, tenant_id=second_tenant.id, a2a_server_name="Tenant-B Log")

        # Admin should see both
        admin_headers = auth_headers(admin_user)
        resp_admin = await async_client.get("/api/admin/a2a-call-logs", headers=admin_headers)
        assert resp_admin.status_code == 200
        assert resp_admin.json()["total"] == 2

        # Manager should see only their own tenant's logs
        mgr_headers = auth_headers(manager_user)
        resp_mgr = await async_client.get("/api/admin/a2a-call-logs", headers=mgr_headers)
        assert resp_mgr.status_code == 200
        body = resp_mgr.json()
        assert body["total"] == 1

    async def test_admin_sees_all_tenants(
        self, async_client: httpx.AsyncClient, db_session: AsyncSession,
        test_tenant, second_tenant, admin_user, auth_headers,
    ):
        """Admin sees call logs from all tenants."""
        await create_call_log(db_session, tenant_id=test_tenant.id)
        await create_call_log(db_session, tenant_id=second_tenant.id)

        headers = auth_headers(admin_user)
        response = await async_client.get("/api/admin/a2a-call-logs", headers=headers)
        assert response.status_code == 200
        assert response.json()["total"] == 2
