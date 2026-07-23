# =============================================================================
# PH Agent Hub — pause_and_report Tool
# =============================================================================
# A @tool-decorated function that lets an autopilot agent voluntarily
# pause execution and report intermediate findings.  The autopilot
# controller detects the pause and waits for a steering instruction
# from the user before resuming.
#
# This tool is injected as an extra_tool alongside task_complete
# during autopilot execution.
# =============================================================================

import logging

from agent_framework import FunctionInvocationContext, tool

logger = logging.getLogger(__name__)


@tool
async def pause_and_report(
    summary: str,
    ctx: FunctionInvocationContext | None = None,
) -> str:
    """Pause and report progress to the user.

    Call this when you have made significant progress but need more
    guidance before continuing, or when the user's goal requires
    clarification.  The autopilot will pause and present your
    summary to the user, who can then provide a steering instruction
    or cancel the task.

    Args:
        summary: A concise summary of what you have accomplished so far
            and what you are waiting for.  Be specific.

    Returns:
        A confirmation that the pause has been registered.
    """
    if ctx is None:
        logger.warning(
            "pause_and_report called without FunctionInvocationContext — no-op"
        )
        return (
            "[pause_and_report called outside autopilot context — "
            "summary was not recorded]"
        )

    pause_state = ctx.kwargs.get("pause_state")
    if pause_state is None:
        logger.warning(
            "pause_and_report called without pause_state in kwargs — no-op"
        )
        return (
            "[pause_and_report: no pause_state available — "
            "this tool only works in autopilot context.]"
        )

    pause_state["paused"] = True
    pause_state["summary"] = summary

    logger.info(
        "pause_and_report called — summary length=%d, preview=%r",
        len(summary), summary[:120],
    )

    return (
        "[The autopilot has been paused and your progress report has been "
        "sent to the user.  They will review it and may provide a steering "
        "instruction or ask you to continue.]"
    )
