# =============================================================================
# PH Agent Hub — Abuse Scenario Tests
# =============================================================================
# Tests common abuse patterns: forged tokens, SQL injection, XSS, etc.
# =============================================================================

import time

import httpx
import pytest
import pytest_asyncio
from jose import jwt as jose_jwt

from src.core.config import settings
from src.core.jwt import create_access_token
from src.main import app

pytestmark = [
    pytest.mark.security,
    pytest.mark.integration,
]


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    """Create an async HTTP client with DB override."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestForgedTokens:
    """Verify forged and tampered tokens are rejected."""

    async def test_tampered_jwt_rejected(self, async_client):
        """Verify a JWT with modified payload raises 401."""
        token = create_access_token({
            "sub": "user-123",
            "tenant_id": "tenant-abc",
            "role": "user",
        })
        parts = token.split(".")
        tampered = f"{parts[0]}.eyJmYWtlIjp0cnVlfQ.{parts[2]}"

        response = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {tampered}"},
        )
        assert response.status_code == 401

    async def test_missing_auth_header(self, async_client):
        """Verify request without Authorization header returns 401."""
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 401

    async def test_malformed_auth_header(self, async_client):
        """Verify malformed Authorization header returns 401."""
        response = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer invalid-base64!"},
        )
        assert response.status_code == 401

    async def test_empty_token_rejected(self, async_client):
        """Verify empty token returns 401."""
        response = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 401


class TestSQLInjection:
    """Verify SQL injection attempts are handled gracefully."""

    async def test_sql_injection_in_login_email(self, async_client):
        """Verify SQL injection in login email field doesn't cause 500."""
        payloads = [
            "' OR '1'='1",
            "'; DROP TABLE users; --",
            "' UNION SELECT * FROM users --",
            "admin'--",
        ]
        for payload in payloads:
            response = await async_client.post(
                "/api/auth/login",
                data={"username": payload, "password": "password"},
            )
            # Should return 401 (auth failure), not 500 (server error) or 200 (bypass)
            assert response.status_code == 401, (
                f"SQL injection payload '{payload}' returned {response.status_code}"
            )


class TestTokenReplay:
    """Verify token replay after logout is rejected."""

    async def test_expired_token_rejected(self, async_client):
        """Verify an expired JWT token returns 401."""
        expired = jose_jwt.encode(
            {
                "sub": "user-123",
                "tenant_id": "tenant-abc",
                "role": "user",
                "exp": int(time.time()) - 3600,  # 1 hour ago
            },
            settings.JWT_SECRET,
            algorithm="HS256",
        )
        response = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert response.status_code == 401


class TestOAuthStateAbuse:
    """Verify OAuth state integrity protections against abuse (Issue #345).

    Ensures forged, replayed, SQL-injected, and XSS-injected state values
    are rejected rather than silently accepted or causing server errors.
    """

    async def test_sql_injection_in_oauth_state_rejected(
        self, async_client,
    ):
        """Verify SQL injection in the state parameter returns 422, not 500."""
        payloads = [
            "' OR '1'='1",
            "'; DROP TABLE users; --",
            "' UNION SELECT * FROM users --",
            "1:email_tool:'; DROP TABLE users; --",
        ]
        for payload in payloads:
            resp = await async_client.get(
                "/api/credentials/oauth/google/callback",
                params={"code": "abc", "state": payload},
            )
            assert resp.status_code == 422, (
                f"SQL injection in state '{payload}' returned {resp.status_code}"
            )

    async def test_xss_in_oauth_state_rejected(
        self, async_client,
    ):
        """Verify XSS in the state parameter returns 422 (not reflected)."""
        xss_payloads = [
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:alert('xss')",
        ]
        for payload in xss_payloads:
            resp = await async_client.get(
                "/api/credentials/oauth/google/callback",
                params={"code": "abc", "state": payload},
            )
            assert resp.status_code == 422, (
                f"XSS in state '{payload}' returned {resp.status_code}"
            )
            # Ensure the payload is NOT reflected in the response body
            body = resp.text
            assert "<script>" not in body
            assert "alert" not in body

    async def test_replayed_oauth_state_rejected(
        self, async_client, test_user, test_tenant, test_tool,
    ):
        """Verify a consumed OAuth state cannot be replayed."""
        from unittest.mock import patch
        from src.core.redis import store_oauth_state

        nonce = "abuse-replay-nonce"
        await store_oauth_state(nonce, test_user.id, "email_tool", ttl=300)

        mock_tokens = {
            "access_token": "ya29.mock",
            "refresh_token": "1//mock",
            "expires_at": 9999999999,
            "scope": "https://www.googleapis.com/auth/gmail.modify",
            "token_type": "Bearer",
            "id_token": "header.payload.sig",
        }

        with patch(
            "src.core.oauth.exchange_google_code",
            return_value=mock_tokens,
        ):
            # First use — should succeed
            resp1 = await async_client.get(
                "/api/credentials/oauth/google/callback",
                params={"code": "code-1", "state": nonce},
            )
            assert resp1.status_code == 302, resp1.text

            # Second use with same state — should be rejected
            resp2 = await async_client.get(
                "/api/credentials/oauth/google/callback",
                params={"code": "code-2", "state": nonce},
            )
            assert resp2.status_code == 422, resp2.text

    async def test_empty_oauth_state_rejected(
        self, async_client,
    ):
        """Verify empty/missing state returns 422."""
        resp = await async_client.get(
            "/api/credentials/oauth/google/callback",
            params={"code": "abc"},
        )
        assert resp.status_code == 422, resp.text
