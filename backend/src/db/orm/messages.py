# =============================================================================
# PH Agent Hub — ORM: Messages & Message Feedback
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, Integer, Text, DateTime, Enum, ForeignKey, JSON, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from .sessions import Session
from .models import Model
from .users import User


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("sessions.id"), nullable=False
    )
    sender: Mapped[str] = mapped_column(
        Enum("user", "assistant", "system", name="message_sender_enum"), nullable=False
    )
    content: Mapped[list | None] = mapped_column(JSON, nullable=True)
    model_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("models.id"), nullable=True
    )
    model: Mapped["Model | None"] = relationship(
        "Model", lazy="selectin", foreign_keys=[model_id]
    )
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summarized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MessageFeedback(Base):
    __tablename__ = "message_feedback"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    message_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("messages.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id"), nullable=False
    )
    rating: Mapped[str] = mapped_column(
        Enum("up", "down", name="feedback_rating_enum"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
