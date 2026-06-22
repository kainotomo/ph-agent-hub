# =============================================================================
# PH Agent Hub — ORM: Tools
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Enum, ForeignKey, JSON, Text, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        Enum("erpnext", "membrane", "custom", "datetime", "web_search", "fetch_url", "weather", "calculator", "wikipedia", "rss_feed", "currency_exchange", "market_overview", "etf_data", "stock_data", "portfolio", "sec_filings", "pdf_extractor", "code_interpreter", "sql_query", "document_generation", "browser", "rag_search", "github", "calendar", "image_generation", "slack", "email", "mcp", "tasks", "a2a", name="tool_type_enum"), nullable=False
    )
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="general", server_default="general"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
