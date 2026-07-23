# =============================================================================
# PH Agent Hub — Service: Scheduled Tasks
# =============================================================================
#
# CRUD operations for ScheduledTask records plus scheduler-query helpers.
# The scheduler polling loop in main.py calls get_due_tasks() and
# record_run_result() to drive execution.
# =============================================================================

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, func, and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.config import settings
from ..db.orm.scheduled_tasks import ScheduledTask


async def create_scheduled_task(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    goal: str,
    schedule_description: str,
    cron_expression: str,
    timezone: str = "UTC",
    template_session_id: str | None = None,
) -> ScheduledTask:
    """Create a new scheduled task and compute its first ``next_run_at``."""
    from croniter import croniter

    next_run_at = _compute_next_run(cron_expression, timezone)

    task = ScheduledTask(
        tenant_id=tenant_id,
        user_id=user_id,
        goal=goal,
        schedule_description=schedule_description,
        cron_expression=cron_expression,
        timezone=timezone,
        state=ScheduledTask.STATE_ACTIVE,
        next_run_at=next_run_at,
        template_session_id=template_session_id,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def get_scheduled_task(
    db: AsyncSession,
    task_id: str,
) -> ScheduledTask | None:
    """Fetch a single scheduled task by ID."""
    return await db.get(
        ScheduledTask, task_id,
        options=[selectinload(ScheduledTask.last_run_session)],
    )


async def list_scheduled_tasks(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    state: str | None = None,
) -> tuple[list[ScheduledTask], int]:
    """List scheduled tasks for a user, with optional state filter."""
    query = select(ScheduledTask).where(
        ScheduledTask.user_id == user_id,
        ScheduledTask.tenant_id == tenant_id,
        ScheduledTask.state != ScheduledTask.STATE_DELETED,
    )
    count_query = select(func.count()).select_from(ScheduledTask).where(
        ScheduledTask.user_id == user_id,
        ScheduledTask.tenant_id == tenant_id,
        ScheduledTask.state != ScheduledTask.STATE_DELETED,
    )

    if state:
        query = query.where(ScheduledTask.state == state)
        count_query = count_query.where(ScheduledTask.state == state)

    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    result = await db.execute(
        query
        .order_by(
            ScheduledTask.next_run_at.is_(None).asc(),
            ScheduledTask.next_run_at.asc(),
        )
        .offset(offset)
        .limit(limit)
    )
    items = list(result.scalars().all())
    return items, total


async def update_scheduled_task(
    db: AsyncSession,
    task_id: str,
    user_id: str,
    **kwargs,
) -> ScheduledTask:
    """Update a scheduled task's fields. Recomputes ``next_run_at`` if
    ``cron_expression`` or ``timezone`` changed."""
    task = await db.get(ScheduledTask, task_id)
    if task is None or task.user_id != user_id or task.state == ScheduledTask.STATE_DELETED:
        raise ValueError("Scheduled task not found or access denied")

    cron_changed = "cron_expression" in kwargs or "timezone" in kwargs
    for key, value in kwargs.items():
        setattr(task, key, value)

    if cron_changed:
        task.next_run_at = _compute_next_run(
            task.cron_expression, task.timezone,
        )
    await db.commit()
    await db.refresh(task)
    return task


async def delete_scheduled_task(
    db: AsyncSession,
    task_id: str,
    user_id: str,
) -> bool:
    """Soft-delete a scheduled task (state=DELETED)."""
    task = await db.get(ScheduledTask, task_id)
    if task is None or task.user_id != user_id:
        return False
    task.state = ScheduledTask.STATE_DELETED
    task.next_run_at = None
    await db.commit()
    return True


async def pause_scheduled_task(
    db: AsyncSession,
    task_id: str,
    user_id: str,
) -> ScheduledTask:
    """Pause a scheduled task (state=PAUSED, next_run_at cleared)."""
    task = await _get_owned(db, task_id, user_id)
    task.state = ScheduledTask.STATE_PAUSED
    task.next_run_at = None
    await db.commit()
    await db.refresh(task)
    return task


async def resume_scheduled_task(
    db: AsyncSession,
    task_id: str,
    user_id: str,
) -> ScheduledTask:
    """Resume a paused scheduled task (state=ACTIVE, next_run_at recomputed)."""
    task = await _get_owned(db, task_id, user_id)
    task.state = ScheduledTask.STATE_ACTIVE
    task.next_run_at = _compute_next_run(task.cron_expression, task.timezone)
    await db.commit()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# Scheduler helpers
# ---------------------------------------------------------------------------


async def get_due_tasks(db: AsyncSession) -> list[ScheduledTask]:
    """Return all ACTIVE tasks where ``next_run_at <= now()``.

    Used by the scheduler polling loop.  The caller should set
    ``next_run_at = None`` immediately after retrieving to prevent
    double-execution (the dedup guard).
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ScheduledTask)
        .where(
            ScheduledTask.state == ScheduledTask.STATE_ACTIVE,
            ScheduledTask.next_run_at.isnot(None),
            ScheduledTask.next_run_at <= now,
        )
        .order_by(ScheduledTask.next_run_at.asc())
    )
    return list(result.scalars().all())


async def record_run_result(
    db: AsyncSession,
    task_id: str,
    *,
    status: str,
    session_id: str | None = None,
    error: str | None = None,
) -> ScheduledTask:
    """Record the result of a scheduled task execution and compute the next
    ``next_run_at`` using its cron expression."""
    task = await db.get(ScheduledTask, task_id)
    if task is None:
        raise ValueError(f"ScheduledTask {task_id} not found")

    task.last_run_at = datetime.now(timezone.utc)
    task.last_run_status = status
    task.last_run_session_id = session_id
    task.last_run_error = error
    task.run_count = (task.run_count or 0) + 1

    # Only compute next run if the task is still active
    if task.state == ScheduledTask.STATE_ACTIVE:
        try:
            task.next_run_at = _compute_next_run(task.cron_expression, task.timezone)
        except Exception:
            task.next_run_at = None
    else:
        task.next_run_at = None

    await db.commit()
    await db.refresh(task)
    return task


def _compute_next_run(cron_expression: str, timezone_name: str = "UTC") -> datetime | None:
    """Compute the next datetime matching the given cron expression.

    Returns ``None`` if the expression cannot be parsed.
    """
    from croniter import croniter

    try:
        import pytz
        tz = pytz.timezone(timezone_name)
        base = datetime.now(tz)
        cron = croniter(cron_expression, base)
        next_dt = cron.get_next(datetime)
        return next_dt.astimezone(timezone.utc)
    except (ValueError, KeyError, ImportError):
        return None


async def _get_owned(db: AsyncSession, task_id: str, user_id: str) -> ScheduledTask:
    """Fetch a scheduled task and verify ownership."""
    task = await db.get(ScheduledTask, task_id)
    if task is None or task.user_id != user_id or task.state == ScheduledTask.STATE_DELETED:
        raise ValueError("Scheduled task not found or access denied")
    return task
