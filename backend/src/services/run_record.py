# =============================================================================
# PH Agent Hub — Durable chat run record (Issue #608)
# =============================================================================
# Single writer for the `chat_runs` table plus the startup orphan sweep.
#
# Contract:
#   * `start_run` inserts the row before the agent stream is consumed.
#   * `finalize_run` writes exactly one terminal state.  `completed` is
#     downgraded to `failed` when no assistant message was persisted — this
#     is the production bug from #607, where `completed` only meant "no
#     exception escaped".
#   * `mark_orphan_runs_lost` is the missing `lost` path: rows left without
#     `finished_at` by a killed process become `lost` on the next startup.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.orm.chat_runs import ChatRun
from ..db.orm.messages import Message
from .run_registry import (
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_LOST,
    get_run,
)

logger = logging.getLogger(__name__)

#: Bounds so a provider message cannot bloat the row.
ERROR_MESSAGE_MAX_CHARS = 1000
ERROR_CODE_MAX_CHARS = 64

#: `outcome` values accepted by the table.
TERMINAL_OUTCOMES = {"completed", "failed", "cancelled", "lost"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """MariaDB returns naive datetimes; treat them as UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    aware = _as_aware(value)
    return aware.isoformat().replace("+00:00", "Z") if aware else None


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO timestamp from a run snapshot (naive values are UTC)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _as_aware(parsed)


def row_is_recently_alive(
    row: ChatRun,
    now: datetime | None = None,
) -> bool:
    """True when an unfinished row carries a fresh durable heartbeat.

    Issue #632 — a missing Redis lease is not proof that the run died: an
    event-loop stall (blocking tool call) or a Redis restart can drop the
    lease while the process keeps working.  The heartbeat task writes
    ``last_heartbeat_at`` every few seconds, so the row itself is the
    independent proof of life used to avoid declaring a live run ``lost``.
    Rows written before the column existed fall back to ``started_at``.
    """
    if row.finished_at is not None:
        return False
    last = _as_aware(row.last_heartbeat_at) or _as_aware(row.started_at)
    if last is None:
        return False
    current = now or _utcnow()
    return (current - last).total_seconds() <= max(
        int(settings.RUN_LOST_GRACE_SECONDS), 1,
    )


def _state_is_recently_alive(state: dict[str, Any], now: datetime) -> bool:
    """Same rule as :func:`row_is_recently_alive` for a snapshot dict."""
    last = _parse_iso(state.get("last_heartbeat_at")) or _parse_iso(
        state.get("started_at")
    )
    if last is None:
        return False
    return (now - last).total_seconds() <= max(
        int(settings.RUN_LOST_GRACE_SECONDS), 1,
    )


def _lease_owns(lease: Any, state: dict[str, Any]) -> bool:
    """Whether *lease* is the run described by *state*."""
    return (
        lease is not None
        and getattr(lease, "run_id", None) == state.get("run_id")
    )


async def _lease_snapshot(session_id: str) -> tuple[bool, Any]:
    """Read the session's lease, distinguishing "absent" from "unavailable".

    Returns ``(ok, lease)``.  ``ok=False`` means the registry could not be
    read at all — callers must not fabricate a terminal state from that.
    """
    try:
        return True, await get_run(session_id)
    except Exception:
        logger.warning(
            "Could not read the run lease for session %s", session_id,
            exc_info=True,
        )
        return False, None


@asynccontextmanager
async def _session_scope(db: AsyncSession | None) -> AsyncIterator[AsyncSession]:
    """Use the caller's session when given (tests), otherwise a fresh one.

    The background task must not share its agent session with bookkeeping:
    an open agent transaction would be committed or rolled back by the
    record write.
    """
    if db is not None:
        yield db
        return
    from ..db.base import AsyncSessionLocal

    async with AsyncSessionLocal() as own_db:
        yield own_db


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def start_run(
    *,
    run_id: str,
    session_id: str,
    user_id: str,
    tenant_id: str,
    kind: str = "chat",
    started_at: datetime | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Insert the running row for *run_id* (idempotent)."""
    async with _session_scope(db) as session:
        existing = await session.get(ChatRun, run_id)
        if existing is None:
            started = started_at or _utcnow()
            session.add(
                ChatRun(
                    id=run_id,
                    session_id=session_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    kind=kind or "chat",
                    started_at=started,
                    # Issue #632 — the durable proof of life starts at the
                    # same instant as the row, so a crash before the first
                    # heartbeat still has a grace window instead of an
                    # immediate `lost`.
                    last_heartbeat_at=started,
                    finished_at=None,
                    outcome=None,
                    persisted=False,
                    client_attached=False,
                    last_event_seq=0,
                    tokens_in=0,
                    tokens_out=0,
                )
            )
        await session.commit()


async def touch_run_heartbeat(
    run_id: str,
    *,
    now: datetime | None = None,
    db: AsyncSession | None = None,
) -> bool:
    """Refresh the durable proof of life for *run_id*.

    Only an unfinished row is updated: a run that already reported a
    terminal state must never look alive again.  Returns ``True`` when a row
    was updated (``False`` before ``start_run`` has written the row, or after
    it was finalized).
    """
    stamp = now or _utcnow()
    async with _session_scope(db) as session:
        result = await session.execute(
            update(ChatRun)
            .where(ChatRun.id == run_id, ChatRun.finished_at.is_(None))
            .values(last_heartbeat_at=stamp)
        )
        await session.commit()
        return bool(getattr(result, "rowcount", 0))


async def finalize_run(
    *,
    run_id: str,
    session_id: str,
    outcome: str,
    error_code: str | None = None,
    error_message: str | None = None,
    message_id: str | None = None,
    persisted: bool = False,
    client_attached: bool = False,
    last_event_seq: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    """Write the terminal state for *run_id* and return the stored snapshot.

    A missing row is created (the terminal state is never lost because the
    start write failed).  `completed` without a persisted answer is
    downgraded to `failed` / `not_persisted`.
    """
    outcome = outcome if outcome in TERMINAL_OUTCOMES else OUTCOME_FAILED
    persisted = bool(persisted)
    if outcome == OUTCOME_COMPLETED and not persisted:
        outcome = OUTCOME_FAILED
        error_code = error_code or "not_persisted"
        error_message = error_message or (
            "The run completed without persisting an assistant message."
        )

    async with _session_scope(db) as session:
        # The answer row must exist before `persisted=True` is recorded: the
        # FK rejects a dangling id, and a missing answer is a failed run, not
        # a run that lost its terminal state.
        if persisted and message_id:
            exists = (
                await session.execute(
                    select(Message.id).where(
                        Message.id == message_id,
                        Message.session_id == session_id,
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                message_id = None
                persisted = False
                if outcome == OUTCOME_COMPLETED:
                    outcome = OUTCOME_FAILED
                    error_code = error_code or "not_persisted"
                    error_message = error_message or (
                        "The run completed without persisting an assistant "
                        "message."
                    )

        row = await session.get(ChatRun, run_id)
        if row is None:
            row = ChatRun(
                id=run_id,
                session_id=session_id,
                user_id="",
                tenant_id="",
                kind="chat",
                started_at=_utcnow(),
            )
            session.add(row)

        row.outcome = outcome
        row.finished_at = _utcnow()
        row.error_code = (error_code or None)
        if row.error_code:
            row.error_code = row.error_code[:ERROR_CODE_MAX_CHARS]
        row.error_message = (
            error_message[:ERROR_MESSAGE_MAX_CHARS] if error_message else None
        )
        row.message_id = message_id if persisted else None
        row.persisted = persisted
        row.client_attached = bool(client_attached)
        row.last_event_seq = int(last_event_seq or 0)
        row.tokens_in = int(tokens_in or 0)
        row.tokens_out = int(tokens_out or 0)

        await session.commit()
        await session.refresh(row)
        snapshot = _row_to_dict(row)

    logger.info(
        "PH-RUN-RECORD run_id=%s session_id=%s outcome=%s persisted=%s "
        "message_id=%s client_attached=%s last_event_seq=%s",
        run_id, session_id, outcome, persisted, snapshot.get("message_id"),
        snapshot.get("client_attached"), snapshot.get("last_event_seq"),
    )
    return snapshot


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _row_to_dict(row: ChatRun) -> dict[str, Any]:
    return {
        "run_id": row.id,
        "session_id": row.session_id,
        "state": row.outcome
        or ("running" if row.finished_at is None else "unknown"),
        "started_at": _iso(row.started_at),
        "last_heartbeat_at": _iso(row.last_heartbeat_at),
        "finished_at": _iso(row.finished_at),
        "error_code": row.error_code,
        "error_message": row.error_message,
        "message_id": row.message_id,
        "persisted": bool(row.persisted),
        "client_attached": bool(row.client_attached),
        "last_event_seq": int(row.last_event_seq or 0),
        "tokens_in": int(row.tokens_in or 0),
        "tokens_out": int(row.tokens_out or 0),
        "kind": row.kind,
    }


async def get_run_state(
    db: AsyncSession,
    session_id: str,
) -> dict[str, Any] | None:
    """Return the most recent run record for *session_id*, or ``None``."""
    result = await db.execute(
        select(ChatRun)
        .where(ChatRun.session_id == session_id)
        .order_by(ChatRun.started_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return _row_to_dict(row) if row is not None else None


async def repair_unfinished_run(
    db: AsyncSession,
    session_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the latest run, resolving a stale ``running`` row on read.

    Liveness has two independent sources (Issue #632): the expiry of the
    Redis lease and the run's durable ``last_heartbeat_at``.  A row is only
    resolved to ``lost`` when *both* are stale for ``RUN_LOST_GRACE_SECONDS``
    — otherwise a transient lease blip (event-loop stall in a blocking tool
    call, Redis restart) would mark a live run lost and show the user
    "The run's lease expired before a terminal state was recorded."

    Before writing the terminal state the lease and the row are re-read after
    ``RUN_LOST_CONFIRM_SECONDS`` so a heartbeat task resuming from the same
    stall can re-acquire the lease first.
    """
    state = await get_run_state(db, session_id)
    if state is None or state.get("state") != "running":
        return state

    current = now or _utcnow()
    ok, lease = await _lease_snapshot(session_id)
    if not ok:
        # Registry unavailable — do not fabricate a terminal state.
        return state
    if _lease_owns(lease, state) or _state_is_recently_alive(state, current):
        return state

    # Confirm before recording a terminal state: after a stall the heartbeat
    # may be about to re-acquire the lease, and a read must never win that race.
    delay = max(0.0, float(settings.RUN_LOST_CONFIRM_SECONDS))
    if delay:
        await asyncio.sleep(delay)
    ok, lease = await _lease_snapshot(session_id)
    if not ok:
        return state
    fresh = await get_run_state(db, session_id)
    if fresh is None or fresh.get("state") != "running":
        return fresh
    if _lease_owns(lease, fresh) or _state_is_recently_alive(fresh, _utcnow()):
        return fresh

    await finalize_run(
        run_id=fresh["run_id"],
        session_id=session_id,
        outcome=OUTCOME_LOST,
        error_code="orphaned",
        error_message=(
            "The run's lease expired before a terminal state was recorded."
        ),
        persisted=bool(fresh.get("persisted")),
        client_attached=bool(fresh.get("client_attached")),
        last_event_seq=int(fresh.get("last_event_seq") or 0),
        tokens_in=int(fresh.get("tokens_in") or 0),
        tokens_out=int(fresh.get("tokens_out") or 0),
        db=db,
    )
    return await get_run_state(db, session_id)


async def get_outcome_from_db(
    db: AsyncSession,
    *,
    session_id: str,
    run_id: str | None,
    user_id: str,
) -> str | None:
    """Return the recorded outcome of *run_id*, owner-scoped.

    Used by the status snapshot when the short-lived Redis completion marker
    has expired: the durable row still knows how the run ended, and
    answering "lost" while the database says ``completed`` is wrong.
    """
    if not run_id:
        return None
    result = await db.execute(
        select(ChatRun).where(
            ChatRun.id == run_id,
            ChatRun.session_id == session_id,
            ChatRun.user_id == user_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None or row.finished_at is None or not row.outcome:
        return None
    return str(row.outcome)


# ---------------------------------------------------------------------------
# Orphan sweep (the `lost` path)
# ---------------------------------------------------------------------------


def _lease_is_live(lease: Any, run_id: str, now: datetime) -> bool:
    """A row is still owned when its lease exists, matches and heartbeats."""
    if lease is None or getattr(lease, "run_id", None) != run_id:
        return False
    raw = getattr(lease, "heartbeat_at", None)
    if not raw:
        return False
    try:
        heartbeat = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return False
    heartbeat = _as_aware(heartbeat) or now
    from ..core.config import settings

    age = (now - heartbeat).total_seconds()
    # A live process refreshes the heartbeat every few seconds; a killed one
    # stops immediately.  Allow one full lease TTL of slack so a slow tick is
    # not mistaken for a dead run.
    return age <= max(int(settings.RUN_LEASE_TTL_SECONDS), 1)


async def mark_orphan_runs_lost(
    db: AsyncSession | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Mark unfinished run rows whose lease is gone/expired as ``lost``.

    Called once on startup and periodically by every replica.  Issue #632:
    a row is only dead when its lease is gone **and** its durable heartbeat
    is stale.  Rows owned by a live replica (matching run id and fresh
    heartbeat) and rows whose heartbeat is still fresh are left alone.
    """
    now = now or _utcnow()
    marked = 0

    async with _session_scope(db) as session:
        result = await session.execute(
            select(ChatRun).where(ChatRun.finished_at.is_(None))
        )
        rows = list(result.scalars().all())
        for row in rows:
            try:
                lease = await get_run(row.session_id)
            except Exception:
                logger.warning(
                    "Could not read lease while sweeping run %s", row.id,
                    exc_info=True,
                )
                # Without the registry we cannot prove the run is dead; the
                # next sweep will retry rather than fabricating a `lost`.
                continue
            if _lease_is_live(lease, row.id, now):
                continue
            if row_is_recently_alive(row, now):
                # The lease key blipped but the owning process is still
                # heartbeating: do not declare a live run dead.
                logger.info(
                    "PH-RUN-SWEEP keeping run %s for session %s — lease "
                    "absent but durable heartbeat is fresh",
                    row.id, row.session_id,
                )
                continue

            row.outcome = OUTCOME_LOST
            row.finished_at = now
            row.error_code = "orphaned"
            row.error_message = (
                "The backend process ended before this run reported a "
                "terminal state."
            )
            row.persisted = False
            marked += 1

        if marked:
            await session.commit()

    if marked:
        logger.warning(
            "PH-RUN-SWEEP marked %d orphaned run(s) as lost", marked,
        )
    return marked
