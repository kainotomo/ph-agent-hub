# =============================================================================
# PH Agent Hub — ORM: Skills & Skill Allowed Tools
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, Integer, Float, DateTime, Enum, ForeignKey, JSON, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant
from .users import User
from .templates import Template
from .prompts import Prompt
from .models import Model
from .tools import Tool


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    execution_type: Mapped[str] = mapped_column(
        Enum("agent", "workflow", "prompt_based", "workflow_based", name="skill_execution_enum"), nullable=False
    )
    maf_target_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("templates.id"), nullable=True
    )
    default_prompt_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True
    )
    default_model_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("models.id"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(
        Enum("tenant", "user", name="skill_visibility_enum"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ---- Cross-session memory (Issue #229) -----------------------------------
    cross_session_retrieval_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    cross_session_max_snippets: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False
    )
    cross_session_min_score: Mapped[float] = mapped_column(
        Float, default=0.30, nullable=False
    )

    # ---- A2A Agent Card metadata (Issue #408) ------------------------------
    a2a_metadata: Mapped[dict | None] = mapped_column(
        JSON, nullable=True,
        comment="A2A Agent Card per-skill metadata: {inputModes, outputModes, examples, tags}",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SkillAllowedTool(Base):
    __tablename__ = "skill_allowed_tools"

    skill_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("skills.id"), primary_key=True
    )
    tool_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tools.id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
