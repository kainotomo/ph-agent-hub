# =============================================================================
# PH Agent Hub — ORM: Memory
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, Enum, ForeignKey, UniqueConstraint, event, func
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
            "user_id", "tenant_id", "key", "scope_session_id",
            name="uq_memory_user_tenant_key_scope",
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
    scope_session_id: Mapped[str] = mapped_column(
        CHAR(36), nullable=False, default="", server_default=""
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


# =============================================================================
# Scope sentinel maintenance
# =============================================================================
# ``scope_session_id`` exists purely so that the UNIQUE key
# ``uq_memory_user_tenant_key_scope`` can make *global* memory entries
# (``session_id IS NULL``) unique.  MySQL/MariaDB treat NULLs as distinct in
# unique indexes, so the NULL is replaced by a sentinel empty string.
#
# MariaDB rejects IFNULL / COALESCE / CONCAT_WS inside a generated column
# (error 1901), including on VIRTUAL columns once they are indexed, so the
# value is maintained here rather than by the database.  Centralising it in a
# mapper event covers every ORM insert and update path.
# =============================================================================


@event.listens_for(Memory, "before_insert")
@event.listens_for(Memory, "before_update")
def _sync_scope_session_id(mapper, connection, target: Memory) -> None:
    """Mirror ``session_id`` into ``scope_session_id`` (``''`` when global)."""
    target.scope_session_id = target.session_id or ""

