# =============================================================================
# PH Agent Hub — API: Scheduled Tasks
# =============================================================================
#
# REST endpoints for managing scheduled tasks (Issue #297).
# Follows the same patterns as background_tasks.py and notifications.py.
# =============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..core.dependencies import get_current_user, get_db
from ..db.orm.users import User as UserORM
from ..models.scheduled_task import (
    ScheduledTaskCreate,
    ScheduledTaskListResponse,
    ScheduledTaskResponse,
    ScheduledTaskUpdate,
)
from ..services.scheduled_task_service import (
    create_scheduled_task,
    delete_scheduled_task,
    get_scheduled_task,
    list_scheduled_tasks,
    pause_scheduled_task,
    resume_scheduled_task,
    update_scheduled_task,
)
from ..core.exceptions import NotFoundError, ValidationError

router = APIRouter(prefix="/scheduled-tasks", tags=["scheduled-tasks"])


@router.get("", response_model=ScheduledTaskListResponse)
async def get_scheduled_tasks(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    state: str | None = Query(None, description="Filter by state (ACTIVE, PAUSED)"),
    db=Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List scheduled tasks for the current user."""
    offset = (page - 1) * page_size
    items, total = await list_scheduled_tasks(
        db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        limit=page_size,
        offset=offset,
        state=state,
    )
    return ScheduledTaskListResponse(
        items=[ScheduledTaskResponse.model_validate(t) for t in items],
        total=total,
    )


@router.get("/{task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task_detail(
    task_id: str,
    db=Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Get a single scheduled task by ID."""
    task = await get_scheduled_task(db, task_id)
    if task is None or task.user_id != current_user.id:
        raise NotFoundError("Scheduled task not found")
    return ScheduledTaskResponse.model_validate(task)


@router.post("", response_model=ScheduledTaskResponse, status_code=201)
async def create_scheduled_task_endpoint(
    body: ScheduledTaskCreate,
    db=Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Create a new scheduled task."""
    # Validate cron expression
    from croniter import croniter
    try:
        croniter(body.cron_expression)
    except (ValueError, KeyError) as exc:
        raise ValidationError(f"Invalid cron expression: {exc}")

    task = await create_scheduled_task(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        goal=body.goal,
        schedule_description=body.schedule_description,
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        template_session_id=body.template_session_id,
    )
    return ScheduledTaskResponse.model_validate(task)


@router.patch("/{task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task_endpoint(
    task_id: str,
    body: ScheduledTaskUpdate,
    db=Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Update an existing scheduled task."""
    # Validate cron if provided
    if body.cron_expression:
        from croniter import croniter
        try:
            croniter(body.cron_expression)
        except (ValueError, KeyError) as exc:
            raise ValidationError(f"Invalid cron expression: {exc}")

    kwargs = body.model_dump(exclude_unset=True)
    if not kwargs:
        raise ValidationError("No fields to update")

    try:
        task = await update_scheduled_task(
            db, task_id, current_user.id, **kwargs,
        )
    except ValueError as exc:
        raise NotFoundError(str(exc))
    return ScheduledTaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=204)
async def delete_scheduled_task_endpoint(
    task_id: str,
    db=Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Soft-delete a scheduled task."""
    deleted = await delete_scheduled_task(db, task_id, current_user.id)
    if not deleted:
        raise NotFoundError("Scheduled task not found")


@router.post("/{task_id}/pause", response_model=ScheduledTaskResponse)
async def pause_scheduled_task_endpoint(
    task_id: str,
    db=Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Pause a scheduled task."""
    try:
        task = await pause_scheduled_task(db, task_id, current_user.id)
    except ValueError as exc:
        raise NotFoundError(str(exc))
    return ScheduledTaskResponse.model_validate(task)


@router.post("/{task_id}/resume", response_model=ScheduledTaskResponse)
async def resume_scheduled_task_endpoint(
    task_id: str,
    db=Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Resume a paused scheduled task."""
    try:
        task = await resume_scheduled_task(db, task_id, current_user.id)
    except ValueError as exc:
        raise NotFoundError(str(exc))
    return ScheduledTaskResponse.model_validate(task)
