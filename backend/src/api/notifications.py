# =============================================================================
# PH Agent Hub — Notifications API Router
# =============================================================================
# Endpoints for the notification center (bell icon + dropdown in the
# frontend).  Notifications are created by the system when background
# tasks complete, fail, or are cancelled.
# =============================================================================

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_user, get_db
from ..core.exceptions import NotFoundError
from ..db.orm.users import User as UserORM
from ..models.notification import (
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from ..services.notification_service import (
    get_unread_count,
    list_notifications,
    mark_all_read,
    mark_read,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
async def get_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    unread_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
) -> NotificationListResponse:
    """List notifications for the current user, newest first."""
    offset = (page - 1) * page_size
    items, total = await list_notifications(
        db,
        current_user.id,
        current_user.tenant_id,
        limit=page_size,
        offset=offset,
        unread_only=unread_only,
    )
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in items],
        total=total,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_notification_count(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
) -> UnreadCountResponse:
    """Get the count of unread notifications (for the bell badge)."""
    count = await get_unread_count(db, current_user.id, current_user.tenant_id)
    return UnreadCountResponse(count=count)


@router.post("/{notification_id}/read", response_model=dict)
async def mark_notification_read(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
) -> dict:
    """Mark a single notification as read."""
    success = await mark_read(db, notification_id, current_user.id)
    if not success:
        raise NotFoundError("Notification not found")
    return {"status": "ok"}


@router.post("/read-all", response_model=dict)
async def mark_all_notifications_read(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
) -> dict:
    """Mark all unread notifications as read."""
    count = await mark_all_read(db, current_user.id, current_user.tenant_id)
    return {"status": "ok", "updated": count}
