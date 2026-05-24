# =============================================================================
# PH Agent Hub — Session Service (CRUD + tool activation)
# =============================================================================

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.sessions import Session, SessionActiveTool
from ..db.orm.tools import Tool
from ..db.orm.users import User
from ..db.orm.skills import Skill, SkillAllowedTool
from ..services.model_service import list_models as _svc_list_models


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


async def create_session(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    title: str,
    is_temporary: bool = False,
    is_pinned: bool = False,
    selected_template_id: str | None = None,
    selected_skill_id: str | None = None,
    selected_model_id: str | None = None,
    thinking_enabled: bool | None = None,
    temperature: float | None = None,
) -> Session:
    """Create a new permanent session.

    If no selected_model_id is provided, auto-assigns:
    1. user.default_model_id
    2. First accessible enabled model for the user
    """
    # Auto-assign model if none provided
    if selected_model_id is None:
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if user and user.default_model_id:
            selected_model_id = user.default_model_id
        else:
            # First accessible enabled model
            models, _ = await _svc_list_models(
                db, tenant_id=tenant_id, user_id=user_id
            )
            enabled = [m for m in models if m.enabled]
            if enabled:
                selected_model_id = enabled[0].id

    session = Session(
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
        is_temporary=is_temporary,
        is_pinned=is_pinned,
        selected_template_id=selected_template_id,
        selected_skill_id=selected_skill_id,
        selected_model_id=selected_model_id,
        thinking_enabled=thinking_enabled,
        temperature=temperature,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session_by_id(db: AsyncSession, session_id: str) -> Session | None:
    """Look up a session by primary key."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    return result.scalar_one_or_none()


async def list_sessions_for_user(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
) -> list[Session]:
    """Return all permanent sessions for a user in their tenant.

    Temporary sessions are excluded from the list.
    """
    stmt = (
        select(Session)
        .where(
            Session.user_id == user_id,
            Session.tenant_id == tenant_id,
            Session.is_temporary == False,  # noqa: E712
        )
        .order_by(Session.updated_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_admin_sessions(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    is_pinned: bool | None = None,
    is_temporary: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[Session], int]:
    """List sessions for admin views with filtering, sorting, pagination.

    Includes eager-loaded tags for display.
    """
    from sqlalchemy.orm import selectinload
    from ..db.orm.tags import Tag as TagORM, SessionTag as SessionTagORM

    stmt = (
        select(Session)
        .options(selectinload(Session.tags))
    )

    if tenant_id is not None:
        stmt = stmt.where(Session.tenant_id == tenant_id)
    if is_pinned is not None:
        stmt = stmt.where(Session.is_pinned == is_pinned)
    if is_temporary is not None:
        stmt = stmt.where(Session.is_temporary == is_temporary)

    if tag:
        stmt = (
            stmt
            .join(SessionTagORM, SessionTagORM.session_id == Session.id)
            .join(TagORM, TagORM.id == SessionTagORM.tag_id)
            .where(TagORM.name == tag.strip().lower())
        )

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(stmt, search, [Session.title])
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "title": Session.title,
            "updated_at": Session.updated_at,
            "created_at": Session.created_at,
        },
        default_sort=Session.updated_at.desc(),
    )

    items, total = await paginate(db, stmt, page=page, page_size=page_size)
    # Ensure unique results due to joins
    seen = set()
    unique_items = []
    for s in items:
        if s.id not in seen:
            seen.add(s.id)
            unique_items.append(s)
    return unique_items, total


async def update_session(
    db: AsyncSession,
    session_id: str,
    **fields,
) -> Session:
    """Update a session's fields. Raises NotFoundError if missing."""
    session = await get_session_by_id(db, session_id)
    if session is None:
        raise NotFoundError("Session not found")

    for key, value in fields.items():
        if hasattr(session, key):
            setattr(session, key, value)

    await db.commit()
    await db.refresh(session)
    return session


async def delete_session(db: AsyncSession, session_id: str) -> None:
    """Delete a session by ID.

    Clears all FK references (message feedback, file uploads, messages,
    active tools), then deletes the session. Raises NotFoundError if missing.
    """
    from sqlalchemy import delete as sa_delete
    from ..db.orm.messages import Message, MessageFeedback
    from ..db.orm.sessions import SessionActiveTool
    from ..db.orm.file_uploads import FileUpload

    session = await get_session_by_id(db, session_id)
    if session is None:
        raise NotFoundError("Session not found")

    # Get all message IDs for this session first
    result = await db.execute(
        select(Message.id).where(Message.session_id == session_id)
    )
    message_ids = [row[0] for row in result.all()]

    # 0. Delete file uploads BEFORE messages (otherwise FK constraint fails)
    from ..services import upload_service

    await upload_service.delete_uploads_for_session(db, session_id)

    # 1. Delete message feedbacks for these messages
    if message_ids:
        await db.execute(
            sa_delete(MessageFeedback).where(
                MessageFeedback.message_id.in_(message_ids)
            )
        )
        await db.flush()

    # 2. Delete all messages in this session
    await db.execute(
        sa_delete(Message).where(Message.session_id == session_id)
    )
    await db.flush()

    # 4. Delete active tool associations
    await db.execute(
        sa_delete(SessionActiveTool).where(
            SessionActiveTool.session_id == session_id
        )
    )
    await db.flush()

    # 5. Delete session tags
    from ..db.orm.tags import SessionTag

    await db.execute(
        sa_delete(SessionTag).where(SessionTag.session_id == session_id)
    )
    await db.flush()

    # Expire the tags relationship so the ORM doesn't try to delete the
    # already-removed association rows when db.delete(session) is called.
    db.expire(session, ["tags"])

    # 6. Delete session-scoped memories
    from ..db.orm.memory import Memory

    await db.execute(
        sa_delete(Memory).where(Memory.session_id == session_id)
    )
    await db.flush()

    # 7. Delete the session itself and commit all changes
    await db.delete(session)
    await db.commit()


# ---------------------------------------------------------------------------
# Session active tool management
# ---------------------------------------------------------------------------


async def get_session_tools(
    db: AsyncSession, session_id: str
) -> list[Tool]:
    """Return all active tools for a session (as Tool ORM objects)."""
    stmt = (
        select(Tool)
        .join(SessionActiveTool, SessionActiveTool.tool_id == Tool.id)
        .where(SessionActiveTool.session_id == session_id)
        .order_by(Tool.name)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_session_tool_ids(
    db: AsyncSession, session_id: str
) -> list[str]:
    """Return just the tool_id list for a session."""
    stmt = select(SessionActiveTool.tool_id).where(
        SessionActiveTool.session_id == session_id
    )
    result = await db.execute(stmt)
    return [row[0] for row in result.all()]


async def add_session_tool(
    db: AsyncSession,
    session_id: str,
    tool_id: str,
    tenant_id: str,
) -> SessionActiveTool:
    """Activate a tool for a session.

    Validates that the tool exists, belongs to the same tenant, and is enabled.
    Raises NotFoundError if the session or tool is not found.
    Raises ValidationError if the tool is disabled or from a different tenant.
    """
    # Verify session exists
    session = await get_session_by_id(db, session_id)
    if session is None:
        raise NotFoundError("Session not found")

    # Verify tool exists, is enabled, and belongs to the tenant
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise NotFoundError("Tool not found")
    if not tool.enabled:
        raise ValidationError("Tool is disabled")
    if tool.tenant_id != tenant_id:
        raise ValidationError("Tool does not belong to this tenant")

    # Check for duplicate
    existing = await db.execute(
        select(SessionActiveTool).where(
            SessionActiveTool.session_id == session_id,
            SessionActiveTool.tool_id == tool_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValidationError("Tool already active for this session")

    sat = SessionActiveTool(session_id=session_id, tool_id=tool_id)
    db.add(sat)
    await db.commit()
    await db.refresh(sat)
    return sat


async def remove_session_tool(
    db: AsyncSession,
    session_id: str,
    tool_id: str,
) -> None:
    """Deactivate a tool for a session.

    Raises NotFoundError if the association does not exist.
    """
    stmt = delete(SessionActiveTool).where(
        SessionActiveTool.session_id == session_id,
        SessionActiveTool.tool_id == tool_id,
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        raise NotFoundError("Tool not active for this session")
    await db.commit()


async def sync_session_tools_for_skill(
    db: AsyncSession,
    session_id: str,
    old_skill_id: str | None,
    new_skill_id: str | None,
    tenant_id: str,
    always_on_ids: list[str] | None = None,
) -> None:
    """Sync the session's active tools when the selected skill changes.

    When a skill is selected, its tools are auto-activated. When the skill
    is changed or cleared, the previous skill's tools are removed.
    Always-on tools are never removed.

    Resolution:
        to_remove = old_skill_tool_ids − new_skill_tool_ids − always_on_ids
        to_add    = new_skill_tool_ids − current_active_ids
    """
    always_on_set = set(always_on_ids or [])

    # Fetch tool IDs for old skill
    old_skill_tool_ids: set[str] = set()
    if old_skill_id:
        result = await db.execute(
            select(SkillAllowedTool.tool_id).where(
                SkillAllowedTool.skill_id == old_skill_id
            )
        )
        old_skill_tool_ids = {row[0] for row in result.all()}

    # Fetch tool IDs for new skill
    new_skill_tool_ids: set[str] = set()
    if new_skill_id:
        result = await db.execute(
            select(SkillAllowedTool.tool_id).where(
                SkillAllowedTool.skill_id == new_skill_id
            )
        )
        new_skill_tool_ids = {row[0] for row in result.all()}

    # Get current session active tool IDs
    current_ids = set(await _get_session_tool_ids(db, session_id))

    # Compute diff
    to_remove = old_skill_tool_ids - new_skill_tool_ids - always_on_set
    to_add = new_skill_tool_ids - current_ids

    # Apply changes
    for tool_id in to_remove:
        await db.execute(
            delete(SessionActiveTool).where(
                SessionActiveTool.session_id == session_id,
                SessionActiveTool.tool_id == tool_id,
            )
        )

    for tool_id in to_add:
        # Validate tool exists, is enabled, and belongs to tenant
        result = await db.execute(
            select(Tool).where(
                Tool.id == tool_id,
                Tool.enabled == True,  # noqa: E712
                Tool.tenant_id == tenant_id,
            )
        )
        tool = result.scalar_one_or_none()
        if tool is None:
            continue  # Skip invalid tools silently
        db.add(SessionActiveTool(session_id=session_id, tool_id=tool_id))

    if to_remove or to_add:
        await db.commit()


# ---------------------------------------------------------------------------
# Finalize (convert temporary → permanent)
# ---------------------------------------------------------------------------


async def finalize_session(
    db: AsyncSession,
    temp_data: dict,
    temp_messages: list[dict],
) -> Session:
    """Convert a temporary (Redis) session into a permanent (MariaDB) session.

    Creates a permanent Session record with the same ID, migrates all
    messages, activates tools, and re-points file uploads.
    Returns the created Session ORM object.
    """
    import uuid as _uuid
    from datetime import datetime, timezone
    from ..db.orm.messages import Message
    from sqlalchemy import update
    from ..db.orm.file_uploads import FileUpload

    session_id = temp_data["id"]

    # Auto-assign model if none was set (mirrors create_session logic)
    selected_model_id = temp_data.get("selected_model_id")
    if selected_model_id is None:
        user_result = await db.execute(
            select(User).where(User.id == temp_data["user_id"])
        )
        user = user_result.scalar_one_or_none()
        if user and user.default_model_id:
            selected_model_id = user.default_model_id
        else:
            models, _ = await _svc_list_models(
                db,
                tenant_id=temp_data["tenant_id"],
                user_id=temp_data["user_id"],
            )
            enabled = [m for m in models if m.enabled]
            if enabled:
                selected_model_id = enabled[0].id

    # 1. Create permanent session with the existing temp session ID
    session = Session(
        id=session_id,
        tenant_id=temp_data["tenant_id"],
        user_id=temp_data["user_id"],
        title=temp_data.get("title", "New Chat"),
        is_temporary=False,
        is_pinned=temp_data.get("is_pinned", False),
        selected_template_id=temp_data.get("selected_template_id"),
        selected_skill_id=temp_data.get("selected_skill_id"),
        selected_model_id=selected_model_id,
        thinking_enabled=temp_data.get("thinking_enabled"),
        temperature=temp_data.get("temperature"),
        cross_session_retrieval_enabled=temp_data.get(
            "cross_session_retrieval_enabled"
        ),
    )
    db.add(session)
    await db.flush()

    # 2. Migrate messages from Redis to MariaDB
    for msg in temp_messages:
        created_at = msg.get("created_at")
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
        else:
            created_at = datetime.now(timezone.utc)

        message = Message(
            id=msg.get("id", str(_uuid.uuid4())),
            session_id=session_id,
            sender=msg.get("sender", "user"),
            content=msg.get("content"),
            model_id=msg.get("model_id"),
            tool_calls=msg.get("tool_calls"),
            tokens_in=msg.get("tokens_in"),
            tokens_out=msg.get("tokens_out"),
            created_at=created_at,
        )
        db.add(message)
    await db.flush()

    # 3. Migrate active tool associations
    active_tool_ids: list[str] = temp_data.get("active_tool_ids", [])
    for tool_id in active_tool_ids:
        sat = SessionActiveTool(session_id=session_id, tool_id=tool_id)
        db.add(sat)
    await db.flush()

    # 4. Re-point file uploads to the permanent session
    #    (uploaded_file_ids is stored in the temp blob, but the actual
    #    FileUpload rows in MariaDB already reference session_id — no
    #    update needed since we used the same ID.)

    await db.commit()
    await db.refresh(session)
    return session


# ---------------------------------------------------------------------------
# Session tag management
# ---------------------------------------------------------------------------


async def get_or_create_tag(
    db: AsyncSession, tenant_id: str, name: str
) -> "Tag":
    """Get an existing tag by (tenant_id, name) or create it.

    Tag names are lower-cased and stripped before lookup/insert.
    """
    from ..db.orm.tags import Tag

    name = name.strip().lower()
    if not name:
        raise ValidationError("Tag name cannot be empty")

    result = await db.execute(
        select(Tag).where(Tag.tenant_id == tenant_id, Tag.name == name)
    )
    tag = result.scalar_one_or_none()
    if tag:
        return tag

    tag = Tag(tenant_id=tenant_id, name=name)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


async def list_tenant_tags(
    db: AsyncSession, tenant_id: str
) -> list["Tag"]:
    """Return all tags for a tenant, ordered by name."""
    from ..db.orm.tags import Tag

    result = await db.execute(
        select(Tag)
        .where(Tag.tenant_id == tenant_id)
        .order_by(Tag.name)
    )
    return list(result.scalars().all())


async def get_session_tags(
    db: AsyncSession, session_id: str
) -> list["Tag"]:
    """Return all tags associated with a session."""
    from ..db.orm.tags import Tag, SessionTag

    result = await db.execute(
        select(Tag)
        .join(SessionTag, SessionTag.tag_id == Tag.id)
        .where(SessionTag.session_id == session_id)
        .order_by(Tag.name)
    )
    return list(result.scalars().all())


async def add_tag_to_session(
    db: AsyncSession, session_id: str, tag_id: str
) -> bool:
    """Add a tag to a session. Returns True if added, False if already present."""
    from ..db.orm.tags import SessionTag

    # Check for existing association
    existing = await db.execute(
        select(SessionTag).where(
            SessionTag.session_id == session_id,
            SessionTag.tag_id == tag_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return False

    st = SessionTag(session_id=session_id, tag_id=tag_id)
    db.add(st)
    await db.commit()
    return True


async def remove_tag_from_session(
    db: AsyncSession, session_id: str, tag_id: str
) -> None:
    """Remove a tag from a session. Silently succeeds if not present."""
    from ..db.orm.tags import SessionTag

    await db.execute(
        delete(SessionTag).where(
            SessionTag.session_id == session_id,
            SessionTag.tag_id == tag_id,
        )
    )
    await db.commit()


async def list_sessions_by_tag(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    tag_name: str,
) -> list["Session"]:
    """Return sessions for a user that have the given tag name."""
    from ..db.orm.tags import Tag, SessionTag

    result = await db.execute(
        select(Session)
        .join(SessionTag, SessionTag.session_id == Session.id)
        .join(Tag, Tag.id == SessionTag.tag_id)
        .where(
            Session.user_id == user_id,
            Session.tenant_id == tenant_id,
            Tag.name == tag_name.strip().lower(),
            Session.is_temporary == False,  # noqa: E712
        )
        .order_by(Session.updated_at.desc())
    )
    return list(result.scalars().all())


async def delete_session_tags(
    db: AsyncSession, session_id: str
) -> None:
    """Delete all tag associations for a session (used during session deletion)."""
    from ..db.orm.tags import SessionTag

    await db.execute(
        delete(SessionTag).where(SessionTag.session_id == session_id)
    )
    await db.commit()
