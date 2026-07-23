# =============================================================================
# PH Agent Hub — task_complete Tool
# =============================================================================
# A @tool-decorated function that lets an autopilot agent signal "the goal
# has been fully achieved" before the controller continues to the next turn.
#
# The tool stores the completion summary in a mutable ``completion_state``
# dict passed through ``ctx.kwargs``.  The autopilot controller
# (``agents/autopilot.py``) reads this dict after each ``agent.run()``
# to decide whether to stop the loop.
#
# This tool is ONLY available during autopilot execution — it is injected
# as an ``extra_tool`` by the autopilot controller, never added to the
# normal session tool set.
#
# The ``ctx`` parameter is automatically injected by the MAF framework
# when the function has a ``FunctionInvocationContext`` annotation.
# See ``FunctionTool._discover_injected_parameters()``.
# =============================================================================

import logging

from agent_framework import FunctionInvocationContext, tool

logger = logging.getLogger(__name__)


@tool
async def task_complete(
    summary: str,
    ctx: FunctionInvocationContext | None = None,
) -> str:
    """Signal that the user's goal has been fully achieved.

    Call this ONLY when you have completely satisfied the user's original
    objective.  Provide a concise summary of what was accomplished,
    including key findings, results, or recommendations.

    Do NOT call this for intermediate milestones — only when the entire
    task is finished and ready for delivery.

    Args:
        summary: A comprehensive summary of what was accomplished.
            Include key results, data points, and recommendations.

    Returns:
        A confirmation that the task has been marked complete.
    """
    if ctx is None:
        logger.warning(
            "task_complete called without FunctionInvocationContext — no-op"
        )
        return "[task_complete called outside autopilot context — summary was not recorded]"

    completion_state = ctx.kwargs.get("completion_state")
    if completion_state is None:
        logger.warning(
            "task_complete called without completion_state in kwargs — no-op"
        )
        return (
            "[task_complete: no completion_state available — "
            "summary was not recorded. This tool only works in autopilot context.]"
        )

    completion_state["done"] = True
    completion_state["summary"] = summary

    logger.info(
        "task_complete called — summary length=%d, preview=%r",
        len(summary), summary[:120],
    )

    return (
        "[Task has been marked as complete.  The final summary has been "
        "recorded and will be delivered to the user.]"
    )
