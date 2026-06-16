# =============================================================================
# PH Agent Hub — JWT Token Validation Tests
# =============================================================================
# Tests token creation, decoding, expiry, tampering, and secret separation
# between user tokens and guest/demo tokens.
# =============================================================================

import time

import pytest
from jose import JWTError, jwt as jose_jwt

from src.core.config import settings
from src.core.jwt import (
    create_access_token,
    create_refresh_token,
    create_guest_token,
    create_demo_token,
    decode_token,
    decode_guest_token,
)

pytestmark = [
    pytest.mark.security,
    pytest.mark.unit,
]


class TestAccessToken:
    """Tests for user JWT access tokens."""

    def test_create_and_decode(self):
        """Verify happy path: create token, decode, check claims."""
        payload = {"sub": "user-123", "tenant_id": "tenant-abc", "role": "user"}
        token = create_access_token(payload)
        decoded = decode_token(token)

        assert decoded["sub"] == "user-123"
        assert decoded["tenant_id"] == "tenant-abc"
        assert decoded["role"] == "user"
        assert "iat" in decoded
        assert "exp" in decoded
        assert decoded["exp"] > decoded["iat"]

    def test_expired_token_raises_error(self):
        """Verify expired token raises JWTError."""
        payload = {
            "sub": "user-123",
            "tenant_id": "tenant-abc",
            "role": "user",
            "exp": int(time.time()) - 3600,  # 1 hour ago
            "iat": int(time.time()) - 7200,
        }
        token = jose_jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
        with pytest.raises(JWTError):
            decode_token(token)

    def test_tampered_token_raises_error(self):
        """Verify tampered token (modified payload) raises JWTError."""
        payload = {"sub": "user-123", "tenant_id": "tenant-abc", "role": "user"}
        token = create_access_token(payload)

        # Tamper with the payload section
        parts = token.split(".")
        tampered = parts[0] + ".eyJmYWtlIjp0cnVlfQ." + parts[2]

        with pytest.raises(JWTError):
            decode_token(tampered)

    def test_wrong_secret_fails(self):
        """Verify token signed with a different secret cannot be decoded."""
        payload = {"sub": "user-123", "tenant_id": "tenant-abc", "role": "user"}
        token = jose_jwt.encode(payload, "wrong-secret", algorithm="HS256")
        with pytest.raises(JWTError):
            decode_token(token)

    def test_missing_sub_claim(self):
        """Verify token without sub claim can still be decoded (handled by caller)."""
        payload = {"tenant_id": "tenant-abc", "role": "user"}
        token = create_access_token(payload)
        decoded = decode_token(token)
        assert decoded.get("sub") is None


class TestRefreshToken:
    """Tests for refresh tokens (longer TTL, JTI support)."""

    def test_create_and_decode(self):
        """Verify refresh token creation and decoding."""
        payload = {"sub": "user-123", "tenant_id": "tenant-abc", "role": "user", "jti": "unique-id"}
        token = create_refresh_token(payload)
        decoded = decode_token(token)

        assert decoded["sub"] == "user-123"
        assert decoded["jti"] == "unique-id"
        assert decoded["exp"] > decoded["iat"]

    def test_expired_refresh_token_raises_error(self):
        """Verify expired refresh token raises JWTError."""
        payload = {
            "sub": "user-123",
            "tenant_id": "tenant-abc",
            "role": "user",
            "jti": "unique-id",
            "exp": int(time.time()) - 3600,
        }
        token = jose_jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
        with pytest.raises(JWTError):
            decode_token(token)


class TestGuestToken:
    """Tests for guest tokens (used by embed widget)."""

    def test_create_and_decode(self):
        """Verify guest token creation and decoding."""
        payload = {"sub": "embed-123", "tenant_id": "tenant-abc", "type": "guest", "session_id": "sess-1"}
        token = create_guest_token(payload)
        decoded = decode_guest_token(token)

        assert decoded["sub"] == "embed-123"
        assert decoded["tenant_id"] == "tenant-abc"
        assert decoded["type"] == "guest"
        assert decoded["session_id"] == "sess-1"

    def test_guest_token_missing_claims(self):
        """Verify guest token with missing sub or tenant_id can be decoded (validated by caller)."""
        payload = {"type": "guest"}
        token = create_guest_token(payload)
        decoded = decode_guest_token(token)
        assert decoded.get("sub") is None

    def test_user_token_cannot_be_used_as_guest_token(self):
        """Verify a regular user JWT is rejected by decode_guest_token."""
        payload = {"sub": "user-123", "tenant_id": "tenant-abc", "role": "user"}
        token = create_access_token(payload)
        with pytest.raises(JWTError):
            decode_guest_token(token)

    def test_guest_token_cannot_be_used_as_user_token(self):
        """Verify a guest token is rejected by decode_token."""
        payload = {"sub": "embed-123", "tenant_id": "tenant-abc", "type": "guest"}
        token = create_guest_token(payload)
        with pytest.raises(JWTError):
            decode_token(token)


class TestDemoToken:
    """Tests for demo tokens (anonymous demo access)."""

    def test_create_and_decode(self):
        """Verify demo token creation and decoding."""
        payload = {"sub": "demo-tenant-id", "tenant_id": "demo-tenant-id", "type": "demo", "session_id": "sess-1"}
        token = create_demo_token(payload)
        decoded = decode_guest_token(token)

        assert decoded["sub"] == "demo-tenant-id"
        assert decoded["type"] == "demo"

    def test_demo_token_type_validation(self):
        """Verify demo token has type='demo'."""
        payload = {"sub": "demo-tenant-id", "tenant_id": "demo-tenant-id", "type": "demo"}
        token = create_demo_token(payload)
        decoded = decode_guest_token(token)
        assert decoded["type"] == "demo"

    def test_nondemo_token_rejected_by_demo_guard(self):
        """Verify a non-demo guest token is rejected by demo-type check."""
        payload = {"sub": "embed-123", "tenant_id": "tenant-abc", "type": "guest"}
        token = create_guest_token(payload)
        decoded = decode_guest_token(token)
        assert decoded["type"] != "demo"
