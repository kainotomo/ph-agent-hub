# =============================================================================
# PH Agent Hub — Background Tasks API Router
# =============================================================================
# Endpoints for listing, viewing, and cancelling background tasks.
#
# "Starting" a background task is handled by ``POST /chat/session/{id}/message``
# with ``body.background=True`` (see ``chat.py`` for the send-message entry
# point).  These endpoints provide the post-hoc query/cancel surface.
# =============================================================================

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_user, get_db
from ..core.exceptions import NotFoundError, ValidationError
from ..core.redis import set_stream_cancel
from ..db.orm.users import User as UserORM
from ..models.background_task import (
    BackgroundTaskCancelResponse,
    BackgroundTaskListResponse,
    BackgroundTaskResponse,
)
from ..services.autopilot_service import (
    list_background_tasks,
    set_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/background-tasks", tags=["background-tasks"])


@router.get("", response_model=BackgroundTaskListResponse)
async def get_background_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
) -> BackgroundTaskListResponse:
    """List the current user's background tasks, newest first."""
    offset = (page - 1) * page_size
    items, total = await list_background_tasks(
        db,
        current_user.id,
        limit=page_size,
        offset=offset,
        state=state,
    )
    return BackgroundTaskListResponse(
        items=[BackgroundTaskResponse.model_validate(t) for t in items],
        total=total,
    )


@router.get("/{task_id}", response_model=BackgroundTaskResponse)
async def get_background_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
) -> BackgroundTaskResponse:
    """Get details of a single background task."""
    from ..services.autopilot_service import get_run as _get_run

    run = await _get_run(db, task_id)
    if run is None:
        raise NotFoundError("Background task not found")

    # Verify ownership via the session's user_id
    from ..db.orm.sessions import Session as SessionORM

    session = await db.get(SessionORM, run.session_id)
    if session is None or session.user_id != current_user.id:
        raise NotFoundError("Background task not found")

    return BackgroundTaskResponse.model_validate(run)


@router.delete("/{task_id}", response_model=BackgroundTaskCancelResponse)
async def cancel_background_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
) -> BackgroundTaskCancelResponse:
    """Cancel a running background task.

    Uses the same Redis ``stream:cancel:`` mechanism as the streaming
    SSE endpoint.  The agent runner checks this flag between turns and
    stops execution.
    """
    from ..services.autopilot_service import get_run as _get_run
    from ..db.orm.sessions import Session as SessionORM

    run = await _get_run(db, task_id)
    if run is None:
        raise NotFoundError("Background task not found")

    # Verify ownership
    session = await db.get(SessionORM, run.session_id)
    if session is None or session.user_id != current_user.id:
        raise NotFoundError("Background task not found")

    if run.state != run.STATE_EXECUTING:
        raise ValidationError(
            f"Task is in state '{run.state}' — only EXECUTING tasks can be cancelled"
        )

    # Set the Redis cancellation flag for the session (best-effort)
    try:
        await set_stream_cancel(run.session_id)
    except Exception:
        logger.warning(
            "Failed to set Redis cancel flag for session %s — "
            "agent may not notice immediately",
            run.session_id,
        )

    # Also update the DB state immediately for responsive UI feedback
    await set_state(db, task_id, run.STATE_CANCELLED)

    logger.info(
        "User %s cancelled background task %s (session %s)",
        current_user.id, task_id, run.session_id,
    )

    return BackgroundTaskCancelResponse(task_id=task_id)
