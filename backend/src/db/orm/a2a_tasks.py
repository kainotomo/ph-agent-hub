# =============================================================================
# PH Agent Hub — ORM: A2A Tasks (Persistent Task Store)
# =============================================================================
#
# Tracks the full A2A task lifecycle: submitted → working →
# input_required / auth_required → completed / failed / canceled.
#
# Each A2A task is backed by a ph-agent-hub Session that stores the
# actual conversation history and tool state.  The task record bridges
# the A2A protocol model to the internal session model.
# =============================================================================

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, TextClause, func
from sqlalchemy.dialects.mysql import CHAR, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class A2aTask(Base):
    __tablename__ = "a2a_tasks"

    # --- Identity ---
    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    context_id: Mapped[str] = mapped_column(
        CHAR(36), nullable=False, index=True,
        comment="A2A context ID — groups related tasks in a multi-turn exchange",
    )

    # --- Link to ph-agent-hub session ---
    session_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True, index=True,
        comment="The ph-agent-hub Session backing this task's conversation",
    )

    # --- Task lifecycle ---
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True,
        comment=(
            "One of: TASK_STATE_SUBMITTED, TASK_STATE_WORKING, "
            "TASK_STATE_INPUT_REQUIRED, TASK_STATE_AUTH_REQUIRED, "
            "TASK_STATE_COMPLETED, TASK_STATE_FAILED, "
            "TASK_STATE_CANCELED, TASK_STATE_REJECTED"
        ),
    )

    # --- Payload ---
    artifacts: Mapped[dict | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"),
        nullable=True,
        comment="JSON array of artifact dicts (artifactId, name, parts)",
    )
    history: Mapped[dict | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"),
        nullable=True,
        comment="JSON array of A2A message history entries",
    )
    status_message: Mapped[dict | None] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"),
        nullable=True,
        comment=(
            "JSON object — error detail when FAILED, "
            "agent question when INPUT_REQUIRED, "
            "credential description when AUTH_REQUIRED"
        ),
    )

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(),
    )

    # --- Relationships ---
    session = relationship("Session", backref="a2a_tasks", lazy="selectin")
