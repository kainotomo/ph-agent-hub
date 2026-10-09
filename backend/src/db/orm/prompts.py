# =============================================================================
# PH Agent Hub — ORM: Prompts
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, ForeignKey, func
from sqlalchemy.dialects.mysql import CHAR, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant
from .users import User
from .templates import Template


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False
    )
    template_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("templates.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    content: Mapped[str] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
