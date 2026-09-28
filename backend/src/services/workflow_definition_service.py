# =============================================================================
# PH Agent Hub — Workflow Definition Service (CRUD)
# =============================================================================
# Tenant-scoped CRUD for workflow definitions persisted in the database.
# Mirrors the skill_service pattern for list/create/update/delete.
# =============================================================================

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import ConflictError, NotFoundError
from ..db.orm.workflow_definitions import WorkflowDefinitionRecord


async def list_definitions(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    search: str | None = None,
    visibility: str | None = None,
    enabled: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[WorkflowDefinitionRecord], int]:
    """Return workflow definitions with optional filtering, sorting, and pagination.

    For admin listing: when ``tenant_id`` is None, all tenants are visible.
    When ``tenant_id`` is provided, only that tenant's definitions are returned.
    """
    stmt = select(WorkflowDefinitionRecord)

    if tenant_id is not None:
        stmt = stmt.where(WorkflowDefinitionRecord.tenant_id == tenant_id)

    if visibility is not None:
        stmt = stmt.where(WorkflowDefinitionRecord.visibility == visibility)

    if enabled is not None:
        stmt = stmt.where(WorkflowDefinitionRecord.enabled == enabled)

    from ..core.pagination import apply_search, apply_sorting, paginate

    stmt = apply_search(
        stmt, search,
        [WorkflowDefinitionRecord.key, WorkflowDefinitionRecord.name, WorkflowDefinitionRecord.description],
    )
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "key": WorkflowDefinitionRecord.key,
            "name": WorkflowDefinitionRecord.name,
            "visibility": WorkflowDefinitionRecord.visibility,
            "enabled": WorkflowDefinitionRecord.enabled,
            "created_at": WorkflowDefinitionRecord.created_at,
            "updated_at": WorkflowDefinitionRecord.updated_at,
        },
        default_sort=WorkflowDefinitionRecord.created_at.desc(),
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_definition(db: AsyncSession, definition_id: str) -> WorkflowDefinitionRecord | None:
    """Look up a workflow definition by primary key."""
    result = await db.execute(
        select(WorkflowDefinitionRecord).where(WorkflowDefinitionRecord.id == definition_id)
    )
    return result.scalar_one_or_none()


async def create_definition(
    db: AsyncSession,
    *,
    tenant_id: str,
    key: str,
    name: str,
    description: str | None,
    definition: dict,
    visibility: str,
    enabled: bool,
    actor_id: str | None,
    signature_hash: str | None = None,
) -> WorkflowDefinitionRecord:
    """Create a new workflow definition record.

    Raises ``ConflictError`` if a row with the same ``(tenant_id, key)`` already exists.
    """
    # Check for duplicate (tenant_id, key)
    result = await db.execute(
        select(WorkflowDefinitionRecord)
        .where(
            WorkflowDefinitionRecord.tenant_id == tenant_id,
            WorkflowDefinitionRecord.key == key,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"Workflow definition with key '{key}' already exists for tenant '{tenant_id}'"
        )

    record = WorkflowDefinitionRecord(
        tenant_id=tenant_id,
        key=key,
        name=name,
        description=description,
        definition=definition,
        visibility=visibility,
        enabled=enabled,
        signature_hash=signature_hash,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def update_definition(
    db: AsyncSession,
    definition_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    definition: dict | None = None,
    visibility: str | None = None,
    enabled: bool | None = None,
    actor_id: str | None = None,
) -> WorkflowDefinitionRecord:
    """Update a workflow definition record.

    Raises ``NotFoundError`` if the definition does not exist.
    """
    result = await db.execute(
        select(WorkflowDefinitionRecord).where(WorkflowDefinitionRecord.id == definition_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"Workflow definition '{definition_id}' not found")

    if name is not None:
        record.name = name
    if description is not None:
        record.description = description
    if definition is not None:
        record.definition = definition
    if visibility is not None:
        record.visibility = visibility
    if enabled is not None:
        record.enabled = enabled
    record.updated_by = actor_id

    await db.commit()
    await db.refresh(record)
    return record


async def delete_definition(db: AsyncSession, definition_id: str) -> None:
    """Delete a workflow definition by ID.

    Raises ``NotFoundError`` if the definition does not exist.
    """
    result = await db.execute(
        select(WorkflowDefinitionRecord).where(WorkflowDefinitionRecord.id == definition_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise NotFoundError(f"Workflow definition '{definition_id}' not found")

    await db.delete(record)
    await db.commit()
