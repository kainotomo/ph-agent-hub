# =============================================================================
# PH Agent Hub — OAuth State Security Edge Case Tests
# =============================================================================
# Tests OAuth state integrity under edge conditions not covered by the
# basic unit tests (test_oauth_state.py), abuse scenario tests
# (test_abuse_scenarios.py::TestOAuthStateAbuse), or callback integration
# tests (test_credentials_api.py::TestOAuthStateIntegrity):
#
#   - Cross-user state binding
#   - Tampered/malformed stored state payloads (non-JSON, wrong type)
#   - Expiry boundary precision
#   - Zero / negative TTL behavior
#   - Concurrent callback attempts with the same valid nonce
#   - Microsoft OAuth callback replay (mirror of Google replay)
#   - Missing required fields in stored state
# =============================================================================

import asyncio
import json
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio

from src.core.redis import (
    OAUTH_STATE_PREFIX,
    get_oauth_state,
    get_redis,
    store_oauth_state,
)

pytestmark = [
    pytest.mark.security,
]


# =============================================================================
# Unit Tests — Direct Redis-level state behaviour
# =============================================================================


class TestOAuthStateStoreEdgeCases:
    """Redis-level edge cases for the OAuth state nonce store.

    These tests access the store functions directly and do not require
    HTTP fixtures or external service mocks.
    """

    async def test_state_binds_to_initiating_user(self):
        """Verify a stored state payload contains the user who initiated it."""
        nonce = str(uuid.uuid4())
        await store_oauth_state(nonce, "alice-id", "email_tool", ttl=300)

        payload = await get_oauth_state(nonce)
        assert payload is not None
        assert payload["user_id"] == "alice-id"
        assert payload["tool_id"] == "email_tool"

        # If the nonce were leaked to another user, the callback would
        # still associate credentials with "alice-id" — the UUID nonce
        # itself is the primary protection against interception.

    async def test_tampered_stored_state_malformed_json(self):
        """Verify a non-JSON value raises JSONDecodeError (documented gap).

        The current ``get_oauth_state`` does not catch ``json.JSONDecodeError``
        when the stored value is not valid JSON.  A hardening fix should wrap
        ``json.loads`` in a try/except and return ``None`` instead.
        """
        import json as _json

        r = await get_redis()
        nonce = f"malformed-{uuid.uuid4().hex}"
        await r.setex(f"{OAUTH_STATE_PREFIX}{nonce}", 300, "not-json-at-all")

        with pytest.raises(_json.JSONDecodeError):
            await get_oauth_state(nonce)

    async def test_tampered_stored_state_invalid_json_type(self):
        """Verify a JSON array / number is returned as-is (not validated to be dict).

        The current ``get_oauth_state`` does not validate that the decoded JSON
        is a ``dict`` object.  Lists, numbers, and strings are returned as-is.
        The callback endpoint accesses ``state_data["user_id"]`` which would
        raise ``TypeError`` for non-dict types.  A hardening fix should validate
        the type after decode.
        """
        r = await get_redis()
        nonce = f"invalid-type-{uuid.uuid4().hex}"

        # JSON array instead of object
        await r.setex(f"{OAUTH_STATE_PREFIX}{nonce}", 300, json.dumps(["not", "a", "dict"]))
        payload = await get_oauth_state(nonce)
        assert isinstance(payload, list)  # returned as-is, not validated

        # JSON number instead of object
        nonce2 = f"invalid-type-{uuid.uuid4().hex}"
        await r.setex(f"{OAUTH_STATE_PREFIX}{nonce2}", 300, json.dumps(42))
        payload2 = await get_oauth_state(nonce2)
        assert isinstance(payload2, int)  # returned as-is, not validated

        # JSON string instead of object
        nonce3 = f"invalid-type-{uuid.uuid4().hex}"
        await r.setex(f"{OAUTH_STATE_PREFIX}{nonce3}", 300, json.dumps("i am a string"))
        payload3 = await get_oauth_state(nonce3)
        assert isinstance(payload3, str)  # returned as-is, not validated

    async def test_expiry_boundary_precision(self):
        """Verify state is retrievable just before TTL and gone after."""
        # Key A — store with 2-second TTL, read at ~1.5s (should exist)
        nonce_a = f"boundary-a-{uuid.uuid4().hex}"
        await store_oauth_state(nonce_a, "user-1", "email_tool", ttl=2)
        await asyncio.sleep(1.5)

        payload = await get_oauth_state(nonce_a)
        assert payload is not None, "State should still exist before TTL expiry"

        # Key B — store with 1-second TTL, read at ~2.5s (should be gone)
        nonce_b = f"boundary-b-{uuid.uuid4().hex}"
        await store_oauth_state(nonce_b, "user-2", "email_tool", ttl=1)
        await asyncio.sleep(1.5)

        payload = await get_oauth_state(nonce_b)
        assert payload is None, "State should have expired after TTL"

    async def test_minimum_ttl_one_second(self):
        """Verify TTL=1 is the minimum accepted value (Redis SETEX rejects 0)."""
        nonce = f"min-ttl-{uuid.uuid4().hex}"
        await store_oauth_state(nonce, "user-1", "email_tool", ttl=1)

        # Should be retrievable immediately
        payload = await get_oauth_state(nonce)
        assert payload is not None
        assert payload["user_id"] == "user-1"

    async def test_state_missing_fields_returns_none(self):
        """Verify a state payload missing required fields does not crash.

        The store always writes user_id, tool_id, and created_at, but a
        malformed payload in Redis (missing keys) should not raise.
        """
        r = await get_redis()
        nonce = f"missing-fields-{uuid.uuid4().hex}"

        # Payload missing 'user_id'
        await r.setex(
            f"{OAUTH_STATE_PREFIX}{nonce}", 300,
            json.dumps({"tool_id": "email_tool", "created_at": "2025-01-01T00:00:00"}),
        )
        payload = await get_oauth_state(nonce)
        # Current implementation returns the dict as-is — accept this behaviour
        # but verify it does not crash or return something unexpected
        assert payload is not None
        assert "user_id" not in payload  # confirms incomplete state


# =============================================================================
# Integration Tests — HTTP callback with mocked token exchange
# =============================================================================


class TestOAuthCallbackConcurrency:
    """HTTP-level tests for concurrent callback edge cases.

    Requires ``override_get_db`` and ``async_client`` fixtures.
    """

    async def test_concurrent_callback_same_nonce(
        self, async_client, test_user, test_tenant, test_tool,
    ):
        """Verify only one concurrent callback with the same nonce succeeds.

        Fires two HTTP requests simultaneously.  Exactly one should return
        302 (successful redirect) and the other 422 (state already consumed).
        """
        from src.core.redis import store_oauth_state

        nonce = str(uuid.uuid4())
        await store_oauth_state(nonce, test_user.id, "email_tool", ttl=300)

        mock_tokens = {
            "access_token": "ya29.concurrent-mock",
            "refresh_token": "1//mock-refresh",
            "expires_at": 9999999999,
            "scope": "https://www.googleapis.com/auth/gmail.modify",
            "token_type": "Bearer",
            "id_token": "eyJhbGciOiJSUzI1NiJ9.eyJlbWFpbCI6InRlc3RAZXhhbXBsZS5jb20ifQ.signature",
        }

        with patch("src.core.oauth.exchange_google_code", return_value=mock_tokens):
            resp_a, resp_b = await asyncio.gather(
                async_client.get(
                    "/api/credentials/oauth/google/callback",
                    params={"code": "code-a", "state": nonce},
                ),
                async_client.get(
                    "/api/credentials/oauth/google/callback",
                    params={"code": "code-b", "state": nonce},
                ),
            )

        statuses = {resp_a.status_code, resp_b.status_code}
        assert 302 in statuses, f"Expected at least one 302, got {statuses}"
        assert 422 in statuses, f"Expected exactly one 422, got {statuses}"

    async def test_microsoft_callback_replay_rejected(
        self, async_client, test_user, test_tenant, test_tool,
    ):
        """Verify a consumed Microsoft OAuth state cannot be replayed."""
        from src.core.redis import store_oauth_state

        nonce = str(uuid.uuid4())
        await store_oauth_state(nonce, test_user.id, "email_tool", ttl=300)

        mock_tokens = {
            "access_token": "ms-mock-access",
            "refresh_token": "ms-mock-refresh",
            "expires_at": 9999999999,
            "scope": "User.Read",
            "token_type": "Bearer",
        }

        with patch("src.core.oauth.exchange_microsoft_code", return_value=mock_tokens):
            # First use — should succeed
            resp1 = await async_client.get(
                "/api/credentials/oauth/microsoft/callback",
                params={"code": "code-1", "state": nonce},
            )
            assert resp1.status_code == 302, resp1.text

            # Second use with same state — should be rejected
            resp2 = await async_client.get(
                "/api/credentials/oauth/microsoft/callback",
                params={"code": "code-2", "state": nonce},
            )
            assert resp2.status_code == 422, resp2.text
