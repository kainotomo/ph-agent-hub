# =============================================================================
# PH Agent Hub — Rate Limiter Tests
# =============================================================================
# Tests that rate limiting is enforced on sensitive endpoints.
# =============================================================================

import httpx
import pytest
import pytest_asyncio

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


class TestLoginRateLimit:
    """Verify rate limiting on the /auth/login endpoint."""

    async def test_login_rate_limit_enforced(self, async_client):
        """Verify rapid login attempts trigger rate limiting."""
        responses = []
        for _ in range(8):
            response = await async_client.post(
                "/api/auth/login",
                data={
                    "username": "test@example.com",
                    "password": "wrong-password",
                },
            )
            responses.append(response.status_code)

        # The default rate limit is 5/minute. At least one request
        # beyond the limit should get 429 (Too Many Requests).
        rate_limited = [s for s in responses if s == 429]
        assert len(rate_limited) >= 1, (
            f"Expected at least one 429, got statuses: {responses}"
        )

    async def test_other_endpoints_not_rate_limited(self, async_client):
        """Verify endpoints without rate limits are not throttled."""
        for _ in range(10):
            response = await async_client.get("/api/auth/me")
            # Should get 401 (no auth), not 429 (rate limited)
            assert response.status_code == 401, f"Expected 401, got {response.status_code}"
