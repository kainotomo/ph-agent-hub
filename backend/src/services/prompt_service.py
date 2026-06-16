# =============================================================================
# PH Agent Hub — Prompt Service (CRUD)
# =============================================================================

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError
from ..db.orm.prompts import Prompt


async def list_prompts(
    db: AsyncSession, user_id: str, tenant_id: str | None = None
) -> list[Prompt]:
    """Return all prompts owned by the user, optionally filtered by tenant."""
    stmt = select(Prompt).where(Prompt.user_id == user_id)
    if tenant_id is not None:
        stmt = stmt.where(Prompt.tenant_id == tenant_id)
    stmt = stmt.order_by(Prompt.created_at)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_prompt_by_id(db: AsyncSession, prompt_id: str) -> Prompt | None:
    """Look up a prompt by primary key."""
    result = await db.execute(select(Prompt).where(Prompt.id == prompt_id))
    return result.scalar_one_or_none()


async def create_prompt(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    title: str,
    description: str,
    content: str,
    template_id: str | None = None,
) -> Prompt:
    """Create a new prompt owned by the given user."""
    prompt = Prompt(
        tenant_id=tenant_id,
        user_id=user_id,
        title=title,
        description=description,
        content=content,
        template_id=template_id,
    )
    db.add(prompt)
    await db.commit()
    await db.refresh(prompt)
    return prompt


async def update_prompt(db: AsyncSession, prompt_id: str, **fields) -> Prompt:
    """Update a prompt's fields. Raises NotFoundError if missing."""
    prompt = await get_prompt_by_id(db, prompt_id)
    if prompt is None:
        raise NotFoundError("Prompt not found")

    for key, value in fields.items():
        if hasattr(prompt, key):
            setattr(prompt, key, value)

    await db.commit()
    await db.refresh(prompt)
    return prompt


async def delete_prompt(db: AsyncSession, prompt_id: str) -> None:
    """Delete a prompt by ID. Raises NotFoundError if missing.

    Any sessions or skills that referenced this prompt will have their
    reference set to NULL (handled by ON DELETE SET NULL FK).
    """
    prompt = await get_prompt_by_id(db, prompt_id)
    if prompt is None:
        raise NotFoundError("Prompt not found")

    await db.delete(prompt)
    await db.commit()
