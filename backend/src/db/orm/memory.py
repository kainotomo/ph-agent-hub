# =============================================================================
# PH Agent Hub — ORM: Memory
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant
from .users import User
from .sessions import Session


class Memory(Base):
    __tablename__ = "memory"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "tenant_id", "key", "session_id",
            name="uq_memory_user_tenant_key_session",
        ),
    )

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False
    )
    session_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("sessions.id"), nullable=True
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        Enum("automatic", "manual", name="memory_source_enum"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )
