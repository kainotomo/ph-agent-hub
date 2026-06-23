# =============================================================================
# PH Agent Hub — request_auth Tool
# =============================================================================
# A @tool-decorated function that lets an A2A agent signal "this task needs
# credentials from the user before it can continue."
#
# The tool stores the auth request info (provider, tool_type, scopes) in
# Redis under ``auth_request:{task_id}``.  The A2A task lifecycle layer
# (``a2a_server.py``) checks this flag after ``agent.run()`` returns and
# transitions the task to ``AUTH_REQUIRED`` with the info as
# ``status_message``.
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

from ..core.redis import store_a2a_auth_request

logger = logging.getLogger(__name__)


@tool
async def request_auth(
    provider: str,
    tool_type: str,
    scopes: list[str] | None = None,
    reason: str | None = None,
    ctx: FunctionInvocationContext | None = None,
) -> str:
    """Request authentication from the user for a tool or service.

    Call this when you need the user to provide OAuth credentials for an
    external service before you can proceed.  The task will be paused in
    ``AUTH_REQUIRED`` state, and execution will resume once the user
    authenticates.

    Args:
        provider: The OAuth provider name (e.g. ``"google"``,
            ``"microsoft"``).
        tool_type: The tool type that needs credentials (e.g.
            ``"email"``, ``"calendar"``, ``"tasks"``).
        scopes: Optional list of OAuth scopes required (e.g.
            ``["https://www.googleapis.com/auth/gmail.send"]``).
        reason: Optional human-readable explanation of why auth is
            needed (e.g. "I need to send an email on your behalf").

    Returns:
        A confirmation message that the auth request has been registered.
    """
    if ctx is None:
        logger.warning(
            "request_auth called without FunctionInvocationContext — no-op"
        )
        return (
            "[request_auth called outside A2A context — "
            "no auth request was stored]"
        )

    task_id = ctx.kwargs.get("task_id")
    if not task_id:
        logger.warning(
            "request_auth called without task_id in invocation kwargs — no-op"
        )
        return (
            "[request_auth: no task_id available — auth request was not "
            "registered. This tool only works in A2A task context.]"
        )

    auth_info: dict[str, object] = {
        "provider": provider,
        "tool_type": tool_type,
    }
    if scopes:
        auth_info["scopes"] = scopes
    if reason:
        auth_info["reason"] = reason

    # If auth was just completed for this provider on resume, don't
    # re-request it — tell the agent to retry the actual tool instead.
    # This prevents an infinite loop where the agent calls request_auth,
    # the task is resumed, and the agent calls request_auth again.
    auth_completed = ctx.kwargs.get("auth_completed", False)
    auth_provider = ctx.kwargs.get("auth_provider", "")
    if auth_completed and auth_provider == provider:
        logger.info(
            "request_auth skipped for task %s — auth already "
            "completed for %s", task_id, provider,
        )
        return (
            f"[Authentication for {provider} ({tool_type}) was already "
            f"completed. Please retry the operation using the tool directly.]"
        )

    await store_a2a_auth_request(task_id, auth_info)
    logger.info(
        "request_auth stored auth request for task %s: provider=%s, tool_type=%s",
        task_id, provider, tool_type,
    )
    return (
        f"[Auth requested for {provider} ({tool_type}). "
        f"The task will be paused until you provide credentials.]"
    )
