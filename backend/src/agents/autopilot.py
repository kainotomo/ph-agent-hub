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
    pause_state: dict[str, Any] = {"paused": False, "summary": ""}

    # Build the task_complete and pause_and_report tools once and reuse on every turn.
    from ..tools.task_complete import task_complete
    from ..tools.pause_and_report import pause_and_report

    task_complete_tools = [task_complete, pause_and_report]

    last_response_text = ""
    last_message_id = ""

    for turn in range(1, max_turns + 1):
        logger.info(
            "Autopilot turn %d/%d for session %s",
            turn, max_turns, session_data.get("id", "?"),
        )

        # ---- Prepare the user message and tools for this turn ----------
        # Turn 1: do NOT give task_complete so the agent must report
        # intermediate progress before it can declare the task done.
        if turn == 1:
            turn_message = (
                f"## Goal\n\n{goal}\n\n"
                "Work autonomously toward this goal using the available tools. "
                "Make progress and report what you have done. "
                "You will be asked to continue if more work remains."
            )
            turn_extra_tools: list = []
            turn_fn_kwargs: dict | None = None
        else:
            # Build a context-aware prompt from the agent's own last response.
            # Extracts the tail of the previous turn so the agent can pick
            # up where it left off instead of re-orienting from scratch.
            _last_tail = (
                last_response_text[-600:].strip()
                if last_response_text
                else ""
            )
            if _last_tail:
                _context_hint = (
                    f"Your previous response ended with:\n\n"
                    f"… {_last_tail}\n\n"
                )
            else:
                _context_hint = ""
            turn_message = (
                f"{_context_hint}"
                "Continue working toward the original goal. "
                "Use the available tools to make further progress. "
                "When you have fully achieved the objective call "
                "`task_complete(summary=...)` to signal completion."
            )
            turn_extra_tools = task_complete_tools
            turn_fn_kwargs = {"completion_state": completion_state, "pause_state": pause_state}

        # ---- Run the agent for this turn --------------------------------
        from .runner import run_agent

        response_text, message_id = await run_agent(
            session_data=session_data,
            user_message=turn_message,
            db=db,
            current_user=current_user,
            extra_tools=turn_extra_tools,
            function_invocation_kwargs=turn_fn_kwargs,
        )

        last_response_text = response_text
        last_message_id = message_id

        # ---- Check if the agent signalled pause ---------------------------
        if pause_state["paused"]:
            logger.info(
                "Autopilot paused on turn %d/%d via pause_and_report tool "
                "for session %s (summary length=%d)",
                turn, max_turns, session_data.get("id", "?"),
                len(pause_state["summary"]),
            )
            return response_text, message_id

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
    autopilot_run_id: str | None = None,
    start_turn: int = 1,
) -> None:
    """Execute an agent autonomously across multiple turns, writing SSE
    events to *bridge* for live streaming to the frontend.

    Same meta-loop as :func:`run_autopilot` but uses
    :func:`run_agent_stream` for each turn and forwards all agent events
    (token, tool_start, tool_result, etc.) to the bridge.  Autopilot
    lifecycle events (turn_start, turn_complete, complete, pause) are also
    written to the bridge.

    When *start_turn* > 1 (resume from pause), the steering instruction
    from the ``AutopilotRun`` is injected and the turn-1 restrictions
    (no task_complete/pause_and_report tools) are skipped.

    Args:
        session_data: Unified session dict (from DB or Redis).
        goal: The user's objective for the autopilot run.
        db: Active async DB session.
        current_user: The authenticated user.
        bridge: A ``StreamBridge`` instance to write SSE events into.
        max_turns: Maximum number of agent invocations before forcing
            a summary (default: ``settings.AUTOPILOT_MAX_TURNS``).
        autopilot_run_id: Optional ``AutopilotRun.id`` for persisting
            state across turns (Phase 3 resilience).
        start_turn: The turn number to start from (1 = fresh run,
            >1 = resume from pause).
    """
    if max_turns is None:
        max_turns = settings.AUTOPILOT_MAX_TURNS
    max_tokens = settings.AUTOPILOT_MAX_TOKENS

    completion_state: dict[str, Any] = {"done": False, "summary": ""}
    pause_state: dict[str, Any] = {"paused": False, "summary": ""}
    cumulative_tokens_in = 0
    cumulative_tokens_out = 0

    from ..tools.task_complete import task_complete
    from ..tools.pause_and_report import pause_and_report

    task_complete_tools = [task_complete, pause_and_report]
    session_id = session_data.get("id", "?")
    findings: list[dict] = []

    # ---- Track previous turn output for context-aware prompts -------------
    # Initialised here (before the loop) so the else branch on the first
    # iteration (including resume) doesn't raise UnboundLocalError.
    _last_turn_response = ""

    # ---- Inject steering instruction on resume ----------------------------
    steering_instruction: str | None = None
    if start_turn > 1 and autopilot_run_id:
        from ..services.autopilot_service import get_run as _ap_get_run
        from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
        async with _AsyncSessionLocal() as _ap_db:
            _run = await _ap_get_run(_ap_db, autopilot_run_id)
            if _run and _run.steering_instruction:
                steering_instruction = _run.steering_instruction
                logger.info(
                    "Resuming autopilot %s from turn %d with steering: %.80s",
                    autopilot_run_id, start_turn, steering_instruction,
                )
        await bridge.put({
            "event": "autopilot_resume",
            "data": json.dumps({
                "turn": start_turn,
                "max_turns": max_turns,
            }),
        })

    for turn in range(start_turn, max_turns + 1):
        # ---- Token budget check -----------------------------------------
        if max_tokens > 0 and (cumulative_tokens_in + cumulative_tokens_out) >= max_tokens:
            logger.warning(
                "Autopilot token budget exceeded (%d >= %d) for session %s",
                cumulative_tokens_in + cumulative_tokens_out, max_tokens, session_id,
            )
            await bridge.put({
                "event": "autopilot_error",
                "data": json.dumps({
                    "message": f"Token budget of {max_tokens} exceeded",
                    "turn": turn,
                }),
            })
            if autopilot_run_id:
                from ..services.autopilot_service import set_state as _ap_set_state
                await _ap_set_state(
                    db, autopilot_run_id, "FAILED",
                    error_message=f"Token budget of {max_tokens} exceeded",
                )
            return

        # ---- Check for user-initiated pause (DB state) --------------------
        if autopilot_run_id:
            from ..services.autopilot_service import get_run as _ap_get_run
            from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
            async with _AsyncSessionLocal() as _check_db:
                _current_run = await _ap_get_run(_check_db, autopilot_run_id)
                if _current_run and _current_run.state == "PAUSED":
                    logger.info(
                        "User-initiated pause detected for session %s at turn %d",
                        session_id, turn,
                    )
                    await bridge.put({
                        "event": "autopilot_pause",
                        "data": json.dumps({
                            "reason": "User paused the autopilot",
                            "turn": turn,
                        }),
                    })
                    return

        # ---- Prepare the user message and tools for this turn ----------
        # Turn 1 (original run only): do NOT give task_complete so the
        # agent must report intermediate progress before it can complete.
        if turn == 1 and start_turn == 1:
            # Turn 1: the user's original message was already persisted
            # by _handle_streaming_autopilot, so we pass the instructions
            # as a user message but skip DB persistence (skip_user_persist).
            # This prevents a duplicate user message in the conversation.
            turn_message = (
                f"## Goal\n\n{goal}\n\n"
                "Work autonomously toward this goal using the available tools. "
                "Make progress and report what you have done. "
                "You will be asked to continue if more work remains."
            )
            turn_extra_tools: list = []
            turn_fn_kwargs: dict | None = None
        else:
            # Build a context-aware prompt from the previous turn's output.
            # Captured from the message_complete event (see below).
            _last_tail = (
                _last_turn_response[-600:].strip()
                if _last_turn_response
                else ""
            )
            if _last_tail:
                _context_hint = (
                    f"Your previous response ended with:\n\n"
                    f"… {_last_tail}\n\n"
                )
            else:
                _context_hint = ""
            # Inject the user's steering instruction on the first resume turn.
            _steer_hint = (
                f"\nThe user provided this steering instruction:\n"
                f"{steering_instruction}\n\n"
                if steering_instruction and turn == start_turn
                else ""
            )
            turn_message = (
                f"{_context_hint}"
                f"{_steer_hint}"
                "Continue working toward the original goal. "
                "Use the available tools to make further progress. "
                "When you have fully achieved the objective call "
                "`task_complete(summary=...)` to signal completion."
            )
            turn_extra_tools = task_complete_tools
            turn_fn_kwargs = {"completion_state": completion_state, "pause_state": pause_state}

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
        # Each turn gets its own DB session so run_agent_stream()'s
        # internal commits don't invalidate the session for the next turn.
        turn_message_id = str(uuid.uuid4())

        from .runner import run_agent_stream
        from ..db.base import AsyncSessionLocal as _AsyncSessionLocal

        turn_tokens_in = 0
        turn_tokens_out = 0

        async with _AsyncSessionLocal() as turn_db:
            async for event_dict in run_agent_stream(
                session_data=session_data,
                user_message=turn_message,
                db=turn_db,
                current_user=current_user,
                message_id=turn_message_id,
                extra_tools=turn_extra_tools,
                function_invocation_kwargs=turn_fn_kwargs,
                # Turn 1's user message was already persisted by
                # _handle_streaming_autopilot — skip the duplicate.
                # Turns 2+ are persisted by run_agent_stream (below).
                skip_user_persist=(turn == 1 and start_turn == 1),
            ):
                # Extract token counts and response text from
                # message_complete events for the next turn's prompt.
                if event_dict.get("event") == "message_complete":
                    data = event_dict.get("data", "{}")
                    if isinstance(data, str):
                        try:
                            parsed = json.loads(data)
                            turn_tokens_in = parsed.get("tokens_in", 0) or 0
                            turn_tokens_out = parsed.get("tokens_out", 0) or 0
                            _last_turn_response = (
                                parsed.get("content", "") or ""
                            )
                        except (json.JSONDecodeError, TypeError):
                            pass
                await bridge.put(event_dict)

        cumulative_tokens_in += turn_tokens_in
        cumulative_tokens_out += turn_tokens_out

        # ---- Check for user-initiated pause right after the agent run ----
        # Catches the case where the user clicked Pause during the agent's
        # execution.  Only pause if the agent did NOT already complete —
        # task_complete takes priority over a delayed pause request.
        if autopilot_run_id and not completion_state["done"]:
            from ..services.autopilot_service import get_run as _ap_get_run
            from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
            async with _AsyncSessionLocal() as _check_db:
                _current_run = await _ap_get_run(_check_db, autopilot_run_id)
                if _current_run and _current_run.state == "PAUSED":
                    logger.info(
                        "User-initiated pause detected for session %s "
                        "at turn %d (after agent run)",
                        session_id, turn,
                    )
                    if autopilot_run_id:
                        from ..services.autopilot_service import (
                            update_turn as _ap_update_turn,
                        )
                        async with _AsyncSessionLocal() as _ap_db:
                            await _ap_update_turn(
                                _ap_db, autopilot_run_id, turn,
                                tokens_in=turn_tokens_in,
                                tokens_out=turn_tokens_out,
                            )
                    await bridge.put({
                        "event": "autopilot_pause",
                        "data": json.dumps({
                            "reason": "User paused the autopilot",
                            "turn": turn,
                        }),
                    })
                    return

        # ---- Persist findings and turn progress -------------------------
        if autopilot_run_id and turn >= 1:
            from ..services.autopilot_service import update_turn as _ap_update_turn
            # Use a separate DB session to avoid transaction conflicts with
            # run_agent_stream() which commits messages in the same session.
            from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
            async with _AsyncSessionLocal() as _ap_db:
                await _ap_update_turn(
                    _ap_db, autopilot_run_id, turn,
                    tokens_in=turn_tokens_in,
                    tokens_out=turn_tokens_out,
                )

        # ---- Emit autopilot_turn_complete event -------------------------
        await bridge.put({
            "event": "autopilot_turn_complete",
            "data": json.dumps({
                "turn": turn,
                "max_turns": max_turns,
            }),
        })

        # ---- Check if the agent signalled pause (agent-initiated) --------
        if pause_state["paused"]:
            logger.info(
                "Autopilot paused on turn %d/%d via pause_and_report tool "
                "for session %s (summary length=%d)",
                turn, max_turns, session_id,
                len(pause_state["summary"]),
            )
            if autopilot_run_id:
                from ..services.autopilot_service import (
                    set_state as _ap_set_state,
                    update_turn as _ap_update_turn,
                )
                from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
                async with _AsyncSessionLocal() as _ap_db:
                    await _ap_update_turn(
                        _ap_db, autopilot_run_id, turn,
                        tokens_in=turn_tokens_in,
                        tokens_out=turn_tokens_out,
                        finding={
                            "turn": turn,
                            "summary": pause_state["summary"][:500],
                        },
                    )
                    await _ap_set_state(_ap_db, autopilot_run_id, "PAUSED")
            await bridge.put({
                "event": "autopilot_pause",
                "data": json.dumps({
                    "reason": pause_state["summary"],
                    "turn": turn,
                }),
            })
            return

        # ---- Check if the agent signalled completion --------------------
        if completion_state["done"]:
            logger.info(
                "Autopilot stream completed on turn %d/%d for session %s "
                "(summary length=%d)",
                turn, max_turns, session_id,
                len(completion_state["summary"]),
            )
            # Persist final state using a separate DB session
            if autopilot_run_id:
                from ..services.autopilot_service import (
                    set_state as _ap_set_state,
                    update_turn as _ap_update_turn,
                )
                from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
                async with _AsyncSessionLocal() as _ap_db:
                    await _ap_update_turn(
                        _ap_db, autopilot_run_id, turn,
                        tokens_in=turn_tokens_in,
                        tokens_out=turn_tokens_out,
                        finding={
                            "turn": turn,
                            "summary": completion_state["summary"][:500],
                        },
                    )
                    await _ap_set_state(_ap_db, autopilot_run_id, "COMPLETED")
            await bridge.put({
                "event": "autopilot_complete",
                "data": json.dumps({
                    "summary": completion_state["summary"],
                    "turn": turn,
                }),
            })
            # Rewrite the first user message to show only the original goal
            # (remove autopilot boilerplate instructions).  Only rewrite
            # if the message actually contains boilerplate ("## Goal") —
            # not when it was a prior normal chat in the same session.
            from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
            async with _AsyncSessionLocal() as _cleanup_db:
                from sqlalchemy import select as sa_select
                from ..db.orm.messages import Message
                first_user = await _cleanup_db.execute(
                    sa_select(Message)
                    .where(
                        Message.session_id == session_data.get("id"),
                        Message.sender == "user",
                    )
                    .order_by(Message.created_at.asc())
                    .limit(1)
                )
                first_msg = first_user.scalar_one_or_none()
                if first_msg:
                    text = ""
                    if isinstance(first_msg.content, list):
                        for block in first_msg.content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                break
                    # Only rewrite if the message contains autopilot
                    # boilerplate ("## Goal") — not a plain user prompt.
                    if "## Goal" in text:
                        first_msg.content = [{"type": "text", "text": goal}]
                await _cleanup_db.commit()
            return

    # ---- Max turns reached — emit a summary event -----------------------
    logger.warning(
        "Autopilot stream reached max turns (%d) for session %s",
        max_turns, session_id,
    )
    if autopilot_run_id:
        from ..services.autopilot_service import set_state as _ap_set_state
        from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
        async with _AsyncSessionLocal() as _ap_db:
            await _ap_set_state(
                _ap_db, autopilot_run_id, "FAILED",
                error_message=f"Reached max turns ({max_turns})",
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
    # Rewrite the first user message to show only the original goal.
    # Only rewrite if the message contains autopilot boilerplate
    # ("## Goal") — not a prior normal chat message.
    from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
    async with _AsyncSessionLocal() as _cleanup_db:
        from sqlalchemy import select as sa_select
        from ..db.orm.messages import Message
        first_user = await _cleanup_db.execute(
            sa_select(Message)
            .where(
                Message.session_id == session_data.get("id"),
                Message.sender == "user",
            )
            .order_by(Message.created_at.asc())
            .limit(1)
        )
        first_msg = first_user.scalar_one_or_none()
        if first_msg:
            text = ""
            if isinstance(first_msg.content, list):
                for block in first_msg.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        break
            if "## Goal" in text:
                first_msg.content = [{"type": "text", "text": goal}]
        await _cleanup_db.commit()
