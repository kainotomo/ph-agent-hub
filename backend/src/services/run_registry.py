# =============================================================================
# PH Agent Hub — Authoritative Session Run Registry (Issue #604)
# =============================================================================
# A single source of truth for "is this chat session running?".
#
# The registry lives in Redis so every replica (and every browser tab)
# observes the same state:
#
#   run:{session_id}          hash  — the live run lease
#   runs:user:{user_id}       set   — sessions with a lease, indexed by owner
#   run:last:{session_id}     str   — short-lived completion marker (JSON)
#
# A run is "running" iff its lease key exists and has not expired.  The
# lease heartbeat is refreshed every ``RUN_HEARTBEAT_INTERVAL_SECONDS``;
# if the owning process dies, the key expires after
# ``RUN_LEASE_TTL_SECONDS`` (seconds, not the five-minute TTL of the old
# ``stream:active:*`` flag).
#
# Acquire, heartbeat and release are atomic Lua scripts so two replicas
# can never hold the same session at once and an old run can never release
# (or heartbeat) a newer run's lease.
# =============================================================================

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..core.config import settings
from ..core.redis import get_redis

logger = logging.getLogger(__name__)

RUN_KEY_PREFIX = "run:"
RUN_USER_INDEX_PREFIX = "runs:user:"
RUN_LAST_KEY_PREFIX = "run:last:"
RUN_CANCEL_KEY_PREFIX = "run:cancel:"
STREAM_CANCEL_KEY_PREFIX = "stream:cancel:"

#: Lease states stored in the hash.  Only ``running`` is currently used.
RUN_STATE_RUNNING = "running"

#: Terminal outcomes written to the completion marker.
OUTCOME_COMPLETED = "completed"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_FAILED = "failed"
OUTCOME_LOST = "lost"


# ---------------------------------------------------------------------------
# Lease model
# ---------------------------------------------------------------------------


@dataclass
class RunLease:
    """A live run lease owned by this process."""

    session_id: str
    run_id: str
    kind: str
    user_id: str
    tenant_id: str
    owner: str
    started_at: str
    heartbeat_at: str
    state: str = RUN_STATE_RUNNING

    #: Set by the spawn helpers once the background task owns lifecycle
    #: cleanup.  The request-scoped context manager must not release a
    #: handed-off lease.
    handed_off: bool = field(default=False, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "owner": self.owner,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "state": self.state,
        }


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def run_key(session_id: str) -> str:
    return f"{RUN_KEY_PREFIX}{session_id}"


def user_index_key(user_id: str) -> str:
    return f"{RUN_USER_INDEX_PREFIX}{user_id}"


def last_run_key(session_id: str) -> str:
    return f"{RUN_LAST_KEY_PREFIX}{session_id}"


def cancel_key(session_id: str) -> str:
    return f"{STREAM_CANCEL_KEY_PREFIX}{session_id}"


def run_cancel_key(run_id: str) -> str:
    return f"{RUN_CANCEL_KEY_PREFIX}{run_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_id() -> str:
    """Identify this backend process (host + pid) for debugging."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _lease_from_hash(session_id: str, raw: dict[str, Any]) -> RunLease | None:
    run_id = raw.get("run_id")
    if not run_id:
        return None
    return RunLease(
        session_id=session_id,
        run_id=run_id,
        kind=raw.get("kind", "chat"),
        user_id=raw.get("user_id", ""),
        tenant_id=raw.get("tenant_id", ""),
        owner=raw.get("owner", ""),
        started_at=raw.get("started_at", ""),
        heartbeat_at=raw.get("heartbeat_at", ""),
        state=raw.get("state", RUN_STATE_RUNNING),
    )


# ---------------------------------------------------------------------------
# Atomic scripts
# ---------------------------------------------------------------------------

# KEYS: run, user index, cancel
# ARGV: run_id, kind, user_id, tenant_id, owner, started_at, heartbeat_at,
#       lease_ttl, session_id, index_ttl
_ACQUIRE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
    return 0
end
redis.call('HSET', KEYS[1],
    'run_id', ARGV[1],
    'kind', ARGV[2],
    'user_id', ARGV[3],
    'tenant_id', ARGV[4],
    'owner', ARGV[5],
    'started_at', ARGV[6],
    'heartbeat_at', ARGV[7],
    'state', 'running')
redis.call('EXPIRE', KEYS[1], ARGV[8])
redis.call('SADD', KEYS[2], ARGV[9])
redis.call('EXPIRE', KEYS[2], ARGV[10])
-- A cancellation flag left over from a previous run must not kill this one.
redis.call('DEL', KEYS[3])
return 1
"""

# KEYS: run, user index
# ARGV: run_id, heartbeat_at, lease_ttl, session_id, index_ttl
_HEARTBEAT_SCRIPT = """
if redis.call('HGET', KEYS[1], 'run_id') ~= ARGV[1] then
    return 0
end
redis.call('HSET', KEYS[1], 'heartbeat_at', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('SADD', KEYS[2], ARGV[4])
redis.call('EXPIRE', KEYS[2], ARGV[5])
return 1
"""

# KEYS: run, user index, last marker
# ARGV: run_id, session_id, marker_json, marker_ttl
_RELEASE_SCRIPT = """
if redis.call('HGET', KEYS[1], 'run_id') ~= ARGV[1] then
    return 0
end
redis.call('DEL', KEYS[1])
redis.call('SREM', KEYS[2], ARGV[2])
redis.call('SET', KEYS[3], ARGV[3], 'EX', ARGV[4])
return 1
"""

# KEYS: cancel key
# ARGV: expected run id (empty string = no owner check), ttl, value
_CLEAR_CANCEL_SCRIPT = """
if ARGV[1] ~= '' and redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call('SET', KEYS[1], ARGV[3], 'EX', ARGV[2])
return 1
"""

# KEYS: cancel key
# ARGV: expected run id (empty string = unconditional delete)
_DELETE_CANCEL_SCRIPT = """
if ARGV[1] ~= '' and redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
return redis.call('DEL', KEYS[1])
"""


# ---------------------------------------------------------------------------
# Lease lifecycle
# ---------------------------------------------------------------------------


async def acquire_run(
    session_id: str,
    user_id: str,
    tenant_id: str,
    kind: str = "chat",
) -> RunLease | None:
    """Atomically acquire the per-session run lease.

    Returns the new :class:`RunLease`, or ``None`` when another run already
    holds the session.  The caller should surface ``409 session_busy`` with
    the existing ``run_id`` in that case.
    """
    run_id = str(uuid.uuid4())
    now = _now_iso()
    owner = _owner_id()

    r = await get_redis()
    acquired = await r.eval(
        _ACQUIRE_SCRIPT,
        3,
        run_key(session_id),
        user_index_key(user_id),
        cancel_key(session_id),
        run_id,
        kind,
        user_id,
        tenant_id,
        owner,
        now,
        now,
        int(settings.RUN_LEASE_TTL_SECONDS),
        session_id,
        int(settings.RUN_USER_INDEX_TTL_SECONDS),
    )
    if not acquired:
        return None

    lease = RunLease(
        session_id=session_id,
        run_id=run_id,
        kind=kind,
        user_id=user_id,
        tenant_id=tenant_id,
        owner=owner,
        started_at=now,
        heartbeat_at=now,
    )
    _local_leases.add((session_id, run_id))
    logger.info(
        "Acquired run %s for session %s (kind=%s, owner=%s)",
        run_id, session_id, kind, owner,
    )
    return lease


async def heartbeat_run(lease: RunLease) -> bool:
    """Refresh *lease* if this process still owns it.

    Returns ``False`` when the lease was lost (expired or replaced) — the
    caller should stop heartbeating.
    """
    r = await get_redis()
    refreshed = await r.eval(
        _HEARTBEAT_SCRIPT,
        2,
        run_key(lease.session_id),
        user_index_key(lease.user_id),
        lease.run_id,
        _now_iso(),
        int(settings.RUN_LEASE_TTL_SECONDS),
        lease.session_id,
        int(settings.RUN_USER_INDEX_TTL_SECONDS),
    )
    return bool(refreshed)


async def release_run(
    lease: RunLease,
    outcome: str = OUTCOME_COMPLETED,
) -> bool:
    """Release *lease* if this process still owns it.

    Writes a short-lived ``run:last:`` marker carrying the terminal
    *outcome* so the status stream can distinguish a graceful finish from
    a lease that simply expired (crashed host).
    """
    marker = json.dumps({
        "run_id": lease.run_id,
        "session_id": lease.session_id,
        "kind": lease.kind,
        "user_id": lease.user_id,
        "outcome": outcome,
        "started_at": lease.started_at,
        "finished_at": _now_iso(),
    })
    r = await get_redis()
    released = await r.eval(
        _RELEASE_SCRIPT,
        3,
        run_key(lease.session_id),
        user_index_key(lease.user_id),
        last_run_key(lease.session_id),
        lease.run_id,
        lease.session_id,
        marker,
        int(settings.RUN_LAST_MARKER_TTL_SECONDS),
    )
    _local_leases.discard((lease.session_id, lease.run_id))
    if released:
        logger.info(
            "Released run %s for session %s (outcome=%s)",
            lease.run_id, lease.session_id, outcome,
        )
    else:
        logger.warning(
            "Run %s for session %s was no longer owned at release (lease lost)",
            lease.run_id, lease.session_id,
        )
    return bool(released)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def get_run(session_id: str) -> RunLease | None:
    """Return the live lease for *session_id*, or ``None``."""
    r = await get_redis()
    raw = await r.hgetall(run_key(session_id))
    if not raw:
        return None
    return _lease_from_hash(session_id, raw)


async def get_runs(session_ids: list[str]) -> dict[str, RunLease]:
    """Return the live leases for *session_ids* in one Redis pipeline.

    Used to annotate list/sidebar responses without an N+1 round-trip.
    """
    ids = [sid for sid in dict.fromkeys(session_ids) if sid]
    if not ids:
        return {}
    r = await get_redis()
    pipe = r.pipeline(transaction=False)
    for sid in ids:
        pipe.hgetall(run_key(sid))
    rows = await pipe.execute()
    runs: dict[str, RunLease] = {}
    for sid, raw in zip(ids, rows):
        lease = _lease_from_hash(sid, raw or {})
        if lease is not None:
            runs[sid] = lease
    return runs


async def get_active_runs_for_user(user_id: str) -> list[RunLease]:
    """Return every live lease owned by *user_id*.

    Stale index members (lease expired after a crash) are pruned as they
    are encountered, so the per-user index self-heals.
    """
    r = await get_redis()
    members = await r.smembers(user_index_key(user_id))
    if not members:
        return []
    session_ids = sorted(members)
    pipe = r.pipeline(transaction=False)
    for sid in session_ids:
        pipe.hgetall(run_key(sid))
    rows = await pipe.execute()

    runs: list[RunLease] = []
    stale: list[str] = []
    for sid, raw in zip(session_ids, rows):
        lease = _lease_from_hash(sid, raw or {})
        if lease is None:
            stale.append(sid)
        else:
            runs.append(lease)
    if stale:
        try:
            await r.srem(user_index_key(user_id), *stale)
        except Exception:
            logger.warning(
                "Failed to prune %d stale run index entries for user %s",
                len(stale), user_id,
            )
    return runs


async def get_last_run_marker(session_id: str) -> dict[str, Any] | None:
    """Return the most recent completion marker for *session_id*."""
    r = await get_redis()
    raw = await r.get(last_run_key(session_id))
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def get_run_outcome(session_id: str, expected_run_id: str) -> str:
    """Resolve how a run ended.

    Returns the recorded outcome when the marker belongs to
    *expected_run_id*, otherwise ``"lost"`` (the lease expired without a
    graceful release — e.g. the backend host died).
    """
    marker = await get_last_run_marker(session_id)
    if marker and marker.get("run_id") == expected_run_id:
        return str(marker.get("outcome") or OUTCOME_COMPLETED)
    return OUTCOME_LOST


async def get_run_outcome_for_user(
    session_id: str,
    expected_run_id: str,
    user_id: str,
) -> str | None:
    """Owner-scoped variant of :func:`get_run_outcome`.

    Returns the recorded outcome only when the completion marker belongs
    to this caller and to the run the client already knew about.  ``None``
    means "nothing to report": no marker, another user's session, or a
    marker for a different run (the lease expired without a graceful
    release, or a newer run has already replaced it).
    """
    marker = await get_last_run_marker(session_id)
    if not marker:
        return None
    if str(marker.get("user_id") or "") != str(user_id):
        return None
    if expected_run_id and marker.get("run_id") != expected_run_id:
        return None
    return str(marker.get("outcome") or OUTCOME_COMPLETED)


# ---------------------------------------------------------------------------
# Cancellation (per run, addressed by run_id)
# ---------------------------------------------------------------------------


async def request_run_cancel(
    session_id: str,
    run_id: str | None = None,
) -> bool:
    """Request cancellation of the current (or addressed) run.

    With *run_id*, only that exact run can be cancelled — a stale id is a
    no-op.  Without it, the cancellation flag is set for whatever run is
    active (or for the next runner check, preserving the pre-#604 contract
    used by widget/demo/background-task callers).
    """
    r = await get_redis()
    if run_id:
        run = await get_run(session_id)
        if run is None or run.run_id != run_id:
            logger.info(
                "Ignoring cancel for session %s — stale run_id %s",
                session_id, run_id,
            )
            return False
        value = run.run_id
    else:
        run = await get_run(session_id)
        value = run.run_id if run is not None else "1"

    # Run-scoped marker first: the runner may clear the session flag as
    # soon as it notices, and the run's own finally block needs a durable
    # signal that this termination was a cancellation.  Setting it before
    # the flag closes that race.
    if run is not None:
        await r.setex(
            run_cancel_key(run.run_id),
            int(settings.STREAM_CANCEL_TTL_SECONDS),
            "1",
        )

    await r.eval(
        _CLEAR_CANCEL_SCRIPT,
        1,
        cancel_key(session_id),
        "",
        int(settings.STREAM_CANCEL_TTL_SECONDS),
        value,
    )
    return True


async def is_run_cancel_requested(run_id: str) -> bool:
    """Return True if *run_id* was explicitly cancelled."""
    r = await get_redis()
    return await r.exists(run_cancel_key(run_id)) > 0


async def clear_run_cancel_marker(run_id: str) -> None:
    """Remove the run-scoped cancellation marker."""
    r = await get_redis()
    await r.delete(run_cancel_key(run_id))


async def clear_run_cancel(session_id: str, run_id: str | None = None) -> None:
    """Clear the cancellation flag, optionally only when owned by *run_id*."""
    r = await get_redis()
    await r.eval(
        _DELETE_CANCEL_SCRIPT,
        1,
        cancel_key(session_id),
        run_id or "",
    )


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

#: Leases acquired by this process (for graceful release on shutdown).
_local_leases: set[tuple[str, str]] = set()


async def release_all_local_runs() -> int:
    """Release every lease this process still owns (graceful shutdown).

    Best-effort: a hard kill cannot run this, which is exactly why the
    lease TTL exists.  Returns the number of leases released.
    """
    released = 0
    for session_id, run_id in list(_local_leases):
        lease = await get_run(session_id)
        if lease is None or lease.run_id != run_id:
            _local_leases.discard((session_id, run_id))
            continue
        try:
            if await release_run(lease, outcome=OUTCOME_CANCELLED):
                released += 1
        except Exception:
            logger.warning(
                "Failed to release run %s for session %s on shutdown",
                run_id, session_id,
                exc_info=True,
            )
    return released
