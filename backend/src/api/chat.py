# =============================================================================
# PH Agent Hub — Chat API Router
# =============================================================================
# Session CRUD (permanent via MariaDB + temporary via Redis), message
# sending (agent run), and session-level tool activation.
# =============================================================================

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from sqlalchemy.orm import selectinload

from ..agents.runner import (
    _msg_get,
    _extract_message_text,
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
from ..db.orm.sessions import Session, SessionActiveTool
from ..db.orm.tools import Tool
from ..db.orm.user_tool_preferences import UserToolPreference
from ..db.orm.users import User as UserORM
from ..services import audit_service, session_service, upload_service
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
    auto_route_enabled: bool = False
    auto_select_tools: bool = True


class SessionUpdate(BaseModel):
    title: str | None = None
    is_pinned: bool | None = None
    selected_template_id: str | None = None
    selected_skill_id: str | None = None
    selected_model_id: str | None = None
    thinking_enabled: bool | None = None
    temperature: float | None = None
    cross_session_retrieval_enabled: bool | None = None
    auto_route_enabled: bool | None = None
    auto_select_tools: bool | None = None


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
    auto_route_enabled: bool = False
    auto_select_tools: bool = True
    tags: list[TagResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    content: str
    file_ids: list[str] | None = None
    temperature: float | None = None
    session_data: SessionCreate | None = None


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


class ExportFormat(str, Enum):
    JSON = "json"
    TXT = "txt"


class ImportResponse(BaseModel):
    session_id: str
    message_count: int


class FileUploadResponse(BaseModel):
    file_id: str
    original_filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
    embedding_warning: str | None = None
    """If set, the embedding API was unavailable and TF-IDF fallback was used."""

    model_config = {"from_attributes": True}


# =============================================================================
# Lazy session creation (Phase 2 — Issue #329)
# =============================================================================


async def _lazy_create_session(
    db: AsyncSession,
    session_id: str,
    session_data: SessionCreate,
    current_user: UserORM,
) -> dict[str, Any]:
    """Create a permanent session with the given URL *session_id*.

    Called by ``send_message`` when the session doesn't exist yet and the
    request includes ``session_data`` (first message sent from a pending
    / lazy frontend session).
    """
    # Resolve active tool IDs (mirrors create_session endpoint logic)
    active_tool_ids = session_data.active_tool_ids
    if not active_tool_ids:
        # Auto-activate: user's always-on tools + skill tools
        # Treat None and [] the same — both mean "no explicit tools selected"
        always_on_ids: set[str] = set()
        try:
            pref_result = await db.execute(
                select(UserToolPreference.tool_id).where(
                    UserToolPreference.user_id == current_user.id,
                    UserToolPreference.always_on == True,  # noqa: E712
                )
            )
            always_on_ids = {row[0] for row in pref_result.all()}
        except Exception:
            pass

        skill_tool_ids: set[str] = set()
        if session_data.selected_skill_id:
            from ..db.orm.skills import SkillAllowedTool

            skill_result = await db.execute(
                select(SkillAllowedTool.tool_id).where(
                    SkillAllowedTool.skill_id == session_data.selected_skill_id
                )
            )
            skill_tool_ids = {row[0] for row in skill_result.all()}

        active_tool_ids = list(always_on_ids | skill_tool_ids)

    session = await session_service.create_session(
        db=db,
        id=session_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=session_data.title or "New Chat",
        is_temporary=False,
        is_pinned=session_data.is_pinned,
        selected_template_id=session_data.selected_template_id,
        selected_skill_id=session_data.selected_skill_id,
        selected_model_id=session_data.selected_model_id,
        thinking_enabled=session_data.thinking_enabled,
        temperature=session_data.temperature,
        auto_route_enabled=session_data.auto_route_enabled,
        auto_select_tools=session_data.auto_select_tools,
    )

    # Activate tools for the new session
    for tool_id in active_tool_ids:
        try:
            await session_service.add_session_tool(
                db=db,
                session_id=session.id,
                tool_id=tool_id,
                tenant_id=current_user.tenant_id,
            )
        except Exception:
            pass

    await db.refresh(session)
    return _session_to_dict(session, active_tool_ids=active_tool_ids)


# =============================================================================
# Internal helpers
# =============================================================================


def _session_to_dict(
    session: Session,
    active_tool_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Convert a Session ORM object to a plain dict for runner consumption.

    When *active_tool_ids* is provided, it is included in the output so
    that ``_resolve_tool_callables`` (which reads ``session_data`` for
    temporary sessions) can find the active tool IDs directly without an
    extra DB query.
    """
    result = {
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
        "auto_route_enabled": session.auto_route_enabled,
        "auto_select_tools": session.auto_select_tools,
        "cross_session_retrieval_enabled": session.cross_session_retrieval_enabled,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }
    if active_tool_ids is not None:
        result["active_tool_ids"] = active_tool_ids
    return result


async def _load_session(
    db: AsyncSession, session_id: str
) -> dict[str, Any]:
    """Load a session from MariaDB or Redis, returning a unified dict.

    Raises NotFoundError if neither source has the session.
    """
    # Try DB first
    session = await session_service.get_session_by_id(db, session_id)
    if session is not None:
        # Also load active tool IDs for permanent sessions
        active_tool_ids: list[str] = []
        try:
            sa_result = await db.execute(
                select(SessionActiveTool.tool_id).where(
                    SessionActiveTool.session_id == session_id
                )
            )
            active_tool_ids = [row[0] for row in sa_result.all()]
        except Exception:
            pass
        return _session_to_dict(session, active_tool_ids=active_tool_ids)

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
    if not active_tool_ids:
        # Auto-activate: user's always-on tools + skill tools
        # Treat None and [] the same — both mean "no explicit tools selected"
        always_on_ids: set[str] = set()
        try:
            pref_result = await db.execute(
                select(UserToolPreference.tool_id).where(
                    UserToolPreference.user_id == current_user.id,
                    UserToolPreference.always_on == True,  # noqa: E712
                )
            )
            always_on_ids = {row[0] for row in pref_result.all()}
        except Exception:
            logger.debug("user_tool_preferences table not available, skipping always-on tools")

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
            "auto_route_enabled": body.auto_route_enabled,
            "auto_select_tools": body.auto_select_tools,
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
            "auto_route_enabled": body.auto_route_enabled,
            "auto_select_tools": body.auto_select_tools,
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
            auto_route_enabled=body.auto_route_enabled,
            auto_select_tools=body.auto_select_tools,
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
        "auto_route_enabled": data.get("auto_route_enabled", False),
        "auto_select_tools": data.get("auto_select_tools", True),
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
        # Capture old values before mutation (for audit)
        old_auto_select_tools = data.get("auto_select_tools", True)

        # Update Redis blob
        data.update(update_fields)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Sync skill tools in Redis blob
        if skill_changed:
            always_on_ids: set[str] = set()
            try:
                pref_result = await db.execute(
                    select(UserToolPreference.tool_id).where(
                        UserToolPreference.user_id == current_user.id,
                        UserToolPreference.always_on == True,  # noqa: E712
                    )
                )
                always_on_ids = {row[0] for row in pref_result.all()}
            except Exception:
                logger.debug("user_tool_preferences table not available, skipping always-on tools")

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

        # Audit: auto_select_tools change
        if "auto_select_tools" in update_fields:
            old_val = old_auto_select_tools
            new_val = update_fields.get("auto_select_tools")
            if old_val != new_val:
                try:
                    await audit_service.write_audit_log(
                        db=db,
                        actor=current_user,
                        action="session.auto_select_tools_changed",
                        target_type="session",
                        target_id=session_id,
                        payload={
                            "old_value": old_val,
                            "new_value": new_val,
                        },
                        tenant_id=current_user.tenant_id,
                    )
                except Exception:
                    logger.warning("Failed to write audit log for auto_select_tools change")

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
            "auto_select_tools": data.get("auto_select_tools", True),
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
            always_on_ids: list[str] = []
            try:
                pref_result = await db.execute(
                    select(UserToolPreference.tool_id).where(
                        UserToolPreference.user_id == current_user.id,
                        UserToolPreference.always_on == True,  # noqa: E712
                    )
                )
                always_on_ids = [row[0] for row in pref_result.all()]
            except Exception:
                logger.debug("user_tool_preferences table not available, skipping always-on tools")

            await session_service.sync_session_tools_for_skill(
                db=db,
                session_id=session_id,
                old_skill_id=old_skill_id,
                new_skill_id=new_skill_id,
                tenant_id=current_user.tenant_id,
                always_on_ids=always_on_ids,
            )

        # Audit: auto_select_tools change
        if "auto_select_tools" in update_fields:
            old_val = data.get("auto_select_tools", True)
            new_val = update_fields.get("auto_select_tools")
            if old_val != new_val:
                try:
                    await audit_service.write_audit_log(
                        db=db,
                        actor=current_user,
                        action="session.auto_select_tools_changed",
                        target_type="session",
                        target_id=session_id,
                        payload={
                            "old_value": old_val,
                            "new_value": new_val,
                        },
                        tenant_id=current_user.tenant_id,
                    )
                except Exception:
                    logger.warning("Failed to write audit log for auto_select_tools change")

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
    # ---- Lazy session creation (Phase 2 — Issue #329) ------------------
    # If the session doesn't exist yet and the request includes session
    # creation data, create it now before processing the first message.
    try:
        data = await _load_session(db, session_id)
        await _require_session_owner(data, current_user)
    except NotFoundError:
        if body.session_data is None:
            raise
        logger.info(
            "Lazy-creating session %s on first message", session_id,
        )
        data = await _lazy_create_session(
            db, session_id, body.session_data, current_user,
        )

    # ---- Temp → permanent promotion (Issue #336) -----------------------
    # If the user uploaded files to a temp session (created by upload_file
    # when the session didn't exist yet) and then sends a message with
    # session_data, promote the session to permanent.
    if data.get("is_temporary") and body.session_data is not None:
        logger.info(
            "Promoting temp session %s to permanent on first message",
            session_id,
        )
        try:
            data = await _lazy_create_session(
                db, session_id, body.session_data, current_user,
            )
        except IntegrityError:
            # Race: upload_file already created the permanent session.
            # Re-fetch the existing session data.
            logger.info(
                "Session %s already promoted to permanent by concurrent request — re-fetching",
                session_id,
            )
            await db.rollback()
            data = await _load_session(db, session_id)
        else:
            # Re-link any files uploaded during the pending phase
            await upload_service.link_pending_uploads_to_session(
                db, session_id, current_user.id,
            )
            # Clean up the stale Redis temp entry
            await delete_temp_session(session_id)

    # ---- Auto-title: if session still has the default title, use the
    #      first user message as the title.  Set a raw truncated title
    #      immediately, then fire a background task that calls a cheap
    #      LLM to generate a proper concise title. -----------------------
    if data.get("title") == "New Chat" and body.content.strip():
        # Defensive: ensure content is a string — Pydantic enforces str, but
        # this belts-and-suspenders against edge cases like raw list content.
        raw_content = body.content if isinstance(body.content, str) else str(body.content)
        logger.info(
            "Auto-title: content type=%s, length=%d, preview=%r",
            type(body.content).__name__,
            len(raw_content),
            raw_content[:80],
        )

        # 1. Immediate fallback title: first 60 chars of the message
        fallback_title = raw_content.strip()[:60]
        logger.info("Auto-title: fallback title=%r", fallback_title)

        if data.get("is_temporary"):
            data["title"] = fallback_title
            await store_temp_session(session_id, data)
        else:
            await session_service.update_session(db, session_id, title=fallback_title)
            await db.flush()
            data["title"] = fallback_title

        # 2. Background task: generate a smarter LLM title
        tenant_id = data.get("tenant_id", current_user.tenant_id)
        _session_id = session_id
        _is_temp = data.get("is_temporary", False)
        _user_msg = raw_content.strip()
        asyncio.ensure_future(
            _generate_title_async(
                session_id=_session_id,
                tenant_id=tenant_id,
                is_temporary=_is_temp,
                user_message=_user_msg,
            )
        )

    # ---- Auto-routing: if enabled and no model assigned yet, route
    #      the first user message to the best model in one shot. -------
    if data.get("auto_route_enabled") and not data.get("selected_model_id"):
        from ..services.router_service import route_message
        from ..db.orm.models import Model as ModelORM

        tenant_id = data.get("tenant_id", current_user.tenant_id)
        user_id = data.get("user_id", current_user.id)

        # 1. Route the message: classifier LLM picks the best model_id
        selected_id = await route_message(db, body.content, tenant_id, user_id)

        # 2. Fallback if router returned nothing
        if selected_id is None:
            user_result = await db.execute(
                select(UserORM).where(UserORM.id == user_id)
            )
            user_row = user_result.scalar_one_or_none()
            if user_row and user_row.default_model_id:
                selected_id = user_row.default_model_id
            else:
                from ..services.model_service import list_models

                models, _ = await list_models(db, tenant_id=tenant_id, user_id=user_id)
                enabled = [m for m in models if m.enabled]
                if enabled:
                    selected_id = enabled[0].id

        # 3. Lock in the model on the session
        if selected_id:
            # Resolve model name for logging
            model_result = await db.execute(
                select(ModelORM).where(ModelORM.id == selected_id)
            )
            picked_model = model_result.scalar_one_or_none()
            model_label = f"{picked_model.name} ({picked_model.provider})" if picked_model else selected_id
            logger.info(
                "🧠 Auto-routed session %s to model %s",
                session_id, model_label,
            )

            data["selected_model_id"] = selected_id
            if data.get("is_temporary"):
                await store_temp_session(session_id, data)
            else:
                await session_service.update_session(
                    db, session_id, selected_model_id=selected_id
                )

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
# Auto-title helpers
# ---------------------------------------------------------------------------


async def _generate_title_async(
    session_id: str,
    tenant_id: str,
    is_temporary: bool,
    user_message: str,
) -> None:
    """Background task: call a cheap LLM to generate a concise session title.

    Sets a raw fallback title immediately; this function improves it.
    Runs in a background ``asyncio.ensure_future`` so it doesn't block the
    message send response.
    """
    import logging as _logging

    _logger = _logging.getLogger(__name__)
    try:
        from ..db.base import AsyncSessionLocal as _AsyncSessionLocal
        from ..db.orm.models import Model as _ModelORM
        from ..models.base import get_chat_client as _get_chat_client
        from agent_framework import Message as _MafMessage
        from sqlalchemy import select

        # Find a cheap eligible model for title generation
        async with _AsyncSessionLocal() as _title_db:
            result = await _title_db.execute(
                select(_ModelORM).where(
                    _ModelORM.tenant_id == tenant_id,
                    _ModelORM.enabled == True,  # noqa: E712
                    _ModelORM.auto_route_eligible == True,  # noqa: E712
                ).order_by(_ModelORM.input_price_per_1m.asc())
            )
            eligible = list(result.scalars().all())

            if not eligible:
                _logger.info(
                    "Auto-title LLM: no eligible model for tenant %s — keeping fallback title",
                    tenant_id,
                )
                return

            classifier = eligible[0]
            _logger.info(
                "Auto-title LLM: using model=%s for title generation",
                classifier.model_id,
            )

            prompt = (
                "You are a chat title generator. Generate a very concise title "
                "(5 words or fewer) for a conversation that starts with this message. "
                "Respond with ONLY the title, nothing else."
            )
            client = _get_chat_client(classifier, thinking_enabled=False)
            maf_messages = [
                _MafMessage("system", [prompt]),
                _MafMessage("user", [user_message]),
            ]
            response = await client.get_response(
                messages=maf_messages,
                options={"temperature": 0.3, "max_tokens": 32},
            )
            # Safe cleanup — DeepSeekThinkingClient doesn't have aclose()
            if hasattr(client, "aclose"):
                await client.aclose()
            elif hasattr(client, "close"):
                await client.close()

            llm_title = (response.messages[-1].text or "").strip().strip('"').strip("'")
            if not llm_title or len(llm_title) > 100:
                _logger.info(
                    "Auto-title LLM: generated title invalid (%r) — keeping fallback",
                    llm_title,
                )
                return

            _logger.info("Auto-title LLM: generated title=%r", llm_title)

            # Update the session title
            if is_temporary:
                from ..core.redis import get_temp_session, store_temp_session

                temp_data = await get_temp_session(session_id)
                if temp_data is not None:
                    temp_data["title"] = llm_title
                    await store_temp_session(session_id, temp_data)
            else:
                await session_service.update_session(
                    _title_db, session_id, title=llm_title
                )
                await _title_db.commit()

    except Exception:
        _logger.exception(
            "Auto-title LLM: failed to generate title for session %s",
            session_id,
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
                    await asyncio.shield(stream_db.rollback())
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await asyncio.shield(stream_db.close())
                except (asyncio.CancelledError, Exception):
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
                # Heartbeat — inner task is still running, do NOT cancel it.
                # Loop sending heartbeats until the inner task completes
                # so the connection stays alive even during long tool calls.
                while True:
                    yield {"event": "heartbeat", "data": "{}"}
                    done2, _pending2 = await asyncio.wait(
                        [anext_task], timeout=interval
                    )
                    if done2:
                        try:
                            event_dict = await anext_task
                        except StopAsyncIteration:
                            return  # break out of outer loop too
                        yield event_dict
                        break  # back to outer loop for next event
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

    NOTE: FastAPI closes the dependency-injected ``db`` session as soon as
    this function returns the ``EventSourceResponse`` — well before the
    streaming generator finishes.  We therefore create a **dedicated**
    session here so the generator's ``finally`` block can persist the
    assistant message without hitting ``ResourceClosedError``.
    """
    from ..db.base import AsyncSessionLocal

    message_id = str(uuid.uuid4())
    _file_ids = file_ids or []

    async def inner_gen() -> AsyncIterator[dict]:
        """The inner generator that yields SSE event dicts."""
        async with AsyncSessionLocal() as stream_db:
            try:
                async for event_dict in run_agent_stream(
                    session_data=data,
                    user_message=user_text,
                    db=stream_db,
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
                    await asyncio.shield(stream_db.rollback())
                except (asyncio.CancelledError, Exception):
                    pass
                try:
                    await asyncio.shield(stream_db.close())
                except (asyncio.CancelledError, Exception):
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
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Retrieve follow-up questions for a session from Redis.

    Questions are generated asynchronously by a background task after
    the SSE stream closes.  This endpoint is polled once by the frontend
    when the stream finishes.  Returns an empty list if questions haven't
    been generated yet or if the model doesn't support them.
    """
    from ..core.redis import get_follow_up_questions as _redis_get_follow_up

    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    questions = await _redis_get_follow_up(current_user.tenant_id, session_id)
    return FollowUpQuestionsResponse(questions=questions or [])


# =============================================================================
# Session Context Window — Issue #309
# =============================================================================


class SessionContextResponse(BaseModel):
    """Context window usage for a session.

    Uses the most recent assistant message's provider-reported tokens_in
    as the total context consumed.  context_length comes from the model.
    """
    tokens_used: int = 0
    context_length: int | None = None
    percentage: float | None = None
    """Computed percentage (0.0–100.0), or None if context_length is unknown."""


@router.get(
    "/session/{session_id}/context",
    response_model=SessionContextResponse,
)
async def get_session_context(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Get context window usage for a session.

    Returns the most recent provider-reported token count and the
    session's model context length, so the UI can render a progress
    indicator.
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    is_temporary = data.get("is_temporary", False)

    # Resolve model context_length
    context_length: int | None = None
    model_id = data.get("selected_model_id")
    if model_id:
        try:
            from ..db.orm.models import Model as ModelORM
            model_result = await db.execute(
                select(ModelORM).where(ModelORM.id == model_id)
            )
            model_row = model_result.scalar_one_or_none()
            if model_row is not None:
                context_length = model_row.context_length
        except Exception:
            logger.warning(
                "Failed to resolve context_length for model %s", model_id,
            )

    # Get messages to find the most recent token count
    from ..agents.runner import (
        _get_messages_for_session,
        _estimate_tokens,
        _extract_message_text,
    )

    all_messages = await _get_messages_for_session(db, session_id, is_temporary)

    # Check if any messages have been summarized
    has_summarized = any(
        _msg_get(m, "summarized", False) for m in all_messages
    )

    if has_summarized:
        # After summarization, the actual context is: summary + recent messages.
        # Estimate the current tokens by summing the summary system message
        # and all non-summarized messages.
        tokens_used = 0
        for msg in all_messages:
            sender = _msg_get(msg, "sender", "")
            is_summarized = _msg_get(msg, "summarized", False)

            # Include system messages (the summary) and non-summarized messages
            if sender == "system" or not is_summarized:
                text = _extract_message_text(_msg_get(msg, "content"))
                tokens_used += _estimate_tokens(text)
    else:
        # No summarization — use the most recent assistant's provider-reported tokens_in
        tokens_used = 0
        for msg in reversed(all_messages):
            sender = _msg_get(msg, "sender", "")
            if sender != "assistant":
                continue
            tokens_in = _msg_get(msg, "tokens_in", None)
            if tokens_in is not None and tokens_in > 0:
                tokens_used = tokens_in
                break

    # Compute percentage
    percentage: float | None = None
    if context_length is not None and context_length > 0:
        percentage = round(tokens_used / context_length * 100, 1)

    return SessionContextResponse(
        tokens_used=tokens_used,
        context_length=context_length,
        percentage=percentage,
    )


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
    """Upload a file to a session (stored in MinIO).

    Supports lazy (pending) sessions: if the session doesn't exist yet
    (e.g. "New Chat" before the first message), a minimal temporary Redis
    session is created on-the-fly.  The session will be promoted to
    permanent when the first message is sent (see ``send_message``).
    """
    try:
        data = await _load_session(db, session_id)
        await _require_session_owner(data, current_user)
    except NotFoundError:
        logger.info(
            "Lazy-creating temp session %s on file upload", session_id,
        )
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "id": session_id,
            "tenant_id": current_user.tenant_id,
            "user_id": current_user.id,
            "title": "New Chat",
            "is_temporary": True,
            "is_pinned": False,
            "selected_template_id": None,
            "selected_skill_id": None,
            "selected_model_id": None,
            "auto_route_enabled": False,
            "auto_select_tools": True,
            "thinking_enabled": None,
            "temperature": None,
            "active_tool_ids": None,
            "created_at": now,
            "updated_at": now,
        }
        await store_temp_session(session_id, data)

    # ---- Temp → permanent promotion (Issue #368) -----------------------
    # If the session is temporary (either newly lazy-created or loaded
    # from Redis as “New Chat”), promote it to permanent before
    # proceeding with the upload.
    if data.get("is_temporary"):
        logger.info(
            "Promoting temp session %s to permanent on file upload",
            session_id,
        )
        # Snapshot existing temp messages BEFORE promotion — must
        # migrate them to MariaDB or they are lost with the Redis keys
        # (Issue #439).
        temp_messages = await get_temp_messages(session_id)

        session_create = SessionCreate(
            title=data.get("title", "New Chat"),
            is_temporary=False,
            is_pinned=data.get("is_pinned", False),
            selected_template_id=data.get("selected_template_id"),
            selected_skill_id=data.get("selected_skill_id"),
            selected_model_id=data.get("selected_model_id"),
            active_tool_ids=data.get("active_tool_ids"),
            thinking_enabled=data.get("thinking_enabled"),
            temperature=data.get("temperature"),
            auto_route_enabled=data.get("auto_route_enabled", False),
            auto_select_tools=data.get("auto_select_tools", True),
        )
        data = await _lazy_create_session(
            db, session_id, session_create, current_user,
        )

        # Migrate existing temp messages to the new permanent session
        if temp_messages:
            from datetime import timezone as _tz
            for msg in temp_messages:
                created_at = msg.get("created_at")
                if created_at and isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at)
                elif not created_at:
                    created_at = datetime.now(_tz.utc)

                db_msg = Message(
                    id=msg.get("id", str(uuid.uuid4())),
                    session_id=session_id,
                    sender=msg.get("sender", "user"),
                    content=msg.get("content"),
                    model_id=msg.get("model_id"),
                    tool_calls=msg.get("tool_calls"),
                    tokens_in=msg.get("tokens_in"),
                    tokens_out=msg.get("tokens_out"),
                    created_at=created_at,
                )
                db.add(db_msg)
            await db.flush()
            logger.info(
                "Migrated %d temp messages to permanent session %s during upload promotion",
                len(temp_messages), session_id,
            )

        # Re-link any files uploaded during the pending phase
        await upload_service.link_pending_uploads_to_session(
            db, session_id, current_user.id,
        )
        await delete_temp_session(session_id)

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
                upload_service._encode_content_disposition_filename(
                    upload.original_filename
                )
            ),
            "Content-Length": str(upload.size_bytes),
        },
    )


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _sanitize_filename(title: str) -> str:
    """Remove unsafe characters from a session title for use as a filename."""
    safe = re.sub(r'[\\/:*?"<>|]', "_", title)
    safe = safe.strip().replace(" ", "_") or "conversation"
    return safe[:200]


def _build_export_dict(
    session_data: dict,
    messages: list,
) -> dict:
    """Build the versioned JSON export payload.

    Designed for round-trip compatibility with a future import feature.
    Instance-specific IDs (message id, session_id) are deliberately omitted.
    """
    now = datetime.now(timezone.utc).isoformat()

    return {
        "version": 1,
        "exported_at": now,
        "application": "ph-agent-hub",
        "session": {
            "title": session_data.get("title", ""),
            "created_at": _fmt_dt(session_data.get("created_at")),
            "updated_at": _fmt_dt(session_data.get("updated_at")),
            "is_temporary": session_data.get("is_temporary", False),
        },
        "messages": [
            {
                "sender": m.get("sender"),
                "content": m.get("content"),
                "model_name": m.get("model_name"),
                "model_provider": m.get("model_provider"),
                "tool_calls": m.get("tool_calls"),
                "tokens_in": m.get("tokens_in"),
                "tokens_out": m.get("tokens_out"),
                "summarized": m.get("summarized", False),
                "created_at": _fmt_dt(m.get("created_at")),
            }
            for m in messages
        ],
    }


def _build_export_txt(
    session_data: dict,
    messages: list,
) -> str:
    """Build a human-readable plain-text conversation transcript."""
    sep = "=" * 60
    lines: list[str] = [
        sep,
        f"Conversation: {session_data.get('title', 'Untitled')}",
        f"Exported: {datetime.now(timezone.utc).isoformat()}",
        sep,
        "",
    ]

    for m in messages:
        sender = m.get("sender", "unknown")
        summarized = m.get("summarized", False)
        created = _fmt_dt(m.get("created_at"))
        model_name = m.get("model_name")
        model_provider = m.get("model_provider")

        # Build header
        header = f"[{sender.capitalize()}]"
        if created:
            header += f" \u2014 {created}"
        if sender == "assistant" and model_name:
            provider = f" ({model_provider})" if model_provider else ""
            header += f" \u00b7 {model_name}{provider}"
        lines.append(header)

        if summarized:
            lines.append("[Summarized \u2014 content compressed into summary]")
        else:
            text = _extract_message_text(m.get("content"))
            if text:
                lines.append(text)

        # Tool calls rendered inline
        tool_calls = m.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    name = tc.get("name") or (
                        tc.get("function", {}).get("name", "unknown")
                    )
                    lines.append(f"[Tool call: {name}]")

        lines.append("")

    return "\n".join(lines)


def _fmt_dt(dt_val: Any) -> str | None:
    """Format a datetime-like value to ISO string or None."""
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        return dt_val.isoformat()
    if isinstance(dt_val, str):
        return dt_val
    return str(dt_val)


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _validate_import_payload(payload: dict) -> tuple[str, list[dict]]:
    """Validate an import JSON payload and extract (title, messages).

    Raises ValidationError on any structural issue.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Import payload must be a JSON object")

    version = payload.get("version")
    if version != 1:
        raise ValidationError(
            f"Unsupported export version {version}. Only version 1 is supported."
        )

    session = payload.get("session")
    if not isinstance(session, dict):
        raise ValidationError("Missing or invalid 'session' block")

    title = session.get("title", "Imported Chat")
    if not isinstance(title, str) or not title.strip():
        title = "Imported Chat"

    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValidationError("Missing or invalid 'messages' array")

    if not messages:
        raise ValidationError("No messages to import")

    allowed_senders = {"user", "assistant", "system"}
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            raise ValidationError(f"Message at index {i} is not an object")
        sender = msg.get("sender")
        if sender not in allowed_senders:
            raise ValidationError(
                f"Message at index {i}: invalid sender '{sender}'"
            )

    return title.strip(), messages


def _parse_import_datetime(dt_val: Any) -> datetime:
    """Parse a datetime value from import payload to a datetime object."""
    if dt_val is None:
        return datetime.now(timezone.utc)
    if isinstance(dt_val, datetime):
        return dt_val
    if isinstance(dt_val, str):
        try:
            return datetime.fromisoformat(dt_val)
        except (ValueError, TypeError):
            logger.warning(
                "Could not parse datetime '%s', using current time", dt_val
            )
            return datetime.now(timezone.utc)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Export endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/session/{session_id}/export",
)
async def export_session(
    session_id: str,
    format: ExportFormat = ExportFormat.JSON,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Export a conversation session as JSON or plain text.

    Supported formats: json (default), txt.
    The JSON export is versioned for future round-trip import support.
    """
    data = await _load_session(db, session_id)
    await _require_session_owner(data, current_user)

    is_temp = data.get("is_temporary", False)

    # Load messages — dual path (DB / Redis) matching list_messages
    if is_temp:
        msgs = await get_temp_messages(session_id)
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
        orm_messages = result.scalars().all()
        msgs = [
            {
                "id": m.id,
                "session_id": m.session_id,
                "sender": m.sender,
                "content": m.content,
                "model_name": m.model.name if m.model else None,
                "model_provider": m.model.provider if m.model else None,
                "tool_calls": m.tool_calls,
                "tokens_in": m.tokens_in,
                "tokens_out": m.tokens_out,
                "summarized": m.summarized,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
            }
            for m in orm_messages
        ]

    # Build filename from session title
    title = data.get("title", "conversation")
    safe_title = _sanitize_filename(title)

    if format == ExportFormat.JSON:
        payload = _build_export_dict(data, msgs)
        content_bytes = json.dumps(payload, indent=2, default=str).encode("utf-8")
        media_type = "application/json"
        filename = f"{safe_title}.json"
    else:
        text = _build_export_txt(data, msgs)
        content_bytes = text.encode("utf-8")
        media_type = "text/plain; charset=utf-8"
        filename = f"{safe_title}.txt"

    return StreamingResponse(
        content=iter([content_bytes]),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content_bytes)),
        },
    )


# ---------------------------------------------------------------------------
# Import endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/import",
    status_code=201,
    response_model=ImportResponse,
)
async def import_session(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Import a conversation from a JSON export file.

    Creates a new permanent session and populates it with the imported
    messages. The import format matches the ``GET /session/{id}/export``
    JSON output (version 1). Only ``.json`` files are accepted.
    """
    # Validate file type
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise ValidationError("Only .json files are supported for import")

    # Read and parse
    try:
        raw = await file.read()
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Invalid JSON: {e}")

    # Validate structure
    title, messages_data = _validate_import_payload(payload)

    # Create a new permanent session
    session = await session_service.create_session(
        db=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=title,
        is_temporary=False,
    )

    # Bulk insert messages with new UUIDs
    msg_count = 0
    for msg_data in messages_data:
        created_at = _parse_import_datetime(msg_data.get("created_at"))
        msg = Message(
            id=str(uuid.uuid4()),
            session_id=session.id,
            sender=msg_data["sender"],
            content=msg_data.get("content"),
            tool_calls=msg_data.get("tool_calls"),
            tokens_in=msg_data.get("tokens_in"),
            tokens_out=msg_data.get("tokens_out"),
            summarized=msg_data.get("summarized", False),
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(msg)
        msg_count += 1

    await db.commit()

    logger.info(
        "User %s imported session %s with %d messages",
        current_user.id,
        session.id,
        msg_count,
    )

    return ImportResponse(
        session_id=session.id,
        message_count=msg_count,
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

