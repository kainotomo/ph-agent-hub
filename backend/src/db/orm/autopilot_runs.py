# =============================================================================
# PH Agent Hub — ORM: Autopilot Runs
# =============================================================================
#
# Tracks the full lifecycle of an autopilot execution: executing →
# completed / failed / cancelled / paused.
#
# Each autopilot run is backed by a Session that stores the conversation
# history.  The run record persists the goal, current turn, cumulative
# token usage, and per-turn findings so the frontend can display live
# progress and recover from server restarts.
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, TextClause, func
from sqlalchemy.dialects.mysql import CHAR, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class AutopilotRun(Base):
    __tablename__ = "autopilot_runs"

    # --- State constants ---
    STATE_EXECUTING = "EXECUTING"
    STATE_COMPLETED = "COMPLETED"
    STATE_FAILED = "FAILED"
    STATE_CANCELLED = "CANCELLED"
    STATE_PAUSED = "PAUSED"

    # --- Identity ---
    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )

    # --- Link to session ---
    session_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
        comment="The Session backing this autopilot run's conversation",
    )
    session = relationship("Session", backref="autopilot_runs", lazy="selectin")

    # --- Goal ---
    goal: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="The original user goal for this autopilot run",
    )

    # --- Lifecycle ---
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, default=STATE_EXECUTING,
        comment="One of: EXECUTING, COMPLETED, FAILED, CANCELLED, PAUSED",
    )

    # --- Turn tracking ---
    current_turn: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Current turn number (1-based, 0 = not started)",
    )
    max_turns: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20,
        comment="Maximum turns configured for this run",
    )

    # --- Token budget ---
    cumulative_tokens_in: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    cumulative_tokens_out: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # --- Payload ---
    plan: Mapped[dict | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"),
        nullable=True,
        comment="JSON: the agent's initial plan (list of steps)",
    )
    findings: Mapped[dict | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"),
        nullable=True,
        comment="JSON array of per-turn findings: [{turn, summary, artifacts}]",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Error detail if state is FAILED",
    )
    steering_instruction: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="User's steering instruction when state is PAUSED (resume injects this)",
    )

    # --- Background task fields (Issue #449) ---
    progress_message: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Latest human-readable progress message (e.g. 'Analyzing moat score…')",
    )
    notification_sent: Mapped[bool] = mapped_column(
        default=False,
        comment="Whether the completion/failure notification has been sent",
    )
    result_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Final result summary from task_complete() or the last response",
    )
    background_task: Mapped[bool] = mapped_column(
        default=False,
        comment="True if this was started as a user-facing background task (vs inline autopilot)",
    )

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
