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

    # --- Logging ---
    LOG_LEVEL: str = "INFO"

    # --- Auto tool selection (Issue #287) ---
    AUTO_SELECT_TOOLS_TOP_K: int = 3

    # --- Agent execution (Issue #317) ---
    AGENT_MAX_STEPS: int = 15
    """Maximum number of tool-call steps before the agent loop terminates.
    Prevents runaway agents that loop indefinitely on tool results."""

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
