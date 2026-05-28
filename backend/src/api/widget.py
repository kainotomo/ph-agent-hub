# =============================================================================
# PH Agent Hub — Widget (Embeddable Chat) API Router
# =============================================================================
# Public endpoints for the embeddable chat widget.  These accept a raw
# guest token (from the website's <script> tag) or a short-lived guest
# JWT (obtained from the config endpoint) in place of user authentication.
# =============================================================================

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ..agents.runner import run_agent_stream
from ..core.dependencies import get_db, get_guest_context, GuestContext
from ..core.exceptions import ForbiddenError, NotFoundError
from ..core.jwt import create_guest_token
from ..core.redis import (
    get_temp_messages,
    get_temp_session,
    set_stream_cancel,
    store_temp_session,
)
from ..services.embed_service import get_embed_config_by_token
from ..core.redis import get_follow_up_questions as _get_follow_up_questions

router = APIRouter(prefix="/widget", tags=["widget"])


# =============================================================================
# Pydantic Schemas
# =============================================================================


class WidgetConfigResponse(BaseModel):
    """Response returned by the config endpoint.

    Contains theme and feature flags so the widget can self-configure,
    plus a short-lived guest JWT for subsequent API calls.
    """

    guest_token: str
    session_id: str
    theme: dict
    feature_flags: dict
    default_model_id: str | None = None
    default_skill_id: str | None = None
    default_template_id: str | None = None


class WidgetSessionResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    is_temporary: bool = True

    model_config = {"from_attributes": True}


class FollowUpQuestionsResponse(BaseModel):
    questions: list[str]


class WidgetMessageCreate(BaseModel):
    content: str
    file_ids: list[str] = []


class WidgetMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


# =============================================================================
# GET /widget/config/{token} — load embed config and bootstrap a session
# =============================================================================


@router.get("/config/{token}", response_model=WidgetConfigResponse)
async def get_widget_config(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Load an embed config by its guest token.

    This is the first call the widget makes.  It:
    1. Looks up the config by the raw token
    2. Creates a temporary Redis session for this visitor
    3. Returns theme, feature flags, and a short-lived guest JWT

    The guest JWT is used as the ``Authorization: Bearer`` header for
    all subsequent widget API calls.
    """
    config = await get_embed_config_by_token(db, token)
    if config is None:
        raise NotFoundError("Invalid guest token")
    if not config.is_active:
        raise ForbiddenError("Embed config is inactive")

    # Create a temporary Redis session for this visitor
    session_id = str(uuid.uuid4())
    guest_user_id = f"guest:{config.id}"
    now = datetime.now(timezone.utc)

    theme = config.theme or {}
    feature_flags = config.feature_flags or {}

    data = {
        "id": session_id,
        "tenant_id": config.tenant_id,
        "user_id": guest_user_id,
        "title": "Widget Chat",
        "is_temporary": True,
        "is_pinned": False,
        "selected_template_id": config.default_template_id,
        "selected_skill_id": config.default_skill_id,
        "selected_model_id": config.default_model_id,
        "thinking_enabled": False,
        "temperature": None,
        "active_tool_ids": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    await store_temp_session(session_id, data)

    # Issue a short-lived guest JWT scoped to this embed config + session
    guest_jwt = create_guest_token({
        "sub": config.id,
        "tenant_id": config.tenant_id,
        "session_id": session_id,
    })

    return WidgetConfigResponse(
        guest_token=guest_jwt,
        session_id=session_id,
        theme=theme,
        feature_flags=feature_flags,
        default_model_id=config.default_model_id,
        default_skill_id=config.default_skill_id,
        default_template_id=config.default_template_id,
    )


# =============================================================================
# GET /widget/session — get current session info
# =============================================================================


@router.get("/session", response_model=WidgetSessionResponse)
async def get_widget_session(
    ctx: GuestContext = Depends(get_guest_context),
):
    """Return the current widget session info.

    The session ID is extracted from the guest JWT.
    """
    session = await get_temp_session(ctx.session_id)
    if session is None:
        raise NotFoundError("Session not found or expired")

    return WidgetSessionResponse(
        id=session["id"],
        tenant_id=session["tenant_id"],
        title=session["title"],
        is_temporary=True,
    )


# =============================================================================
# GET /widget/session/messages — list session messages
# =============================================================================


@router.get("/session/messages", response_model=list[WidgetMessageResponse])
async def list_widget_messages(
    ctx: GuestContext = Depends(get_guest_context),
):
    """List messages in the current widget session."""
    messages = await get_temp_messages(ctx.session_id)

    def _extract_text(msg: dict) -> str:
        """Extract plain text from the message content.

        The runner stores content as ``[{"type": "text", "text": "..."}]``
        for Redis messages, matching the DB Message model format.
        """
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(parts)
        return str(content)

    return [
        WidgetMessageResponse(
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
# POST /widget/session/message — send a message (SSE streaming)
# =============================================================================


@router.post("/session/message")
async def send_widget_message(
    body: WidgetMessageCreate,
    request: Request,
    ctx: GuestContext = Depends(get_guest_context),
    db: AsyncSession = Depends(get_db),
):
    """Send a user message in the widget session and run the agent.

    When ``Accept: text/event-stream`` is sent, returns an SSE stream.
    Otherwise returns a JSON response.
    """
    session = await get_temp_session(ctx.session_id)
    if session is None:
        raise NotFoundError("Session not found or expired")

    # Check if the request wants SSE streaming
    accept = request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return EventSourceResponse(
            _stream_widget_response(
                db=db,
                session_data=session,
                message_content=body.content,
                file_ids=body.file_ids,
                session_id=ctx.session_id,
            )
        )

    # Fallback: run agent synchronously
    from ..agents.runner import run_agent

    result_text, result_msg_id = await run_agent(
        db=db,
        session_data=session,
        user_message=body.content,
        file_ids=body.file_ids,
        current_user=None,
    )

    return {"message_id": result_msg_id, "content": result_text}


# ---------------------------------------------------------------------------
# SSE streaming generator
# ---------------------------------------------------------------------------


async def _stream_widget_response(
    db: AsyncSession,
    session_data: dict,
    message_content: str,
    file_ids: list[str],
    session_id: str,
):
    """Stream agent tokens via SSE for the widget."""
    assistant_message_id = str(uuid.uuid4())
    stream = run_agent_stream(
        session_data=session_data,
        user_message=message_content,
        db=db,
        current_user=None,
        message_id=assistant_message_id,
        file_ids=file_ids,
    )

    async for event_dict in stream:
        # Swallow httpx ContextVar cleanup errors at stream end
        if (
            event_dict.get("event") == "error"
            and "inner_response_telemetry_captured_fields"
            in str(event_dict.get("data", ""))
        ):
            logger.warning(
                "Swallowing httpx ContextVar error — tokens already delivered"
            )
            yield {"event": "message_complete", "data": "{}"}
            return
        yield event_dict


# =============================================================================
# DELETE /widget/session/stream — stop generation
# =============================================================================


@router.delete("/session/stream")
async def stop_widget_stream(
    ctx: GuestContext = Depends(get_guest_context),
):
    """Stop the active stream for the current widget session."""
    await set_stream_cancel(ctx.session_id)
    return {"status": "ok"}


# =============================================================================
# GET /widget/session/follow-up-questions — retrieve follow-up questions
# =============================================================================


@router.get(
    "/session/follow-up-questions",
    response_model=FollowUpQuestionsResponse,
)
async def get_widget_follow_up_questions(
    ctx: GuestContext = Depends(get_guest_context),
):
    """Retrieve follow-up questions for the widget session from Redis.

    Questions are generated asynchronously by a background task after
    the message response completes.  Polled once by the frontend when
    the stream finishes.
    """
    questions = await _get_follow_up_questions(ctx.session_id)
    return FollowUpQuestionsResponse(questions=questions or [])
