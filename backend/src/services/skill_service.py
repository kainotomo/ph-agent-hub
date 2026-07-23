# =============================================================================
# PH Agent Hub — Skill Service (CRUD + join table management)
# =============================================================================

import re

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError
from ..db.orm.skills import Skill, SkillAllowedTool


def _slugify(title: str) -> str:
    """Convert a skill title into a valid maf_target_key.

    Lowercases, replaces non-alphanumeric runs with underscores,
    and strips leading/trailing separators.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower().strip())
    return slug.strip("_")


async def list_skills(
    db: AsyncSession,
    tenant_id: str | None = None,
    user_id: str | None = None,
    visibility: str | None = None,
    *,
    search: str | None = None,
    execution_type: str | None = None,
    enabled: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[Skill], int]:
    """Return skills with optional filtering, sorting, and pagination.

    For user-facing listing: tenant-shared + own personal skills.
    For admin listing: all skills (when tenant_id is None).
    """
    stmt = select(Skill)

    if tenant_id is not None:
        stmt = stmt.where(Skill.tenant_id == tenant_id)

    if visibility is not None:
        stmt = stmt.where(Skill.visibility == visibility)

    if user_id is not None:
        # Return tenant-shared OR the user's own personal skills
        stmt = stmt.where(
            (Skill.visibility == "tenant") | (Skill.user_id == user_id)
        )

    if execution_type is not None:
        stmt = stmt.where(Skill.execution_type == execution_type)
    if enabled is not None:
        stmt = stmt.where(Skill.enabled == enabled)

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(
        stmt, search,
        [Skill.title, Skill.description],
    )
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "title": Skill.title,
            "execution_type": Skill.execution_type,
            "visibility": Skill.visibility,
            "enabled": Skill.enabled,
            "created_at": Skill.created_at,
        },
        default_sort=Skill.created_at,
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_skill_by_id(db: AsyncSession, skill_id: str) -> Skill | None:
    """Look up a skill by primary key."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    return result.scalar_one_or_none()


async def list_skill_tools(
    db: AsyncSession, skill_id: str
) -> list[SkillAllowedTool]:
    """Return all allowed tool associations for a skill."""
    stmt = select(SkillAllowedTool).where(
        SkillAllowedTool.skill_id == skill_id
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_skill_tools_batch(
    db: AsyncSession, skill_ids: list[str]
) -> dict[str, list[str]]:
    """Batch-fetch tool mappings for multiple skills in one query.

    Returns ``{skill_id: [tool_id, ...]}``.  Skills with no tools will
    have an empty list.
    """
    if not skill_ids:
        return {}
    stmt = select(SkillAllowedTool).where(
        SkillAllowedTool.skill_id.in_(skill_ids)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    mapping: dict[str, list[str]] = {}
    for row in rows:
        mapping.setdefault(row.skill_id, []).append(row.tool_id)
    return mapping


async def _get_skill_tool_ids(db: AsyncSession, skill_id: str) -> list[str]:
    """Return just the tool_id list for a skill."""
    tools = await list_skill_tools(db, skill_id)
    return [t.tool_id for t in tools]


async def create_skill(
    db: AsyncSession,
    tenant_id: str,
    title: str,
    execution_type: str,
    description: str | None = None,
    maf_target_key: str | None = None,
    visibility: str = "tenant",
    user_id: str | None = None,
    template_id: str | None = None,
    default_prompt_id: str | None = None,
    default_model_id: str | None = None,
    enabled: bool = True,
    tool_ids: list[str] | None = None,
    cross_session_retrieval_enabled: bool = False,
    cross_session_max_snippets: int = 3,
    cross_session_min_score: float = 0.30,
    a2a_metadata: dict | None = None,
    goal: str | None = None,
    constraints: list[str] | None = None,
    success_criteria: str | None = None,
    agent_config: dict | None = None,
) -> Skill:
    """Create a new skill with optional tool associations.

    If ``maf_target_key`` is not provided it is auto-generated from
    *title* via ``_slugify()``.
    """
    if not maf_target_key:
        maf_target_key = _slugify(title)

    skill = Skill(
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
        description=description,
        execution_type=execution_type,
        maf_target_key=maf_target_key,
        visibility=visibility,
        template_id=template_id,
        default_prompt_id=default_prompt_id,
        default_model_id=default_model_id,
        enabled=enabled,
        cross_session_retrieval_enabled=cross_session_retrieval_enabled,
        cross_session_max_snippets=cross_session_max_snippets,
        cross_session_min_score=cross_session_min_score,
        a2a_metadata=a2a_metadata,
        goal=goal,
        constraints=constraints,
        success_criteria=success_criteria,
        agent_config=agent_config,
    )
    db.add(skill)
    await db.flush()  # Get the skill ID before adding join rows

    # Insert join table rows
    if tool_ids:
        for tid in tool_ids:
            db.add(SkillAllowedTool(skill_id=skill.id, tool_id=tid))

    await db.commit()
    await db.refresh(skill)
    return skill


async def update_skill(
    db: AsyncSession,
    skill_id: str,
    tool_ids: list[str] | None = None,
    **fields,
) -> Skill:
    """Update a skill's fields. Replace tool associations if provided.

    Raises NotFoundError if the skill does not exist.
    """
    skill = await get_skill_by_id(db, skill_id)
    if skill is None:
        raise NotFoundError("Skill not found")

    # Update scalar fields
    for key, value in fields.items():
        if hasattr(skill, key):
            setattr(skill, key, value)

    # Replace join table rows if tool_ids is provided
    if tool_ids is not None:
        await db.execute(
            delete(SkillAllowedTool).where(
                SkillAllowedTool.skill_id == skill_id
            )
        )
        for tid in tool_ids:
            db.add(SkillAllowedTool(skill_id=skill_id, tool_id=tid))

    await db.commit()
    await db.refresh(skill)
    return skill


async def delete_skill(db: AsyncSession, skill_id: str) -> None:
    """Delete a skill by ID. Raises NotFoundError if missing."""
    skill = await get_skill_by_id(db, skill_id)
    if skill is None:
        raise NotFoundError("Skill not found")

    # Clear FK references in sessions that point to this skill
    from ..db.orm.sessions import Session as SessionORM
    from sqlalchemy import update as sa_update

    await db.execute(
        sa_update(SessionORM)
        .where(SessionORM.selected_skill_id == skill_id)
        .values(selected_skill_id=None)
    )

    # Delete join table rows (skill_allowed_tools)
    await db.execute(
        delete(SkillAllowedTool).where(
            SkillAllowedTool.skill_id == skill_id
        )
    )

    await db.delete(skill)
    await db.commit()
