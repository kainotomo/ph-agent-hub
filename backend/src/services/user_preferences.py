# =============================================================================
# PH Agent Hub — User Preferences Service
# =============================================================================
# Per-user settings stored as one JSON blob on ``users``.  The blob is
# always normalised against a typed default set before a caller sees it, so
# a NULL column, a partially written value or an unknown key from an older
# client can never leak to the API.
# =============================================================================

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.orm.users import User

#: Browser notification defaults.  ``enabled`` is opt-in: the browser only
#: allows a permission prompt from a user gesture, so a notification can
#: never be on before the user asked for one.
DEFAULT_NOTIFICATION_PREFS: dict[str, bool] = {
    "enabled": False,
    "on_completion": True,
    "on_failure": True,
    "sound": False,
}


def normalize_notification_prefs(raw: Any) -> dict[str, bool]:
    """Return a complete, typed preference dict for *raw*.

    Unknown keys are dropped and non-boolean values fall back to the
    default, so storing garbage can never produce an unusable setting.
    """
    prefs = dict(DEFAULT_NOTIFICATION_PREFS)
    if isinstance(raw, dict):
        for key in prefs:
            value = raw.get(key)
            if isinstance(value, bool):
                prefs[key] = value
    return prefs


async def get_notification_prefs(
    db: AsyncSession, user_id: str,
) -> dict[str, bool]:
    """Read the preferences of *user_id* (defaults when the column is NULL)."""
    result = await db.execute(
        select(User.notification_prefs).where(User.id == user_id)
    )
    return normalize_notification_prefs(result.scalar_one_or_none())


async def update_notification_prefs(
    db: AsyncSession, user_id: str, prefs: dict[str, Any],
) -> dict[str, bool]:
    """Store the normalised preferences of *user_id* and return them."""
    normalized = normalize_notification_prefs(prefs)
    user = await db.get(User, user_id)
    if user is not None:
        user.notification_prefs = normalized
        await db.commit()
    return normalized
