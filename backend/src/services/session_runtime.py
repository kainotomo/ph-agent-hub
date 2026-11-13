# =============================================================================
# PH Agent Hub — Session runtime view
# =============================================================================
# One composed answer to "what is this conversation doing?".
#
# Three stores hold partial facts with different lifetimes:
#
#   * Redis lease        — is a process working on it *right now*?  (seconds)
#   * `chat_runs` row    — how did the last run end?                (forever)
#   * completion marker  — short-lived cache of that outcome        (90 s)
#
# Before this module every API exposed one of them, and the browser had to
# decide which to believe.  `resolve_runtime()` merges them once, on the
# server, and every endpoint returns the same `revision`-stamped shape.  A
# client then applies "highest revision wins" instead of reconciling flags.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.orm.chat_runs import ChatRun
from .run_registry import get_last_run_markers, get_runs

#: Terminal run states.
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "lost"})


def _as_aware(value: datetime) -> datetime:
    """MariaDB returns naive datetimes; treat them as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def parse_iso(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _as_aware(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _as_aware(parsed)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_aware(value).isoformat().replace("+00:00", "Z")


def revision_of(value: Any, *, terminal: bool = False) -> int:
    """Per-session ordering token: whole seconds, plus a terminal rank.

    MariaDB stores ``DATETIME`` at whole-second precision, so a finish can
    share a second with its start (and would otherwise sort *before* it after
    truncation).  Multiplying seconds by ten and adding one for a terminal
    state keeps the token strictly increasing within a second: ``running`` is
    ``s*10`` and every terminal state is ``s*10+1``.  A new run in the same
    second as the previous finish can lag by at most one poll — acceptable,
    and the chat stream carries the live view meanwhile.
    """
    parsed = parse_iso(value)
    if parsed is None:
        return 0
    return int(parsed.timestamp()) * 10 + (1 if terminal else 0)


@dataclass
class SessionRuntime:
    session_id: str
    state: str
    run_id: str | None = None
    outcome: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    revision: int = 0
    error_code: str | None = None
    message_id: str | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "revision": self.revision,
            "error_code": self.error_code,
            "message_id": self.message_id,
        }


def idle_runtime(session_id: str) -> SessionRuntime:
    return SessionRuntime(session_id=session_id, state="idle")


async def _latest_runs(
    db: AsyncSession,
    session_ids: Iterable[str],
    *,
    user_id: str | None = None,
) -> dict[str, ChatRun]:
    """Return the newest `ChatRun` row per session (one window query)."""
    ids = [sid for sid in dict.fromkeys(session_ids) if sid]
    if not ids:
        return {}

    ranked = (
        select(
            ChatRun.id.label("id"),
            func.row_number()
            .over(
                partition_by=ChatRun.session_id,
                order_by=ChatRun.started_at.desc(),
            )
            .label("rank"),
        )
        .where(ChatRun.session_id.in_(ids))
        .subquery()
    )
    query = select(ChatRun).join(ranked, ChatRun.id == ranked.c.id).where(
        ranked.c.rank == 1,
    )
    if user_id:
        query = query.where(ChatRun.user_id == user_id)
    rows = (await db.execute(query)).scalars().all()
    return {row.session_id: row for row in rows}


def _from_lease(lease: Any) -> SessionRuntime:
    started = lease.started_at
    return SessionRuntime(
        session_id=lease.session_id,
        state="running",
        run_id=lease.run_id,
        started_at=started,
        revision=revision_of(started),
    )


def _from_row(row: ChatRun) -> SessionRuntime:
    started = iso(row.started_at)
    finished = iso(row.finished_at)
    if row.outcome in TERMINAL_STATES:
        state = str(row.outcome)
    elif row.finished_at is None:
        # No lease reached this function's caller and the row never got a
        # terminal state: the process that owned it is gone.
        state = "lost"
    else:
        state = "unknown"
    return SessionRuntime(
        session_id=row.session_id,
        state=state,
        run_id=row.id,
        outcome=state if state in TERMINAL_STATES else None,
        started_at=started,
        finished_at=finished,
        revision=revision_of(finished or started, terminal=True),
        error_code=row.error_code,
        message_id=row.message_id,
    )


def _from_marker(session_id: str, marker: dict[str, Any]) -> SessionRuntime:
    outcome = str(marker.get("outcome") or "completed")
    started = marker.get("started_at")
    finished = marker.get("finished_at")
    return SessionRuntime(
        session_id=session_id,
        state=outcome if outcome in TERMINAL_STATES else "completed",
        run_id=marker.get("run_id"),
        outcome=outcome,
        started_at=started,
        finished_at=finished,
        revision=revision_of(finished or started, terminal=True),
    )


async def resolve_runtime(
    db: AsyncSession,
    session_ids: Iterable[str],
    *,
    user_id: str | None = None,
) -> dict[str, SessionRuntime]:
    """Compose the authoritative runtime view for *session_ids*.

    Precedence, highest first:

    1. a live lease → ``running`` (liveness cannot be inferred from a row);
    2. the newest ``chat_runs`` row → its terminal outcome, or ``lost`` when
       the row is unfinished and no lease exists;
    3. the completion marker when no durable row is available (the finalize
       write is best-effort, the marker is the fallback record);
    4. ``idle``.

    The result is keyed by session id, one entry per requested session that
    exists in at least one store.
    """
    ids = [sid for sid in dict.fromkeys(session_ids) if sid]
    if not ids:
        return {}

    leases = await get_runs(ids)
    rows = await _latest_runs(db, ids, user_id=user_id)
    markers: dict[str, dict[str, Any]] = {}
    # Only sessions without a durable row or with an unfinished one need the
    # marker fallback; skip the MGET for everything else.
    needs_marker = [
        sid
        for sid in ids
        if sid not in leases
        and (sid not in rows or rows[sid].finished_at is None)
    ]
    if needs_marker:
        markers = await get_last_run_markers(needs_marker)

    view: dict[str, SessionRuntime] = {}
    for sid in ids:
        lease = leases.get(sid)
        if lease is not None:
            view[sid] = _from_lease(lease)
            continue

        row = rows.get(sid)
        marker = markers.get(sid)

        # A marker written by the run's own release is more specific than an
        # unfinished row: it proves the process reached a terminal state even
        # if the durable write failed.  Owner-scoped callers must not see
        # another user's marker.
        if marker is not None and user_id is not None:
            marker_owner = str(marker.get("user_id") or "")
            if marker_owner and marker_owner != str(user_id):
                marker = None

        if marker is not None and (row is None or row.finished_at is None):
            if row is None or marker.get("run_id") == row.id:
                view[sid] = _from_marker(sid, marker)
                continue

        if row is not None:
            view[sid] = _from_row(row)
            continue

        view[sid] = idle_runtime(sid)

    return view


async def resolve_one(
    db: AsyncSession,
    session_id: str,
    *,
    user_id: str | None = None,
) -> SessionRuntime:
    """Composed view for a single session."""
    view = await resolve_runtime(db, [session_id], user_id=user_id)
    return view.get(session_id) or idle_runtime(session_id)
