# =============================================================================
# PH Agent Hub — Memory Service
# =============================================================================
# CRUD for the ``memory`` table.  Called by ``api/memory.py``.
# =============================================================================

import logging

from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ..db.orm.memory import Memory
from .embedding_service import rank_by_similarity

logger = logging.getLogger(__name__)

# ---- validation constants ----------------------------------------------------

MEMORY_KEY_MAX_CHARS = 255
MEMORY_VALUE_MAX_CHARS = 8000


async def list_memory(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    session_id: str | None = None,
    page: int | None = None,
    page_size: int = 50,
) -> tuple[list[Memory], int]:
    """List memory entries for a user, optionally scoped to a session.

    When ``page`` is None, returns all matching rows (no pagination).
    When ``page`` is an integer, returns a paginated slice with total count.
    Global memories (session_id IS NULL) are always included alongside
    session-scoped ones when ``session_id`` is provided.
    """
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.tenant_id == tenant_id,
    )
    if session_id is not None:
        stmt = stmt.where(
            (Memory.session_id == session_id) | (Memory.session_id.is_(None))
        )
    stmt = stmt.order_by(Memory.created_at.desc())

    from ..core.pagination import paginate
    return await paginate(db, stmt, page=page, page_size=page_size)


async def list_all_memories(
    db: AsyncSession,
    tenant_id: str | None = None,
    *,
    search: str | None = None,
    source: str | None = None,
    user_id: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[Memory], int]:
    """List all memory entries (admin) with filtering, sorting, pagination."""
    stmt = select(Memory)

    if tenant_id is not None:
        stmt = stmt.where(Memory.tenant_id == tenant_id)
    if user_id is not None:
        stmt = stmt.where(Memory.user_id == user_id)
    if source is not None:
        stmt = stmt.where(Memory.source == source)

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(stmt, search, [Memory.key, Memory.value])
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "key": Memory.key,
            "source": Memory.source,
            "created_at": Memory.created_at,
        },
        default_sort=Memory.created_at.desc(),
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


async def find_memory(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    key: str,
    session_id: str | None,
) -> Memory | None:
    """Find the memory entry for a key within a scope.

    ``session_id=None`` matches global entries (``session_id IS NULL``).
    Ordering plus ``.first()`` keeps the lookup deterministic and tolerant of
    any legacy duplicate rows, so it can never raise ``MultipleResultsFound``.
    """
    scope = (
        Memory.session_id.is_(None)
        if session_id is None
        else Memory.session_id == session_id
    )
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.tenant_id == tenant_id,
            Memory.key == key,
            scope,
        )
        .order_by(Memory.created_at, Memory.id)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def find_global_memory(
    db: AsyncSession, *, user_id: str, tenant_id: str, key: str,
) -> Memory | None:
    """Find the global memory entry for a key (session_id IS NULL)."""
    return await find_memory(
        db, user_id=user_id, tenant_id=tenant_id, key=key, session_id=None,
    )


async def set_global_memory(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    key: str,
    value: str,
    source: str = "automatic",
    commit: bool = True,
) -> Memory:
    """Create or update a global memory entry.

    Returns the Memory object.  Commits only when ``commit`` is True.
    """
    existing = await find_global_memory(
        db, user_id=user_id, tenant_id=tenant_id, key=key,
    )
    if existing is not None:
        existing.value = value
    else:
        memory = Memory(
            tenant_id=tenant_id,
            user_id=user_id,
            key=key,
            value=value,
            session_id=None,
            source=source,
        )
        db.add(memory)
        existing = memory

    if commit:
        await db.commit()
        await db.refresh(existing)
    return existing


async def remove_global_memory(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    key: str,
    commit: bool = True,
) -> bool:
    """Delete a global memory entry by key.

    Returns True if a row was deleted, False if none was found.
    Commits only when ``commit`` is True.
    """
    memory = await find_global_memory(
        db, user_id=user_id, tenant_id=tenant_id, key=key,
    )
    if memory is None:
        return False

    await db.execute(delete(Memory).where(Memory.id == memory.id))
    if commit:
        await db.commit()
    return True


async def memory_key_conflict(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    key: str,
    session_id: str | None,
    exclude_id: str | None = None,
) -> bool:
    """Return True when another row shares the same scope and key."""
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.tenant_id == tenant_id,
            Memory.key == key,
        )
    )
    # Same scope: None matches IS NULL, value matches = value.
    if session_id is None:
        stmt = stmt.where(Memory.session_id.is_(None))
    else:
        stmt = stmt.where(Memory.session_id == session_id)
    if exclude_id is not None:
        stmt = stmt.where(Memory.id != exclude_id)

    result = await db.execute(stmt)
    return result.first() is not None


async def create_memory(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    key: str,
    value: str,
    session_id: str | None = None,
    source: str = "manual",
) -> Memory:
    """Create a new memory entry."""
    memory = Memory(
        tenant_id=tenant_id,
        user_id=user_id,
        key=key,
        value=value,
        session_id=session_id,
        source=source,
    )
    db.add(memory)
    await db.commit()
    await db.refresh(memory)
    return memory


async def delete_memory(
    db: AsyncSession,
    memory_id: str,
    user_id: str,
    tenant_id: str,
) -> Memory:
    """Delete a memory entry.  Validates ownership before deleting.

    Returns the deleted entry so callers can audit it.
    """
    result = await db.execute(
        select(Memory).where(Memory.id == memory_id)
    )
    memory = result.scalar_one_or_none()

    if memory is None:
        raise NotFoundError("Memory entry not found")
    if memory.user_id != user_id or memory.tenant_id != tenant_id:
        raise ForbiddenError("You do not own this memory entry")

    await db.execute(delete(Memory).where(Memory.id == memory_id))
    await db.commit()
    return memory


async def prune_memories(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    max_entries: int | None = None,
    retention_days: int | None = None,
    commit: bool = True,
) -> int:
    """Enforce the memory growth policy for one user (Issue #569).

    Only GLOBAL entries (session_id IS NULL) are considered, and only
    rows with source="automatic" are ever deleted - user-created
    (source="manual") entries are never pruned.

    Two independent rules apply:
      * entry cap - when the user's global entry count exceeds max_entries
        (default settings.MEMORY_MAX_ENTRIES_PER_USER), the oldest
        automatic entries are deleted until the count fits.  0 disables.
      * age - when retention_days > 0 (default settings.MEMORY_RETENTION_DAYS),
        automatic entries older than that many days are deleted.

    Returns the number of deleted rows.  Commits only when commit is True.
    """
    if max_entries is None:
        max_entries = settings.MEMORY_MAX_ENTRIES_PER_USER
    if retention_days is None:
        retention_days = settings.MEMORY_RETENTION_DAYS

    base_where = [
        Memory.user_id == user_id,
        Memory.tenant_id == tenant_id,
        Memory.session_id.is_(None),
        Memory.source == "automatic",
    ]

    deleted_count = 0

    # 1. Age-based retention rule.
    if retention_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        age_stmt = delete(Memory).where(*base_where, Memory.created_at < cutoff)
        age_result = await db.execute(age_stmt)
        if age_result.rowcount is not None:
            deleted_count += age_result.rowcount

    # 2. Entry-cap rule.
    if max_entries > 0:
        total_stmt = select(func.count()).where(
            Memory.user_id == user_id,
            Memory.tenant_id == tenant_id,
            Memory.session_id.is_(None),
        )
        total_result = await db.execute(total_stmt)
        total = total_result.scalar_one()

        if total > max_entries:
            ids_to_delete = (
                select(Memory.id)
                .where(*base_where)
                .order_by(Memory.created_at.asc(), Memory.id.asc())
                .limit(total - max_entries)
            )
            ids_result = await db.execute(ids_to_delete)
            ids = [row[0] for row in ids_result.all()]
            if ids:
                delete_stmt = delete(Memory).where(Memory.id.in_(ids))
                del_result = await db.execute(delete_stmt)
                if del_result.rowcount is not None:
                    deleted_count += del_result.rowcount

    if commit:
        await db.commit()
    return deleted_count


async def clear_memories(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
) -> int:
    """Delete every memory entry for the user.  Returns rows deleted."""
    delete_stmt = delete(Memory).where(
        Memory.user_id == user_id,
        Memory.tenant_id == tenant_id,
    )
    result = await db.execute(delete_stmt)
    await db.commit()
    rowcount = result.rowcount
    return rowcount if rowcount is not None else 0


async def merge_memories(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    target_id: str,
    source_ids: list[str],
) -> Memory:
    """Merge memory entries into target_id and delete the sources.

    Ownership is validated for every involved row (NotFoundError when a
    row does not exist, ForbiddenError when it belongs to another user or
    tenant).  target_id must not appear in source_ids (ValidationError).
    Source values are appended to the target value in created_at order,
    skipping a source whose value is already contained in the merged
    text.  If the merged value would exceed MEMORY_VALUE_MAX_CHARS a
    ValidationError is raised and nothing is changed.  Returns the
    refreshed target row.
    """
    all_ids = list(dict.fromkeys([target_id] + list(source_ids)))
    rows_result = await db.execute(
        select(Memory).where(Memory.id.in_(all_ids))
    )
    rows = rows_result.scalars().all()

    if len(rows) != len(all_ids):
        raise NotFoundError("One or more memory entries not found")

    row_map = {row.id: row for row in rows}

    # Ownership is validated for the target as well as every source, otherwise
    # a user could merge text into somebody else's entry.
    for row_id in all_ids:
        row = row_map[row_id]
        if row.user_id != user_id or row.tenant_id != tenant_id:
            raise ForbiddenError("You do not own one or more memory entries")

    if target_id in source_ids:
        raise ValidationError("target_id must not appear in source_ids")

    target = row_map[target_id]

    # Build the merged value from the sources only, oldest first.  ``created_at``
    # can still be None on a row that was just flushed, so guard the sort key.
    sources_sorted = sorted(
        (row_map[src_id] for src_id in dict.fromkeys(source_ids)),
        key=lambda r: (r.created_at is None, r.created_at or datetime.min, r.id),
    )
    merged_value = target.value
    for src in sources_sorted:
        src_stripped = src.value.strip()
        if src_stripped and src_stripped not in merged_value:
            merged_value = merged_value + "\n\n" + src_stripped

    if len(merged_value) > MEMORY_VALUE_MAX_CHARS:
        raise ValidationError(
            f"Merged value would exceed maximum length of {MEMORY_VALUE_MAX_CHARS} characters"
        )

    # Update the target and delete the sources in one transaction so a failure
    # cannot leave the merge half-applied.
    target.value = merged_value
    await db.execute(delete(Memory).where(Memory.id.in_(list(source_ids))))
    await db.commit()
    await db.refresh(target)
    return target


async def admin_update_memory(
    db: AsyncSession,
    memory_id: str,
    key: str | None = None,
    value: str | None = None,
) -> Memory:
    """Update a memory entry.  Admin use only — no ownership check."""
    result = await db.execute(
        select(Memory).where(Memory.id == memory_id)
    )
    memory = result.scalar_one_or_none()

    if memory is None:
        raise NotFoundError("Memory entry not found")

    if key is not None:
        memory.key = key
    if value is not None:
        memory.value = value

    await db.commit()
    await db.refresh(memory)
    return memory


async def update_memory(
    db: AsyncSession,
    memory_id: str,
    user_id: str,
    tenant_id: str,
    key: str | None = None,
    value: str | None = None,
) -> Memory:
    """Update a memory entry's key and/or value. Validates ownership."""
    result = await db.execute(
        select(Memory).where(Memory.id == memory_id)
    )
    memory = result.scalar_one_or_none()

    if memory is None:
        raise NotFoundError("Memory entry not found")
    if memory.user_id != user_id or memory.tenant_id != tenant_id:
        raise ForbiddenError("You do not own this memory entry")

    # Validate input lengths before applying changes.
    if key is not None and len(key) > MEMORY_KEY_MAX_CHARS:
        raise ValidationError("Key exceeds maximum length of 255 characters")
    if value is not None and len(value) > MEMORY_VALUE_MAX_CHARS:
        raise ValidationError("Value exceeds maximum length of 8000 characters")

    # Check for key conflicts in the same scope, ignoring this row itself so
    # that editing the value without renaming the key is not a conflict.
    if key is not None:
        if await memory_key_conflict(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            key=key,
            session_id=memory.session_id,
            exclude_id=memory.id,
        ):
            raise ConflictError("A memory entry with this key already exists")

    if key is not None:
        memory.key = key
    if value is not None:
        memory.value = value

    await db.commit()
    await db.refresh(memory)
    return memory


async def admin_delete_memory(
    db: AsyncSession,
    memory_id: str,
) -> None:
    """Delete a memory entry by ID.  Admin use only — no ownership check."""
    result = await db.execute(select(Memory).where(Memory.id == memory_id))
    memory = result.scalar_one_or_none()
    if memory is None:
        raise NotFoundError("Memory entry not found")
    await db.execute(delete(Memory).where(Memory.id == memory_id))
    await db.commit()


async def upsert_memory(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    key: str,
    value: str,
    session_id: str | None = None,
    source: str = "automatic",
) -> Memory:
    """Insert or update a memory entry by (user_id, tenant_id, key, session_id).

    Handles both global entries (``session_id=None``) and session-scoped ones.
    The UNIQUE key ``uq_memory_user_tenant_key_scope`` guarantees that a
    concurrent insert cannot create a duplicate.
    """
    existing = await find_memory(
        db,
        user_id=user_id,
        tenant_id=tenant_id,
        key=key,
        session_id=session_id,
    )

    if existing is not None:
        existing.value = value
    else:
        existing = Memory(
            tenant_id=tenant_id,
            user_id=user_id,
            key=key,
            value=value,
            session_id=session_id,
            source=source,
        )
        db.add(existing)

    await db.commit()
    await db.refresh(existing)
    return existing


async def delete_memory_by_key(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    key: str,
) -> bool:
    """Delete a memory entry by key (global entries only — session_id IS NULL).

    Thin wrapper over :func:`remove_global_memory`.
    """
    return await remove_global_memory(
        db,
        user_id=user_id,
        tenant_id=tenant_id,
        key=key,
    )


async def select_memories_for_prompt(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    query: str | None = None,
    limit: int = 50,
    candidate_limit: int | None = None,
    semantic_ranking: bool = True,
) -> tuple[list[Memory], int]:
    """Select up to ``limit`` global memory entries for prompt injection.

    When *query* is provided, semantic ranking is enabled, and more entries
    exist than *limit*, the entry set is ranked by relevance to *query* (via
    :func:`.embedding_service.rank_by_similarity`) instead of being taken
    purely by recency.  In every other case the previous most-recently-updated
    ordering is preserved, so the common small-memory case never pays for
    embeddings.

    Returns ``(selected, total)`` where *total* is the true number of global
    entries for the user, so callers can report omissions.
    """
    base_where = [
        Memory.user_id == user_id,
        Memory.tenant_id == tenant_id,
        Memory.session_id.is_(None),
    ]

    # 1. Count total global entries.
    total = (
        await db.execute(select(func.count()).where(*base_where))
    ).scalar_one()

    # 2. No entries — nothing to return.
    if total == 0:
        return [], 0

    # 3. Default candidate limit from settings.
    if candidate_limit is None:
        candidate_limit = settings.MEMORY_PROMPT_CANDIDATE_ENTRIES

    # 4. Recency-based ordering (updated_at desc, falling back to created_at).
    recency_ordering = (
        func.coalesce(Memory.updated_at, Memory.created_at).desc(),
        Memory.id,
    )

    # 5. Skip semantic ranking when no query, disabled, or already within limit.
    if not query or not semantic_ranking or total <= limit:
        stmt = (
            select(Memory)
            .where(*base_where)
            .order_by(*recency_ordering)
            .limit(limit)
        )
        result = await db.execute(stmt)
        rows = list(result.scalars().all())
        return rows, total

    # 6. Fetch candidates.
    fetch_limit = min(total, max(candidate_limit, limit))
    stmt = (
        select(Memory)
        .where(*base_where)
        .order_by(*recency_ordering)
        .limit(fetch_limit)
    )
    result = await db.execute(stmt)
    candidates = list(result.scalars().all())

    # 7. If candidates fit within limit, just return them.
    if len(candidates) <= limit:
        return candidates, total

    # 8. Rank candidates by similarity to the query.
    texts = [str(m.key) + ": " + str(m.value) for m in candidates]
    try:
        ranked = await rank_by_similarity(query, texts)
    except Exception:  # noqa: BLE001
        logger.warning("Semantic memory ranking failed; falling back to recency", exc_info=True)
        ranked = []

    # 9. Fallback — return most recent if ranking produced nothing.
    if not ranked:
        return candidates[:limit], total

    # 10. Reorder candidates by ranked index and take top ``limit``.
    selected = [candidates[i] for i, _score in ranked][:limit]
    return selected, total
