# =============================================================================
# PH Agent Hub — Configuration (Single Source of Truth)
# =============================================================================
# Loads all environment variables once at startup via Pydantic BaseSettings.
# Every other module imports `settings` from here.
# =============================================================================

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Database ---
    DATABASE_URL: str

    # --- Cache ---
    REDIS_URL: str

    # --- Object Storage (MinIO) ---
    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET_PREFIX: str
    MINIO_PUBLIC_ENDPOINT: str = ""

    # --- File Upload Limits ---
    UPLOAD_MAX_SIZE_BYTES: int = 104_857_600  # 100 MiB
    UPLOAD_ALLOWED_TYPES: str = (
        "text/plain,text/csv,text/markdown,application/pdf,"
        "application/json,image/png,image/jpeg,image/gif,image/webp,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
        "application/msword,"
        "application/vnd.ms-excel,"
        "application/vnd.ms-powerpoint"
    )

    # --- Authentication (JWT) ---
    JWT_SECRET: str
    JWT_EXPIRES_IN: int = 3600
    JWT_REFRESH_EXPIRES_IN: int = 2_592_000

    # --- Encryption ---
    ENCRYPTION_KEY: str

    # --- DeepSeek Stabilizer ---
    DEEPSEEK_MAX_RETRIES: int = 3
    DEEPSEEK_STRIP_REASONING: bool = True
    DEEPSEEK_VALIDATE_TOOL_CALLS: bool = True
    DEEPSEEK_JSON_REPAIR: bool = True

    # --- Session ---
    TEMPORARY_SESSION_TTL_SECONDS: int = 86400  # 24 hours
    DEMO_SESSION_TTL_SECONDS: int = 3600  # 1 hour

    # --- Cross-session memory (Issue #229) ---
    CROSS_SESSION_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # --- Embedding API (RAG + cross-session memory) ---
    OPENAI_API_KEY: str = ""
    """API key for the embedding endpoint (OpenAI-compatible)."""
    EMBEDDING_API_URL: str = ""
    """Override embedding endpoint URL (default: https://api.openai.com/v1/embeddings)."""

    # --- Security ---
    COOKIE_SECURE: bool = False
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"
    LOGIN_RATE_LIMIT: str = "5/minute"
    SEED_ALLOW_WEAK_PASSWORD: bool = False

    # --- Embeddable Widget ---
    EMBED_GUEST_TOKEN_SECRET: str = ""

    # --- Widget Rate Limiting (Issue #349) ---
    WIDGET_CONFIG_LIMIT: str = "30/hour"
    """Per-IP rate limit for GET /widget/config/{token} (bootstrap)."""
    WIDGET_MESSAGE_LIMIT: str = "20/minute"
    """Per-guest message rate limit for POST /widget/session/message (short window)."""
    WIDGET_TOTAL_MESSAGE_LIMIT: str = "100/hour"
    """Per-guest total message rate limit for POST /widget/session/message (long window)."""
    WIDGET_SESSION_READ_LIMIT: str = "60/minute"
    """Per-guest read rate limit for GET /widget/session, /widget/session/messages,
    and DELETE /widget/session/stream."""

    # --- Logging ---
    LOG_LEVEL: str = "INFO"

    # --- Auto tool selection (Issue #287, #439) ---
    AUTO_SELECT_TOOLS_TOP_K: int = 8

    # --- Agent execution (Issue #317) ---
    AGENT_MAX_STEPS: int = 15
    """Maximum number of tool-call steps before the agent loop terminates.
    Prevents runaway agents that loop indefinitely on tool results."""

    # --- Autopilot (Issue #446) ---
    AUTOPILOT_MAX_TURNS: int = 20
    """Maximum number of agent-invocation turns before the autopilot
    controller forces a summary.  Protects against runaway autonomous
    sessions that don't call ``task_complete``."""
    AUTOPILOT_MAX_TOKENS: int = 0
    """Maximum cumulative tokens (input + output) before the autopilot
    stops.  ``0`` means no limit (default)."""

    # --- Parallel tool execution (Issue #447) ---
    AGENT_PARALLEL_TOOLS_ENABLED: bool = True
    """When True, injects system prompt guidance telling the LLM to batch
    independent tool calls into a single response.  The MAF framework
    already executes batched calls via asyncio.gather; this flag controls
    whether the agent is prompted to produce batched calls."""

    # --- OAuth (Issue #312) ---
    GOOGLE_CLIENT_ID: str = ""
    """Google OAuth client ID for Gmail/Calendar/Tasks access."""
    GOOGLE_CLIENT_SECRET: str = ""
    """Google OAuth client secret."""
    MS_CLIENT_ID: str = ""
    """Microsoft OAuth client ID for Outlook/Calendar/Tasks access."""
    MS_CLIENT_SECRET: str = ""
    """Microsoft OAuth client secret."""
    API_BASE_URL: str = "http://localhost:8000"
    """Public-facing base URL of the API (for OAuth callbacks)."""
    FRONTEND_URL: str = "http://localhost:3000"
    """Public-facing base URL of the frontend (for OAuth redirect)."""

    # --- Licensing (Issue #243) ---
    MAX_FREE_TENANTS: int = 3
    LICENSE_PUBLIC_KEY: str = ""

    # --- A2A (Agent-to-Agent) Protocol Server (Issue #404) ---
    A2A_SERVER_ENABLED: bool = False
    """Enable A2A server endpoint (/.well-known/agent-card.json)."""
    A2A_PUBLIC_URL: str = ""
    """Public-facing base URL for the Agent Card (e.g. https://api.example.com)."""
    A2A_ORGANIZATION_NAME: str = "PH Agent Hub"
    """Organization name shown in the Agent Card provider field."""
    A2A_ORGANIZATION_URL: str = ""
    """Organization URL shown in the Agent Card provider field."""
    A2A_DOCS_URL: str = ""
    """Documentation URL shown in the Agent Card."""

    # --- A2A Task lifecycle (Issue #411) ---
    A2A_TASK_TTL_SECONDS: int = 86400
    """Default TTL for completed/canceled A2A task records (24h)."""
    A2A_TASK_CANCEL_TTL_SECONDS: int = 120
    """TTL for Redis cancellation flags (2 min — agent should notice fast)."""

    # --- A2A Resilience defaults (Issue #409) ---
    # These are used as fallbacks when per-server config columns are null.
    A2A_DEFAULT_RETRY_MAX_ATTEMPTS: int = 3
    """Default max retry attempts for A2A transient errors."""
    A2A_DEFAULT_RETRY_BACKOFF_BASE_SECONDS: float = 1.0
    """Default exponential backoff base in seconds."""
    A2A_DEFAULT_RETRY_BACKOFF_MAX_SECONDS: float = 60.0
    """Default exponential backoff cap in seconds."""
    A2A_DEFAULT_TIMEOUT_CONNECT_SECONDS: float = 30.0
    """Default HTTP connect timeout in seconds."""
    A2A_DEFAULT_TIMEOUT_READ_SECONDS: float = 300.0
    """Default HTTP read timeout for non-streaming in seconds."""
    A2A_DEFAULT_TIMEOUT_STREAM_SECONDS: float = 600.0
    """Default HTTP read timeout for streaming in seconds."""
    A2A_DEFAULT_CIRCUIT_BREAKER_THRESHOLD: int = 5
    """Default consecutive failures to trip circuit breaker."""
    A2A_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS: int = 60
    """Default window to reset failure count."""
    A2A_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 300
    """Default cooldown before probe attempt."""

    model_config = {"env_file": ".env", "extra": "ignore"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.ENCRYPTION_KEY:
            raise ValueError(
                "ENCRYPTION_KEY is required but empty. "
                "Generate one with: python -c "
                "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )


settings = Settings()
