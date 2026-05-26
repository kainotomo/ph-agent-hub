# =============================================================================
# PH Agent Hub — ORM: Tenants
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Boolean, Numeric, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    balance_euros: Mapped[float | None] = mapped_column(
        Numeric(precision=12, scale=6), nullable=True, default=None
    )
    warning_threshold_eur: Mapped[float | None] = mapped_column(
        Numeric(precision=12, scale=6), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
