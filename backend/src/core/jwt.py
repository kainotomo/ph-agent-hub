# =============================================================================
# PH Agent Hub — JWT Encode / Decode
# =============================================================================
# Single-module rule: ONLY this file imports `python-jose`.
# =============================================================================

from datetime import datetime, timedelta, timezone

from jose import jwt as jose_jwt

from .config import settings


def create_access_token(payload: dict) -> str:
    """Create a signed JWT access token.

    The provided payload should contain at minimum:
        sub (subject / user id), tenant_id, role
    The `exp` (expiration) and `iat` (issued-at) claims are added automatically.
    """
    now = datetime.now(timezone.utc)
    to_encode = payload.copy()
    to_encode.update(
        {
            "iat": now,
            "exp": now + timedelta(seconds=settings.JWT_EXPIRES_IN),
        }
    )
    return jose_jwt.encode(to_encode, settings.JWT_SECRET, algorithm="HS256")


def create_refresh_token(payload: dict) -> str:
    """Create a signed JWT refresh token with longer TTL."""
    now = datetime.now(timezone.utc)
    to_encode = payload.copy()
    to_encode.update(
        {
            "iat": now,
            "exp": now + timedelta(seconds=settings.JWT_REFRESH_EXPIRES_IN),
        }
    )
    return jose_jwt.encode(to_encode, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Returns the claims dict."""
    return jose_jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])


# ---------------------------------------------------------------------------
# Guest (widget) tokens — short-lived, separate secret
# ---------------------------------------------------------------------------


def create_guest_token(payload: dict) -> str:
    """Create a short-lived JWT for embedded widget guests.

    Payload should contain:
        sub: embed_config_id
        tenant_id: tenant to scope the session to
        type: "guest"

    The token has a **short** TTL (5 minutes) and uses a separate secret
    from user access tokens for isolation.
    """
    now = datetime.now(timezone.utc)
    to_encode = payload.copy()
    to_encode.update(
        {
            "iat": now,
            "exp": now + timedelta(seconds=300),  # 5 minutes
            "type": "guest",
        }
    )
    return jose_jwt.encode(to_encode, settings.EMBED_GUEST_TOKEN_SECRET, algorithm="HS256")


def create_demo_token(payload: dict) -> str:
    """Create a short-lived JWT for demo (anonymous) sessions.

    Payload should contain:
        sub: tenant_id (the demo tenant)
        session_id: the temporary session ID
        type: "demo"

    Uses the same guest token secret for isolation from user JWTs,
    but carries a different ``type`` claim so endpoints can distinguish
    demo tokens from widget guest tokens.
    """
    now = datetime.now(timezone.utc)
    to_encode = payload.copy()
    to_encode.update(
        {
            "iat": now,
            "exp": now + timedelta(seconds=300),  # 5 minutes
            "type": "demo",
        }
    )
    return jose_jwt.encode(to_encode, settings.EMBED_GUEST_TOKEN_SECRET, algorithm="HS256")


def decode_guest_token(token: str) -> dict:
    """Decode and validate a guest JWT. Returns the claims dict."""
    return jose_jwt.decode(token, settings.EMBED_GUEST_TOKEN_SECRET, algorithms=["HS256"])
