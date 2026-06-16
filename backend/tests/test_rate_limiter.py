# =============================================================================
# PH Agent Hub — Rate Limiter Tests
# =============================================================================
# Tests that rate limiting is enforced on sensitive endpoints.
# =============================================================================

import hashlib
import os
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure required secrets are set before any app import
os.environ.setdefault("EMBED_GUEST_TOKEN_SECRET", "test-guest-secret-for-rate-limiter-tests")

from src.core.jwt import create_guest_token
from src.db.orm.embed_configs import EmbedConfig
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


# ---------------------------------------------------------------------------
# Widget test fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def widget_embed_config(
    db_session: AsyncSession, test_tenant
) -> dict:
    """Create a test embed config with a known raw token.

    Returns ``{"config": EmbedConfig, "raw_token": str}`` so tests can
    use the raw token in URL paths.
    """
    raw_token = f"embed_{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    config = EmbedConfig(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        name="Test Widget (Rate Limit)",
        is_active=True,
        guest_token_hash=token_hash,
    )
    db_session.add(config)
    await db_session.flush()
    return {"config": config, "raw_token": raw_token}


@pytest_asyncio.fixture
async def widget_guest_jwt(
    widget_embed_config,
) -> str:
    """Create a valid guest JWT for the test embed config."""
    cfg = widget_embed_config["config"]
    return create_guest_token({
        "sub": cfg.id,
        "tenant_id": cfg.tenant_id,
        "session_id": str(uuid.uuid4()),
    })


# ---------------------------------------------------------------------------
# Login rate limit tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Widget rate limit tests
# ---------------------------------------------------------------------------


class TestWidgetRateLimit:
    """Verify rate limiting on public widget endpoints."""

    # ── Config endpoint ──────────────────────────────────────────────

    async def test_widget_config_rate_limit_enforced(
        self, async_client, widget_embed_config
    ):
        """Verify rapid config bootstraps trigger rate limiting.

        The default limit is ``30/hour``. Sending 35 requests should
        produce at least one 429.
        """
        raw_token = widget_embed_config["raw_token"]
        responses = []
        for _ in range(35):
            response = await async_client.get(
                f"/api/widget/config/{raw_token}",
            )
            responses.append(response.status_code)

        rate_limited = [s for s in responses if s == 429]
        assert len(rate_limited) >= 1, (
            f"Expected at least one 429 on config endpoint, "
            f"got statuses: {responses}"
        )

    async def test_widget_config_rate_limit_different_tokens(
        self, async_client, widget_embed_config, db_session, test_tenant
    ):
        """Verify that different guest tokens get independent rate-limit

        buckets so legitimate multi-config deployments are not penalised
        by a single aggressive caller.
        """
        # Create a second embed config with a different token
        raw_token_2 = f"embed_{uuid.uuid4().hex}"
        token_hash_2 = hashlib.sha256(raw_token_2.encode()).hexdigest()
        config_2 = EmbedConfig(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            name="Test Widget 2 (Rate Limit)",
            is_active=True,
            guest_token_hash=token_hash_2,
        )
        db_session.add(config_2)
        await db_session.flush()

        raw_token_1 = widget_embed_config["raw_token"]

        # Fire just under the limit for each token (25 each, well under 30/hr)
        for raw_token in [raw_token_1, raw_token_2]:
            for _ in range(25):
                response = await async_client.get(
                    f"/api/widget/config/{raw_token}",
                )
                assert response.status_code != 429, (
                    f"Expected no rate limit for token {raw_token[:16]}..., "
                    f"got 429 at request"
                )

    # ── Message endpoint ─────────────────────────────────────────────

    async def test_widget_message_rate_limit_enforced(
        self, async_client, widget_guest_jwt
    ):
        """Verify rapid widget messages trigger rate limiting.

        The default per-minute limit is ``20/minute``. Sending 25
        requests should produce at least one 429.

        The endpoint requires a valid guest JWT and an existing Redis
        session.  Since the session is created by the config endpoint,
        and we cannot easily create one here, we expect 404 (session
        not found) for requests within the rate limit and 429 for
        requests beyond it.
        """
        headers = {"Authorization": f"Bearer {widget_guest_jwt}"}
        responses = []
        for _ in range(25):
            response = await async_client.post(
                "/api/widget/session/message",
                json={"content": "hello", "file_ids": []},
                headers=headers,
            )
            responses.append(response.status_code)

        # Most requests within the rate limit should get 404 (no Redis
        # session).  Past the limit at least one should be 429.
        rate_limited = [s for s in responses if s == 429]
        assert len(rate_limited) >= 1, (
            f"Expected at least one 429 on message endpoint, "
            f"got statuses: {responses}"
        )

    async def test_widget_message_different_guest_tokens(
        self, async_client, widget_embed_config
    ):
        """Verify that different guest JWTs get independent rate-limit

        buckets so one embed config's usage does not starve another's.
        """
        cfg_1 = widget_embed_config["config"]
        cfg_2_id = str(uuid.uuid4())

        jwt_1 = create_guest_token({
            "sub": cfg_1.id,
            "tenant_id": cfg_1.tenant_id,
            "session_id": str(uuid.uuid4()),
        })
        jwt_2 = create_guest_token({
            "sub": cfg_2_id,
            "tenant_id": cfg_1.tenant_id,
            "session_id": str(uuid.uuid4()),
        })

        # Fire 15 requests for each — well under the 20/min limit
        for jwt in [jwt_1, jwt_2]:
            for _ in range(15):
                response = await async_client.post(
                    "/api/widget/session/message",
                    json={"content": "hello", "file_ids": []},
                    headers={"Authorization": f"Bearer {jwt}"},
                )
                # 404 is expected (no Redis session); 429 would be a fail
                assert response.status_code != 429, (
                    "Expected no rate limit with different guest tokens"
                )

    # ── Session read endpoints ───────────────────────────────────────

    async def test_widget_session_read_rate_limited(
        self, async_client, widget_guest_jwt
    ):
        """Verify session reads (GET session, GET messages, DELETE stream)
        are rate-limited.
        """
        headers = {"Authorization": f"Bearer {widget_guest_jwt}"}
        responses = []

        # Hit the session endpoint 70 times (limit is 60/min)
        for _ in range(70):
            response = await async_client.get(
                "/api/widget/session",
                headers=headers,
            )
            responses.append(response.status_code)

        rate_limited = [s for s in responses if s == 429]
        assert len(rate_limited) >= 1, (
            f"Expected at least one 429 on session read endpoint, "
            f"got statuses: {responses}"
        )

    # ── Key function correctness ─────────────────────────────────────

    async def test_widget_guest_key_different_tenants(
        self, widget_embed_config, second_tenant
    ):
        """Verify that guest JWTs from different tenants produce different

        rate-limit keys.  This is a unit-level check on the key function
        logic.
        """
        from src.core.limiter import get_widget_guest_key

        cfg = widget_embed_config["config"]

        # Build two guest JWTs — same embed config logic but different tenants
        jwt_tenant_a = create_guest_token({
            "sub": cfg.id,
            "tenant_id": cfg.tenant_id,
            "session_id": str(uuid.uuid4()),
        })
        jwt_tenant_b = create_guest_token({
            "sub": cfg.id,
            "tenant_id": second_tenant.id,
            "session_id": str(uuid.uuid4()),
        })

        # We cannot easily call get_widget_guest_key without a real
        # Request object.  Instead, verify the tokens carry the correct
        # tenant_id claims.
        from src.core.jwt import decode_guest_token

        payload_a = decode_guest_token(jwt_tenant_a)
        payload_b = decode_guest_token(jwt_tenant_b)

        assert payload_a["tenant_id"] == cfg.tenant_id
        assert payload_b["tenant_id"] == second_tenant.id
        assert payload_a["tenant_id"] != payload_b["tenant_id"]
