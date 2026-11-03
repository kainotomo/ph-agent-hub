# =============================================================================
# PH Agent Hub — Template Service (CRUD)
# =============================================================================

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.budget import normalize_agent_budget_config
from ..core.config import settings
from ..core.exceptions import NotFoundError
from ..db.orm.templates import Template
from ..db.orm.users import User


async def list_templates(
    db: AsyncSession,
    tenant_id: str | None = None,
    current_user: User | None = None,
    *,
    search: str | None = None,
    scope: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[Template], int]:
    """Return templates with optional filtering, sorting, and pagination.

    Visibility rules (when current_user is provided):
    - scope=tenant: visible to all users in the tenant
    - scope=user: visible only to the assigned user
    - scope=role: visible to admin/manager roles (pending clarification)

    When current_user is None (admin listing), returns all templates.
    """
    stmt = select(Template)

    if tenant_id is not None:
        stmt = stmt.where(Template.tenant_id == tenant_id)

    if current_user is not None and current_user.role not in ("admin", "manager"):
        # Regular users see tenant-scoped + their own user-scoped templates
        stmt = stmt.where(
            (Template.scope == "tenant")
            | (
                (Template.scope == "user")
                & (Template.assigned_user_id == current_user.id)
            )
        )
    elif tenant_id is None and current_user is None:
        # No tenant scoping for admin listing — return all
        pass

    if scope is not None:
        stmt = stmt.where(Template.scope == scope)

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(
        stmt, search,
        [Template.title, Template.description],
    )
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "title": Template.title,
            "scope": Template.scope,
            "created_at": Template.created_at,
        },
        default_sort=Template.created_at,
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_template_by_id(db: AsyncSession, template_id: str) -> Template | None:
    """Look up a template by primary key."""
    result = await db.execute(select(Template).where(Template.id == template_id))
    return result.scalar_one_or_none()


async def create_template(
    db: AsyncSession,
    tenant_id: str,
    title: str,
    system_prompt: str,
    scope: str,
    description: str | None = None,
    assigned_user_id: str | None = None,
    agent_config: dict | None = None,
) -> Template:
    """Create a new template."""
    agent_config = normalize_agent_budget_config(
        agent_config,
        step_ceiling=getattr(settings, "AGENT_MAX_STEPS_CEILING", 500),
    )
    template = Template(
        tenant_id=tenant_id,
        title=title,
        description=description,
        system_prompt=system_prompt,
        scope=scope,
        assigned_user_id=assigned_user_id,
        agent_config=agent_config,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


async def update_template(
    db: AsyncSession,
    template_id: str,
    **fields,
) -> Template:
    """Update a template's fields.

    Raises NotFoundError if the template does not exist.
    """
    template = await get_template_by_id(db, template_id)
    if template is None:
        raise NotFoundError("Template not found")

    # Normalise agent_config before applying scalar fields
    if "agent_config" in fields and fields["agent_config"] is not None:
        fields["agent_config"] = normalize_agent_budget_config(
            fields["agent_config"],
            step_ceiling=getattr(settings, "AGENT_MAX_STEPS_CEILING", 500),
        )

    # Update scalar fields
    for key, value in fields.items():
        if hasattr(template, key):
            setattr(template, key, value)

    await db.commit()
    await db.refresh(template)
    return template


async def delete_template(db: AsyncSession, template_id: str) -> None:
    """Delete a template by ID. Raises NotFoundError if missing."""
    template = await get_template_by_id(db, template_id)
    if template is None:
        raise NotFoundError("Template not found")

    # Clear FK references in sessions that point to this template
    from ..db.orm.sessions import Session as SessionORM
    from sqlalchemy import update as sa_update

    await db.execute(
        sa_update(SessionORM)
        .where(SessionORM.selected_template_id == template_id)
        .values(selected_template_id=None)
    )

    # Clear FK references in skills that point to this template
    from ..db.orm.skills import Skill as SkillORM

    await db.execute(
        sa_update(SkillORM)
        .where(SkillORM.template_id == template_id)
        .values(template_id=None)
    )

    await db.delete(template)
    await db.commit()
