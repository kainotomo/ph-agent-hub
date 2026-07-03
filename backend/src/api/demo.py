# =============================================================================
# PH Agent Hub — Demo (Anonymous) API Router
# =============================================================================
# Public endpoints for the "Try It Now" demo experience.
# No authentication required — uses anonymous guest JWTs scoped to the
# configured demo tenant.
# =============================================================================

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse


class FollowUpQuestionsResponse(BaseModel):
    questions: list[str]

from ..agents.runner import run_agent
from ..core.config import settings
from ..core.dependencies import get_db, get_demo_context, DemoContext
from ..core.exceptions import NotFoundError, ServiceUnavailableError, ValidationError
from ..core.jwt import create_demo_token
from ..core.limiter import limiter
from ..core.redis import (
    get_temp_messages,
    get_temp_session,
    set_stream_cancel,
    store_temp_session,
)
from ..services.settings_service import get_setting, get_all_settings
from ..services.tenant_service import get_demo_tenant
from ..services.upload_service import create_upload

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])


# =============================================================================
# Pydantic Schemas
# =============================================================================


class DemoStatusResponse(BaseModel):
    """Public status — tells the frontend whether demo mode is enabled."""

    enabled: bool


class DemoConfigResponse(BaseModel):
    """Response from creating a new demo session."""

    guest_token: str
    session_id: str
    theme: dict = {}
    feature_flags: dict = {}
    default_model_id: str | None = None
    default_skill_id: str | None = None
    default_template_id: str | None = None


class DemoSessionResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    is_temporary: bool = True

    model_config = {"from_attributes": True}


class DemoMessageCreate(BaseModel):
    content: str
    file_ids: list[str] = []


class DemoMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FileUploadResponse(BaseModel):
    file_id: str
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    embedding_warning: str | None = None
    """If set, the embedding API was unavailable and TF-IDF fallback was used."""


# ---------------------------------------------------------------------------
# Helper: check if demo mode is enabled via app setting
# ---------------------------------------------------------------------------


async def _is_demo_enabled(db: AsyncSession) -> bool:
    """Check the ``demo_enabled`` app setting."""
    val = await get_setting(db, "demo_enabled", "false")
    return val == "true" or val == "1"


async def _assert_demo_enabled(db: AsyncSession) -> None:
    """Raise if demo mode is disabled."""
    if not await _is_demo_enabled(db):
        raise ServiceUnavailableError("Demo mode is not enabled")


# ---------------------------------------------------------------------------
# Rate limiting keys
# ---------------------------------------------------------------------------

DEMO_SESSION_LIMIT = "10/hour"
DEMO_MESSAGE_LIMIT = "20/minute"
DEMO_TOTAL_MESSAGE_LIMIT = "50/hour"


# =============================================================================
# GET /demo/status — public status check
# =============================================================================


@router.get("/status", response_model=DemoStatusResponse)
async def get_demo_status(
    db: AsyncSession = Depends(get_db),
):
    """Return whether the demo mode is enabled.

    Used by the frontend to decide whether to show the "Try It Now"
    button on the login page.  No authentication required.
    """
    enabled = await _is_demo_enabled(db)
    return DemoStatusResponse(enabled=enabled)


# =============================================================================
# POST /demo/session — create a new demo session
# =============================================================================


@router.post("/session", response_model=DemoConfigResponse)
@limiter.limit(DEMO_SESSION_LIMIT)
async def create_demo_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create an anonymous demo session under the demo tenant.

    This is the first endpoint the demo page calls.  It:
    1. Checks that demo mode is enabled
    2. Looks up the demo tenant
    3. Creates a temporary Redis session with 1-hour TTL
    4. Returns a short-lived demo guest JWT for subsequent calls

    Rate limited to 10 requests per hour per IP.
    """
    await _assert_demo_enabled(db)

    tenant = await get_demo_tenant(db)
    if tenant is None:
        raise ServiceUnavailableError(
            "Demo mode is enabled but no demo tenant is configured. "
            "An admin must mark a tenant as the demo tenant first."
        )

    session_id = str(uuid.uuid4())
    guest_user_id = f"demo:{tenant.id}"
    now = datetime.now(timezone.utc)

    # Read demo feature flags from app settings
    feature_flags_raw = await get_setting(db, "demo_feature_flags", "{}")
    try:
        feature_flags = json.loads(feature_flags_raw)
    except (json.JSONDecodeError, TypeError):
        feature_flags = {}
    if not isinstance(feature_flags, dict):
        feature_flags = {}

    # Disable follow-up questions by default in demo mode
    feature_flags["follow_up_questions"] = False

    data = {
        "id": session_id,
        "tenant_id": tenant.id,
        "user_id": guest_user_id,
        "title": "Demo Chat",
        "is_temporary": True,
        "is_pinned": False,
        "selected_template_id": None,
        "selected_skill_id": None,
        "selected_model_id": None,
        "thinking_enabled": False,
        "temperature": None,
        "active_tool_ids": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    await store_temp_session(session_id, data, ttl=settings.DEMO_SESSION_TTL_SECONDS)

    guest_jwt = create_demo_token({
        "sub": tenant.id,
        "session_id": session_id,
    })

    logger.info(
        "Demo session created: %s under tenant %s (TTL=%ds)",
        session_id,
        tenant.id,
        settings.DEMO_SESSION_TTL_SECONDS,
    )

    return DemoConfigResponse(
        guest_token=guest_jwt,
        session_id=session_id,
        feature_flags=feature_flags,
    )


# =============================================================================
# GET /demo/session — get current demo session info
# =============================================================================


@router.get("/session", response_model=DemoSessionResponse)
async def get_demo_session(
    ctx: DemoContext = Depends(get_demo_context),
    db: AsyncSession = Depends(get_db),
):
    """Return the current demo session info.

    The session ID is extracted from the demo guest JWT.
    """
    await _assert_demo_enabled(db)

    session = await get_temp_session(ctx.session_id)
    if session is None:
        raise NotFoundError("Session not found or expired")

    return DemoSessionResponse(
        id=session["id"],
        tenant_id=session["tenant_id"],
        title=session["title"],
        is_temporary=True,
    )


# =============================================================================
# GET /demo/session/messages — list session messages
# =============================================================================


@router.get("/session/messages", response_model=list[DemoMessageResponse])
async def list_demo_messages(
    ctx: DemoContext = Depends(get_demo_context),
    db: AsyncSession = Depends(get_db),
):
    """List messages in the current demo session."""
    await _assert_demo_enabled(db)

    messages = await get_temp_messages(ctx.session_id)

    def _extract_text(msg: dict) -> str:
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(parts)
        return str(content)

    return [
        DemoMessageResponse(
            id=m.get("id", ""),
            session_id=m.get("session_id", ctx.session_id),
            role=m.get("sender", "user"),
            content=_extract_text(m),
            created_at=datetime.fromisoformat(
                m.get("created_at", datetime.now(timezone.utc).isoformat())
            ),
        )
        for m in messages
    ]


# =============================================================================
# POST /demo/session/message — send a message (SSE streaming)
# =============================================================================


@router.post("/session/message")
@limiter.limit(DEMO_MESSAGE_LIMIT)
@limiter.limit(DEMO_TOTAL_MESSAGE_LIMIT)
async def send_demo_message(
    body: DemoMessageCreate,
    request: Request,
    ctx: DemoContext = Depends(get_demo_context),
    db: AsyncSession = Depends(get_db),
):
    """Send a user message in the demo session and run the agent.

    Supports SSE streaming when ``Accept: text/event-stream`` is sent.
    Rate limited to 30 requests per minute per session.
    """
    await _assert_demo_enabled(db)

    session = await get_temp_session(ctx.session_id)
    if session is None:
        raise NotFoundError("Session not found or expired")

    accept = request.headers.get("accept", "")
    # Don't use SSE for demo — return a regular JSON response instead.
    # The frontend handles token-by-token display for authenticated chat,
    # but for the demo, a single-response is simpler and avoids fetchEventSource issues.
    result_text, result_msg_id = await run_agent(
        db=db,
        session_data=session,
        user_message=body.content,
        file_ids=body.file_ids,
        current_user=None,
    )

    return {"message_id": result_msg_id, "content": result_text}


async def _stream_demo_response(
    db: AsyncSession,
    session_data: dict,
    message_content: str,
    file_ids: list[str],
    session_id: str,
):
    """Stream an agent response for a demo session.

    Uses real SSE streaming with StreamingResponse.  The known
    httpx ContextVar error at stream end is intercepted and
    converted to message_complete so the frontend gets a
    clean finish.
    """
    try:
        async for event in run_agent_stream(
            db=db,
            session_data=session_data,
            user_message=message_content,
            file_ids=file_ids,
            current_user=None,
        ):
            # Convert cleanup ContextVar errors into message_complete
            if event.get("event") == "error" and "inner_response_telemetry_captured_fields" in str(event.get("data", "")):
                logger.warning("Swallowing httpx ContextVar error — tokens already delivered")
                yield {
                    "event": "message_complete",
                    "data": "{}",
                }
                return
            yield event
    except Exception as exc:
        logger.warning("Stream error (likely context cleanup): %s", exc)
        yield {
            "event": "message_complete",
            "data": "{}",
        }


async def _is_stream_cancelled(session_id: str) -> bool:
    """Check if the stream was cancelled via DELETE."""
    from ..core.redis import get_redis

    r = await get_redis()
    key = f"stream_cancel:{session_id}"
    result = await r.get(key)
    return result is not None


# =============================================================================
# POST /demo/session/upload — upload a file
# =============================================================================


@router.post(
    "/session/upload",
    response_model=FileUploadResponse,
    status_code=201,
)
async def upload_demo_file(
    file: UploadFile = File(...),
    ctx: DemoContext = Depends(get_demo_context),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file to the demo session (stored in MinIO).

    Files are stored as temporary uploads and cleaned up when the
    session expires or the periodic cleanup task runs.
    """
    await _assert_demo_enabled(db)

    session = await get_temp_session(ctx.session_id)
    if session is None:
        raise NotFoundError("Session not found or expired")

    if not file.filename:
        raise ValidationError("File must have a filename")

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"

    upload = await create_upload(
        db=db,
        session_data=session,
        current_user=None,
        file_bytes=file_bytes,
        original_filename=file.filename,
        content_type=content_type,
    )

    # Check whether the embedding API has credentials configured
    from ..tools.rag_search import _check_embedding_available

    embed_ok, embed_reason = _check_embedding_available()

    return FileUploadResponse(
        file_id=upload.id,
        original_filename=upload.original_filename,
        content_type=upload.content_type,
        size_bytes=upload.size_bytes,
        created_at=upload.created_at,
        embedding_warning=embed_reason if not embed_ok else None,
    )


# =============================================================================
# DELETE /demo/session/stream — cancel an active stream
# =============================================================================


@router.delete("/session/stream", status_code=204)
async def cancel_demo_stream(
    ctx: DemoContext = Depends(get_demo_context),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an active stream for the demo session."""
    await _assert_demo_enabled(db)
    await set_stream_cancel(ctx.session_id)


# =============================================================================
# GET /demo/session/follow-up-questions — retrieve follow-up questions
# =============================================================================


@router.get(
    "/session/follow-up-questions",
    response_model=FollowUpQuestionsResponse,
)
async def get_demo_follow_up_questions(
    ctx: DemoContext = Depends(get_demo_context),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve follow-up questions for the demo session from Redis.

    Questions are generated asynchronously by a background task after
    the message response completes.  Polled once by the frontend when
    the stream finishes.
    """
    await _assert_demo_enabled(db)
    from ..core.redis import get_follow_up_questions as _redis_get_follow_up

    questions = await _redis_get_follow_up(ctx.session_id)
    return FollowUpQuestionsResponse(questions=questions or [])
