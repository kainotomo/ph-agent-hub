# =============================================================================
# PH Agent Hub — Memory Service
# =============================================================================
# CRUD for the ``memory`` table.  Called by ``api/memory.py``.
# =============================================================================

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ..db.orm.memory import Memory

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
) -> None:
    """Delete a memory entry.  Validates ownership before deleting."""
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
