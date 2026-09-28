# =============================================================================
# PH Agent Hub — ORM: Workflow Definitions
# =============================================================================
# One row is one published ``WorkflowDefinition``: the immutable blueprint the
# workflow engine loads to start a run.
#
# ``definition`` is one atomically-validated JSON document — a serialized
# ``WorkflowDefinition.model_dump()`` payload — and must not be partially
# mutated.  ``tenant_id`` is carried explicitly so every resolution path can
# be tenant-scoped with a single filter.
# =============================================================================

import uuid
from datetime import datetime

from sqlalchemy import String, Text, Boolean, JSON, Enum, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant


class WorkflowDefinitionRecord(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition: Mapped[dict] = mapped_column(
        JSON, nullable=False,
        comment="Validated WorkflowDefinition.model_dump() payload",
    )
    visibility: Mapped[str] = mapped_column(
        Enum("tenant", "user", name="workflow_definition_visibility_enum"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    signature_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_workflow_definitions_tenant_key"),
    )
