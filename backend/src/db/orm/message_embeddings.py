# =============================================================================
# PH Agent Hub — ORM: Message Embeddings (Cross-Session Memory)
# =============================================================================
# Stores embedding vectors for user messages to enable semantic retrieval
# across sessions (Issue #229).
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, JSON, ForeignKey, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .users import User
from .tenants import Tenant
from .messages import Message


class MessageEmbedding(Base):
    __tablename__ = "message_embeddings"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    message_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
