# =============================================================================
# PH Agent Hub — Tool: Propose Schedule
# =============================================================================
#
# Agent tool for creating scheduled tasks from chat.  The agent calls this
# tool after the user describes a recurring schedule in natural language.
# The tool validates the cron expression and stores the proposal in Redis
# so the frontend can show a confirmation card.
#
# Usage (inside autopilot, turn 2+):
#
#   propose_schedule(
#       cron_expression="0 20 * * 5",
#       goal="Check portfolio holdings against Buy Below / Sell Above targets",
#       schedule_description="Every Friday at 8pm",
#       timezone="UTC",
#   )
# =============================================================================

from __future__ import annotations

import json
import logging

from agent_framework import tool

from ..core.redis import get_redis

logger = logging.getLogger(__name__)

# Redis prefix and TTL for schedule proposals
PROPOSAL_PREFIX = "schedule:proposal:"
PROPOSAL_TTL = 300  # 5 minutes — user must confirm before this expires


@tool
async def propose_schedule(
    cron_expression: str,
    goal: str,
    schedule_description: str,
    timezone: str = "UTC",
) -> dict:
    """Propose a scheduled agent task.

    Call this tool when the user asks you to set up a recurring or
    one-time scheduled task.  The tool validates the cron expression,
    stores the proposal for frontend confirmation, and returns the
    next execution datetime.

    Args:
        cron_expression: Standard cron expression describing the
            schedule.  Examples:
            - "0 20 * * 5"       → Every Friday at 8pm
            - "0 8 * * 1-5"      → Every weekday at 8am
            - "0 0 1 * *"        → 1st of every month at midnight
            - "*/30 * * * *"     → Every 30 minutes
            - "0 9 * * *"        → Every day at 9am
            - "0 0 * * 0"        → Every Sunday at midnight
            - "0 0 1 1 *"        → Every year on January 1st
            Use https://crontab.guru to verify expressions.
        goal: The agent goal to execute on this schedule.  Be specific
            about what the agent should do (e.g. "Check portfolio
            holdings against Buy Below / Sell Above targets and alert
            me of any crossings").
        schedule_description: Human-readable description of the
            schedule (e.g. "Every Friday at 8pm", "Every weekday at
            9am").
        timezone: IANA timezone name (e.g. "UTC", "Europe/London",
            "America/New_York").  Defaults to "UTC".

    Returns:
        A dict with:
            - "ok": bool
            - "next_run_at": ISO datetime string of the next execution,
              or None if the cron expression is invalid
            - "message": Human-readable confirmation message
            - "proposal_key": Redis key (for frontend confirmation)
    """
    from croniter import croniter

    # Validate cron expression
    try:
        import pytz
        tz = pytz.timezone(timezone)
        from datetime import datetime
        base = datetime.now(tz)
        cron = croniter(cron_expression, base)
        next_dt = cron.get_next(datetime)
        next_run_utc = next_dt.astimezone(pytz.utc)
        next_run_iso = next_run_utc.isoformat()
    except (ValueError, KeyError, ImportError) as exc:
        return {
            "ok": False,
            "next_run_at": None,
            "message": f"Invalid cron expression or timezone: {exc}",
            "proposal_key": None,
        }

    next_run_human = next_dt.strftime("%A, %B %d, %Y at %H:%M %Z")

    # Try to store the proposal in Redis (best-effort — if Redis is down,
    # the frontend can still work with the tool result directly)
    proposal_key = None
    try:
        redis = await get_redis()
        proposal_key = f"{PROPOSAL_PREFIX}{goal[:80]}:{cron_expression}"
        proposal_data = {
            "cron_expression": cron_expression,
            "goal": goal,
            "schedule_description": schedule_description,
            "timezone": timezone,
            "next_run_at": next_run_iso,
        }
        await redis.setex(proposal_key, PROPOSAL_TTL, json.dumps(proposal_data))
    except Exception:
        logger.warning("Failed to store schedule proposal in Redis", exc_info=True)

    return {
        "ok": True,
        "next_run_at": next_run_iso,
        "message": (
            f"I'll schedule this task: **{goal}**. "
            f"It will run {schedule_description} "
            f"(next: {next_run_human}). "
            "Please confirm to create the schedule."
        ),
        "proposal_key": proposal_key,
    }
