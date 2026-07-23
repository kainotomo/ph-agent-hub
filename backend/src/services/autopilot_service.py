# =============================================================================
# PH Agent Hub — Autopilot Run Service
# =============================================================================
# CRUD + state transitions for AutopilotRun records.
# Used by the autopilot controller to persist state across turns.
# =============================================================================

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.orm.autopilot_runs import AutopilotRun

logger = logging.getLogger(__name__)


async def create_autopilot_run(
    db: AsyncSession,
    session_id: str,
    goal: str,
    max_turns: int = 20,
) -> AutopilotRun:
    """Create a new AutopilotRun record with state=EXECUTING."""
    run = AutopilotRun(
        session_id=session_id,
        goal=goal,
        state=AutopilotRun.STATE_EXECUTING,
        max_turns=max_turns,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    logger.info(
        "Created AutopilotRun %s for session %s (max_turns=%d)",
        run.id, session_id, max_turns,
    )
    return run


async def update_turn(
    db: AsyncSession,
    run_id: str,
    current_turn: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    finding: dict[str, Any] | None = None,
) -> None:
    """Update turn progress and optionally append a finding."""
    run = await get_run(db, run_id)
    if run is None:
        logger.warning("AutopilotRun %s not found — cannot update turn", run_id)
        return

    run.current_turn = current_turn
    run.cumulative_tokens_in += tokens_in
    run.cumulative_tokens_out += tokens_out

    if finding:
        current_findings: list[dict] = []
        if run.findings:
            if isinstance(run.findings, str):
                current_findings = json.loads(run.findings)
            else:
                current_findings = list(run.findings)
        current_findings.append(finding)
        run.findings = json.dumps(current_findings)

    await db.commit()


async def set_state(
    db: AsyncSession,
    run_id: str,
    state: str,
    error_message: str | None = None,
) -> None:
    """Transition an AutopilotRun to a new state."""
    run = await get_run(db, run_id)
    if run is None:
        logger.warning("AutopilotRun %s not found — cannot set state", run_id)
        return

    run.state = state
    if error_message:
        run.error_message = error_message
    await db.commit()

    logger.info("AutopilotRun %s → state=%s", run_id, state)


async def set_steering_instruction(
    db: AsyncSession,
    run_id: str,
    instruction: str,
) -> None:
    """Store a steering instruction and set state to PAUSED."""
    run = await get_run(db, run_id)
    if run is None:
        logger.warning("AutopilotRun %s not found — cannot steer", run_id)
        return

    run.state = AutopilotRun.STATE_PAUSED
    run.steering_instruction = instruction
    await db.commit()

    logger.info(
        "AutopilotRun %s → PAUSED with steering: %.80s",
        run_id, instruction,
    )


async def get_run(
    db: AsyncSession,
    run_id: str,
) -> AutopilotRun | None:
    """Fetch an AutopilotRun by ID."""
    result = await db.execute(
        select(AutopilotRun).where(AutopilotRun.id == run_id)
    )
    return result.scalar_one_or_none()


async def get_run_by_session(
    db: AsyncSession,
    session_id: str,
) -> AutopilotRun | None:
    """Fetch the latest AutopilotRun for a session."""
    result = await db.execute(
        select(AutopilotRun)
        .where(AutopilotRun.session_id == session_id)
        .order_by(AutopilotRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_executing_runs(
    db: AsyncSession,
) -> list[AutopilotRun]:
    """Fetch all runs still in EXECUTING state (used for server restart recovery)."""
    result = await db.execute(
        select(AutopilotRun).where(
            AutopilotRun.state == AutopilotRun.STATE_EXECUTING
        )
    )
    return list(result.scalars().all())


async def fail_stale_runs(
    db: AsyncSession,
    error_message: str = "Server restarted mid-execution",
) -> list[AutopilotRun]:
    """Mark all EXECUTING runs as FAILED (used on FastAPI startup)."""
    runs = await get_executing_runs(db)
    for run in runs:
        run.state = AutopilotRun.STATE_FAILED
        run.error_message = error_message
    if runs:
        await db.commit()
        logger.info(
            "Marked %d stale AutopilotRun(s) as FAILED", len(runs),
        )
    return runs
