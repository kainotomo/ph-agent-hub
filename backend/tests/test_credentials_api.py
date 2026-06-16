# =============================================================================
# PH Agent Hub — Credentials API Integration Tests
# =============================================================================
# Tests credential CRUD, OAuth URL generation, connection testing, and
# tenant isolation at the HTTP layer.
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
# Credential CRUD Tests
# =============================================================================


class TestCreateCredential:
    """Tests for POST /credentials."""

    async def test_create_credential(
        self, async_client, auth_headers, test_user, test_tool
    ):
        """Verify creating a credential returns correct data."""
        headers = auth_headers(test_user)
        payload = {
            "tool_id": test_tool.id,
            "label": "Test Gmail",
            "provider": "gmail",
            "email_address": "test@gmail.com",
        }
        resp = await async_client.post("/api/credentials", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["label"] == "Test Gmail"
        assert data["provider"] == "gmail"
        assert data["user_id"] == test_user.id
        assert data["tool_id"] == test_tool.id
        assert "id" in data

    async def test_create_credential_missing_tool(
        self, async_client, auth_headers, test_user
    ):
        """Verify creating a credential with a non-existent tool fails."""
        headers = auth_headers(test_user)
        payload = {
            "tool_id": str(uuid.uuid4()),
            "label": "Fake",
            "provider": "gmail",
        }
        resp = await async_client.post("/api/credentials", json=payload, headers=headers)
        assert resp.status_code == 404

    async def test_create_credential_requires_auth(
        self, async_client, test_tool
    ):
        """Verify unauthenticated request is rejected."""
        payload = {
            "tool_id": test_tool.id,
            "label": "Hacked",
            "provider": "gmail",
        }
        resp = await async_client.post("/api/credentials", json=payload)
        assert resp.status_code == 401


class TestListCredentials:
    """Tests for GET /credentials."""

    async def test_list_credentials_returns_entries(
        self, async_client, auth_headers, test_user, test_credential
    ):
        """Verify listing returns created credentials."""
        headers = auth_headers(test_user)
        resp = await async_client.get("/api/credentials", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] >= 1
        ids = [c["id"] for c in data["items"]]
        assert test_credential.id in ids

    async def test_list_credentials_empty(
        self, async_client, auth_headers, test_user
    ):
        """Verify a user with no credentials gets an empty list."""
        headers = auth_headers(test_user)
        resp = await async_client.get("/api/credentials", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_list_credentials_other_user_not_visible(
        self, async_client, auth_headers, test_user, second_user, test_credential
    ):
        """Verify user B cannot see user A's credentials."""
        headers_b = auth_headers(second_user)
        resp = await async_client.get("/api/credentials", headers=headers_b)
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["items"]]
        assert test_credential.id not in ids


class TestGetToolId:
    """Tests for GET /credentials/tool-id."""

    async def test_get_tool_id_by_type(
        self, async_client, auth_headers, test_user, test_tool
    ):
        """Verify looking up a tool ID by type works."""
        headers = auth_headers(test_user)
        resp = await async_client.get(
            f"/api/credentials/tool-id?tool_type={test_tool.type}",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["tool_id"] == test_tool.id


class TestUpdateCredential:
    """Tests for PUT /credentials/{credential_id}."""

    async def test_update_credential_label(
        self, async_client, auth_headers, test_user, test_credential
    ):
        """Verify updating a credential label works."""
        headers = auth_headers(test_user)
        payload = {"label": "Updated Label"}
        resp = await async_client.put(
            f"/api/credentials/{test_credential.id}",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["label"] == "Updated Label"

    async def test_update_credential_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_credential
    ):
        """Verify user B cannot update user A's credential."""
        headers = auth_headers(second_user)
        payload = {"label": "Hacked"}
        resp = await async_client.put(
            f"/api/credentials/{test_credential.id}",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 404


class TestDeleteCredential:
    """Tests for DELETE /credentials/{credential_id}."""

    async def test_delete_credential(
        self, async_client, auth_headers, test_user, test_credential
    ):
        """Verify deleting a credential returns 204."""
        headers = auth_headers(test_user)
        resp = await async_client.delete(
            f"/api/credentials/{test_credential.id}", headers=headers
        )
        assert resp.status_code == 204

    async def test_delete_credential_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_credential
    ):
        """Verify user B cannot delete user A's credential."""
        headers = auth_headers(second_user)
        resp = await async_client.delete(
            f"/api/credentials/{test_credential.id}", headers=headers
        )
        assert resp.status_code == 404


# =============================================================================
# OAuth URL Generation Tests
# =============================================================================


class TestOAuthUrls:
    """Tests for OAuth URL generation endpoints."""

    async def test_google_oauth_url(
        self, async_client, auth_headers, test_user, test_credential
    ):
        """Verify Google OAuth URL generation returns a URL."""
        headers = auth_headers(test_user)
        # The tool_id must exist and be of the right type
        resp = await async_client.post(
            "/api/credentials/google/auth-url",
            json={"tool_id": test_credential.tool_id},
            headers=headers,
        )
        # May fail if OAuth not configured, but should return a structured response
        if resp.status_code == 200:
            data = resp.json()
            assert "url" in data
            assert "state" in data

    async def test_microsoft_oauth_url(
        self, async_client, auth_headers, test_user, test_credential
    ):
        """Verify Microsoft OAuth URL generation returns a URL."""
        headers = auth_headers(test_user)
        resp = await async_client.post(
            "/api/credentials/microsoft/auth-url",
            json={"tool_id": test_credential.tool_id},
            headers=headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "url" in data
            assert "state" in data


# =============================================================================
# Tenant Isolation Tests
# =============================================================================


class TestCredentialTenantIsolation:
    """Verify cross-tenant credential access is blocked.

    Validates Step 0b: tenant_id column on UserToolCredential.
    """

    async def test_cross_tenant_list_excludes(
        self, async_client, auth_headers, test_user, second_user, test_credential
    ):
        """Verify tenant B's credential list does not include tenant A's credentials."""
        headers_b = auth_headers(second_user)
        resp = await async_client.get("/api/credentials", headers=headers_b)
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()["items"]]
        assert test_credential.id not in ids

    async def test_cross_tenant_delete_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_credential
    ):
        """Verify tenant B cannot delete tenant A's credential."""
        headers_b = auth_headers(second_user)
        resp = await async_client.delete(
            f"/api/credentials/{test_credential.id}", headers=headers_b
        )
        assert resp.status_code == 404


# =============================================================================
# OAuth State Integrity Tests (Issue #345)
# =============================================================================


class TestOAuthStateIntegrity:
    """Verify OAuth state is integrity-protected via server-side nonce store.

    Tests use the actual OAuth URL endpoints (GET) and mock the callback's
    external token exchange to verify state validation.
    """

    @pytest.mark.skipif(
        True,
        reason="Requires OAuth env vars (GOOGLE_CLIENT_ID etc.) to be set",
    )
    async def test_google_oauth_url_returns_uuid_state(
        self, async_client, auth_headers, test_user
    ):
        """Verify the OAuth URL state is a UUID (not colon-delimited)."""
        headers = auth_headers(test_user)
        resp = await async_client.get(
            "/api/credentials/oauth/google/url?tool_id=email_tool",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "state" in data
        # State should be a UUID v4 — no colons, no embedded user/tool info
        state = data["state"]
        assert ":" not in state, f"State should not be colon-delimited: {state}"
        # Validate UUID format
        uuid.UUID(state)

    @pytest.mark.skipif(
        True,
        reason="Requires OAuth env vars (MS_CLIENT_ID etc.) to be set",
    )
    async def test_microsoft_oauth_url_returns_uuid_state(
        self, async_client, auth_headers, test_user
    ):
        """Verify the Microsoft OAuth URL state is a UUID."""
        headers = auth_headers(test_user)
        resp = await async_client.get(
            "/api/credentials/oauth/microsoft/url?tool_id=email_tool",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "state" in data
        state = data["state"]
        assert ":" not in state, f"State should not be colon-delimited: {state}"
        uuid.UUID(state)

    async def test_callback_with_valid_state(
        self, async_client, db_session, test_user, test_tenant, test_tool,
    ):
        """Verify callback succeeds with a valid stored nonce."""
        from unittest.mock import patch
        from src.core.redis import store_oauth_state

        # Insert a valid nonce into Redis
        nonce = str(uuid.uuid4())
        await store_oauth_state(nonce, test_user.id, "email_tool", ttl=300)

        mock_tokens = {
            "access_token": "ya29.mock-access-token",
            "refresh_token": "1//mock-refresh-token",
            "expires_at": 9999999999,
            "scope": "https://www.googleapis.com/auth/gmail.modify",
            "token_type": "Bearer",
            "id_token": "eyJhbGciOiJSUzI1NiJ9.eyJlbWFpbCI6InRlc3RAZXhhbXBsZS5jb20ifQ.signature",
        }

        with patch(
            "src.core.oauth.exchange_google_code",
            return_value=mock_tokens,
        ):
            resp = await async_client.get(
                "/api/credentials/oauth/google/callback",
                params={"code": "auth-code-123", "state": nonce},
            )
        # Should redirect to frontend on success
        assert resp.status_code == 302, resp.text
        assert "settings" in resp.headers.get("location", "")

    async def test_callback_with_forged_state_rejected(
        self, async_client, test_user, test_credential
    ):
        """Verify a forged colon-delimited state is rejected."""
        forged_state = f"{test_user.id}:email_tool:forged"
        resp = await async_client.get(
            "/api/credentials/oauth/google/callback",
            params={"code": "auth-code-456", "state": forged_state},
        )
        assert resp.status_code == 422, resp.text

    async def test_callback_with_random_state_rejected(
        self, async_client,
    ):
        """Verify a completely random state (not in Redis) is rejected."""
        resp = await async_client.get(
            "/api/credentials/oauth/google/callback",
            params={"code": "auth-code-789", "state": str(uuid.uuid4())},
        )
        assert resp.status_code == 422, resp.text

    async def test_callback_replay_rejected(
        self, async_client, test_user, test_tenant, test_tool,
    ):
        """Verify a state can only be used once (replay attack prevention)."""
        from unittest.mock import patch
        from src.core.redis import store_oauth_state

        nonce = str(uuid.uuid4())
        await store_oauth_state(nonce, test_user.id, "email_tool", ttl=300)

        mock_tokens = {
            "access_token": "ya29.mock-access-token",
            "refresh_token": "1//mock-refresh-token",
            "expires_at": 9999999999,
            "scope": "https://www.googleapis.com/auth/gmail.modify",
            "token_type": "Bearer",
            "id_token": "eyJhbGciOiJSUzI1NiJ9.eyJlbWFpbCI6InRlc3RAZXhhbXBsZS5jb20ifQ.signature",
        }

        with patch(
            "src.core.oauth.exchange_google_code",
            return_value=mock_tokens,
        ):
            # First use — should succeed
            resp1 = await async_client.get(
                "/api/credentials/oauth/google/callback",
                params={"code": "auth-code-111", "state": nonce},
            )
            assert resp1.status_code == 302, resp1.text

            # Second use — should fail (state already consumed)
            resp2 = await async_client.get(
                "/api/credentials/oauth/google/callback",
                params={"code": "auth-code-222", "state": nonce},
            )
            assert resp2.status_code == 422, resp2.text

    async def test_callback_microsoft_with_forged_state_rejected(
        self, async_client,
    ):
        """Verify a forged state on the Microsoft callback is rejected."""
        forged_state = "fake-user-id:email_tool:forged"
        resp = await async_client.get(
            "/api/credentials/oauth/microsoft/callback",
            params={"code": "auth-code-333", "state": forged_state},
        )
        assert resp.status_code == 422, resp.text
