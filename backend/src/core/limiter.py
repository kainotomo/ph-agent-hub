# =============================================================================
# PH Agent Hub — Rate Limiter (slowapi singleton)
# =============================================================================
# Single-module rule: ONLY this file imports `slowapi`.
# =============================================================================

import hashlib
import logging

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .jwt import decode_guest_token

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)


def reset_limiter():
    """Reset the in-memory rate limiter state.

    Used in tests to prevent rate-limit state from leaking across
    test cases.
    """
    limiter._storage.reset()


# ---------------------------------------------------------------------------
# Widget-specific key functions
# ---------------------------------------------------------------------------


def _ip_only_key(request: Request) -> str:
    """Return a plain IP-based key as a safety-net fallback."""
    return get_remote_address(request)


def _extract_bearer_token(request: Request) -> str | None:
    """Extract a Bearer token from the Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def get_widget_config_key(request: Request) -> str:
    """Rate-limit key for ``GET /widget/config/{token}``.

    Uses a hash of the guest token from the URL path combined with the
    client IP.  Falls back to IP-only if the token is missing.
    """
    ip = get_remote_address(request)
    # Token is the last path segment: /widget/config/{token}
    token = request.url.path.rstrip("/").split("/")[-1]
    if token and token != "config":
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
        return f"widget_config:{ip}:{token_hash}"
    logger.warning("get_widget_config_key: no token in path, fallback to IP")
    return f"widget_config:{ip}"


def get_widget_guest_key(request: Request) -> str:
    """Rate-limit key for guest-authenticated widget endpoints.

    Uses a composite of (IP, tenant_id, embed_config_id) extracted from
    the guest JWT.  Falls back to IP-only with a ``noauth_`` prefix so
    that a tighter default rate can be applied.
    """
    ip = get_remote_address(request)
    token = _extract_bearer_token(request)
    if token is None:
        logger.warning("get_widget_guest_key: no Bearer token, fallback to IP")
        return f"widget_guest_noauth:{ip}"

    try:
        payload = decode_guest_token(token)
    except Exception:
        logger.warning("get_widget_guest_key: invalid JWT, fallback to IP")
        return f"widget_guest_noauth:{ip}"

    tenant_id = payload.get("tenant_id", "")
    embed_config_id = payload.get("sub", "")
    if not tenant_id or not embed_config_id:
        logger.warning("get_widget_guest_key: JWT missing claims, fallback to IP")
        return f"widget_guest_noauth:{ip}"

    config_hash = hashlib.sha256(embed_config_id.encode()).hexdigest()[:16]
    return f"widget_guest:{ip}:{tenant_id}:{config_hash}"


__all__ = [
    "limiter",
    "RateLimitExceeded",
    "reset_limiter",
    "get_widget_config_key",
    "get_widget_guest_key",
]
