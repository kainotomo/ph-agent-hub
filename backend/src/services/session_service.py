# =============================================================================
# PH Agent Hub — Session Service (CRUD + tool activation)
# =============================================================================

import asyncio

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError, SessionBusyError, ValidationError
from ..db.orm.messages import Message
from ..db.orm.sessions import Session, SessionActiveTool
from ..db.orm.tools import Tool
from ..db.orm.users import User
from ..db.orm.autopilot_runs import AutopilotRun
from ..db.orm.skills import Skill, SkillAllowedTool
from ..services.model_service import list_models as _svc_list_models
from .run_registry import (
    begin_session_delete,
    clear_session_run_state,
    end_session_delete,
    get_run,
    request_run_cancel,
)


# ---------------------------------------------------------------------------
# Sidebar (grouped, server-paged) data source
# ---------------------------------------------------------------------------

SIDEBAR_UNFILED_KEY = "unfiled"
DEFAULT_SIDEBAR_PER_GROUP = 5
MAX_SIDEBAR_PER_GROUP = 50


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


async def create_session(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    title: str,
    id: str | None = None,
    is_temporary: bool = False,
    is_pinned: bool = False,
    selected_template_id: str | None = None,
    selected_skill_id: str | None = None,
    selected_model_id: str | None = None,
    thinking_enabled: bool | None = None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    auto_route_enabled: bool = False,
    auto_select_tools: bool = True,
    folder_id: str | None = None,
) -> Session:
    """Create a new permanent session.

    When *id* is provided the session will be created with that explicit
    primary key (used by lazy persistence — ``send_message`` creates the
    session on first message with the URL session ID).

    If auto_route_enabled is True, selected_model_id is intentionally kept
    None — the model will be resolved on the first user message by the
    router service.
    """
    # Auto-assign model only if auto-routing is NOT enabled
    if not auto_route_enabled and selected_model_id is None:
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
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        auto_route_enabled=auto_route_enabled,
        auto_select_tools=auto_select_tools,
        folder_id=folder_id,
    )
    if id is not None:
        session.id = id
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session_by_id(db: AsyncSession, session_id: str) -> Session | None:
    """Look up a session by primary key."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    return result.scalar_one_or_none()


def _visible_sessions_filters(
    user_id: str, tenant_id: str
) -> tuple:
    """Return SQLAlchemy filter conditions for sessions visible to a user.

    Filters to the given user/tenant, excludes temporary sessions, and keeps
    only sessions that either have a non-deleted message or were created
    within the last hour (so recently created empty sessions are preserved).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    has_messages = exists(
        select(1)
        .where(
            Message.session_id == Session.id,
            Message.is_deleted == False,  # noqa: E712
        )
        .correlate(Session)
    )
    return (
        Session.user_id == user_id,
        Session.tenant_id == tenant_id,
        Session.is_temporary == False,  # noqa: E712
        (has_messages | (Session.created_at >= cutoff)),
    )


async def list_sessions_for_user(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
) -> list[Session]:
    """Return permanent sessions for a user in their tenant.

    Temporary sessions are excluded from the list.  Empty sessions (zero
    messages) older than 1 hour are automatically purged.  Recently
    created empty sessions (< 1 hour) are preserved so the user can still
    configure them before sending their first message.
    """
    # Purge abandoned empty sessions older than 1 hour
    await _purge_empty_sessions(db, user_id, tenant_id)

    visible = _visible_sessions_filters(user_id, tenant_id)

    stmt = (
        select(Session)
        .where(*visible)
        .order_by(Session.updated_at.desc())
        .limit(2000)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_sidebar_summary(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    per_group: int = DEFAULT_SIDEBAR_PER_GROUP,
) -> tuple[list[Session], dict[str | None, int]]:
    """Return a grouped sidebar summary for a user.

    Returns up to *per_group* sessions per folder (grouped by ``folder_id``,
    where ``None`` is the Unfiled group) plus a per-folder total count
    (``dict[str | None, int]`` keyed by ``folder_id``).  Sessions are ordered
    by pinned status, then ``updated_at`` desc, then id desc within each
    group.
    """
    await _purge_empty_sessions(db, user_id, tenant_id)

    visible = _visible_sessions_filters(user_id, tenant_id)

    # Windowed row-number per folder, then keep only the ids within the
    # per-group limit.  The subquery must expose the session id (not the row
    # number) so it can be used with ``Session.id.in_(...)``.
    ranked = (
        select(
            Session.id.label("id"),
            func.row_number().over(
                partition_by=Session.folder_id,
                order_by=(
                    Session.is_pinned.desc(),
                    Session.updated_at.desc(),
                    Session.id.desc(),
                ),
            ).label("rn"),
        )
        .where(*visible)
        .subquery()
    )
    top_ids = select(ranked.c.id).where(ranked.c.rn <= per_group)

    result = await db.execute(select(Session).where(Session.id.in_(top_ids)))
    sessions = list(result.scalars().all())

    # Sort in Python (pinned first, then updated_at desc, then id desc)
    sessions = sorted(
        sessions,
        key=lambda s: (s.is_pinned, s.updated_at, s.id),
        reverse=True,
    )

    # Per-folder totals
    totals_stmt = (
        select(Session.folder_id, func.count(Session.id))
        .where(*visible)
        .group_by(Session.folder_id)
    )
    totals_result = await db.execute(totals_stmt)
    totals = dict(totals_result.all())

    return sessions, totals


async def list_sidebar_group_page(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    folder_id: str | None,
    offset: int,
    limit: int = DEFAULT_SIDEBAR_PER_GROUP,
) -> tuple[list[Session], int]:
    """Return one server-paged group of sessions for a user.

    Returns up to *limit* sessions for the given *folder_id* (``None`` =
    Unfiled) plus the total number of matching sessions.  Sessions are ordered
    by pinned status, then ``updated_at`` desc, then id desc.
    """
    visible = _visible_sessions_filters(user_id, tenant_id)
    scope = (
        Session.folder_id.is_(None)
        if folder_id is None
        else Session.folder_id == folder_id
    )

    total = await db.scalar(
        select(func.count(Session.id)).where(*visible, scope)
    ) or 0

    stmt = (
        select(Session)
        .where(*visible, scope)
        .order_by(
            Session.is_pinned.desc(),
            Session.updated_at.desc(),
            Session.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all()), total


async def _purge_empty_sessions(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
) -> int:
    """Hard-delete permanent sessions with zero messages older than 1 hour.

    Returns the number of sessions deleted.  Called automatically by
    ``list_sessions_for_user()`` — no explicit invocation needed.
    """
    from sqlalchemy import delete as sa_delete
    from ..db.orm.sessions import SessionActiveTool as SAT
    from ..db.orm.file_uploads import FileUpload
    from ..db.orm.memory import Memory
    from ..db.orm.tags import SessionTag

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)

    has_messages = exists(
        select(1)
        .where(
            Message.session_id == Session.id,
            Message.is_deleted == False,  # noqa: E712
        )
        .correlate(Session)
    )

    result = await db.execute(
        select(Session).where(
            Session.user_id == user_id,
            Session.tenant_id == tenant_id,
            Session.is_temporary == False,  # noqa: E712
            ~has_messages,
            Session.created_at < cutoff,
        )
    )
    sessions = list(result.scalars().all())
    if not sessions:
        return 0

    session_ids = [s.id for s in sessions]

    # 1. Delete file uploads (MinIO objects + DB rows)
    from ..services import upload_service

    for sid in session_ids:
        await upload_service.delete_uploads_for_session(db, sid)

    # 2. Delete session-scoped memories
    await db.execute(
        sa_delete(Memory).where(Memory.session_id.in_(session_ids))
    )

    # 3. Delete active tool associations
    await db.execute(
        sa_delete(SAT).where(SAT.session_id.in_(session_ids))
    )

    # 4. Delete session tags
    await db.execute(
        sa_delete(SessionTag).where(
            SessionTag.session_id.in_(session_ids)
        )
    )

    # 5. Delete the sessions themselves
    for s in sessions:
        db.expire(s, ["tags"])
        await db.delete(s)

    await db.commit()

    return len(session_ids)


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
    """Update a session's fields. Raises NotFoundError if missing.

    Retries on MariaDB 1020 ("Record has changed since last read") to
    handle concurrent updates from background tasks (auto-title, tagging)
    that may touch the same session row in a separate DB session.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = await db.execute(
                select(Session)
                .where(Session.id == session_id)
                .with_for_update()
            )
            session = result.scalar_one_or_none()
            if session is None:
                raise NotFoundError("Session not found")

            for key, value in fields.items():
                if hasattr(session, key):
                    setattr(session, key, value)

            await db.commit()
            await db.refresh(session)
            return session

        except OperationalError as exc:
            if "1020" in str(exc) and attempt < max_retries - 1:
                await db.rollback()
                backoff = 0.1 * (attempt + 1)
                await asyncio.sleep(backoff)
                continue
            raise


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

    # A live run must be stopped before its session disappears: the run's
    # terminal write would otherwise hit a deleted row (and a client would
    # keep a spinner for a conversation that no longer exists).
    lease = await get_run(session_id)
    if lease is not None:
        raise SessionBusyError(session_id, lease.run_id, lease.kind)

    owner_id = session.user_id

    # Get all message IDs for this session first
    result = await db.execute(
        select(Message.id).where(Message.session_id == session_id)
    )
    message_ids = [row[0] for row in result.all()]

    # 0. Delete autopilot runs (Phase 3 — FK has ON DELETE CASCADE but
    #    SQLAlchemy tries to SET NULL which fails on NOT NULL column).
    await db.execute(
        sa_delete(AutopilotRun).where(AutopilotRun.session_id == session_id)
    )

    # 1. Delete file uploads BEFORE messages (otherwise FK constraint fails)
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

    # 8. Drop the run-registry trace (lease, marker, cancellation flags and
    #    the per-user index entry), so no tab can show a spinner for a
    #    conversation that is gone.
    try:
        await clear_session_run_state(session_id, owner_id)
    except Exception:
        # Registry cleanup is best-effort: the registry entries expire on
        # their own (lease TTL / marker TTL) and the session row is already
        # deleted, which is the durable outcome.
        import logging
        logging.getLogger(__name__).warning(
            "Failed to clear run state for deleted session %s",
            session_id, exc_info=True,
        )


async def delete_sessions_batch(
    db: AsyncSession,
    session_ids: list[str],
    user_id: str,
    tenant_id: str,
    admin_override: bool = False,
) -> dict:
    """Delete multiple sessions by ID in a single transaction.

    For each ID, checks existence and ownership.  Separates DB (permanent)
    sessions from Redis (temporary) sessions.  Returns a dict with:
      - ``deleted``: number of sessions successfully deleted
      - ``skipped``: list of ``{"id": str, "reason": str}`` for sessions that
        could not be deleted (not found, not owned, streaming, already temp)
      - ``errors``: list of ``{"id": str, "error": str}`` for unexpected errors

    When ``admin_override=True``, skips user ownership checks (the caller is
    responsible for any authorization, e.g. manager tenant scoping).

    The function processes DB sessions in bulk using ``.in_(session_ids)``
    for FK-dependent tables (same cascade pattern as ``_purge_empty_sessions``),
    then handles temp (Redis) sessions one at a time.
    """
    from sqlalchemy import delete as sa_delete
    from ..core.redis import delete_temp_session, get_temp_session
    from ..db.orm.messages import Message, MessageFeedback
    from ..db.orm.sessions import SessionActiveTool as SAT
    from ..db.orm.file_uploads import FileUpload
    from ..db.orm.memory import Memory
    from ..db.orm.tags import SessionTag
    from ..services import upload_service

    skipped: list[dict] = []
    errors: list[dict] = []
    db_session_ids: list[str] = []
    temp_session_ids: list[str] = []
    owners: dict[str, str] = {}
    locks: dict[str, str] = {}

    # --- Phase 1: Validate each session ---
    for sid in session_ids:
        # Try DB lookup first
        session_orm = await get_session_by_id(db, sid)
        if session_orm is not None:
            if not admin_override and session_orm.user_id != user_id:
                skipped.append({"id": sid, "reason": "Not owned by current user"})
                continue
            if not admin_override and session_orm.tenant_id != tenant_id:
                skipped.append({"id": sid, "reason": "Session belongs to a different tenant"})
                continue
            owners[sid] = session_orm.user_id
            db_session_ids.append(sid)
            continue

        # Try Redis (temporary session)
        temp = await get_temp_session(sid)
        if temp is not None:
            t_user = temp.get("user_id")
            t_tenant = temp.get("tenant_id")
            if not admin_override and t_user != user_id:
                skipped.append({"id": sid, "reason": "Not owned by current user"})
                continue
            if not admin_override and t_tenant != tenant_id:
                skipped.append({"id": sid, "reason": "Session belongs to a different tenant"})
                continue
            owners[sid] = str(t_user or user_id)
            temp_session_ids.append(sid)
            continue

        # Not found anywhere
        skipped.append({"id": sid, "reason": "Session not found"})

    # --- Phase 1.5: Stop live runs and lock the sessions ---
    # A session cannot be deleted while an agent is writing into it, and no
    # new run may start once the deletion owns the lock.  Busy sessions are
    # cancelled and skipped (the caller can retry once they stop) instead of
    # deleting a row a live run still needs.
    accepted: list[str] = []
    for sid in db_session_ids + temp_session_ids:
        lease = await get_run(sid)
        if lease is not None:
            await request_run_cancel(sid, lease.run_id)
            skipped.append({"id": sid, "reason": "Agent is running"})
            continue
        token = await begin_session_delete(sid)
        if token is None:
            skipped.append({"id": sid, "reason": "Deletion already in progress"})
            continue
        locks[sid] = token
        accepted.append(sid)

    accepted_set = set(accepted)
    db_session_ids = [sid for sid in db_session_ids if sid in accepted_set]
    temp_session_ids = [sid for sid in temp_session_ids if sid in accepted_set]

    async def _release_session_locks(deleted_ids: list[str]) -> None:
        for sid in deleted_ids:
            try:
                await clear_session_run_state(sid, owners.get(sid, user_id))
            except Exception:
                pass
        for sid, token in locks.items():
            try:
                await end_session_delete(sid, token)
            except Exception:
                pass

    # --- Phase 2: Delete DB (permanent) sessions ---
    if db_session_ids:
        # Collect all message IDs across all targeted sessions
        msg_result = await db.execute(
            select(Message.id).where(Message.session_id.in_(db_session_ids))
        )
        all_message_ids = [row[0] for row in msg_result.all()]

        # 0. Delete autopilot runs
        await db.execute(
            sa_delete(AutopilotRun).where(AutopilotRun.session_id.in_(db_session_ids))
        )

        # 1. Delete file uploads BEFORE messages
        for sid in db_session_ids:
            await upload_service.delete_uploads_for_session(db, sid)

        # 2. Delete message feedbacks
        if all_message_ids:
            await db.execute(
                sa_delete(MessageFeedback).where(
                    MessageFeedback.message_id.in_(all_message_ids)
                )
            )
            await db.flush()

        # 3. Delete messages
        await db.execute(
            sa_delete(Message).where(Message.session_id.in_(db_session_ids))
        )
        await db.flush()

        # 4. Delete active tool associations
        await db.execute(
            sa_delete(SAT).where(SAT.session_id.in_(db_session_ids))
        )
        await db.flush()

        # 5. Delete session tags
        await db.execute(
            sa_delete(SessionTag).where(SessionTag.session_id.in_(db_session_ids))
        )
        await db.flush()

        # 6. Delete session-scoped memories
        await db.execute(
            sa_delete(Memory).where(Memory.session_id.in_(db_session_ids))
        )
        await db.flush()

        # 7. Delete the session rows
        for sid in db_session_ids:
            session_orm = await get_session_by_id(db, sid)
            if session_orm is not None:
                db.expire(session_orm, ["tags"])
                await db.delete(session_orm)

        await db.flush()

    # --- Phase 3: Delete temp (Redis) sessions ---
    for sid in temp_session_ids:
        try:
            # Clean up file uploads before deleting Redis keys
            temp = await get_temp_session(sid)
            if temp:
                uploaded_ids: list[str] = temp.get("uploaded_file_ids", [])
                for file_id in uploaded_ids:
                    await upload_service._delete_temp_upload_by_id(db, file_id)
            await delete_temp_session(sid)
        except Exception as exc:
            errors.append({"id": sid, "error": str(exc)})
            temp_session_ids.remove(sid)

    # --- Phase 4: Commit and finalize ---
    if db_session_ids:
        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            errors.append({"id": "batch", "error": f"Transaction failed: {exc}"})
            # Reconstruct skipped from what we tried
            deleted_count = 0
            for sid in db_session_ids:
                skipped.insert(0, {"id": sid, "reason": "Transaction rolled back"})
            db_session_ids = []
            await _release_session_locks(list(temp_session_ids))
            return {
                "deleted": deleted_count,
                "skipped": skipped,
                "errors": errors,
            }

    deleted_count = len(db_session_ids) + len(temp_session_ids)

    await _release_session_locks(db_session_ids + temp_session_ids)

    return {
        "deleted": deleted_count,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Batch move sessions into a folder
# ---------------------------------------------------------------------------


async def move_sessions_batch(
    db: AsyncSession,
    session_ids: list[str],
    folder_id: str | None,
    user_id: str,
    tenant_id: str,
) -> dict:
    """Move multiple sessions into a folder (None = Unfiled) in one call.

    Skips (rather than fails) ids that are missing, not owned, cross-tenant,
    temporary, or already in the target folder.  Returns
    ``{"moved": int, "skipped": [{"id", "reason"}]}``.

    No ``errors`` list: the write is one statement, so an unexpected DB
    failure propagates as an HTTP error (frontend ``onError``).
    """
    from ..core.redis import get_temp_session

    # --- Phase 1: Resolve session ids (DB + Redis) -------------------------
    stmt = select(Session).where(Session.id.in_(session_ids))
    result = await db.execute(stmt)
    rows = result.scalars().all()
    row_map: dict[str, Session] = {row.id: row for row in rows}

    to_move: list[str] = []
    skipped: list[dict[str, str]] = []

    for sid in session_ids:
        # --- DB session ---
        if sid in row_map:
            row = row_map[sid]
            if row.user_id != user_id:
                skipped.append({"id": sid, "reason": "Not owned by current user"})
                continue
            if row.tenant_id != tenant_id:
                skipped.append({"id": sid, "reason": "Session belongs to a different tenant"})
                continue
            if row.is_temporary:
                skipped.append({"id": sid, "reason": "Temporary chats cannot be filed"})
                continue
            if (row.folder_id or None) == folder_id:
                skipped.append({"id": sid, "reason": "Already in this folder"})
                continue
            to_move.append(sid)
            continue

        # --- Redis (temporary) session ---
        temp = await get_temp_session(sid)
        if temp is not None:
            skipped.append({"id": sid, "reason": "Temporary chats cannot be filed"})
            continue

        # --- Not found anywhere ---
        skipped.append({"id": sid, "reason": "Session not found"})

    # --- Phase 2: Single UPDATE for all sessions to move -------------------
    if to_move:
        await db.execute(
            update(Session)
            .where(Session.id.in_(to_move))
            .values(folder_id=folder_id)
        )
        await db.commit()

    return {
        "moved": len(to_move),
        "skipped": skipped,
    }


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
        reasoning_effort=temp_data.get("reasoning_effort"),
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
