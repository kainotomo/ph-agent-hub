# =============================================================================
# PH Agent Hub — Group Service (CRUD for User Groups, Members, Model Assignments)
# =============================================================================

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import ForbiddenError, NotFoundError, ConflictError
from ..db.orm.groups import UserGroup, UserGroupMember, ModelGroup, ToolGroup
from ..db.orm.models import Model
from ..db.orm.tools import Tool
from ..db.orm.users import User


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------


async def list_groups(
    db: AsyncSession,
    tenant_id: str | None = None,
    *,
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[UserGroup], int]:
    """Return groups with optional filtering, sorting, and pagination."""
    stmt = select(UserGroup)
    if tenant_id is not None:
        stmt = stmt.where(UserGroup.tenant_id == tenant_id)

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(stmt, search, [UserGroup.name])
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "name": UserGroup.name,
            "created_at": UserGroup.created_at,
        },
        default_sort=UserGroup.name,
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_group_by_id(db: AsyncSession, group_id: str) -> UserGroup | None:
    """Look up a group by primary key."""
    result = await db.execute(select(UserGroup).where(UserGroup.id == group_id))
    return result.scalar_one_or_none()


async def create_group(
    db: AsyncSession,
    tenant_id: str,
    name: str,
) -> UserGroup:
    """Create a new user group."""
    group = UserGroup(tenant_id=tenant_id, name=name)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


async def update_group(
    db: AsyncSession, group_id: str, name: str
) -> UserGroup:
    """Update a group's name. Raises NotFoundError if missing."""
    group = await get_group_by_id(db, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    group.name = name
    await db.commit()
    await db.refresh(group)
    return group


async def delete_group(db: AsyncSession, group_id: str) -> None:
    """Delete a group by ID. Cascades to members and model assignments."""
    group = await get_group_by_id(db, group_id)
    if group is None:
        raise NotFoundError("Group not found")

    # Delete members and model assignments first
    await db.execute(
        delete(UserGroupMember).where(UserGroupMember.group_id == group_id)
    )
    await db.execute(
        delete(ModelGroup).where(ModelGroup.group_id == group_id)
    )

    await db.delete(group)
    await db.commit()


# ---------------------------------------------------------------------------
# Member Management
# ---------------------------------------------------------------------------


async def add_member(
    db: AsyncSession, group_id: str, user_id: str
) -> UserGroupMember:
    """Add a user to a group. No-op if already a member."""
    # Check group exists
    group = await get_group_by_id(db, group_id)
    if group is None:
        raise NotFoundError("Group not found")

    # Check user exists
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found")

    # Verify tenant boundary
    if group.tenant_id != user.tenant_id:
        raise ForbiddenError("Cannot add a user from a different tenant to this group")

    # Check if already a member
    existing = await db.execute(
        select(UserGroupMember).where(
            UserGroupMember.group_id == group_id,
            UserGroupMember.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        # Already a member — return existing
        return existing.scalar_one()

    member = UserGroupMember(user_id=user_id, group_id=group_id)
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


async def remove_member(
    db: AsyncSession, group_id: str, user_id: str
) -> None:
    """Remove a user from a group. No-op if not a member."""
    await db.execute(
        delete(UserGroupMember).where(
            UserGroupMember.group_id == group_id,
            UserGroupMember.user_id == user_id,
        )
    )
    await db.commit()


async def list_group_members(
    db: AsyncSession, group_id: str
) -> list[User]:
    """Return all users in a group."""
    result = await db.execute(
        select(User)
        .join(UserGroupMember, UserGroupMember.user_id == User.id)
        .where(UserGroupMember.group_id == group_id)
        .order_by(User.display_name)
    )
    return list(result.scalars().all())


async def list_user_groups(
    db: AsyncSession, user_id: str
) -> list[UserGroup]:
    """Return all groups a user belongs to."""
    result = await db.execute(
        select(UserGroup)
        .join(UserGroupMember, UserGroupMember.group_id == UserGroup.id)
        .where(UserGroupMember.user_id == user_id)
        .order_by(UserGroup.name)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Model-Group Assignment
# ---------------------------------------------------------------------------


async def assign_model_to_group(
    db: AsyncSession, group_id: str, model_id: str
) -> ModelGroup:
    """Assign a model to a group. No-op if already assigned."""
    # Check group exists
    group = await get_group_by_id(db, group_id)
    if group is None:
        raise NotFoundError("Group not found")

    # Check model exists
    model_result = await db.execute(select(Model).where(Model.id == model_id))
    model = model_result.scalar_one_or_none()
    if model is None:
        raise NotFoundError("Model not found")

    # Verify tenant boundary
    if group.tenant_id != model.tenant_id:
        raise ForbiddenError("Cannot assign a model from a different tenant to this group")

    # Check if already assigned
    existing = await db.execute(
        select(ModelGroup).where(
            ModelGroup.group_id == group_id,
            ModelGroup.model_id == model_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return existing.scalar_one()

    mg = ModelGroup(model_id=model_id, group_id=group_id)
    db.add(mg)
    await db.commit()
    await db.refresh(mg)
    return mg


async def remove_model_from_group(
    db: AsyncSession, group_id: str, model_id: str
) -> None:
    """Remove a model from a group. No-op if not assigned."""
    await db.execute(
        delete(ModelGroup).where(
            ModelGroup.group_id == group_id,
            ModelGroup.model_id == model_id,
        )
    )
    await db.commit()


async def list_group_models(
    db: AsyncSession, group_id: str
) -> list[Model]:
    """Return all models assigned to a group."""
    result = await db.execute(
        select(Model)
        .join(ModelGroup, ModelGroup.model_id == Model.id)
        .where(ModelGroup.group_id == group_id)
        .order_by(Model.name)
    )
    return list(result.scalars().all())


async def list_model_groups(
    db: AsyncSession, model_id: str
) -> list[UserGroup]:
    """Return all groups a model is assigned to."""
    result = await db.execute(
        select(UserGroup)
        .join(ModelGroup, ModelGroup.group_id == UserGroup.id)
        .where(ModelGroup.model_id == model_id)
        .order_by(UserGroup.name)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Tool-Group Assignment
# ---------------------------------------------------------------------------


async def assign_tool_to_group(
    db: AsyncSession, group_id: str, tool_id: str
) -> ToolGroup:
    """Assign a tool to a group. No-op if already assigned."""
    # Check group exists
    group = await get_group_by_id(db, group_id)
    if group is None:
        raise NotFoundError("Group not found")

    # Check tool exists
    tool_result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = tool_result.scalar_one_or_none()
    if tool is None:
        raise NotFoundError("Tool not found")

    # Verify tenant boundary
    if group.tenant_id != tool.tenant_id:
        raise ForbiddenError("Cannot assign a tool from a different tenant to this group")

    # Check if already assigned
    existing = await db.execute(
        select(ToolGroup).where(
            ToolGroup.group_id == group_id,
            ToolGroup.tool_id == tool_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return existing.scalar_one()

    tg = ToolGroup(tool_id=tool_id, group_id=group_id)
    db.add(tg)
    await db.commit()
    await db.refresh(tg)
    return tg


async def remove_tool_from_group(
    db: AsyncSession, group_id: str, tool_id: str
) -> None:
    """Remove a tool from a group. No-op if not assigned."""
    await db.execute(
        delete(ToolGroup).where(
            ToolGroup.group_id == group_id,
            ToolGroup.tool_id == tool_id,
        )
    )
    await db.commit()


async def list_group_tools(
    db: AsyncSession, group_id: str
) -> list[Tool]:
    """Return all tools assigned to a group."""
    result = await db.execute(
        select(Tool)
        .join(ToolGroup, ToolGroup.tool_id == Tool.id)
        .where(ToolGroup.group_id == group_id)
        .order_by(Tool.name)
    )
    return list(result.scalars().all())


async def list_tool_groups(
    db: AsyncSession, tool_id: str
) -> list[UserGroup]:
    """Return all groups a tool is assigned to."""
    result = await db.execute(
        select(UserGroup)
        .join(ToolGroup, ToolGroup.group_id == UserGroup.id)
        .where(ToolGroup.tool_id == tool_id)
        .order_by(UserGroup.name)
    )
    return list(result.scalars().all())
