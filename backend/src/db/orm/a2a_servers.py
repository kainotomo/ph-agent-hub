# =============================================================================
# PH Agent Hub — ORM: A2A Servers
# =============================================================================
# Stores A2A (Agent-to-Agent) remote agent connection configurations.
# Each server can expose multiple skills (discovered via the A2A Agent Card)
# that are registered as individual Tool records with type="a2a".
# =============================================================================

import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, Enum, ForeignKey, JSON, Text, Float, Integer, func, text as sa_text
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant


class A2aServer(Base):
    __tablename__ = "a2a_servers"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Configurable Agent Card path (default: IANA-registered /.well-known/agent-card.json)
    agent_card_path: Mapped[str] = mapped_column(
        String(512), nullable=False, server_default=sa_text("'/.well-known/agent-card.json'")
    )
    protocol_binding: Mapped[str] = mapped_column(
        Enum(
            "jsonrpc",
            "rest",
            "grpc",
            name="a2a_protocol_binding_enum",
        ),
        nullable=False,
    )
    auth_scheme: Mapped[str | None] = mapped_column(
        Enum(
            "none",
            "api_key",
            "bearer",
            "oauth2",
            name="a2a_auth_scheme_enum",
        ),
        nullable=True,
    )
    # Encrypted auth token (Fernet-encrypted at the service layer)
    auth_token: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Fernet-encrypted auth token"
    )
    # Encrypted JSON blob of custom HTTP headers
    headers: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Fernet-encrypted JSON dict of HTTP headers"
    )

    # --- OAuth2 configuration (Issue #418) ---
    oauth2_client_id: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="OAuth2 client identifier for Authorization Code flow",
    )
    # Fernet-encrypted OAuth2 client secret
    oauth2_client_secret: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Fernet-encrypted OAuth2 client secret",
    )
    oauth2_authorize_url: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="OAuth2 authorization endpoint URL",
    )
    oauth2_token_url: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="OAuth2 token endpoint URL",
    )
    oauth2_scopes: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Space-separated OAuth2 scope string",
    )
    # Fernet-encrypted JSON blob of OAuth2 runtime tokens
    oauth2_tokens: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Fernet-encrypted JSON blob of OAuth2 tokens (access_token, refresh_token, expires_at)",
    )

    # Null = all skills allowed; list = subset of skill IDs
    allowed_skills: Mapped[list | None] = mapped_column(
        JSON, nullable=True,
        comment="Null = all skills allowed; list = subset of skill IDs",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Retry configuration ---
    retry_max_attempts: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=sa_text("3"),
        comment="Max retry attempts for transient errors (null = use global default)",
    )
    retry_backoff_base_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=sa_text("1.0"),
        comment="Base seconds for exponential backoff (null = use global default)",
    )
    retry_backoff_max_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=sa_text("60.0"),
        comment="Max seconds for exponential backoff (null = use global default)",
    )

    # --- Timeout configuration ---
    timeout_connect_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=sa_text("30.0"),
        comment="HTTP connect timeout in seconds (null = use global default)",
    )
    timeout_read_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=sa_text("300.0"),
        comment="HTTP read timeout in seconds for non-streaming calls (null = use global default)",
    )
    timeout_stream_seconds: Mapped[float | None] = mapped_column(
        Float, nullable=True, server_default=sa_text("600.0"),
        comment="HTTP read timeout in seconds for streaming calls (null = use global default)",
    )

    # --- Circuit breaker configuration ---
    circuit_breaker_threshold: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=sa_text("5"),
        comment="Consecutive failures to trip circuit breaker (null = use global default)",
    )
    circuit_breaker_window_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=sa_text("60"),
        comment="Time window in seconds to reset failure count (null = use global default)",
    )
    circuit_breaker_cooldown_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True, server_default=sa_text("300"),
        comment="Cooldown in seconds before probe attempt (null = use global default)",
    )

    # Cached AgentCard JSON (reduces network calls on sync)
    agent_card_cache: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="Cached AgentCard JSON from last discovery"
    )
    agent_card_cached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
