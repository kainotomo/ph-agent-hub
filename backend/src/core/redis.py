# =============================================================================
# PH Agent Hub — Async Redis Client (JTI Denylist)
# =============================================================================
# Single-module rule: ONLY this file imports `redis.asyncio`.
# =============================================================================

import asyncio

import redis.asyncio as aioredis

from .config import settings

# ---------------------------------------------------------------------------
# Lazy singleton Redis connection
# ---------------------------------------------------------------------------
_redis: aioredis.Redis | None = None
_redis_lock: asyncio.Lock | None = None


async def get_redis() -> aioredis.Redis:
    """Return a connected async Redis client (lazy singleton)."""
    global _redis, _redis_lock
    if _redis_lock is None:
        _redis_lock = asyncio.Lock()
    async with _redis_lock:
        if _redis is None:
            _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        return _redis


# ---------------------------------------------------------------------------
# Denylist helpers — JTI revocation for refresh tokens
# ---------------------------------------------------------------------------
DENYLIST_PREFIX = "jti_denylist:"


async def add_to_denylist(jti: str, ttl_seconds: int) -> None:
    """Add a JWT JTI to the Redis denylist with an absolute TTL."""
    r = await get_redis()
    await r.setex(f"{DENYLIST_PREFIX}{jti}", ttl_seconds, "1")


async def is_denylisted(jti: str) -> bool:
    """Check whether a JTI is present in the Redis denylist."""
    r = await get_redis()
    return await r.exists(f"{DENYLIST_PREFIX}{jti}") > 0


# ---------------------------------------------------------------------------
# Temporary session helpers — Redis-backed session storage
# ---------------------------------------------------------------------------
TEMP_SESSION_PREFIX = "session:tmp:"


async def store_temp_session(
    session_id: str, data: dict, ttl: int | None = None
) -> None:
    """Store a temporary session JSON blob in Redis.

    Args:
        session_id: The session UUID.
        data: JSON-serialisable dict of session fields.
        ttl: TTL in seconds (defaults to settings.TEMPORARY_SESSION_TTL_SECONDS).
    """
    import json

    r = await get_redis()
    key = f"{TEMP_SESSION_PREFIX}{session_id}"
    ttl = ttl if ttl is not None else settings.TEMPORARY_SESSION_TTL_SECONDS
    await r.setex(key, ttl, json.dumps(data))


async def get_temp_session(session_id: str) -> dict | None:
    """Retrieve a temporary session JSON blob from Redis.

    Returns None if the key does not exist.
    """
    import json

    r = await get_redis()
    key = f"{TEMP_SESSION_PREFIX}{session_id}"
    raw = await r.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def delete_temp_session(session_id: str) -> None:
    """Delete a temporary session and its messages from Redis."""
    r = await get_redis()
    key = f"{TEMP_SESSION_PREFIX}{session_id}"
    msg_key = f"{TEMP_SESSION_PREFIX}{session_id}:messages"
    await r.delete(key, msg_key)


async def append_temp_message(session_id: str, msg: dict) -> None:
    """Append a message dict to the temporary session's message list.

    Messages are stored as a JSON list at ``session:tmp:{id}:messages``.
    The session TTL is refreshed on each append.
    """
    import json

    r = await get_redis()
    msg_key = f"{TEMP_SESSION_PREFIX}{session_id}:messages"
    session_key = f"{TEMP_SESSION_PREFIX}{session_id}"

    # Atomically append and refresh TTL
    pipe = r.pipeline()
    pipe.get(msg_key)
    pipe.ttl(session_key)
    existing_raw, ttl = await pipe.execute()

    messages: list[dict] = json.loads(existing_raw) if existing_raw else []
    messages.append(msg)

    ttl = ttl if ttl and ttl > 0 else settings.TEMPORARY_SESSION_TTL_SECONDS
    pipe2 = r.pipeline()
    pipe2.setex(msg_key, ttl, json.dumps(messages))
    # Refresh the session blob TTL too so it doesn't expire before messages
    pipe2.expire(session_key, ttl)
    await pipe2.execute()


async def get_temp_messages(session_id: str) -> list[dict]:
    """Retrieve all messages for a temporary session.

    Returns an empty list if no messages exist.
    """
    import json

    r = await get_redis()
    msg_key = f"{TEMP_SESSION_PREFIX}{session_id}:messages"
    raw = await r.get(msg_key)
    if raw is None:
        return []
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Stream cancellation helpers — used by the SSE streaming endpoint to
# signal an in-progress agent run that it should abort.
# ---------------------------------------------------------------------------
STREAM_CANCEL_PREFIX = "stream:cancel:"


async def set_stream_cancel(session_id: str, ttl: int = 60) -> None:
    """Set a stream cancellation flag for *session_id*.

    The flag auto-expires after *ttl* seconds to prevent stale keys.
    """
    r = await get_redis()
    await r.setex(f"{STREAM_CANCEL_PREFIX}{session_id}", ttl, "1")


async def check_stream_cancel(session_id: str) -> bool:
    """Return True if a cancellation has been requested for *session_id*."""
    r = await get_redis()
    return await r.exists(f"{STREAM_CANCEL_PREFIX}{session_id}") > 0


async def clear_stream_cancel(session_id: str) -> None:
    """Remove the stream cancellation flag for *session_id*."""
    r = await get_redis()
    await r.delete(f"{STREAM_CANCEL_PREFIX}{session_id}")


# ---------------------------------------------------------------------------
# Follow-up questions helpers — stored in Redis so the frontend can fetch
# them via a lightweight REST endpoint after the SSE stream closes.
# ---------------------------------------------------------------------------
FOLLOW_UP_PREFIX = "follow_up:"


async def store_follow_up_questions(
    tenant_id: str, session_id: str, questions: list[str], ttl: int = 60
) -> None:
    """Store follow-up questions for a tenant-scoped session with a short TTL.

    The short TTL ensures stale questions don't linger if the user
    navigates away before fetching them.
    """
    import json

    r = await get_redis()
    key = f"{FOLLOW_UP_PREFIX}{tenant_id}:{session_id}"
    await r.setex(key, ttl, json.dumps(questions))


async def get_follow_up_questions(tenant_id: str, session_id: str) -> list[str] | None:
    """Retrieve follow-up questions from Redis, scoped by tenant.

    Returns None if the key does not exist (questions not yet generated
    or already expired).
    """
    import json

    r = await get_redis()
    key = f"{FOLLOW_UP_PREFIX}{tenant_id}:{session_id}"
    raw = await r.get(key)
    if raw is None:
        return None
    return json.loads(raw)


# ---------------------------------------------------------------------------
# OAuth state helpers — server-side nonce store for OAuth state integrity
# (Issue #345).  State values are single-use and auto-expire.
# ---------------------------------------------------------------------------
OAUTH_STATE_PREFIX = "oauth_state:"
OAUTH_STATE_TTL = 600  # 10 minutes


async def store_oauth_state(
    nonce: str,
    user_id: str,
    tool_id: str,
    ttl: int = OAUTH_STATE_TTL,
) -> None:
    """Store an OAuth state nonce in Redis with a TTL.

    Args:
        nonce: A random UUID v4 — the ``state`` parameter sent to the OAuth provider.
        user_id: The authenticated user who initiated the OAuth flow.
        tool_id: The tool type being connected (e.g. ``email_tool``).
        ttl: TTL in seconds (default 600 = 10 minutes).
    """
    import json
    import time

    r = await get_redis()
    payload = json.dumps({
        "user_id": user_id,
        "tool_id": tool_id,
        "created_at": time.time(),
    })
    await r.setex(f"{OAUTH_STATE_PREFIX}{nonce}", ttl, payload)


async def get_oauth_state(nonce: str) -> dict | None:
    """Retrieve and **delete** an OAuth state nonce (atomic get-delete).

    Returns the parsed payload dict on first retrieval, or ``None`` if the
    key does not exist (unknown, expired, or already consumed — one-time use).
    """
    import json

    r = await get_redis()
    key = f"{OAUTH_STATE_PREFIX}{nonce}"
    raw = await r.getdel(key)
    if raw is None:
        return None
    return json.loads(raw)
