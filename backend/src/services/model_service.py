# =============================================================================
# PH Agent Hub — Model Service (CRUD)
# =============================================================================

from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError
from ..db.orm.models import Model
from ..db.orm.groups import ModelGroup, UserGroupMember
from ..db.orm.sessions import Session
from ..db.orm.users import User
from ..db.orm.skills import Skill
from ..db.orm.messages import Message


async def list_models(
    db: AsyncSession,
    tenant_id: str | None = None,
    user_id: str | None = None,
    *,
    search: str | None = None,
    provider: str | None = None,
    enabled: bool | None = None,
    is_public: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[Model], int]:
    """Return models with optional filtering, sorting, and pagination.

    When user_id is provided, only returns models where:
    - is_public=True, OR
    - the model is assigned to a group the user belongs to
    """
    stmt = select(Model)
    if tenant_id is not None:
        stmt = stmt.where(Model.tenant_id == tenant_id)

    if user_id is not None:
        # Subquery: group IDs the user belongs to
        user_group_subq = (
            select(ModelGroup.model_id)
            .join(UserGroupMember, UserGroupMember.group_id == ModelGroup.group_id)
            .where(UserGroupMember.user_id == user_id)
        )
        stmt = stmt.where(
            or_(
                Model.is_public == True,  # noqa: E712
                Model.id.in_(user_group_subq),
            )
        )

    if provider is not None:
        stmt = stmt.where(Model.provider == provider)
    if enabled is not None:
        stmt = stmt.where(Model.enabled == enabled)
    if is_public is not None:
        stmt = stmt.where(Model.is_public == is_public)

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(
        stmt, search,
        [Model.name, Model.model_id, Model.provider],
    )
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "name": Model.name,
            "provider": Model.provider,
            "enabled": Model.enabled,
            "created_at": Model.created_at,
            "max_tokens": Model.max_tokens,
        },
        default_sort=Model.created_at,
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_model_by_id(db: AsyncSession, model_id: str) -> Model | None:
    """Look up a model by primary key."""
    result = await db.execute(select(Model).where(Model.id == model_id))
    return result.scalar_one_or_none()


async def create_model(
    db: AsyncSession,
    tenant_id: str,
    name: str,
    provider: str,
    api_key: str,
    base_url: str | None = None,
    enabled: bool = True,
    is_public: bool = False,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    thinking_enabled: bool = False,
    reasoning_effort: str | None = None,
    follow_up_questions_enabled: bool = False,
    model_id: str | None = None,
    context_length: int | None = None,
    input_price_per_1m: float | None = None,
    output_price_per_1m: float | None = None,
    cache_hit_price_per_1m: float | None = None,
) -> Model:
    """Create a new model. api_key is transparently encrypted by the ORM.

    For Ollama models, api_key is auto-filled with "ollama" when empty
    (Ollama does not require authentication but the column is non-nullable).
    """
    # Auto-fill api_key for Ollama (no auth required)
    if provider.lower() == "ollama" and not api_key:
        api_key = "ollama"

    model = Model(
        tenant_id=tenant_id,
        name=name,
        model_id=model_id,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        enabled=enabled,
        is_public=is_public,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
        follow_up_questions_enabled=follow_up_questions_enabled,
        context_length=context_length,
        input_price_per_1m=input_price_per_1m,
        output_price_per_1m=output_price_per_1m,
        cache_hit_price_per_1m=cache_hit_price_per_1m,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return model


async def update_model(db: AsyncSession, model_id: str, **fields) -> Model:
    """Update a model's fields. Raises NotFoundError if missing.

    For Ollama models, api_key is auto-filled with "ollama" when empty
    (Ollama does not require authentication but the column is non-nullable).
    """
    model = await get_model_by_id(db, model_id)
    if model is None:
        raise NotFoundError("Model not found")

    # Determine effective provider: use the incoming value if provided,
    # otherwise fall back to the current provider from DB.
    provider = fields.get("provider", model.provider)
    if "api_key" in fields and not fields["api_key"] and provider.lower() == "ollama":
        fields["api_key"] = "ollama"

    for key, value in fields.items():
        if hasattr(model, key):
            setattr(model, key, value)

    await db.commit()
    await db.refresh(model)
    return model


async def delete_model(db: AsyncSession, model_id: str) -> None:
    """Delete a model by ID. Raises NotFoundError if missing.
    Clears all references (sessions, users, skills, messages, groups) first."""
    model = await get_model_by_id(db, model_id)
    if model is None:
        raise NotFoundError("Model not found")

    # Clear selected_model_id in sessions that reference this model
    await db.execute(
        update(Session)
        .where(Session.selected_model_id == model_id)
        .values(selected_model_id=None)
    )

    # Clear default_model_id in users that reference this model
    await db.execute(
        update(User)
        .where(User.default_model_id == model_id)
        .values(default_model_id=None)
    )

    # Clear default_model_id in skills that reference this model
    await db.execute(
        update(Skill)
        .where(Skill.default_model_id == model_id)
        .values(default_model_id=None)
    )

    # Clear model_id in messages that reference this model
    await db.execute(
        update(Message)
        .where(Message.model_id == model_id)
        .values(model_id=None)
    )

    # Remove model from any assigned groups
    await db.execute(
        ModelGroup.__table__.delete().where(ModelGroup.model_id == model_id)
    )

    await db.delete(model)
    await db.commit()
