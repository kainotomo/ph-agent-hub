# =============================================================================
# PH Agent Hub — Tool: Confirm Schedule
# =============================================================================
#
# Called by the agent (during autopilot) after the user confirms a
# proposed schedule.  Creates the ScheduledTask record in the database.
#
# Requires ``session_data`` to be in ``ctx.kwargs`` (passed via
# ``function_invocation_kwargs`` from the autopilot controller).
# =============================================================================

from __future__ import annotations

import logging

from agent_framework import tool

logger = logging.getLogger(__name__)


@tool
async def confirm_schedule(
    cron_expression: str,
    goal: str,
    schedule_description: str,
    timezone: str = "UTC",
) -> dict:
    """Confirm and create a scheduled agent task.

    Call this tool when the user explicitly confirms that they want the
    schedule set up.  Creates the schedule in the database so the
    scheduler loop will execute it at the specified times.

    Args:
        cron_expression: Standard cron expression for the schedule.
            Examples: "0 20 * * 5" (weekly Friday), "0 8 * * 1-5" (weekdays).
        goal: The agent goal to execute on this schedule. Be specific
            about what the agent should do.
        schedule_description: Human-readable description (e.g. "Every
            Friday at 8pm", "Every weekday at 9am").
        timezone: IANA timezone name (default "UTC").

    Returns:
        Dict with:
            - "ok": bool
            - "schedule_id": str (if created)
            - "next_run_at": ISO datetime string
            - "message": confirmation message
    """
    # We need the function invocation context to get the user/session info
    from agent_framework import FunctionInvocationContext

    # Try to get ctx from caller — the MAF framework injects it
    # automatically when the tool has a ctx parameter.
    ctx = None
    import inspect
    for frame_info in inspect.stack():
        local_ctx = frame_info.frame.f_locals.get("ctx")
        if local_ctx is not None and hasattr(local_ctx, "kwargs"):
            ctx = local_ctx
            break

    if ctx is None:
        logger.warning(
            "confirm_schedule called without FunctionInvocationContext — cannot create schedule"
        )
        return {
            "ok": False,
            "schedule_id": None,
            "next_run_at": None,
            "message": "Could not create schedule (missing context). "
                       "Please create it from the Scheduled Tasks page.",
        }

    session_data = ctx.kwargs.get("session_data", {})
    user_id = session_data.get("user_id")
    tenant_id = session_data.get("tenant_id")

    if not user_id or not tenant_id:
        logger.warning(
            "confirm_schedule called without user_id/tenant_id in session_data"
        )
        return {
            "ok": False,
            "schedule_id": None,
            "next_run_at": None,
            "message": "Could not create schedule (missing user context).",
        }

    # Open a fresh DB session and create the schedule
    try:
        from ..db.base import AsyncSessionLocal
        from ..services.scheduled_task_service import create_scheduled_task

        async with AsyncSessionLocal() as _db:
            task = await create_scheduled_task(
                _db,
                tenant_id=tenant_id,
                user_id=user_id,
                goal=goal,
                schedule_description=schedule_description,
                cron_expression=cron_expression,
                timezone=timezone,
            )
            next_run_iso = (
                task.next_run_at.isoformat() if task.next_run_at else None
            )

        logger.info(
            "Created scheduled task %s for user %s: %s",
            task.id, user_id, goal[:80],
        )
        return {
            "ok": True,
            "schedule_id": task.id,
            "next_run_at": next_run_iso,
            "message": (
                f"✅ Schedule created! The task **{goal}** will run "
                f"{schedule_description}. "
                f"You can view and manage it in the Scheduled Tasks page."
            ),
        }
    except Exception as exc:
        logger.error("Failed to create scheduled task: %s", exc)
        return {
            "ok": False,
            "schedule_id": None,
            "next_run_at": None,
            "message": f"Failed to create schedule: {exc}",
        }
