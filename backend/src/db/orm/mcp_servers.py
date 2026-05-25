# =============================================================================
# PH Agent Hub — ORM: MCP Servers
# =============================================================================
# Stores MCP server connection configurations. Each server can expose multiple
# tools (discovered via the MCP protocol) that are registered as individual
# Tool records with type="mcp".
# =============================================================================

import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, Enum, ForeignKey, JSON, Text, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    transport: Mapped[str] = mapped_column(
        Enum(
            "stdio",
            "streamable_http",
            "websocket",
            name="mcp_transport_enum",
        ),
        nullable=False,
    )
    # For streamable_http / websocket transport
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # For stdio transport
    command: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    args: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Encrypted JSON blobs (encrypted/decrypted at the service layer)
    env_vars: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Fernet-encrypted JSON dict of env vars")
    headers: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Fernet-encrypted JSON dict of HTTP headers")
    # Null = all tools allowed; list = subset of tool names
    allowed_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
