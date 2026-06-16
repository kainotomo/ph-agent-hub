# =============================================================================
# PH Agent Hub — Auth API Endpoint Tests
# =============================================================================
# Tests the /auth/login, /auth/refresh, /auth/logout, /auth/me endpoints
# using httpx.AsyncClient for fully async test support.
# =============================================================================

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.jwt import create_access_token, create_refresh_token
from src.core.security import hash_password
from src.db.orm.users import User
from src.main import app

pytestmark = [
    pytest.mark.security,
    pytest.mark.integration,
]


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    """Create an async HTTP client for the FastAPI app with test DB override."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def login_user(db_session: AsyncSession, test_tenant) -> dict:
    """Create a user with a known password for login tests."""
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        email=f"login-test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("CorrectPassword123!"),
        display_name="Login Test User",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return {"user": user, "password": "CorrectPassword123!"}


@pytest_asyncio.fixture
async def inactive_login_user(db_session: AsyncSession, test_tenant) -> dict:
    """Create an inactive user for login rejection tests."""
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        email=f"inactive-login-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("CorrectPassword123!"),
        display_name="Inactive Login User",
        role="user",
        is_active=False,
    )
    db_session.add(user)
    await db_session.flush()
    return {"user": user, "password": "CorrectPassword123!"}


# ═══════════════════════════════════════════════════════════════════════
# Login tests
# ═══════════════════════════════════════════════════════════════════════


class TestLogin:
    """Tests for POST /auth/login."""

    async def test_login_success(self, async_client, login_user):
        """Verify login with valid credentials returns access token and refresh cookie."""
        response = await async_client.post(
            "/api/auth/login",
            data={
                "username": login_user["user"].email,
                "password": login_user["password"],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        # Refresh cookie should be set
        assert "refresh_token" in response.cookies

    async def test_login_wrong_password(self, async_client, login_user):
        """Verify wrong password returns 401."""
        response = await async_client.post(
            "/api/auth/login",
            data={
                "username": login_user["user"].email,
                "password": "WrongPassword!",
            },
        )
        assert response.status_code == 401

    async def test_login_nonexistent_user(self, async_client):
        """Verify login with unknown email returns 401."""
        response = await async_client.post(
            "/api/auth/login",
            data={
                "username": "nonexistent@example.com",
                "password": "SomePassword!",
            },
        )
        assert response.status_code == 401

    async def test_login_inactive_user(self, async_client, inactive_login_user):
        """Verify inactive user cannot log in."""
        response = await async_client.post(
            "/api/auth/login",
            data={
                "username": inactive_login_user["user"].email,
                "password": inactive_login_user["password"],
            },
        )
        assert response.status_code == 401

    async def test_login_missing_fields(self, async_client):
        """Verify login with missing fields returns 422."""
        response = await async_client.post("/api/auth/login", data={})
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# Token refresh tests
# ═══════════════════════════════════════════════════════════════════════


class TestRefresh:
    """Tests for POST /auth/refresh."""

    async def test_refresh_success(self, async_client, test_user):
        """Verify valid refresh token returns new access token."""
        refresh_token = create_refresh_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
            "jti": "test-jti-refresh-1",
        })
        async_client.cookies.set("refresh_token", refresh_token)

        response = await async_client.post("/api/auth/refresh")
        assert response.status_code == 200, response.text
        data = response.json()
        assert "access_token" in data

    async def test_refresh_without_cookie(self, async_client):
        """Verify missing refresh cookie returns 401."""
        response = await async_client.post("/api/auth/refresh")
        assert response.status_code == 401

    async def test_refresh_expired_token(self, async_client):
        """Verify expired refresh token returns 401."""
        import time
        from jose import jwt as jose_jwt
        from src.core.config import settings

        expired = jose_jwt.encode(
            {
                "sub": "user-id",
                "exp": int(time.time()) - 3600,
                "jti": "test-jti-expired",
            },
            settings.JWT_SECRET,
            algorithm="HS256",
        )
        async_client.cookies.set("refresh_token", expired)
        response = await async_client.post("/api/auth/refresh")
        assert response.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# Logout tests
# ═══════════════════════════════════════════════════════════════════════


class TestLogout:
    """Tests for POST /auth/logout."""

    async def test_logout_success(self, async_client, test_user):
        """Verify logout clears the refresh cookie."""
        access_token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        refresh_token = create_refresh_token({
            "sub": test_user.id,
            "jti": "test-jti-logout-1",
        })
        response = await async_client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
            cookies={"refresh_token": refresh_token},
        )
        assert response.status_code == 200, response.text
        # Refresh cookie should be cleared
        set_cookie = response.headers.get("set-cookie", "")
        assert "refresh_token=" in set_cookie
        assert "Max-Age=0" in set_cookie.lower() or "max-age=0" in set_cookie.lower()

    async def test_logout_without_token(self, async_client):
        """Verify logout without auth still succeeds (clears non-existent cookie)."""
        response = await async_client.post("/api/auth/logout")
        # Logout is an unprotected endpoint that clears the refresh cookie
        assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# /me endpoint tests
# ═══════════════════════════════════════════════════════════════════════


class TestMe:
    """Tests for GET /auth/me."""

    async def test_me_authenticated(self, async_client, test_user):
        """Verify authenticated user can access /me."""
        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        response = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["email"] == test_user.email
        assert data["tenant_id"] == test_user.tenant_id

    async def test_me_no_token(self, async_client):
        """Verify /me without token returns 401."""
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 401

    async def test_me_expired_token(self, async_client):
        """Verify /me with expired token returns 401."""
        import time
        from jose import jwt as jose_jwt
        from src.core.config import settings

        expired = jose_jwt.encode(
            {
                "sub": "user-id",
                "tenant_id": "tenant-id",
                "role": "user",
                "exp": int(time.time()) - 3600,
            },
            settings.JWT_SECRET,
            algorithm="HS256",
        )
        response = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert response.status_code == 401
