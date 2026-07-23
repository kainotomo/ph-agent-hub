# =============================================================================
# PH Agent Hub — ORM: Notifications
# =============================================================================
#
# Persistent in-app notification records.  Created when background tasks
# complete / fail, and can be extended for system notifications in the
# future.
#
# The frontend polls ``GET /notifications/unread-count`` for the badge
# and displays recent notifications in a bell dropdown.
# =============================================================================

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, TextClause, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class Notification(Base):
    __tablename__ = "notifications"

    # --- Type constants ---
    TYPE_TASK_COMPLETED = "TASK_COMPLETED"
    TYPE_TASK_FAILED = "TASK_FAILED"
    TYPE_TASK_CANCELLED = "TASK_CANCELLED"
    TYPE_TASK_SCHEDULED_COMPLETED = "TASK_SCHEDULED_COMPLETED"
    TYPE_TASK_SCHEDULED_FAILED = "TASK_SCHEDULED_FAILED"

    # --- Identity ---
    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )

    # --- Ownership ---
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user = relationship("User", backref="notifications", lazy="selectin")
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    tenant = relationship("Tenant", backref="notifications", lazy="selectin")

    # --- Content ---
    type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment="Notification type: TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED, TASK_SCHEDULED_COMPLETED, TASK_SCHEDULED_FAILED",
    )
    title: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Short human-readable title (e.g. 'Portfolio analysis complete')",
    )
    body: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Optional longer description or result summary",
    )

    # --- Link to the originating task (if applicable) ---
    reference_id: Mapped[str | None] = mapped_column(
        CHAR(36), nullable=True,
        comment="ID of the related entity (e.g. AutopilotRun.id, session_id)",
    )
    reference_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
        comment="Entity type: 'autopilot_run', 'session'",
    )

    # --- Read state ---
    is_read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True,
        comment="Whether the user has seen this notification",
    )

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(),
        index=True,
    )
