# =============================================================================
# PH Agent Hub — OAuth State Nonce Store Tests
# =============================================================================
# Tests the server-side OAuth state integrity mechanism (Issue #345):
#   - store_oauth_state / get_oauth_state round-trip
#   - one-time consumption (atomic get-delete)
#   - TTL expiry
#   - unknown nonce rejection
# =============================================================================

import asyncio

import pytest
import pytest_asyncio

from src.core.redis import (
    get_redis,
    store_oauth_state,
    get_oauth_state,
    OAUTH_STATE_PREFIX,
)

pytestmark = [
    pytest.mark.security,
    pytest.mark.unit,
]


class TestOAuthStateStore:
    """Tests for the Redis-backed OAuth state nonce store."""

    async def test_store_and_retrieve(self):
        """Verify round-trip store → retrieve returns correct payload."""
        nonce = "test-nonce-1"
        user_id = "user-abc"
        tool_id = "email_tool"

        await store_oauth_state(nonce, user_id, tool_id, ttl=300)
        result = await get_oauth_state(nonce)

        assert result is not None
        assert result["user_id"] == user_id
        assert result["tool_id"] == tool_id
        assert "created_at" in result

    async def test_unknown_nonce_returns_none(self):
        """Verify a nonce that was never stored returns None."""
        result = await get_oauth_state("nonexistent-nonce")
        assert result is None

    async def test_one_time_use(self):
        """Verify the state is consumed on first retrieval (atomic get-delete)."""
        nonce = "test-nonce-2"
        await store_oauth_state(nonce, "user-1", "email_tool", ttl=300)

        # First retrieval succeeds
        first = await get_oauth_state(nonce)
        assert first is not None

        # Second retrieval returns None (key was deleted)
        second = await get_oauth_state(nonce)
        assert second is None

    async def test_expired_state_returns_none(self):
        """Verify an expired state returns None and can't be used again."""
        nonce = "test-nonce-expired"
        await store_oauth_state(nonce, "user-1", "email_tool", ttl=1)

        # Wait for TTL to expire
        await asyncio.sleep(1.5)

        result = await get_oauth_state(nonce)
        assert result is None

    async def test_ttl_is_set_correctly(self):
        """Verify the stored key has the correct TTL."""
        nonce = "test-nonce-ttl"
        ttl = 600
        await store_oauth_state(nonce, "user-1", "email_tool", ttl=ttl)

        r = await get_redis()
        key_ttl = await r.ttl(f"{OAUTH_STATE_PREFIX}{nonce}")
        assert 0 < key_ttl <= ttl

        # Clean up
        await r.delete(f"{OAUTH_STATE_PREFIX}{nonce}")

    async def test_default_ttl_used(self):
        """Verify store_oauth_state uses the default TTL when not specified."""
        from src.core.redis import OAUTH_STATE_TTL

        nonce = "test-nonce-default-ttl"
        await store_oauth_state(nonce, "user-1", "email_tool")

        r = await get_redis()
        key_ttl = await r.ttl(f"{OAUTH_STATE_PREFIX}{nonce}")
        assert 0 < key_ttl <= OAUTH_STATE_TTL

        # Clean up
        await r.delete(f"{OAUTH_STATE_PREFIX}{nonce}")

    async def test_concurrent_retrieval_only_one_succeeds(self):
        """Verify that concurrent get_oauth_state calls result in only one
        consumer getting the data (atomic GETDEL guarantees this)."""
        nonce = "test-nonce-concurrent"
        await store_oauth_state(nonce, "user-1", "email_tool", ttl=300)

        # Fire two concurrent retrievals
        results = await asyncio.gather(
            get_oauth_state(nonce),
            get_oauth_state(nonce),
        )

        # Exactly one should have the data, the other should be None
        successes = [r for r in results if r is not None]
        assert len(successes) == 1
        assert successes[0]["user_id"] == "user-1"
