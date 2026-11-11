# =============================================================================
# PH Agent Hub — ORM: Chat Runs (Issue #608)
# =============================================================================
#
# Durable terminal record for every chat run.  A run's outcome used to live
# only in the SSE stream: if no client was attached (or the process died),
# nothing recorded that the run ended, why, or whether an answer was saved.
#
# Lifecycle:
#   * the background task inserts the row (outcome NULL, finished_at NULL)
#     before consuming the agent stream;
#   * the producer's single `run_terminal` frame is committed into the row
#     BEFORE it is forwarded to subscribers;
#   * the background task's `finally` guarantees a row exists for every
#     other path (exception, cancellation, crash-before-terminal);
#   * the startup sweep marks leftover unfinished rows `lost`.
#
# `outcome=completed` is only valid together with `persisted=true`.
# =============================================================================

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ChatRun(Base):
    __tablename__ = "chat_runs"
    __table_args__ = (
        # The read endpoint always asks for "the latest run of this session".
        Index("ix_chat_runs_session_started", "session_id", "started_at"),
    )

    #: The run id minted by the Redis lease (single id across lease, bridge,
    #: logs and this record).
    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)

    session_id: Mapped[str] = mapped_column(
        CHAR(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), nullable=False, index=True)

    #: chat | autopilot | workflow
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="chat", server_default="chat",
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    #: completed | failed | cancelled | lost (NULL while running)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: The persisted assistant message, when one was produced.
    message_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    persisted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0",
    )
    client_attached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0",
    )
    last_event_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    tokens_in: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )
    tokens_out: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
