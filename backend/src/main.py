# =============================================================================
# PH Agent Hub — Backend Entry Point
# =============================================================================
# Phase 1: FastAPI app with core utilities, ORM models, storage module,
# and API router stubs wired in.
# =============================================================================

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.admin import router as admin_router
from .api.auth import router as auth_router
from .api.chat import router as chat_router
from .api.memory import router as memory_router
from .api.models import router as models_router
from .api.prompts import router as prompts_router
from .api.skills import router as skills_router
from .api.templates import router as templates_router
from .api.users import router as users_router
from .core.config import settings
from .core.exceptions import AppException, app_exception_handler
from .core.limiter import limiter, RateLimitExceeded


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown hooks
# ---------------------------------------------------------------------------


async def _cleanup_orphaned_temp_uploads() -> None:
    """Periodic background task: delete file uploads for expired temp sessions."""
    from .db.base import AsyncSessionLocal
    from .services.upload_service import delete_orphaned_temp_uploads

    interval = settings.TEMPORARY_SESSION_TTL_SECONDS  # same cadence as TTL
    while True:
        await asyncio.sleep(interval)
        try:
            async with AsyncSessionLocal() as db:
                await delete_orphaned_temp_uploads(db)
        except Exception:
            pass  # Best-effort: never let a cleanup failure crash the task


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: scan MAF registry, load agent identity, start cleanup task."""
    # Configure root logger so INFO/DEBUG logs from all modules
    # appear alongside uvicorn's own logging output.
    log_level = os.environ.get("LOG_LEVEL", settings.LOG_LEVEL).upper()
    level = getattr(logging, log_level, logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)

    from .agents.registry import startup_scan
    from .agents.runner import load_agent_identity
    from .db.base import AsyncSessionLocal

    load_agent_identity()

    async with AsyncSessionLocal() as db:
        await startup_scan(db)

    # Start background cleanup for orphaned temp uploads
    task = asyncio.create_task(_cleanup_orphaned_temp_uploads())

    yield

    task.cancel()


app = FastAPI(title="PH Agent Hub", version="1.11.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
app.state.limiter = limiter

# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RateLimitExceeded, lambda req, exc: __import__("starlette.responses", fromlist=[""]).JSONResponse(
    status_code=429,
    content={"detail": "Too many requests. Please try again later."},
))

# ---------------------------------------------------------------------------
# API Routers
# ---------------------------------------------------------------------------
app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(prompts_router, prefix="/api")
app.include_router(skills_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
