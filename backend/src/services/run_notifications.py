# =============================================================================
# PH Agent Hub — Run Outcome Notifications (Issue #619)
# =============================================================================
# Durable notification records for runs that ended while nobody was
# watching.  A browser notification needs a live page, so this is the
# catch-up path: the bell polls these records, and the client-side channel
# covers the tab that is still open.
#
# Policy, in order:
#   * the user's master switch must be on (``notification_prefs.enabled``)
#   * failed    → when ``on_failure`` is on
#   * completed → only when ``on_completion`` is on, no subscriber was
#                 attached, and the run lasted at least
#                 NOTIFY_MIN_COMPLETED_SECONDS
#   * cancelled / lost → never (user-initiated or unclean shutdown)
#
# Records point at the *session* (``reference_type="session"``) so the bell
# can navigate to it.  Duplicates are detected by comparing the newest
# record of the same type and session with the run start time.
#
# Every entry point swallows its own failures: a notification problem must
# never change how a run ends.
# =============================================================================

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.orm.notifications import Notification
from ..db.orm.sessions import Session as SessionORM
from .notification_service import create_notification
from .user_preferences import get_notification_prefs

logger = logging.getLogger(__name__)

_TITLES = {
    Notification.TYPE_CHAT_RUN_FAILED: "Run failed",
    Notification.TYPE_CHAT_RUN_COMPLETED: "Response ready",
}

_BODIES = {
    Notification.TYPE_CHAT_RUN_FAILED: "“{title}” ended with an error.",
    Notification.TYPE_CHAT_RUN_COMPLETED: "“{title}” finished while you were away.",
}

#: Which user preference gates each record type.
_PREF_BY_TYPE = {
    Notification.TYPE_CHAT_RUN_FAILED: "on_failure",
    Notification.TYPE_CHAT_RUN_COMPLETED: "on_completion",
}


def should_record_outcome(
    outcome: str, *, duration_s: float, attached: bool,
) -> str | None:
    """Return the notification type for a finished run, or ``None``."""
    if outcome == "failed":
        return Notification.TYPE_CHAT_RUN_FAILED
    if (
        outcome == "completed"
        and not attached
        and duration_s >= float(settings.NOTIFY_MIN_COMPLETED_SECONDS)
    ):
        return Notification.TYPE_CHAT_RUN_COMPLETED
    return None


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def _session_title(db: AsyncSession, session_id: str) -> str:
    result = await db.execute(
        select(SessionORM.title).where(SessionORM.id == session_id)
    )
    title = result.scalar_one_or_none()
    return str(title) if title else "New Chat"


async def _already_recorded(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: str,
    type_: str,
    started_at: datetime | None,
) -> bool:
    """True when a record of *type_* for this session was created after
    *started_at* (i.e. this run already produced one)."""
    if started_at is None:
        return False
    result = await db.execute(
        select(Notification.created_at)
        .where(
            Notification.user_id == user_id,
            Notification.reference_id == session_id,
            Notification.type == type_,
        )
        .order_by(Notification.created_at.desc())
        .limit(1)
    )
    last = result.scalar_one_or_none()
    if last is None:
        return False
    return _as_aware(last) >= _as_aware(started_at)


async def _record(
    db: AsyncSession,
    *,
    type_: str,
    session_id: str,
    user_id: str,
    tenant_id: str,
    started_at: datetime | None,
) -> Notification | None:
    if await _already_recorded(
        db,
        user_id=user_id,
        session_id=session_id,
        type_=type_,
        started_at=started_at,
    ):
        return None
    title = await _session_title(db, session_id)
    return await create_notification(
        db,
        user_id=user_id,
        tenant_id=tenant_id,
        type=type_,
        title=_TITLES[type_],
        body=_BODIES[type_].format(title=title),
        reference_id=session_id,
        reference_type="session",
    )


async def record_run_outcome(
    db: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    tenant_id: str,
    outcome: str,
    duration_s: float,
    attached: bool,
    started_at: datetime | None = None,
) -> Notification | None:
    """Persist a notification for a finished run when the policy says so."""
    type_ = should_record_outcome(
        outcome, duration_s=duration_s, attached=attached
    )
    if type_ is None:
        return None

    prefs = await get_notification_prefs(db, user_id)
    if not prefs["enabled"] or not prefs[_PREF_BY_TYPE[type_]]:
        return None

    return await _record(
        db,
        type_=type_,
        session_id=session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        started_at=started_at,
    )


async def record_run_outcome_safe(**kwargs) -> None:
    """``record_run_outcome`` with its own DB session; never raises."""
    from ..db.base import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await record_run_outcome(db, **kwargs)
    except Exception:
        logger.warning(
            "Failed to record run notification (session=%s)",
            kwargs.get("session_id"),
            exc_info=True,
        )
