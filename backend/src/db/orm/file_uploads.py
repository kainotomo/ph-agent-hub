# =============================================================================
# PH Agent Hub — ORM: File Uploads
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, Integer, Text, DateTime, ForeignKey, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant
from .users import User
from .sessions import Session
from .messages import Message


class FileUpload(Base):
    __tablename__ = "file_uploads"

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
    message_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("messages.id"), nullable=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
