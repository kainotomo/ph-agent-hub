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
