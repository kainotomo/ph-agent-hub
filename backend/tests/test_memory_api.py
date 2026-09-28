# =============================================================================
# PH Agent Hub — Memory API Integration Tests
# =============================================================================
# Tests memory CRUD, pagination, cross-user isolation, and tenant isolation
# at the HTTP layer.
# =============================================================================

import uuid

import httpx
import pytest
import pytest_asyncio

from src.main import app

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    """Create an async HTTP client wired to the FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# =============================================================================
# Memory CRUD Tests
# =============================================================================


class TestCreateMemory:
    """Tests for POST /memory."""

    async def test_create_memory_entry(
        self, async_client, auth_headers, test_user
    ):
        """Verify creating a memory entry returns correct data."""
        headers = auth_headers(test_user)
        payload = {"key": "color", "value": "blue"}
        resp = await async_client.post("/api/memory", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["key"] == "color"
        assert data["value"] == "blue"
        assert data["tenant_id"] == test_user.tenant_id
        assert data["user_id"] == test_user.id
        assert data["source"] == "manual"
        assert "id" in data

    async def test_create_memory_with_session_id(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify memory can be associated with a session."""
        headers = auth_headers(test_user)
        payload = {"key": "context", "value": "session data", "session_id": test_session.id}
        resp = await async_client.post("/api/memory", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["session_id"] == test_session.id

    async def test_create_memory_requires_auth(
        self, async_client
    ):
        """Verify unauthenticated request is rejected."""
        payload = {"key": "hack", "value": "data"}
        resp = await async_client.post("/api/memory", json=payload)
        assert resp.status_code == 401

    async def test_create_memory_key_256_chars_returns_422(
        self, async_client, auth_headers, test_user
    ):
        """Verify a key of exactly 256 characters is rejected (max is 255)."""
        headers = auth_headers(test_user)
        payload = {"key": "a" * 256, "value": "short"}
        resp = await async_client.post("/api/memory", json=payload, headers=headers)
        assert resp.status_code == 422

    async def test_create_memory_value_8001_chars_returns_422(
        self, async_client, auth_headers, test_user
    ):
        """Verify a value of 8001 characters is rejected (max is 8000)."""
        headers = auth_headers(test_user)
        payload = {"key": "k", "value": "x" * 8001}
        resp = await async_client.post("/api/memory", json=payload, headers=headers)
        assert resp.status_code == 422

    async def test_create_memory_key_only_spaces_returns_422(
        self, async_client, auth_headers, test_user
    ):
        """Verify a key consisting only of whitespace is rejected."""
        headers = auth_headers(test_user)
        payload = {"key": "   ", "value": "data"}
        resp = await async_client.post("/api/memory", json=payload, headers=headers)
        assert resp.status_code == 422


class TestListMemory:
    """Tests for GET /memory."""

    async def test_list_memory_returns_entries(
        self, async_client, auth_headers, test_user
    ):
        """Verify listing returns created entries wrapped in a pagination envelope."""
        headers = auth_headers(test_user)

        # Create two entries
        await async_client.post("/api/memory", json={"key": "k1", "value": "v1"}, headers=headers)
        await async_client.post("/api/memory", json={"key": "k2", "value": "v2"}, headers=headers)

        resp = await async_client.get("/api/memory", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "page_size" in body
        assert "total_pages" in body
        items = body["items"]
        assert len(items) >= 2
        keys = [e["key"] for e in items]
        assert "k1" in keys
        assert "k2" in keys

    async def test_list_memory_empty(
        self, async_client, auth_headers, test_user
    ):
        """Verify a user with no memory gets an empty items list in the envelope."""
        headers = auth_headers(test_user)
        resp = await async_client.get("/api/memory", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["page"] == 1
        assert body["total_pages"] >= 1

    async def test_list_memory_pagination(
        self, async_client, auth_headers, test_user
    ):
        """Verify pagination parameters are respected."""
        headers = auth_headers(test_user)
        # Create entries
        for i in range(3):
            await async_client.post(
                "/api/memory", json={"key": f"page_{i}", "value": str(i)}, headers=headers
            )

        resp = await async_client.get("/api/memory?page_size=2", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) <= 2

    async def test_list_memory_pagination_regression(
        self, async_client, auth_headers, test_user
    ):
        """Regression test: paginated list returns correct total and total_pages."""
        headers = auth_headers(test_user)
        # Create 3 entries
        for i in range(3):
            await async_client.post(
                "/api/memory", json={"key": f"reg_{i}", "value": str(i)}, headers=headers
            )

        resp = await async_client.get("/api/memory?page=1&page_size=2", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 3
        assert body["total_pages"] == 2


class TestUpdateMemory:
    """Tests for PUT /memory/{memory_id}."""

    async def test_update_memory_value(
        self, async_client, auth_headers, test_user
    ):
        """Verify updating a memory entry works."""
        headers = auth_headers(test_user)
        # Create
        create_resp = await async_client.post(
            "/api/memory", json={"key": "name", "value": "Alice"}, headers=headers
        )
        mem_id = create_resp.json()["id"]

        # Update
        update_resp = await async_client.put(
            f"/api/memory/{mem_id}", json={"value": "Bob"}, headers=headers
        )
        assert update_resp.status_code == 200, update_resp.text
        assert update_resp.json()["value"] == "Bob"

    async def test_update_memory_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user
    ):
        """Verify user B cannot update user A's memory."""
        headers_a = auth_headers(test_user)
        create_resp = await async_client.post(
            "/api/memory", json={"key": "secret", "value": "data"}, headers=headers_a
        )
        mem_id = create_resp.json()["id"]

        headers_b = auth_headers(second_user)
        update_resp = await async_client.put(
            f"/api/memory/{mem_id}", json={"value": "hacked"}, headers=headers_b
        )
        assert update_resp.status_code == 403

    async def test_update_memory_key_conflict_returns_409(
        self, async_client, auth_headers, test_user
    ):
        """Verify renaming an entry onto an existing key returns 409."""
        headers = auth_headers(test_user)
        # Create two entries
        await async_client.post("/api/memory", json={"key": "first", "value": "a"}, headers=headers)
        create_resp = await async_client.post("/api/memory", json={"key": "second", "value": "b"}, headers=headers)
        mem_id = create_resp.json()["id"]

        # Try to rename "second" → "first" (already exists)
        update_resp = await async_client.put(
            f"/api/memory/{mem_id}", json={"key": "first"}, headers=headers
        )
        assert update_resp.status_code == 409

    async def test_update_memory_own_key_change_value_returns_200(
        self, async_client, auth_headers, test_user
    ):
        """Verify updating the value while keeping the same key succeeds."""
        headers = auth_headers(test_user)
        create_resp = await async_client.post(
            "/api/memory", json={"key": "name", "value": "Alice"}, headers=headers
        )
        mem_id = create_resp.json()["id"]

        update_resp = await async_client.put(
            f"/api/memory/{mem_id}", json={"key": "name", "value": "Bob"}, headers=headers
        )
        assert update_resp.status_code == 200, update_resp.text
        assert update_resp.json()["value"] == "Bob"


class TestDeleteMemory:
    """Tests for DELETE /memory/{memory_id}."""

    async def test_delete_memory(
        self, async_client, auth_headers, test_user
    ):
        """Verify deleting a memory entry returns 204."""
        headers = auth_headers(test_user)
        create_resp = await async_client.post(
            "/api/memory", json={"key": "temp", "value": "data"}, headers=headers
        )
        mem_id = create_resp.json()["id"]

        resp = await async_client.delete(f"/api/memory/{mem_id}", headers=headers)
        assert resp.status_code == 204

        # Verify it's gone
        list_resp = await async_client.get("/api/memory", headers=headers)
        ids = [m["id"] for m in list_resp.json()["items"]]
        assert mem_id not in ids

    async def test_delete_memory_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user
    ):
        """Verify user B cannot delete user A's memory."""
        headers_a = auth_headers(test_user)
        create_resp = await async_client.post(
            "/api/memory", json={"key": "mine", "value": "data"}, headers=headers_a
        )
        mem_id = create_resp.json()["id"]

        headers_b = auth_headers(second_user)
        resp = await async_client.delete(f"/api/memory/{mem_id}", headers=headers_b)
        assert resp.status_code == 403


# =============================================================================
# Tenant Isolation Tests
# =============================================================================


class TestMemoryTenantIsolation:
    """Verify cross-tenant memory access is blocked."""

    async def test_cross_tenant_list_excludes(
        self, async_client, auth_headers, test_user, second_user
    ):
        """Verify tenant B's memory list does not include tenant A's entries."""
        # Create memory as tenant A
        headers_a = auth_headers(test_user)
        await async_client.post(
            "/api/memory", json={"key": "tenant_a_secret", "value": "hidden"}, headers=headers_a
        )

        # List as tenant B
        headers_b = auth_headers(second_user)
        resp = await async_client.get("/api/memory", headers=headers_b)
        keys = [m["key"] for m in resp.json()["items"]]
        assert "tenant_a_secret" not in keys

    async def test_cross_tenant_update_forbidden(
        self, async_client, auth_headers, test_user, second_user
    ):
        """Verify tenant B cannot update tenant A's memory."""
        headers_a = auth_headers(test_user)
        create_resp = await async_client.post(
            "/api/memory", json={"key": "secret", "value": "data"}, headers=headers_a
        )
        mem_id = create_resp.json()["id"]

        headers_b = auth_headers(second_user)
        resp = await async_client.put(
            f"/api/memory/{mem_id}", json={"value": "hacked"}, headers=headers_b
        )
        assert resp.status_code == 403

    async def test_cross_tenant_delete_forbidden(
        self, async_client, auth_headers, test_user, second_user
    ):
        """Verify tenant B cannot delete tenant A's memory."""
        headers_a = auth_headers(test_user)
        create_resp = await async_client.post(
            "/api/memory", json={"key": "secret", "value": "data"}, headers=headers_a
        )
        mem_id = create_resp.json()["id"]

        headers_b = auth_headers(second_user)
        resp = await async_client.delete(f"/api/memory/{mem_id}", headers=headers_b)
        assert resp.status_code == 403
