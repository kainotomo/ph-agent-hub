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
from fastapi.middleware.gzip import GZipMiddleware

from .api.admin import router as admin_router
from .api.a2a_oauth import router as a2a_oauth_router
from .api.auth import router as auth_router
from .api.background_tasks import router as background_tasks_router
from .api.chat import router as chat_router
from .api.credentials import router as credentials_router
from .api.demo import router as demo_router
from .api.memory import router as memory_router
from .api.models import router as models_router
from .api.notifications import router as notifications_router
from .api.prompts import router as prompts_router
from .api.scheduled_tasks import router as scheduled_tasks_router
from .api.skills import router as skills_router
from .api.templates import router as templates_router
from .api.users import router as users_router
from .api.widget import router as widget_router
from .core.config import settings

# Conditionally import A2A server router (requires a2a-sdk)
if settings.A2A_SERVER_ENABLED:
    try:
        from .api.a2a_server import router as a2a_server_router
    except ImportError:
        a2a_server_router = None
        import logging
        logging.getLogger(__name__).warning(
            "A2A_SERVER_ENABLED=True but a2a-sdk is not installed."
        )
else:
    a2a_server_router = None
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


async def _cleanup_demo_temp_uploads() -> None:
    """Periodic background task: delete temp file uploads for the demo tenant.

    Runs every 6 hours to keep demo data from accumulating in MinIO and the
    DB.  This is more frequent than the general orphan cleanup (24h) because
    demo data is ephemeral and can build up quickly with anonymous usage.
    """
    from .db.base import AsyncSessionLocal
    from .services.upload_service import delete_demo_temp_uploads

    DEMO_CLEANUP_INTERVAL = 6 * 3600  # 6 hours
    while True:
        await asyncio.sleep(DEMO_CLEANUP_INTERVAL)
        try:
            async with AsyncSessionLocal() as db:
                await delete_demo_temp_uploads(db)
        except Exception:
            pass  # Best-effort: never let a cleanup failure crash the task


async def _timeout_background_tasks() -> None:
    """Periodic background task: auto-cancel background tasks that have
    exceeded the configured timeout (Issue #449).

    Checks every 5 minutes for EXECUTING background tasks whose
    ``created_at`` is older than ``BACKGROUND_TASK_TIMEOUT_SECONDS``.
    """
    from datetime import datetime, timezone, timedelta
    from .db.base import AsyncSessionLocal
    from .services.autopilot_service import get_executing_runs

    CHECK_INTERVAL = 300  # 5 minutes
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            timeout = settings.BACKGROUND_TASK_TIMEOUT_SECONDS
            if timeout <= 0:
                continue  # disabled
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout)
            async with AsyncSessionLocal() as _db:
                runs = await get_executing_runs(_db)
                for run in runs:
                    if run.created_at and run.created_at < cutoff:
                        run.state = run.STATE_FAILED
                        run.error_message = (
                            f"Task timed out after {timeout}s"
                        )
                        import logging as _lg
                        _lg.getLogger(__name__).info(
                            "Timed out background task %s (session %s, "
                            "created %s, timeout=%ds)",
                            run.id, run.session_id,
                            run.created_at, timeout,
                        )
                if runs:
                    await _db.commit()
        except Exception:
            pass  # Best-effort


async def _run_scheduler_loop() -> None:
    """Periodic background task: poll for due scheduled tasks and execute
    them (Issue #297 — Scheduled & Recurring Agent Tasks).

    Runs every ``SCHEDULER_POLL_INTERVAL_SECONDS`` (default 30s).
    Sets ``next_run_at = None`` immediately on retrieval to prevent
    double-execution if execution takes longer than the poll interval.
    """
    from .db.base import AsyncSessionLocal
    from .services.scheduled_task_service import get_due_tasks as _get_due
    from .services.scheduled_task_service import update_scheduled_task as _update_task

    interval = settings.SCHEDULER_POLL_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(interval)
        try:
            async with AsyncSessionLocal() as _db:
                due_tasks = await _get_due(_db)
                if not due_tasks:
                    continue

                for task in due_tasks:
                    task_id = task.id
                    # Dedup guard: clear next_run_at before spawning so
                    # the next poll does NOT pick up the same task.
                    # If the execution succeeds, record_run_result will
                    # recompute the next_run_at.
                    task.next_run_at = None
                    await _db.commit()

                    # Spawn execution — don't block the poll loop.
                    asyncio.create_task(
                        _execute_scheduled_task_wrapper(task_id)
                    )
        except Exception:
            import logging as _lg
            _lg.getLogger(__name__).exception(
                "Error in scheduler polling loop"
            )


async def _execute_scheduled_task_wrapper(task_id: str) -> None:
    """Wrapper that re-fetches the ScheduledTask and executes it.

    Re-fetched within a fresh DB session because the original ORM
    object may be stale or detached.
    """
    from .db.base import AsyncSessionLocal
    from .db.orm.scheduled_tasks import ScheduledTask
    from .services.scheduler_executor import execute_scheduled_task

    try:
        async with AsyncSessionLocal() as _fetch_db:
            task = await _fetch_db.get(ScheduledTask, task_id)
            if task is None:
                return
            # Re-check: state may have changed between poll and execution
            if task.state != ScheduledTask.STATE_ACTIVE:
                return
        await execute_scheduled_task(task)
    except Exception:
        import logging as _lg
        _lg.getLogger(__name__).exception(
            "Fatal error in scheduled task executor for task %s",
            task_id,
        )


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

    # Mark stale autopilot runs as FAILED (server restarted mid-execution)
    try:
        from .services.autopilot_service import fail_stale_runs as _ap_fail_stale
        async with AsyncSessionLocal() as ap_db:
            stale = await _ap_fail_stale(ap_db)
            if stale:
                logger.info(
                    "Marked %d stale autopilot run(s) as FAILED on startup",
                    len(stale),
                )
    except Exception:
        logger.warning("Failed to clean up stale autopilot runs on startup")

    # Start background cleanup for orphaned temp uploads
    orphan_cleanup_task = asyncio.create_task(_cleanup_orphaned_temp_uploads())

    # Start background cleanup for demo tenant temp uploads (every 6 hours)
    demo_cleanup_task = asyncio.create_task(_cleanup_demo_temp_uploads())

    # Start background task timeout cleanup (Issue #449)
    _bg_timeout_task = asyncio.create_task(_timeout_background_tasks())

    # Start scheduler polling loop (Issue #297) — skip in test mode
    if not settings.TESTING:
        _scheduler_task = asyncio.create_task(_run_scheduler_loop())
    else:
        _scheduler_task = None
        logger.info("Scheduler disabled (TESTING=True)")

    yield

    orphan_cleanup_task.cancel()
    demo_cleanup_task.cancel()
    _bg_timeout_task.cancel()
    if _scheduler_task is not None:
        _scheduler_task.cancel()


app = FastAPI(title="PH Agent Hub", version="2.2.0", lifespan=lifespan)

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
app.add_middleware(GZipMiddleware, minimum_size=1000)

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
# A2A server router (/.well-known/agent-card.json, /message:send, etc.)
# mounted before /api routes so well-known path takes priority.
if settings.A2A_SERVER_ENABLED and a2a_server_router is not None:
    app.include_router(a2a_server_router)

app.include_router(auth_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(a2a_oauth_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(prompts_router, prefix="/api")
app.include_router(skills_router, prefix="/api")
app.include_router(widget_router, prefix="/api")
app.include_router(credentials_router, prefix="/api")
app.include_router(demo_router, prefix="/api")
app.include_router(background_tasks_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(scheduled_tasks_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
