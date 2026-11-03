# =============================================================================
# PH Agent Hub — ORM: Templates
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, Enum, ForeignKey, func, JSON
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant
from .users import User


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    agent_config: Mapped[dict | None] = mapped_column(
        JSON, nullable=True,
        comment="Agent overrides: {max_steps, max_input_tokens}",
    )
    scope: Mapped[str] = mapped_column(
        Enum("tenant", "role", "user", name="template_scope_enum"), nullable=False
    )
    assigned_user_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
