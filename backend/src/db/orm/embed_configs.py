# =============================================================================
# PH Agent Hub — ORM: Embed Configurations
# =============================================================================
# Stores embeddable chat widget configurations per tenant.
# Each config generates a guest token that website visitors use to chat
# without a PH Agent Hub user account.
# =============================================================================

import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, Boolean, JSON, ForeignKey, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class EmbedConfig(Base):
    __tablename__ = "embed_configs"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    guest_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_origins: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Theme ---
    theme: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Feature flags ---
    feature_flags: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Optional defaults ---
    default_model_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("models.id"), nullable=True
    )
    default_skill_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("skills.id"), nullable=True
    )
    default_template_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("templates.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
