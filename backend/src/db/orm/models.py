# =============================================================================
# PH Agent Hub — ORM: Models (AI Provider Models)
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, Integer, Float, Numeric, DateTime, ForeignKey, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ...core.encryption import EncryptedString
from .tenants import Tenant


class Model(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(512), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    thinking_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reasoning_effort: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)
    follow_up_questions_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_route_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    context_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Pricing (per 1M tokens, currency-neutral; formatted per app setting)
    input_price_per_1m: Mapped[float | None] = mapped_column(Numeric(precision=12, scale=6), nullable=True)
    output_price_per_1m: Mapped[float | None] = mapped_column(Numeric(precision=12, scale=6), nullable=True)
    cache_hit_price_per_1m: Mapped[float | None] = mapped_column(Numeric(precision=12, scale=6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
