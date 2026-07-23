# =============================================================================
# PH Agent Hub — ORM: Scheduled Tasks
# =============================================================================
#
# Stores agent tasks that execute automatically at specified times or on
# recurring schedules (Issue #297 — Scheduled & Recurring Agent Tasks).
#
# Each scheduled task stores the agent's goal, a cron expression, and
# scheduling metadata.  A polling loop in main.py checks for due tasks
# and spawns autopilot execution via run_autopilot().
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, TextClause, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    # --- State constants ---
    STATE_ACTIVE = "ACTIVE"
    STATE_PAUSED = "PAUSED"
    STATE_DELETED = "DELETED"

    # --- Identity ---
    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )

    # --- Ownership ---
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    tenant = relationship("Tenant", lazy="selectin")
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user = relationship("User", lazy="selectin")

    # --- Goal ---
    goal: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="The agent goal to execute — passed as the autopilot goal",
    )

    # --- Schedule ---
    schedule_description: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Human-readable schedule description (e.g. 'Every Friday at 8pm')",
    )
    cron_expression: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Standard cron expression (e.g. '0 20 * * 5')",
    )
    timezone: Mapped[str] = mapped_column(
        String(50), nullable=False, default="UTC",
        comment="IANA timezone name (e.g. 'Europe/London', 'America/New_York')",
    )

    # --- Lifecycle ---
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, default=STATE_ACTIVE,
        comment="One of: ACTIVE, PAUSED, DELETED",
    )

    # --- Scheduling ---
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
        comment="Next scheduled execution datetime (UTC)",
    )

    # --- Last run info ---
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="When the last execution occurred (UTC)",
    )
    last_run_status: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        comment="Last execution result: SUCCESS, FAILED, or null if never run",
    )
    last_run_session_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        comment="Session ID of the last execution (if any)",
    )
    last_run_session = relationship(
        "Session", lazy="selectin",
        foreign_keys=[last_run_session_id],
    )
    last_run_error: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Error message from the last execution (if it failed)",
    )

    # --- Template session ---
    template_session_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        comment="The session where this schedule was created (for context reference)",
    )
    template_session = relationship("Session", lazy="selectin", foreign_keys=[template_session_id])

    # --- Stats ---
    run_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Total number of successful+failed executions",
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
