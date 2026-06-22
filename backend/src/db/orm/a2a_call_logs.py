# =============================================================================
# PH Agent Hub — ORM: A2A Call Logs (Append-Only, Denormalized)
# =============================================================================
#
# Tracks A2A call reliability metrics: status, latency, retry count, and
# error details.  Foreign keys have been removed so that call-log data
# survives A2A server deletion (useful for post-mortem analysis).
# =============================================================================

import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class A2aCallLog(Base):
    __tablename__ = "a2a_call_logs"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Denormalized entity references (survive deletion)
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), nullable=False
    )
    a2a_server_id: Mapped[str] = mapped_column(
        CHAR(36), nullable=False
    )
    a2a_server_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    skill_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    session_id: Mapped[str | None] = mapped_column(
        CHAR(36), nullable=True
    )
    trace_id: Mapped[str] = mapped_column(
        String(36), nullable=False,
        comment="Correlation ID for the call chain",
    )
    # Call outcome
    status: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="One of: success, timeout, error, circuit_open",
    )
    latency_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        comment="Call duration in milliseconds",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Number of retry attempts made",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Error detail if status is not success",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
