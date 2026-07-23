# =============================================================================
# PH Agent Hub — Notification Service
# =============================================================================
# CRUD for Notification records.  Used by the autopilot controller to
# create notifications when background tasks complete/fail, and by the
# API endpoints for the frontend notification center.
# =============================================================================

from __future__ import annotations

import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.orm.notifications import Notification

logger = logging.getLogger(__name__)


async def create_notification(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    type: str,
    title: str,
    body: str | None = None,
    reference_id: str | None = None,
    reference_type: str | None = None,
) -> Notification:
    """Create a new notification record."""
    notification = Notification(
        user_id=user_id,
        tenant_id=tenant_id,
        type=type,
        title=title,
        body=body,
        reference_id=reference_id,
        reference_type=reference_type,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    logger.info(
        "Created notification %s (type=%s) for user %s",
        notification.id, type, user_id,
    )
    return notification


async def get_notification(
    db: AsyncSession,
    notification_id: str,
) -> Notification | None:
    """Fetch a single notification by ID."""
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    return result.scalar_one_or_none()


async def list_notifications(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    unread_only: bool = False,
) -> tuple[list[Notification], int]:
    """List notifications for a user, newest first.

    Returns ``(items, total_count)``.
    """
    query = select(Notification).where(
        Notification.user_id == user_id,
        Notification.tenant_id == tenant_id,
    )
    count_query = select(func.count(Notification.id)).where(
        Notification.user_id == user_id,
        Notification.tenant_id == tenant_id,
    )

    if unread_only:
        query = query.where(Notification.is_read == False)  # noqa: E712
        count_query = count_query.where(Notification.is_read == False)  # noqa: E712

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Fetch paginated results, newest first
    result = await db.execute(
        query.order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = list(result.scalars().all())
    return items, total


async def get_unread_count(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
) -> int:
    """Get the number of unread notifications for a user."""
    result = await db.execute(
        select(func.count(Notification.id))
        .where(
            Notification.user_id == user_id,
            Notification.tenant_id == tenant_id,
            Notification.is_read == False,  # noqa: E712
        )
    )
    return result.scalar() or 0


async def mark_read(
    db: AsyncSession,
    notification_id: str,
    user_id: str,
) -> bool:
    """Mark a single notification as read. Returns True if found and updated."""
    result = await db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        .values(is_read=True)
    )
    await db.commit()
    return result.rowcount > 0


async def mark_all_read(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
) -> int:
    """Mark all unread notifications as read. Returns count updated."""
    result = await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.tenant_id == tenant_id,
            Notification.is_read == False,  # noqa: E712
        )
        .values(is_read=True)
    )
    await db.commit()
    return result.rowcount


async def delete_notification(
    db: AsyncSession,
    notification_id: str,
    user_id: str,
) -> bool:
    """Delete a single notification. Returns True if found and deleted."""
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
    )
    notification = result.scalar_one_or_none()
    if notification is None:
        return False
    await db.delete(notification)
    await db.commit()
    return True
