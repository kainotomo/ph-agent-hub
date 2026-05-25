# =============================================================================
# PH Agent Hub — Embed Config Service (CRUD)
# =============================================================================

import hashlib
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.embed_configs import EmbedConfig


def _hash_token(token: str) -> str:
    """One-way hash of a guest token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_guest_token() -> str:
    """Generate a cryptographically random guest token string.

    Format: ``embed_<64-char-hex>`` — the raw token is returned to the
    admin once (at creation / regeneration time) and never stored in the
    database.  Only its SHA-256 hash is persisted.
    """
    return "embed_" + os.urandom(32).hex()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def list_embed_configs(
    db: AsyncSession,
    tenant_id: str | None = None,
    *,
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[EmbedConfig], int]:
    """Return embed configs with optional filtering, sorting, and pagination."""
    stmt = select(EmbedConfig)

    if tenant_id is not None:
        stmt = stmt.where(EmbedConfig.tenant_id == tenant_id)

    from ..core.pagination import apply_search, apply_sorting, paginate

    stmt = apply_search(stmt, search, [EmbedConfig.name])
    stmt = apply_sorting(
        stmt,
        sort_by,
        sort_dir,
        column_map={
            "name": EmbedConfig.name,
            "is_active": EmbedConfig.is_active,
            "created_at": EmbedConfig.created_at,
        },
        default_sort=EmbedConfig.created_at,
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# Get by ID
# ---------------------------------------------------------------------------


async def get_embed_config_by_id(
    db: AsyncSession, config_id: str
) -> EmbedConfig | None:
    """Look up an embed config by primary key."""
    result = await db.execute(select(EmbedConfig).where(EmbedConfig.id == config_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Get by guest token (lookup via hash)
# ---------------------------------------------------------------------------


async def get_embed_config_by_token(
    db: AsyncSession, raw_token: str
) -> EmbedConfig | None:
    """Look up an embed config by its raw guest token (hashed lookup)."""
    hashed = _hash_token(raw_token)
    result = await db.execute(
        select(EmbedConfig).where(EmbedConfig.guest_token_hash == hashed)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_embed_config(
    db: AsyncSession,
    tenant_id: str,
    name: str,
    allowed_origins: str | None = None,
    theme: dict | None = None,
    feature_flags: dict | None = None,
    default_model_id: str | None = None,
    default_skill_id: str | None = None,
    default_template_id: str | None = None,
) -> tuple[EmbedConfig, str]:
    """Create a new embed config and return it along with the raw guest token.

    The raw token is shown to the admin exactly once (at creation time).
    Only the SHA-256 hash is stored in the database.
    """
    raw_token = generate_guest_token()
    hashed = _hash_token(raw_token)

    config = EmbedConfig(
        tenant_id=tenant_id,
        name=name,
        guest_token_hash=hashed,
        allowed_origins=allowed_origins,
        is_active=True,
        theme=theme or {},
        feature_flags=feature_flags or {},
        default_model_id=default_model_id,
        default_skill_id=default_skill_id,
        default_template_id=default_template_id,
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)

    return config, raw_token


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def update_embed_config(
    db: AsyncSession,
    config_id: str,
    **kwargs: dict,
) -> EmbedConfig:
    """Update an embed config.  Only provided fields are changed."""
    config = await get_embed_config_by_id(db, config_id)
    if config is None:
        raise NotFoundError("Embed config not found")

    # Never allow direct guest_token_hash updates
    kwargs.pop("guest_token_hash", None)
    kwargs.pop("id", None)

    for key, value in kwargs.items():
        if value is not None:
            setattr(config, key, value)

    config.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(config)
    return config


# ---------------------------------------------------------------------------
# Regenerate token
# ---------------------------------------------------------------------------


async def regenerate_token(
    db: AsyncSession, config_id: str
) -> tuple[EmbedConfig, str]:
    """Regenerate the guest token for an embed config.

    The old token is immediately invalidated (its hash is overwritten).
    Returns the new raw token.
    """
    config = await get_embed_config_by_id(db, config_id)
    if config is None:
        raise NotFoundError("Embed config not found")

    raw_token = generate_guest_token()
    config.guest_token_hash = _hash_token(raw_token)
    config.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(config)

    return config, raw_token


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def delete_embed_config(db: AsyncSession, config_id: str) -> None:
    """Delete an embed config by ID."""
    config = await get_embed_config_by_id(db, config_id)
    if config is None:
        raise NotFoundError("Embed config not found")

    await db.delete(config)
    await db.commit()
