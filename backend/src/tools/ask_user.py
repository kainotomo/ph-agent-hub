# =============================================================================
# PH Agent Hub — ask_user Tool
# =============================================================================
# A @tool-decorated function that lets an A2A agent signal "this task needs
# more input from the user" before it can continue.
#
# The tool stores the question in Redis under ``ask_user:{task_id}``.  The
# A2A task lifecycle layer (``a2a_server.py``) checks this flag after
# ``agent.run()`` returns and transitions the task to
# ``INPUT_REQUIRED`` with the question as ``status_message``.
#
# This is A2A-specific infrastructure — the tool is injected only when
# running in A2A context (via ``function_invocation_kwargs``).
#
# The ``ctx`` parameter is automatically injected by the MAF framework
# when the function has a ``FunctionInvocationContext`` annotation.
# See ``FunctionTool._discover_injected_parameters()``.
# =============================================================================

import logging

from agent_framework import FunctionInvocationContext, tool

from ..core.redis import store_a2a_question

logger = logging.getLogger(__name__)


@tool
async def ask_user(
    question: str,
    ctx: FunctionInvocationContext | None = None,
) -> str:
    """Request additional input from the user.

    Call this when you need the user to provide more information before
    you can complete the task.  The task will be paused in
    ``INPUT_REQUIRED`` state, and execution will resume once the user
    responds.

    Args:
        question: The question to ask the user.  Be specific and clear
            about what information you need.

    Returns:
        A confirmation message that the question has been registered.
    """
    if ctx is None:
        logger.warning("ask_user called without FunctionInvocationContext — no-op")
        return "[ask_user called outside A2A context — no question was stored]"

    task_id = ctx.kwargs.get("task_id")
    if not task_id:
        logger.warning("ask_user called without task_id in invocation kwargs — no-op")
        return (
            "[ask_user: no task_id available — question was not registered. "
            "This tool only works in A2A task context.]"
        )

    await store_a2a_question(task_id, question)
    logger.info("ask_user stored question for task %s", task_id)
    return (
        f"[I've asked the user: '{question}'. "
        f"The task will be paused until the user responds.]"
    )
