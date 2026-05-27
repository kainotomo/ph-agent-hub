# =============================================================================
# PH Agent Hub — ORM: Balance Transactions (Append-Only Audit Log)
# =============================================================================
#
# Every balance change (top-up, deduction, admin adjustment) is recorded here
# as an immutable audit trail.  Positive amounts = funds added (top-up),
# negative amounts = funds deducted (usage or admin adjustment).
# =============================================================================

import uuid
from datetime import datetime

from sqlalchemy import String, Numeric, DateTime, ForeignKey, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    admin_user_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    amount_eur: Mapped[float] = mapped_column(
        Numeric(precision=12, scale=6), nullable=False
    )
    balance_after: Mapped[float] = mapped_column(
        Numeric(precision=12, scale=6), nullable=False
    )
    reason: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    reference_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    reference_id: Mapped[str | None] = mapped_column(
        CHAR(36), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
