# =============================================================================
# PH Agent Hub — Chat API Router
# =============================================================================
# Session CRUD (permanent via MariaDB + temporary via Redis), message
# sending (agent run), and session-level tool activation.
# =============================================================================

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from sqlalchemy.orm import selectinload

from ..agents.runner import (
    _msg_get,
    run_agent,
    run_agent_stream,
)
from ..core.config import settings
from ..core.dependencies import get_current_user, get_db
from ..core.exceptions import (
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from ..core.redis import (
    append_temp_message,
    clear_stream_cancel,
    delete_temp_session,
    get_temp_messages,
    get_temp_session,
    set_stream_cancel,
    store_temp_session,
)
from ..db.orm.messages import Message, MessageFeedback
from ..db.orm.sessions import Session
from ..db.orm.tools import Tool
from ..db.orm.user_tool_preferences import UserToolPreference
from ..db.orm.users import User as UserORM
from ..services import session_service, upload_service
from ..storage import s3

router = APIRouter(prefix="/chat", tags=["chat"])

# =============================================================================
# Pydantic Schemas
# =============================================================================


class SessionCreate(BaseModel):
    title: str = "New Chat"
    is_temporary: bool = False
    is_pinned: bool = False
    selected_template_id: str | None = None
    selected_skill_id: str | None = None
    selected_model_id: str | None = None
    active_tool_ids: list[str] | None = None
    thinking_enabled: bool | None = None
    temperature: float | None = None


class SessionUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None
    selected_template_id: str | None = None
    selected_skill_id: str | None = None
    selected_model_id: str | None = None
    thinking_enabled: bool | None = None
    temperature: float | None = None
    cross_session_retrieval_enabled: bool | None = None


class TagResponse(BaseModel):
    id: str
    name: str
    color: str | None = None

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    title: str
    is_temporary: bool
    is_pinned: bool
    selected_template_id: str | None
    selected_skill_id: str | None
    selected_model_id: str | None
    thinking_enabled: bool | None
    temperature: float | None
    cross_session_retrieval_enabled: bool | None = None
    tags: list[TagResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    content: str
    file_ids: list[str] | None = None
    temperature: float | None = None


class MessageResponse(BaseModel):
    id: str
    session_id: str
    sender: str
    content: list | None
    model_id: str | None
    model_name: str | None = None
    model_provider: str | None = None
    tool_calls: list | None
    tokens_in: int | None = None
    tokens_out: int | None = None
    is_deleted: bool
    summarized: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SendMessageResponse(BaseModel):
    message_id: str
    content: str
    model_id: str | None


class ToolResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    type: str
    category: str
    config: dict | None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AssistantMessageUpdate(BaseModel):
    """Payload for PATCH editing an assistant message in-place."""
    content: str


class FeedbackCreate(BaseModel):
    rating: str
    comment: str | None = None


class FeedbackResponse(BaseModel):
    id: str
    message_id: str
    user_id: str
    rating: str
    comment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class FileUploadResponse(BaseModel):
    file_id: str
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


# =============================================================================
# Internal helpers
# =============================================================================


def _session_to_dict(session: Session) -> dict[str, Any]:
    """Convert a Session ORM object to a plain dict for runner consumption."""
    return {
        "id": session.id,
        "tenant_id": session.tenant_id,
        "user_id": session.user_id,
        "title": session.title,
        "is_temporary": session.is_temporary,
        "is_pinned": session.is_pinned,
        "selected_template_id": session.selected_template_id,
        "selected_skill_id": session.selected_skill_id,
        "selected_model_id": session.selected_model_id,
        "thinking_enabled": session.thinking_enabled,
        "temperature": session.temperature,
        "cross_session_retrieval_enabled": session.cross_session_retrieval_enabled,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


async def _load_session(
    db: AsyncSession, session_id: str
) -> dict[str, Any]:
    """Load a session from MariaDB or Redis, returning a unified dict.

    Raises NotFoundError if neither source has the session.
    """
    # Try DB first
    session = await session_service.get_session_by_id(db, session_id)
    if session is not None:
        return _session_to_dict(session)

    # Try Redis
    temp = await get_temp_session(session_id)
    if temp is not None:
        return temp

    raise NotFoundError("Session not found")


async def _require_session_owner(
    session_data: dict,
    current_user: UserORM,
) -> None:
    """Raise ForbiddenError if the session doesn't belong to the current user."""
    if session_data.get("user_id") != current_user.id:
        raise ForbiddenError("You do not own this session")
    if session_data.get("tenant_id") != current_user.tenant_id:
        raise ForbiddenError("Session belongs to a different tenant")


async def _truncate_after_message(
    db: AsyncSession,
    session_id: str,
    message: Message,
) -> int:
    """Hard-delete all messages in the session after *message*.

    Deletes feedback rows, file uploads, and the messages themselves.
    Returns the count of messages deleted (excludes the target).
    Does NOT commit — the caller is responsible for committing.
    """
    result = await db.execute(
        select(Message)
        .where(
            Message.session_id == session_id,
            Message.is_deleted == False,  # noqa: E712
            Message.created_at > message.created_at,
        )
        .order_by(Message.created_at)
    )
    subsequent = list(result.scalars().all())

    for msg in subsequent:
        # Delete feedback rows (FK constraint)
        await db.execute(
            delete(MessageFeedback).where(
                MessageFeedback.message_id == msg.id
            )
        )
        # Delete file uploads (MinIO + DB rows)
        await upload_service.delete_uploads_for_message(db, msg.id)
        # Hard-delete the message
        await db.delete(msg)

    return len(subsequent)


async def _inject_file_content(
    db: AsyncSession,
    file_ids: list[str],
    user_message: str,
    session_data: dict,
    current_user: UserORM,
) -> tuple[str, list[str]]:
    """Validate file attachments and return (user_message, valid_file_ids).

    - Validates DeepSeek + image → 422
    - Returns the user message UNCHANGED (no text injection — file
      discovery is handled by the built-in ``list_uploaded_files`` tool)
    - Returns valid_file_ids for persistence / ERPNext upload_file tool
    """
    if not file_ids:
        return user_message, []

    # Load FileUpload rows
    from ..db.orm.file_uploads import FileUpload as FileUploadORM

    result = await db.execute(
        select(FileUploadORM).where(
            FileUploadORM.id.in_(file_ids),
            FileUploadORM.user_id == current_user.id,
        )
    )
    uploads = list(result.scalars().all())

    if not uploads:
        return user_message, []

    # Resolve model for provider check
    model_id = session_data.get("selected_model_id")
    model_orm = None
    if model_id:
        from ..db.orm.models import Model as ModelORM

        model_result = await db.execute(
            select(ModelORM).where(ModelORM.id == model_id)
        )
        model_orm = model_result.scalar_one_or_none()

    provider = getattr(model_orm, "provider", "") if model_orm else ""

    IMAGE_TYPES = frozenset({
        "image/png", "image/jpeg", "image/gif", "image/webp",
    })

    valid_ids: list[str] = []
    for upload in uploads:
        is_image = upload.content_type in IMAGE_TYPES

        if is_image and provider == "deepseek":
            raise ValidationError(
                "This model does not support image attachments. "
                "Please use an OpenAI or Anthropic model for images."
            )

        valid_ids.append(upload.id)

    # Return the user message unchanged — file metadata is available
    # to the agent via the built-in list_uploaded_files tool.
    return user_message, valid_ids


# =============================================================================
# Session CRUD
# =============================================================================


@router.post("/session", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Create a permanent (DB) or temporary (Redis) session.

    If ``active_tool_ids`` is not provided, auto-activates tools that the
    user has marked as "always on" in their preferences.
    """
    # Resolve active tool IDs: explicit list > always-on + skill tools > empty
    active_tool_ids = body.active_tool_ids
    if active_tool_ids is None:
        pref_result = await db.execute(
            select(UserToolPreference.tool_id).where(
                UserToolPreference.user_id == current_user.id,
                UserToolPreference.always_on == True,  # noqa: E712
            )
        )
        always_on_ids = {row[0] for row in pref_result.all()}

        # Include skill's tools if a skill is selected
        skill_tool_ids: set[str] = set()
        if body.selected_skill_id:
            from ..db.orm.skills import SkillAllowedTool

            result = await db.execute(
                select(SkillAllowedTool.tool_id).where(
                    SkillAllowedTool.skill_id == body.selected_skill_id
                )
            )
            skill_tool_ids = {row[0] for row in result.all()}

        active_tool_ids = list(always_on_ids | skill_tool_ids)

    if body.is_temporary:
        # Create in Redis
        session_id = str(uuid.uuid4())
        data: dict[str, Any] = {
            "id": session_id,
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "title": body.title,
            "is_temporary": True,
            "is_pinned": body.is_pinned,
            "selected_template_id": body.selected_template_id,
            "selected_skill_id": body.selected_skill_id,
            "selected_model_id": body.selected_model_id,
            "thinking_enabled": body.thinking_enabled,
            "temperature": body.temperature,
            "active_tool_ids": active_tool_ids,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await store_temp_session(session_id, data)

        # Return a SessionResponse-compatible dict
        return {
            "id": session_id,
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "title": body.title,
            "is_temporary": True,
            "is_pinned": body.is_pinned,
            "selected_template_id": body.selected_template_id,
            "selected_skill_id": body.selected_skill_id,
            "selected_model_id": body.selected_model_id,
            "thinking_enabled": body.thinking_enabled,
            "temperature": body.temperature,
            "tags": [],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    else:
        # Create in MariaDB
        session = await session_service.create_session(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            title=body.title,
            is_temporary=False,
            is_pinned=body.is_pinned,
            selected_template_id=body.selected_template_id,
            selected_skill_id=body.selected_skill_id,
            selected_model_id=body.selected_model_id,
            thinking_enabled=body.thinking_enabled,
            temperature=body.temperature,
        )

        # Auto-activate always-on tools for permanent session
        for tool_id in active_tool_ids:
            try:
                await session_service.add_session_tool(
                    db=db,
                    session_id=session.id,
                    tool_id=tool_id,
                    tenant_id=current_user.tenant_id,
                )
            except Exception:
                logger.debug(
                    "Could not auto-activate always-on tool %s for session %s",
                    tool_id,
                    session.id,
                )

        return SessionResponse.model_validate(session)


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List the current user's permanent sessions (temp sessions excluded)."""
    sessions = await session_service.list_sessions_for_user(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )
    return [SessionResponse.model_validate(s) for s in sessions]


@router.get("/session/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Get a session by ID (from DB or Redis)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    return {
        "id": data["id"],
        "tenant_id": data["tenant_id"],
        "user_id": data["user_id"],
        "title": data["title"],
        "is_temporary": data.get("is_temporary", False),
        "is_pinned": data.get("is_pinned", False),
        "selected_template_id": data.get("selected_template_id"),
        "selected_skill_id": data.get("selected_skill_id"),
        "selected_model_id": data.get("selected_model_id"),
        "thinking_enabled": data.get("thinking_enabled"),
        "temperature": data.get("temperature"),
        "cross_session_retrieval_enabled": data.get("cross_session_retrieval_enabled"),
        "tags": data.get("tags", []),
        "created_at": _parse_datetime(data.get("created_at")),
        "updated_at": _parse_datetime(data.get("updated_at")),
    }


@router.put("/session/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    body: SessionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Update a session's fields (DB or Redis)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    is_temp = data.get("is_temporary", False)
    update_fields = body.model_dump(exclude_unset=True)

    # Detect skill change
    old_skill_id = data.get("selected_skill_id")
    skill_changed = "selected_skill_id" in update_fields
    new_skill_id = update_fields.get("selected_skill_id") if skill_changed else None

    if is_temp:
        # Update Redis blob
        data.update(update_fields)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Sync skill tools in Redis blob
        if skill_changed:
            pref_result = await db.execute(
                select(UserToolPreference.tool_id).where(
                    UserToolPreference.user_id == current_user.id,
                    UserToolPreference.always_on == True,  # noqa: E712
                )
            )
            always_on_ids = {row[0] for row in pref_result.all()}

            # Fetch old skill tool IDs
            from ..db.orm.skills import SkillAllowedTool

            old_tool_ids: set[str] = set()
            if old_skill_id:
                result = await db.execute(
                    select(SkillAllowedTool.tool_id).where(
                        SkillAllowedTool.skill_id == old_skill_id
                    )
                )
                old_tool_ids = {row[0] for row in result.all()}

            # Fetch new skill tool IDs
            new_tool_ids: set[str] = set()
            if new_skill_id:
                result = await db.execute(
                    select(SkillAllowedTool.tool_id).where(
                        SkillAllowedTool.skill_id == new_skill_id
                    )
                )
                new_tool_ids = {row[0] for row in result.all()}

            current_active = set(data.get("active_tool_ids", []))
            to_remove = old_tool_ids - new_tool_ids - always_on_ids
            to_add = new_tool_ids - current_active
            data["active_tool_ids"] = list((current_active - to_remove) | to_add)

        await store_temp_session(session_id, data)

        return {
            "id": data["id"],
            "tenant_id": data["tenant_id"],
            "user_id": data["user_id"],
            "title": data["title"],
            "is_temporary": True,
            "is_pinned": data.get("is_pinned", False),
            "selected_template_id": data.get("selected_template_id"),
            "selected_skill_id": data.get("selected_skill_id"),
            "selected_model_id": data.get("selected_model_id"),
            "thinking_enabled": data.get("thinking_enabled"),
            "temperature": data.get("temperature"),
            "cross_session_retrieval_enabled": data.get("cross_session_retrieval_enabled"),
            "tags": [],
            "created_at": _parse_datetime(data.get("created_at")),
            "updated_at": datetime.now(timezone.utc),
        }
    else:
        session = await session_service.update_session(
            db=db,
            session_id=session_id,
            **update_fields,
        )

        if skill_changed:
            pref_result = await db.execute(
                select(UserToolPreference.tool_id).where(
                    UserToolPreference.user_id == current_user.id,
                    UserToolPreference.always_on == True,  # noqa: E712
                )
            )
            always_on_ids = [row[0] for row in pref_result.all()]
            await session_service.sync_session_tools_for_skill(
                db=db,
                session_id=session_id,
                old_skill_id=old_skill_id,
                new_skill_id=new_skill_id,
                tenant_id=current_user.tenant_id,
                always_on_ids=always_on_ids,
            )

        return SessionResponse.model_validate(session)


@router.delete("/session/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Delete a session (DB or Redis). Cleans up file uploads for temp sessions."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    is_temp = data.get("is_temporary", False)

    if is_temp:
        # Clean up associated file uploads (MinIO + DB) before deleting Redis keys
        uploaded_ids: list[str] = data.get("uploaded_file_ids", [])
        for file_id in uploaded_ids:
            await upload_service._delete_temp_upload_by_id(db, file_id)
        await delete_temp_session(session_id)
    else:
        await session_service.delete_session(db, session_id)


@router.post("/session/{session_id}/finalize", response_model=SessionResponse)
async def finalize_temp_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Convert a temporary (Redis) session into a permanent (MariaDB) session.

    Migrates messages, tool activations, and file uploads from Redis to
    MariaDB. Returns the new permanent session.
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    if not data.get("is_temporary", False):
        raise ValidationError("Session is already permanent")

    temp_messages = await get_temp_messages(session_id)
    session = await session_service.finalize_session(db, data, temp_messages)

    # Clean up Redis keys
    await delete_temp_session(session_id)

    logger.info(
        "Finalized temporary session %s — migrated %d messages, %d tools",
        session_id,
        len(temp_messages),
        len(data.get("active_tool_ids", [])),
    )

    return SessionResponse.model_validate(session)


# =============================================================================
# Messages
# =============================================================================


@router.get(
    "/session/{session_id}/messages",
    response_model=list[MessageResponse],
)
async def list_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List messages for a session (DB or Redis)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    is_temp = data.get("is_temporary", False)

    if is_temp:
        msgs = await get_temp_messages(session_id)
        return [
            MessageResponse(
                id=m.get("id", ""),
                session_id=session_id,
                sender=m.get("sender", "user"),
                content=m.get("content"),
                model_id=m.get("model_id"),
                model_name=m.get("model_name"),
                model_provider=m.get("model_provider"),
                tool_calls=m.get("tool_calls"),
                tokens_in=m.get("tokens_in"),
                tokens_out=m.get("tokens_out"),
                is_deleted=m.get("is_deleted", False),
                summarized=m.get("summarized", False),
                created_at=_parse_datetime(m.get("created_at")),
                updated_at=_parse_datetime(m.get("updated_at")),
            )
            for m in msgs
        ]
    else:
        result = await db.execute(
            select(Message)
            .options(selectinload(Message.model))
            .where(
                Message.session_id == session_id,
                Message.is_deleted == False,  # noqa: E712
            )
            .order_by(Message.created_at)
        )
        messages = result.scalars().all()
        return [
            MessageResponse(
                id=m.id,
                session_id=m.session_id,
                sender=m.sender,
                content=m.content,
                model_id=m.model_id,
                model_name=m.model.name if m.model else None,
                model_provider=m.model.provider if m.model else None,
                tool_calls=m.tool_calls,
                tokens_in=m.tokens_in,
                tokens_out=m.tokens_out,
                is_deleted=m.is_deleted,
                summarized=m.summarized,
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in messages
        ]


@router.post(
    "/session/{session_id}/message",
    response_model=SendMessageResponse,
)
async def send_message(
    session_id: str,
    body: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Send a user message and run the agent.

    When the request includes ``Accept: text/event-stream`` the response
    is a Server-Sent Events stream.  Otherwise a plain JSON response is
    returned (Phase 6 backward-compatible path).
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    # ---- Auto-title: if session still has the default title, use the
    #      first user message as the title (truncated to 60 chars). ----
    if data.get("title") == "New Chat" and body.content.strip():
        auto_title = body.content.strip()[:60]
        if data.get("is_temporary"):
            data["title"] = auto_title
            await store_temp_session(session_id, data)
        else:
            await session_service.update_session(db, session_id, title=auto_title)
            data["title"] = auto_title

    # Detect streaming request via Accept header
    accept = request.headers.get("accept", "")
    is_streaming = "text/event-stream" in accept.lower()

    # ---- Inject file content into user message --------------------------
    file_ids = body.file_ids or []
    modified_message, valid_file_ids = await _inject_file_content(
        db=db,
        file_ids=file_ids,
        user_message=body.content,
        session_data=data,
        current_user=current_user,
    )

    if is_streaming:
        # Inject per-message temperature override into session_data
        if body.temperature is not None:
            data["_message_temperature"] = body.temperature
        return await _handle_streaming_message(
            session_id=session_id,
            body=body,
            data=data,
            db=db,
            current_user=current_user,
            modified_message=modified_message,
            valid_file_ids=valid_file_ids,
        )

    # ---- Non-streaming path (Phase 6 backward compat) --------------------
    # Inject per-message temperature override into session_data
    if body.temperature is not None:
        data["_message_temperature"] = body.temperature
    response_text, assistant_msg_id = await run_agent(
        session_data=data,
        user_message=modified_message,
        db=db,
        current_user=current_user,
        file_ids=valid_file_ids,
    )

    model_id = data.get("selected_model_id")

    return SendMessageResponse(
        message_id=assistant_msg_id,
        content=response_text,
        model_id=model_id,
    )


# ---------------------------------------------------------------------------
# Streaming helpers (Phase 7)
# ---------------------------------------------------------------------------


async def _handle_streaming_message(
    session_id: str,
    body: MessageCreate,
    data: dict[str, Any],
    db: AsyncSession,
    current_user: UserORM,
    modified_message: str = "",
    valid_file_ids: list[str] | None = None,
) -> EventSourceResponse:
    """Assemble and return an SSE EventSourceResponse for a streaming agent run.

    NOTE: FastAPI closes the dependency-injected ``db`` session as soon as
    this function returns the ``EventSourceResponse`` — well before the
    streaming generator finishes.  We therefore create a **dedicated**
    session here so the generator's ``finally`` block can persist the
    assistant message, write usage logs, deduct balance, etc. without
    hitting ``ResourceClosedError: This transaction is closed``.
    """
    from ..db.base import AsyncSessionLocal

    message_id = str(uuid.uuid4())
    _valid_file_ids = valid_file_ids or []
    _user_message = modified_message or body.content

    async def inner_gen() -> AsyncIterator[dict]:
        """The inner generator that yields SSE event dicts."""
        async with AsyncSessionLocal() as stream_db:
            try:
                async for event_dict in run_agent_stream(
                    session_data=data,
                    user_message=_user_message,
                    db=stream_db,
                    current_user=current_user,
                    message_id=message_id,
                    file_ids=_valid_file_ids,
                ):
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
            finally:
                # Ensure DB connection is cleanly returned to the pool even
                # when the client disconnects mid-stream (asyncio.CancelledError).
                try:
                    await stream_db.rollback()
                except Exception:
                    pass

    # Wrap with heartbeat to keep proxy connections alive
    gen = _stream_with_heartbeat(inner_gen(), interval=15)

    return EventSourceResponse(gen, media_type="text/event-stream")


async def _stream_with_heartbeat(
    inner_gen: AsyncIterator[dict],
    interval: int = 15,
) -> AsyncIterator[dict]:
    """Wrap an SSE event generator with heartbeat events.

    Emits ``{"event": "heartbeat", "data": "{}"}`` every *interval*
    seconds when no other event has been emitted, to keep proxy
    connections from closing due to idle timeout.

    Uses ``asyncio.wait(..., timeout=interval)`` instead of
    ``asyncio.wait_for`` so that the inner generator task is NOT
    cancelled when the heartbeat fires.  Cancelling the inner task
    would propagate ``CancelledError`` into any currently-running
    tool (e.g. SEC filing extraction), silently aborting it.
    """
    while True:
        try:
            # Wrap __anext__ in a task so we can wait on it without
            # cancelling it when the heartbeat timeout fires.
            anext_task = asyncio.ensure_future(inner_gen.__anext__())
            done, _pending = await asyncio.wait(
                [anext_task], timeout=interval
            )
            if done:
                try:
                    event_dict = await anext_task
                except StopAsyncIteration:
                    break
                yield event_dict
            else:
                # Heartbeat — inner task is still running, do NOT cancel it
                yield {"event": "heartbeat", "data": "{}"}
                # Wait for the inner task to complete, then yield its result
                try:
                    event_dict = await anext_task
                except StopAsyncIteration:
                    break
                yield event_dict
        except StopAsyncIteration:
            break


@router.delete("/session/{session_id}/stream", status_code=204)
async def cancel_stream(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Cancel an active streaming agent run for *session_id*.

    Sets a cancellation flag in Redis that the streaming agent runner
    checks on each token yield.  The stream will terminate and partial
    output will be persisted.
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    await set_stream_cancel(session_id, ttl=60)
    return Response(status_code=204)


# =============================================================================
# Message Branching (Edit / Regenerate / Soft-Delete) — Phase 8
# =============================================================================


@router.put(
    "/session/{session_id}/message/{message_id}",
    response_model=SendMessageResponse,
)
async def edit_user_message(
    session_id: str,
    message_id: str,
    body: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Edit a user message and re-run the agent.

    Hard-deletes the original user message and its assistant response
    (if any), then runs the agent with the new text.  No branching —
    the conversation stays linear.

    When the request includes ``Accept: text/event-stream`` the response
    is a Server-Sent Events stream (same events as send_message).
    Otherwise a plain JSON response is returned.
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    # Reject temporary sessions
    if data.get("is_temporary", False):
        raise ValidationError("Message editing is not supported for temporary sessions")

    # Load the original user message
    msg_result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.session_id == session_id,
        )
    )
    original_msg = msg_result.scalar_one_or_none()
    if original_msg is None:
        raise NotFoundError("Message not found")
    if original_msg.sender != "user":
        raise ValidationError("Only user messages can be edited")

    # Truncate all messages after this user message, then hard-delete
    # the original user message itself.
    await _truncate_after_message(db, session_id, original_msg)
    await upload_service.delete_uploads_for_message(db, message_id)
    await db.delete(original_msg)
    await db.commit()

    # Detect streaming request via Accept header
    accept = request.headers.get("accept", "")
    is_streaming = "text/event-stream" in accept.lower()

    if is_streaming:
        # Inject per-message temperature override into session_data
        if body.temperature is not None:
            data["_message_temperature"] = body.temperature
        return await _handle_streaming_message(
            session_id=session_id,
            body=body,
            data=data,
            db=db,
            current_user=current_user,
            modified_message=body.content,
            valid_file_ids=body.file_ids or [],
        )

    # ---- Non-streaming path ---------------------------------------------
    # Inject per-message temperature override into session_data
    if body.temperature is not None:
        data["_message_temperature"] = body.temperature
    response_text, assistant_msg_id = await run_agent(
        session_data=data,
        user_message=body.content,
        db=db,
        current_user=current_user,
    )

    model_id = data.get("selected_model_id")
    return SendMessageResponse(
        message_id=assistant_msg_id,
        content=response_text,
        model_id=model_id,
    )


@router.delete(
    "/session/{session_id}/message/{message_id}",
    status_code=204,
)
async def delete_message(
    session_id: str,
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Hard-delete a message and its feedback."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    if data.get("is_temporary", False):
        raise ValidationError("Message deletion is not supported for temporary sessions")

    msg_result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.session_id == session_id,
        )
    )
    msg = msg_result.scalar_one_or_none()
    if msg is None:
        raise NotFoundError("Message not found")

    # Truncate all messages after this one
    await _truncate_after_message(db, session_id, msg)

    # Delete feedback rows first (FK constraint)
    await db.execute(
        delete(MessageFeedback).where(
            MessageFeedback.message_id == message_id
        )
    )

    # Cascade: delete attached file uploads (MinIO objects + DB rows)
    await upload_service.delete_uploads_for_message(db, message_id)

    # Hard-delete the message
    await db.delete(msg)

    await db.commit()
    return Response(status_code=204)


@router.patch(
    "/session/{session_id}/message/{message_id}",
    response_model=MessageResponse,
)
async def update_assistant_message(
    session_id: str,
    message_id: str,
    body: AssistantMessageUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Edit an assistant message in-place (no agent re-run).

    Updates the message content and truncates all subsequent messages.
    Unlike PUT (which edits a user message and re-runs the agent),
    this endpoint only modifies the stored text — no regeneration.
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    if data.get("is_temporary", False):
        raise ValidationError(
            "Message editing is not supported for temporary sessions"
        )

    # Load the assistant message
    msg_result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.session_id == session_id,
        )
    )
    msg = msg_result.scalar_one_or_none()
    if msg is None:
        raise NotFoundError("Message not found")
    if msg.sender != "assistant":
        raise ValidationError("Only assistant messages can be edited with PATCH")

    # Truncate all subsequent messages
    deleted_count = await _truncate_after_message(db, session_id, msg)

    # Update content in-place
    msg.content = [{"type": "text", "text": body.content}]
    await db.commit()
    await db.refresh(msg)

    logger.info(
        "Assistant message %s edited in session %s — truncated %d subsequent message(s)",
        message_id,
        session_id,
        deleted_count,
    )

    return MessageResponse.model_validate(msg)


@router.post(
    "/session/{session_id}/message/{message_id}/regenerate",
    response_model=SendMessageResponse,
)
async def regenerate_assistant_message(
    session_id: str,
    message_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Regenerate an assistant response (no branching).

    Hard-deletes the old assistant message, then re-runs the agent
    using the original user message's text.  The conversation stays
    linear — the old response is gone and replaced.

    When the request includes ``Accept: text/event-stream`` the response
    is a Server-Sent Events stream (same events as send_message).
    Otherwise a plain JSON response is returned.
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    if data.get("is_temporary", False):
        raise ValidationError("Regeneration is not supported for temporary sessions")

    # Load the assistant message to regenerate
    msg_result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.session_id == session_id,
        )
    )
    assistant_msg = msg_result.scalar_one_or_none()
    if assistant_msg is None:
        raise NotFoundError("Message not found")
    if assistant_msg.sender != "assistant":
        raise ValidationError("Only assistant messages can be regenerated")

    # Restrict regeneration to the last assistant message in the session.
    last_msg_result = await db.execute(
        select(Message)
        .where(
            Message.session_id == session_id,
            Message.is_deleted == False,  # noqa: E712
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    last_msg = last_msg_result.scalar_one_or_none()
    if last_msg is None or last_msg.id != message_id:
        raise ValidationError(
            "Only the most recent assistant message can be regenerated. "
            "Use edit (PATCH) to modify earlier responses."
        )

    # Find the user message immediately before this assistant message
    all_msgs_result = await db.execute(
        select(Message)
        .where(
            Message.session_id == session_id,
            Message.is_deleted == False,  # noqa: E712
        )
        .order_by(Message.created_at)
    )
    all_msgs = list(all_msgs_result.scalars().all())

    # Find the index of this assistant message and get the preceding user message
    user_text = ""
    user_msg_to_delete = None
    for i, m in enumerate(all_msgs):
        if m.id == message_id and i > 0:
            prev = all_msgs[i - 1]
            if prev.sender == "user":
                user_msg_to_delete = prev
                if prev.content and isinstance(prev.content, list):
                    for item in prev.content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            user_text = item.get("text", "")
                            break
            break

    if not user_text:
        raise ValidationError(
            "Cannot regenerate: could not find the parent user message text"
        )

    # Collect file IDs from the parent user message before deletion
    user_file_ids: list[str] = []
    if user_msg_to_delete is not None:
        from ..db.orm.file_uploads import FileUpload as FileUploadORM

        fu_result = await db.execute(
            select(FileUploadORM).where(
                FileUploadORM.message_id == user_msg_to_delete.id
            )
        )
        user_file_ids = [fu.id for fu in fu_result.scalars().all()]

    # Hard-delete the old assistant message and its uploads.
    # For the user message, preserve the file uploads by unsetting
    # their message_id so they can be re-linked to the new message.
    await upload_service.delete_uploads_for_message(db, message_id)
    await db.delete(assistant_msg)
    if user_msg_to_delete is not None:
        if user_file_ids:
            await db.execute(
                update(FileUploadORM)
                .where(FileUploadORM.id.in_(user_file_ids))
                .values(message_id=None)
            )
        await db.delete(user_msg_to_delete)
    await db.commit()

    # Detect streaming request via Accept header
    accept = request.headers.get("accept", "")
    is_streaming = "text/event-stream" in accept.lower()

    if is_streaming:
        # Reuse the standard streaming path: the user message already exists,
        # we just need a new assistant response.  Streaming regenerate works
        # the same as a normal send — run the agent and stream the response.
        return await _handle_streaming_regenerate(
            session_id=session_id,
            data=data,
            db=db,
            current_user=current_user,
            user_text=user_text,
            file_ids=user_file_ids,
        )

    # ---- Non-streaming path ---------------------------------------------
    response_text, assistant_msg_id = await run_agent(
        session_data=data,
        user_message=user_text,
        db=db,
        current_user=current_user,
        file_ids=user_file_ids,
    )

    model_id = data.get("selected_model_id")
    return SendMessageResponse(
        message_id=assistant_msg_id,
        content=response_text,
        model_id=model_id,
    )


# ---------------------------------------------------------------------------
# Regenerate streaming helper
# ---------------------------------------------------------------------------


async def _handle_streaming_regenerate(
    session_id: str,
    data: dict[str, Any],
    db: AsyncSession,
    current_user: UserORM,
    user_text: str,
    file_ids: list[str] | None = None,
) -> EventSourceResponse:
    """Assemble and return an SSE EventSourceResponse for a streaming regenerate.
    
    Uses the standard run_agent_stream path — the user message already exists
    in the DB, so a new user+assistant pair is persisted (same as send_message).
    """
    message_id = str(uuid.uuid4())
    _file_ids = file_ids or []

    async def inner_gen() -> AsyncIterator[dict]:
        try:
            async for event_dict in run_agent_stream(
                session_data=data,
                user_message=user_text,
                db=db,
                current_user=current_user,
                message_id=message_id,
                file_ids=_file_ids,
            ):
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
        finally:
            # Ensure DB connection is cleanly returned to the pool even
            # when the client disconnects mid-stream (asyncio.CancelledError).
            try:
                await db.rollback()
            except Exception:
                pass

    gen = _stream_with_heartbeat(inner_gen(), interval=15)
    return EventSourceResponse(gen, media_type="text/event-stream")


# =============================================================================
# Message Feedback — Phase 8
# =============================================================================


@router.post(
    "/session/{session_id}/message/{message_id}/feedback",
    response_model=FeedbackResponse,
    status_code=201,
)
async def submit_feedback(
    session_id: str,
    message_id: str,
    body: FeedbackCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Submit feedback (up/down) for an assistant message."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    # Validate rating
    if body.rating not in ("up", "down"):
        raise ValidationError("Rating must be 'up' or 'down'")

    # Load message
    msg_result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.session_id == session_id,
        )
    )
    msg = msg_result.scalar_one_or_none()
    if msg is None:
        raise NotFoundError("Message not found")
    if msg.sender != "assistant":
        raise ValidationError("Feedback can only be submitted for assistant messages")

    feedback = MessageFeedback(
        message_id=message_id,
        user_id=current_user.id,
        rating=body.rating,
        comment=body.comment,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)

    return FeedbackResponse.model_validate(feedback)


# =============================================================================
# Follow-up Questions — Issue #126
# =============================================================================


class FollowUpQuestionsResponse(BaseModel):
    questions: list[str]


@router.get(
    "/session/{session_id}/follow-up-questions",
    response_model=FollowUpQuestionsResponse,
)
async def get_follow_up_questions(
    session_id: str,
    current_user: UserORM = Depends(get_current_user),
):
    """Retrieve follow-up questions for a session from Redis.

    Questions are generated asynchronously by a background task after
    the SSE stream closes.  This endpoint is polled once by the frontend
    when the stream finishes.  Returns an empty list if questions haven't
    been generated yet or if the model doesn't support them.
    """
    from ..core.redis import get_follow_up_questions as _redis_get_follow_up

    questions = await _redis_get_follow_up(session_id)
    return FollowUpQuestionsResponse(questions=questions or [])


# =============================================================================
# Message Summarization — Issue #29
# =============================================================================


class SummarizeRequest(BaseModel):
    """Optional: number of recent message pairs to keep intact."""
    keep_recent_pairs: int = 3


class SummarizeResponse(BaseModel):
    summary: str
    summarized_message_count: int
    tokens_saved: int


@router.post(
    "/session/{session_id}/summarize",
    response_model=SummarizeResponse,
)
async def summarize_session(
    session_id: str,
    body: SummarizeRequest = SummarizeRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Manually summarize the conversation in a session.

    Compresses older messages into a concise summary, keeping the most
    recent *keep_recent_pairs* user/assistant message pairs intact.
    The summary is stored as a system message in the session.

    This is also triggered automatically when the conversation
    approaches the model's context length limit.
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    is_temporary = data.get("is_temporary", False)

    # Resolve the model for the summarization call
    from ..agents.runner import (
        _resolve_session_config,
        _get_messages_for_session,
        _generate_summary,
        _extract_message_text,
        _estimate_tokens,
        _persist_system_message,
    )

    cfg = await _resolve_session_config(
        db, data, current_user.tenant_id, current_user
    )

    # Get all messages
    all_messages = await _get_messages_for_session(db, session_id, is_temporary)

    if len(all_messages) < 4:
        raise ValidationError(
            "Need at least a few messages to summarize. "
            "Keep chatting and try again later."
        )

    # Find non-summarized messages
    non_summarized = [
        m for m in all_messages
        if not _msg_get(m, "summarized", False)
    ]

    # Count user+assistant pairs from the end
    keep_pairs = max(1, body.keep_recent_pairs)
    pair_count = 0
    cutoff_idx = len(non_summarized)
    for i in range(len(non_summarized) - 1, -1, -1):
        msg = non_summarized[i]
        sender = _msg_get(msg, "sender", "")
        if sender == "user":
            pair_count += 1
            if pair_count >= keep_pairs:
                cutoff_idx = i
                break

    messages_to_summarize = non_summarized[:cutoff_idx]

    if not messages_to_summarize:
        raise ValidationError(
            "Nothing to summarize — all messages are already within the "
            f"most recent {keep_pairs} exchange(s)."
        )

    # Generate summary
    summary_text = await _generate_summary(
        model=cfg.model,
        model_client=cfg.model_client,
        messages_to_summarize=messages_to_summarize,
    )

    if not summary_text:
        raise ValidationError(
            "Failed to generate summary. The model may be unavailable."
        )

    # Mark messages as summarized
    if is_temporary:
        from ..core.redis import get_redis, get_temp_messages
        all_raw = await get_temp_messages(session_id)
        for raw_msg in all_raw:
            raw_id = raw_msg.get("id", "")
            for to_mark in messages_to_summarize:
                mark_id = _msg_get(to_mark, "id", "")
                if raw_id == mark_id:
                    raw_msg["summarized"] = True
        r = await get_redis()
        import json as _json
        msg_key = f"session:tmp:{session_id}:messages"
        session_key = f"session:tmp:{session_id}"
        ttl = await r.ttl(session_key)
        if ttl and ttl > 0:
            await r.setex(msg_key, ttl, _json.dumps(all_raw))
        else:
            await r.setex(msg_key, settings.TEMPORARY_SESSION_TTL_SECONDS, _json.dumps(all_raw))
    else:
        for msg in messages_to_summarize:
            if hasattr(msg, "summarized"):
                msg.summarized = True
        await db.commit()

    # Store summary as system message
    summary_content = [{"type": "text", "text": summary_text}]
    await _persist_system_message(
        db=db,
        session_id=session_id,
        is_temporary=is_temporary,
        content=summary_content,
    )

    # Calculate tokens saved
    old_tokens = sum(
        _estimate_tokens(_extract_message_text(
            _msg_get(m, "content")
        ))
        for m in messages_to_summarize
    )
    new_tokens = _estimate_tokens(summary_text)
    tokens_saved = max(0, old_tokens - new_tokens)

    logger.info(
        "Manual summarization for session %s: %d messages compressed, ~%d tokens saved",
        session_id, len(messages_to_summarize), tokens_saved,
    )

    return SummarizeResponse(
        summary=summary_text,
        summarized_message_count=len(messages_to_summarize),
        tokens_saved=tokens_saved,
    )


# =============================================================================
# Session Search — Phase 8
# =============================================================================


@router.get(
    "/sessions/search",
    response_model=list[SessionResponse],
)
async def search_sessions(
    q: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Full-text search across session titles and message content.

    Query is scoped to the authenticated user's tenant and user ID.
    Uses FULLTEXT index on ``sessions.title`` and ``LIKE`` on
    ``messages.content`` (JSON column).
    """
    if not q or not q.strip():
        raise ValidationError("Search query 'q' is required")

    search_term = f"%{q.strip()}%"

    # Search sessions by title (FULLTEXT) and by message content (LIKE)
    from sqlalchemy import text, type_coerce, String as SAString

    # FULLTEXT on sessions.title
    title_stmt = (
        select(Session)
        .where(
            Session.user_id == current_user.id,
            Session.tenant_id == current_user.tenant_id,
            Session.is_temporary == False,  # noqa: E712
            text("MATCH(sessions.title) AGAINST(:query IN NATURAL LANGUAGE MODE)"),
        )
        .params(query=q.strip())
    )

    # LIKE fallback on sessions.title (covers cases where FULLTEXT can't)
    like_stmt = (
        select(Session)
        .where(
            Session.user_id == current_user.id,
            Session.tenant_id == current_user.tenant_id,
            Session.is_temporary == False,  # noqa: E712
            Session.title.ilike(search_term),
        )
    )

    # Search sessions via message content (LIKE on JSON column)
    msg_stmt = (
        select(Session)
        .join(Message, Message.session_id == Session.id)
        .where(
            Session.user_id == current_user.id,
            Session.tenant_id == current_user.tenant_id,
            Session.is_temporary == False,  # noqa: E712
            Message.is_deleted == False,  # noqa: E712
            type_coerce(Message.content, SAString).ilike(search_term),
        )
    )

    # Search sessions via tag names
    from ..db.orm.tags import Tag as TagORM, SessionTag as SessionTagORM
    tag_stmt = (
        select(Session)
        .join(SessionTagORM, SessionTagORM.session_id == Session.id)
        .join(TagORM, TagORM.id == SessionTagORM.tag_id)
        .where(
            Session.user_id == current_user.id,
            Session.tenant_id == current_user.tenant_id,
            Session.is_temporary == False,  # noqa: E712
            TagORM.name.ilike(search_term),
        )
    )

    # Execute all queries
    title_results = await db.execute(title_stmt)
    like_results = await db.execute(like_stmt)
    msg_results = await db.execute(msg_stmt)
    tag_results = await db.execute(tag_stmt)

    # Deduplicate by session ID (union of all four)
    seen: set[str] = set()
    sessions: list[Session] = []
    for s in (
        list(title_results.scalars().all())
        + list(like_results.scalars().all())
        + list(msg_results.scalars().all())
        + list(tag_results.scalars().all())
    ):
        if s.id not in seen:
            seen.add(s.id)
            sessions.append(s)

    return [SessionResponse.model_validate(s) for s in sessions]


# =============================================================================
# File Uploads — Phase 8
# =============================================================================


@router.post(
    "/session/{session_id}/upload",
    response_model=FileUploadResponse,
    status_code=201,
)
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Upload a file to a session (stored in MinIO)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    if not file.filename:
        raise ValidationError("File must have a filename")

    file_bytes = await file.read()
    content_type = file.content_type or "application/octet-stream"

    upload = await upload_service.create_upload(
        db=db,
        session_data=data,
        current_user=current_user,
        file_bytes=file_bytes,
        original_filename=file.filename,
        content_type=content_type,
    )

    return FileUploadResponse(
        file_id=upload.id,
        original_filename=upload.original_filename,
        content_type=upload.content_type,
        size_bytes=upload.size_bytes,
        created_at=upload.created_at,
    )


@router.get(
    "/session/{session_id}/uploads",
    response_model=list[FileUploadResponse],
)
async def list_uploads(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List all file uploads for a session."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    uploads = await upload_service.list_uploads(
        db=db,
        session_id=session_id,
        user_id=current_user.id,
        is_temporary=data.get("is_temporary", False),
        file_ids=data.get("uploaded_file_ids"),
    )
    return [
        FileUploadResponse(
            file_id=u.id,
            original_filename=u.original_filename,
            content_type=u.content_type,
            size_bytes=u.size_bytes,
            created_at=u.created_at,
        )
        for u in uploads
    ]


@router.get(
    "/session/{session_id}/upload/{file_id}/url",
)
async def get_upload_url(
    session_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Generate a presigned download URL for an uploaded file."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    url = await upload_service.generate_presigned_url(
        db=db,
        file_id=file_id,
        user_id=current_user.id,
    )
    return {"url": url}


@router.get(
    "/session/{session_id}/upload/{file_id}/download",
)
async def download_upload(
    session_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Download a file upload through the backend (same domain)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    upload = await upload_service.get_upload_by_id(
        db=db,
        file_id=file_id,
        user_id=current_user.id,
    )
    file_bytes = await s3.download_object(
        bucket=upload.bucket,
        key=upload.storage_key,
    )
    return StreamingResponse(
        content=iter([file_bytes]),
        media_type=upload.content_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{upload.original_filename}"'
            ),
            "Content-Length": str(upload.size_bytes),
        },
    )


@router.delete(
    "/session/{session_id}/upload/{file_id}",
    status_code=204,
)
async def delete_upload(
    session_id: str,
    file_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Delete a file upload (MinIO object + DB row)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    await upload_service.delete_upload(
        db=db,
        file_id=file_id,
        user_id=current_user.id,
    )
    return Response(status_code=204)


@router.get(
    "/session/{session_id}/message/{message_id}/uploads",
    response_model=list[FileUploadResponse],
)
async def list_message_uploads(
    session_id: str,
    message_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List all file uploads linked to a specific message."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    uploads = await upload_service.list_uploads_for_message(
        db=db,
        message_id=message_id,
    )
    return [
        FileUploadResponse(
            file_id=u.id,
            original_filename=u.original_filename,
            content_type=u.content_type,
            size_bytes=u.size_bytes,
            created_at=u.created_at,
        )
        for u in uploads
    ]


@router.get(
    "/session/tools/available",
    response_model=list[ToolResponse],
)
async def list_available_tools(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List all enabled tools available for the current user.

    Tools are filtered by group access control:
    is_public=True OR tool assigned to a group the user belongs to.
    """
    from ..services.tool_service import list_tools

    tools, _ = await list_tools(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )
    enabled = [t for t in tools if t.enabled]
    return [ToolResponse.model_validate(t) for t in enabled]


@router.put("/session/tools/{tool_id}/always-on", status_code=204)
async def set_tool_always_on(
    tool_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Toggle a tool as always-on for the current user.

    Body: {"always_on": true|false}
    When always_on, the tool is automatically activated for new sessions.
    """
    body = await request.json()
    always_on = bool(body.get("always_on", False))

    # Upsert the preference
    existing = await db.execute(
        select(UserToolPreference).where(
            UserToolPreference.user_id == current_user.id,
            UserToolPreference.tool_id == tool_id,
        )
    )
    pref = existing.scalar_one_or_none()

    if pref is None:
        pref = UserToolPreference(
            user_id=current_user.id,
            tool_id=tool_id,
            always_on=always_on,
        )
        db.add(pref)
    else:
        pref.always_on = always_on

    await db.commit()


@router.get("/session/tools/always-on", response_model=list[str])
async def list_always_on_tools(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Return the list of tool IDs the user has marked as always-on."""
    result = await db.execute(
        select(UserToolPreference.tool_id).where(
            UserToolPreference.user_id == current_user.id,
            UserToolPreference.always_on == True,  # noqa: E712
        )
    )
    return [row[0] for row in result.all()]


@router.get(
    "/session/{session_id}/tools",
    response_model=list[ToolResponse],
)
async def list_session_tools(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List active tools for a session (DB or Redis)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    is_temp = data.get("is_temporary", False)

    if is_temp:
        tool_ids = data.get("active_tool_ids", [])
        if not tool_ids:
            return []
        result = await db.execute(
            select(Tool).where(
                Tool.id.in_(tool_ids),
                Tool.tenant_id == current_user.tenant_id,
            )
        )
        tools = result.scalars().all()
        return [ToolResponse.model_validate(t) for t in tools]
    else:
        tools = await session_service.get_session_tools(db, session_id)
        return [ToolResponse.model_validate(t) for t in tools]


@router.post(
    "/session/{session_id}/tools/{tool_id}",
    status_code=204,
)
async def add_session_tool(
    session_id: str,
    tool_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Activate a tool for a session (DB or Redis)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    # Validate tool exists and is enabled
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise NotFoundError("Tool not found")
    if not tool.enabled:
        raise ValidationError("Tool is disabled")
    if tool.tenant_id != current_user.tenant_id:
        raise ValidationError("Tool does not belong to this tenant")

    is_temp = data.get("is_temporary", False)

    if is_temp:
        tool_ids: list[str] = list(data.get("active_tool_ids", []))
        if tool_id not in tool_ids:
            tool_ids.append(tool_id)
            data["active_tool_ids"] = tool_ids
            await store_temp_session(session_id, data)
    else:
        await session_service.add_session_tool(
            db=db,
            session_id=session_id,
            tool_id=tool_id,
            tenant_id=current_user.tenant_id,
        )


@router.delete(
    "/session/{session_id}/tools/{tool_id}",
    status_code=204,
)
async def remove_session_tool(
    session_id: str,
    tool_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Deactivate a tool for a session (DB or Redis)."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    is_temp = data.get("is_temporary", False)

    if is_temp:
        tool_ids: list[str] = list(data.get("active_tool_ids", []))
        if tool_id in tool_ids:
            tool_ids.remove(tool_id)
            data["active_tool_ids"] = tool_ids
            await store_temp_session(session_id, data)
    else:
        await session_service.remove_session_tool(
            db=db,
            session_id=session_id,
            tool_id=tool_id,
        )


# =============================================================================
# Session Tags
# =============================================================================


@router.get("/session/tags", response_model=list[TagResponse])
async def list_tenant_tags(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Return all tags for the current user's tenant."""
    tags = await session_service.list_tenant_tags(
        db=db, tenant_id=current_user.tenant_id
    )
    return [TagResponse.model_validate(t) for t in tags]


@router.post(
    "/session/{session_id}/tags",
    response_model=SessionResponse,
    status_code=201,
)
async def add_tag(
    session_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Add a tag to a session by tag name. Creates the tag if needed."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    if data.get("is_temporary", False):
        raise ValidationError("Tags are not supported for temporary sessions")

    tag_name = (body.get("name") or "").strip()
    if not tag_name:
        raise ValidationError("Tag name is required")

    tag = await session_service.get_or_create_tag(
        db=db,
        tenant_id=current_user.tenant_id,
        name=tag_name,
    )
    await session_service.add_tag_to_session(
        db=db,
        session_id=session_id,
        tag_id=tag.id,
    )

    # Reload the session to get updated tags
    session = await session_service.get_session_by_id(db, session_id)
    return SessionResponse.model_validate(session)


@router.delete(
    "/session/{session_id}/tags/{tag_id}",
    status_code=204,
)
async def remove_tag(
    session_id: str,
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Remove a tag from a session."""
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    if data.get("is_temporary", False):
        raise ValidationError("Tags are not supported for temporary sessions")

    await session_service.remove_tag_from_session(
        db=db,
        session_id=session_id,
        tag_id=tag_id,
    )


@router.get(
    "/sessions/by-tag",
    response_model=list[SessionResponse],
)
async def list_sessions_by_tag(
    tag: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List user's sessions that have a specific tag."""
    sessions = await session_service.list_sessions_by_tag(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        tag_name=tag,
    )
    return [SessionResponse.model_validate(s) for s in sessions]


# =============================================================================
# Utility
# =============================================================================


def _parse_datetime(val: Any) -> datetime:
    """Parse a datetime value that may be a string or datetime object."""
    if val is None:
        logger.debug("_parse_datetime received None, using current time")
        return datetime.now(timezone.utc)
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except (ValueError, TypeError):
        logger.warning("_parse_datetime failed to parse '%s', using current time", val)
        return datetime.now(timezone.utc)

