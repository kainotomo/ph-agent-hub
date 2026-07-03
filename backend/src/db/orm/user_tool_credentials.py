# =============================================================================
# PH Agent Hub — ORM: User Tool Credentials
# =============================================================================
# Per-user credentials for tools that require personal authentication
# (email, calendar, tasks, etc.). Each row represents one connected
# account — a user can have multiple accounts for the same tool type.
#
# Credentials are stored encrypted at rest via EncryptedString.
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ...core.encryption import EncryptedString
from .users import User
from .tools import Tool


class UserToolCredential(Base):
    __tablename__ = "user_tool_credentials"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        comment="Tenant scope for tenant-isolation filtering",
    )
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tool_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="User-defined display name (e.g., 'Work Gmail', 'Personal Outlook')",
    )
    provider: Mapped[str] = mapped_column(
        Enum("gmail", "outlook", "imap", "google", "microsoft", "erpnext", "github",
             name="credential_provider_enum"),
        nullable=False,
    )
    email_address: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="Primary email address for this account",
    )
    credentials: Mapped[str | None] = mapped_column(
        EncryptedString(4096), nullable=True,
        comment="Encrypted JSON — IMAP/SMTP passwords, client IDs, etc.",
    )
    oauth_tokens: Mapped[str | None] = mapped_column(
        EncryptedString(4096), nullable=True,
        comment="Encrypted JSON — access_token, refresh_token, expires_at",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        comment="When true, this account is used when no account_label is specified",
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "expired", "revoked", "error",
             name="credential_status_enum"),
        nullable=False, default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "tool_id", "email_address",
            name="uq_user_tool_credential_email",
        ),
    )
