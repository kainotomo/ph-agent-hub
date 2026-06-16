# =============================================================================
# PH Agent Hub — Rate Limiter (slowapi singleton)
# =============================================================================
# Single-module rule: ONLY this file imports `slowapi`.
# =============================================================================

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


def reset_limiter():
    """Reset the in-memory rate limiter state.

    Used in tests to prevent rate-limit state from leaking across
    test cases.
    """
    limiter._storage.reset()


__all__ = ["limiter", "RateLimitExceeded", "reset_limiter"]
