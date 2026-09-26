# =============================================================================
# PH Agent Hub — ORM: Workflow Checkpoints
# =============================================================================
# One row is one MAF ``WorkflowCheckpoint``: the durable snapshot the workflow
# engine writes after a superstep so a run can resume.
#
# ``id`` is MAF's ``checkpoint_id`` — the engine supplies it, so no default is
# applied here.  ``tenant_id`` is carried explicitly (rather than inferred
# through a session join) so every query can be tenant-scoped with a single
# filter.
# =============================================================================

from datetime import datetime

from sqlalchemy import (
    String,
    Integer,
    Text,
    DateTime,
    Index,
    ForeignKey,
    func,
)
from sqlalchemy.dialects.mysql import CHAR, LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WorkflowCheckpointRecord(Base):
    __tablename__ = "workflow_checkpoints"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    workflow_name: Mapped[str] = mapped_column(String(320), nullable=False)
    graph_signature_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    message_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    previous_checkpoint_id: Mapped[str | None] = mapped_column(
        CHAR(36), nullable=True
    )
    iteration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[str] = mapped_column(
        LONGTEXT().with_variant(Text, "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_wf_checkpoints_tenant_name_created", "tenant_id", "workflow_name", "created_at"),
        Index("ix_wf_checkpoints_tenant_session", "tenant_id", "session_id"),
        Index("ix_wf_checkpoints_expires_at", "expires_at"),
    )
