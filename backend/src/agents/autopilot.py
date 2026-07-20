# =============================================================================
# PH Agent Hub — Autopilot Controller
# =============================================================================
# Meta-loop that chains multiple ``run_agent()`` invocations so the agent
# can autonomously plan, execute, and iterate toward a user-defined goal
# without requiring a new user prompt for each turn.
#
# The agent signals completion by calling the ``task_complete()`` tool.
# The controller reads the completion state from a shared dict passed via
# ``function_invocation_kwargs``.
#
# Usage (called from ``api/chat.py`` when ``body.autopilot`` is ``True``):
#
#   response_text, msg_id = await run_autopilot(
#       session_data=data,
#       goal=body.content,
#       db=db,
#       current_user=current_user,
#   )
#
# Architecture:
#   Turn 1:  run_agent(goal)           → agent plans + executes
#   Turn 2+: run_agent("Continue...")  → agent continues toward goal
#   Final:   task_complete called?     → return accumulated result
#   Fallback: max turns reached        → summary of what was accomplished
# =============================================================================

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.exceptions import ValidationError
from ..db.orm.users import User as UserORM

logger = logging.getLogger(__name__)


async def run_autopilot(
    session_data: dict[str, Any],
    goal: str,
    db: AsyncSession,
    current_user: UserORM,
    max_turns: int | None = None,
) -> tuple[str, str]:
    """Execute an agent autonomously across multiple turns toward *goal*.

    Each turn runs the agent via :func:`run_agent` with the full session
    history for context.  The agent calls ``task_complete(summary)`` to
    signal completion; the controller reads the result from a shared
    ``completion_state`` dict passed via ``function_invocation_kwargs``.

    Args:
        session_data: Unified session dict (from DB or Redis).
        goal: The user's objective for the autopilot run.
        db: Active async DB session.
        current_user: The authenticated user.
        max_turns: Maximum number of agent invocations before forcing
            a summary (default: ``settings.AUTOPILOT_MAX_TURNS``).

    Returns:
        A tuple of ``(final_response_text, final_message_id)`` matching
        the return type of :func:`run_agent` so the caller can use it
        identically.
    """
    if max_turns is None:
        max_turns = settings.AUTOPILOT_MAX_TURNS

    # Mutable container shared with task_complete tool via
    # function_invocation_kwargs.  The tool sets done=True on completion.
    completion_state: dict[str, Any] = {"done": False, "summary": ""}

    # Build the task_complete tool once and reuse it on every turn.
    from ..tools.task_complete import task_complete

    task_complete_tools = [task_complete]

    last_response_text = ""
    last_message_id = ""

    for turn in range(1, max_turns + 1):
        logger.info(
            "Autopilot turn %d/%d for session %s",
            turn, max_turns, session_data.get("id", "?"),
        )

        # ---- Prepare the user message for this turn ---------------------
        if turn == 1:
            # First turn: the goal is the user message.  The system prompt
            # already contains instructions on tool usage; we reinforce the
            # autopilot context.
            turn_message = (
                f"## Goal\n\n{goal}\n\n"
                "Work autonomously toward this goal using the available tools. "
                "When you have fully achieved the objective call "
                "`task_complete()` with a comprehensive summary of what was "
                "accomplished."
            )
        else:
            # Subsequent turns: brief continuation prompt.  The full
            # conversation history (previous turns' messages) is available
            # in the session context, so the agent knows what it did before.
            turn_message = (
                "Continue working toward the original goal. "
                "Use the available tools to make further progress. "
                "Call `task_complete(summary=...)` only when the entire "
                "goal has been fully achieved."
            )

        # ---- Run the agent for this turn --------------------------------
        # We import here to avoid circular imports — autopilot is called
        # from chat.py which is many layers above the runner.
        from .runner import run_agent

        response_text, message_id = await run_agent(
            session_data=session_data,
            user_message=turn_message,
            db=db,
            current_user=current_user,
            extra_tools=task_complete_tools,
            function_invocation_kwargs={
                "completion_state": completion_state,
            },
        )

        last_response_text = response_text
        last_message_id = message_id

        # ---- Check if the agent signalled completion --------------------
        if completion_state["done"]:
            completion_summary = completion_state["summary"]
            logger.info(
                "Autopilot completed on turn %d/%d for session %s "
                "(summary length=%d)",
                turn, max_turns, session_data.get("id", "?"),
                len(completion_summary),
            )
            # Return the agent's final response (which should be the
            # comprehensive result), not the raw summary string.
            return response_text, message_id

    # ---- Max turns reached — build a summary of what was accomplished ----
    logger.warning(
        "Autopilot reached max turns (%d) for session %s — "
        "forcing summarization",
        max_turns, session_data.get("id", "?"),
    )

    try:
        from .runner import run_agent

        summary_text, summary_msg_id = await run_agent(
            session_data=session_data,
            user_message=(
                "You have reached the maximum number of turns allowed for "
                "this task.  Please provide a concise summary of what you "
                "accomplished so far, including any key findings, results, "
                "or recommendations.  Be honest about what was completed "
                "and what remains unfinished."
            ),
            db=db,
            current_user=current_user,
        )
        return summary_text, summary_msg_id
    except Exception as exc:
        logger.exception(
            "Failed to generate max-turns summary for session %s",
            session_data.get("id", "?"),
        )
        raise ValidationError(
            f"Autopilot reached {max_turns} turns without completing the "
            f"goal.  Unable to generate summary: {exc}"
        ) from exc


async def run_autopilot_stream(
    session_data: dict[str, Any],
    goal: str,
    db: AsyncSession,
    current_user: UserORM,
    bridge: Any,
    max_turns: int | None = None,
) -> None:
    """Execute an agent autonomously across multiple turns, writing SSE
    events to *bridge* for live streaming to the frontend.

    Same meta-loop as :func:`run_autopilot` but uses
    :func:`run_agent_stream` for each turn and forwards all agent events
    (token, tool_start, tool_result, etc.) to the bridge.  Autopilot
    lifecycle events (turn_start, turn_complete, complete) are also
    written to the bridge.

    Args:
        session_data: Unified session dict (from DB or Redis).
        goal: The user's objective for the autopilot run.
        db: Active async DB session.
        current_user: The authenticated user.
        bridge: A ``StreamBridge`` instance to write SSE events into.
        max_turns: Maximum number of agent invocations before forcing
            a summary (default: ``settings.AUTOPILOT_MAX_TURNS``).
    """
    if max_turns is None:
        max_turns = settings.AUTOPILOT_MAX_TURNS

    completion_state: dict[str, Any] = {"done": False, "summary": ""}

    from ..tools.task_complete import task_complete

    task_complete_tools = [task_complete]
    session_id = session_data.get("id", "?")

    for turn in range(1, max_turns + 1):
        # ---- Prepare the user message for this turn ---------------------
        if turn == 1:
            turn_message = (
                f"## Goal\n\n{goal}\n\n"
                "Work autonomously toward this goal using the available tools. "
                "When you have fully achieved the objective call "
                "`task_complete()` with a comprehensive summary of what was "
                "accomplished."
            )
        else:
            turn_message = (
                "Continue working toward the original goal. "
                "Use the available tools to make further progress. "
                "Call `task_complete(summary=...)` only when the entire "
                "goal has been fully achieved."
            )

        # ---- Emit autopilot_turn_start event ----------------------------
        await bridge.put({
            "event": "autopilot_turn_start",
            "data": json.dumps({
                "turn": turn,
                "max_turns": max_turns,
                "message": turn_message[:200],
            }),
        })

        # ---- Run the agent for this turn (streaming) --------------------
        # Generate a unique message_id for this turn's conversation bubble.
        turn_message_id = str(uuid.uuid4())

        from .runner import run_agent_stream

        async for event_dict in run_agent_stream(
            session_data=session_data,
            user_message=turn_message,
            db=db,
            current_user=current_user,
            message_id=turn_message_id,
            extra_tools=task_complete_tools,
            function_invocation_kwargs={
                "completion_state": completion_state,
            },
        ):
            await bridge.put(event_dict)

        # ---- Emit autopilot_turn_complete event -------------------------
        await bridge.put({
            "event": "autopilot_turn_complete",
            "data": json.dumps({
                "turn": turn,
                "max_turns": max_turns,
            }),
        })

        # ---- Check if the agent signalled completion --------------------
        if completion_state["done"]:
            logger.info(
                "Autopilot stream completed on turn %d/%d for session %s "
                "(summary length=%d)",
                turn, max_turns, session_id,
                len(completion_state["summary"]),
            )
            await bridge.put({
                "event": "autopilot_complete",
                "data": json.dumps({
                    "summary": completion_state["summary"],
                    "turn": turn,
                }),
            })
            return

    # ---- Max turns reached — emit a summary event -----------------------
    logger.warning(
        "Autopilot stream reached max turns (%d) for session %s",
        max_turns, session_id,
    )
    await bridge.put({
        "event": "autopilot_max_turns",
        "data": json.dumps({
            "max_turns": max_turns,
            "session_id": session_id,
            "message": (
                f"Reached maximum of {max_turns} turns without completing "
                f"the goal.  Here is what was accomplished so far."
            ),
        }),
    })
