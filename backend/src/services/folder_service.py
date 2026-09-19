# =============================================================================
# PH Agent Hub — Folder Service (Issue #526)
# =============================================================================
# User-scoped, single-level folders for organising chat sessions.
#
# A session belongs to at most one folder (``sessions.folder_id``); sessions
# with a NULL ``folder_id`` appear under "Unfiled" in the sidebar.  Unlike
# tags, which are shared across a tenant, folders are personal to a user.
# =============================================================================

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import ConflictError, NotFoundError, ValidationError
from ..db.orm.folders import Folder
from ..db.orm.sessions import Session

MAX_NAME_LENGTH = 100


# ---------------------------------------------------------------------------
# Create / list / lookup
# ---------------------------------------------------------------------------


async def create_folder(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    name: str,
    color: str | None = None,
) -> Folder:
    """Create a folder owned by *user_id*.

    Names are trimmed and must be unique per user (case-insensitive).
    """
    clean_name = _clean_name(name)

    if await get_folder_by_name(db, user_id=user_id, name=clean_name) is not None:
        raise ConflictError(f"A folder named '{clean_name}' already exists")

    max_order = await db.scalar(
        select(func.max(Folder.sort_order)).where(Folder.user_id == user_id)
    )
    next_order = (max_order + 1) if max_order is not None else 0

    folder = Folder(
        tenant_id=tenant_id,
        user_id=user_id,
        name=clean_name,
        color=color or None,
        sort_order=next_order,
    )
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return folder


async def list_folders(db: AsyncSession, *, user_id: str) -> list[Folder]:
    """Return a user's folders ordered by ``sort_order``, then name."""
    result = await db.execute(
        select(Folder)
        .where(Folder.user_id == user_id)
        .order_by(Folder.sort_order, Folder.name)
    )
    return list(result.scalars().all())


async def get_folder(
    db: AsyncSession,
    folder_id: str,
    *,
    user_id: str,
    tenant_id: str | None = None,
) -> Folder | None:
    """Look up a folder scoped to its owner, and optionally to a tenant.

    Every mutation goes through this helper so a user can never read or
    modify another user's (or another tenant's) folder.
    """
    stmt = select(Folder).where(
        Folder.id == folder_id,
        Folder.user_id == user_id,
    )
    if tenant_id is not None:
        stmt = stmt.where(Folder.tenant_id == tenant_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_folder_by_name(
    db: AsyncSession, *, user_id: str, name: str
) -> Folder | None:
    """Case-insensitive lookup by ``(user_id, name)``."""
    result = await db.execute(
        select(Folder).where(
            Folder.user_id == user_id,
            func.lower(Folder.name) == (name or "").strip().lower(),
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Update / delete
# ---------------------------------------------------------------------------


async def update_folder(
    db: AsyncSession,
    folder_id: str,
    *,
    user_id: str,
    tenant_id: str | None = None,
    **fields,
) -> Folder:
    """Rename, recolour, or reorder a folder.

    Only the keys present in *fields* are applied.  Raises ``NotFoundError``
    when the folder does not exist for this owner.
    """
    folder = await get_folder(
        db, folder_id, user_id=user_id, tenant_id=tenant_id
    )
    if folder is None:
        raise NotFoundError("Folder not found")

    if "name" in fields and fields["name"] is not None:
        clean_name = _clean_name(fields["name"])
        clash = await get_folder_by_name(db, user_id=user_id, name=clean_name)
        if clash is not None and clash.id != folder.id:
            raise ConflictError(f"A folder named '{clean_name}' already exists")
        fields["name"] = clean_name

    for key, value in fields.items():
        if key == "color":
            # An explicit empty/null colour clears the folder tint.
            folder.color = value or None
        elif value is not None and hasattr(folder, key):
            setattr(folder, key, value)

    await db.commit()
    await db.refresh(folder)
    return folder


async def delete_folder(
    db: AsyncSession,
    folder_id: str,
    *,
    user_id: str,
    tenant_id: str | None = None,
) -> int:
    """Delete a folder, moving its sessions back to "Unfiled".

    Sessions are never deleted with their folder.  Returns the number of
    sessions that were moved to Unfiled.
    """
    folder = await get_folder(
        db, folder_id, user_id=user_id, tenant_id=tenant_id
    )
    if folder is None:
        raise NotFoundError("Folder not found")

    moved = await count_sessions_in_folder(db, folder.id)

    await db.execute(
        update(Session)
        .where(Session.folder_id == folder.id)
        .values(folder_id=None)
    )
    await db.execute(delete(Folder).where(Folder.id == folder.id))
    await db.commit()
    return moved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def count_sessions_in_folder(db: AsyncSession, folder_id: str) -> int:
    """Return how many sessions are currently filed in a folder."""
    total = await db.scalar(
        select(func.count())
        .select_from(Session)
        .where(Session.folder_id == folder_id)
    )
    return int(total or 0)


def _clean_name(name: str | None) -> str:
    """Validate and normalise a folder name, raising ValidationError."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValidationError("Folder name is required")
    if len(clean_name) > MAX_NAME_LENGTH:
        raise ValidationError(
            f"Folder name must be {MAX_NAME_LENGTH} characters or fewer"
        )
    return clean_name
