# =============================================================================
# PH Agent Hub — Workflow Definition Resolver
# =============================================================================
# Resolves a ``WorkflowDefinition`` from either the tenant's database record or
# a shipped registry module.  A disabled DB definition is refused; a key present
# in both sources is an explicit collision error and never silently resolved.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.registry import DuplicateMAFKeyError, get_registered
from ..agents.workflows.definition import WorkflowDefinition
from ..agents.workflows.engine import load_workflow_definition
from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.workflow_definitions import WorkflowDefinitionRecord


class DefinitionSource(str, Enum):
    """Which source delivered the resolved definition."""

    DATABASE = "database"
    MODULE = "module"


@dataclass(frozen=True)
class ResolvedDefinition:
    """A workflow definition paired with its source metadata.

    Attributes:
        definition: The resolved ``WorkflowDefinition``.
        source:     Whether it came from the database or a shipped module.
        record:     The ``WorkflowDefinitionRecord`` when source is ``DATABASE``,
                    otherwise ``None``.
    """

    definition: WorkflowDefinition
    source: DefinitionSource
    record: Any | None = None


async def load_definition(
    db: AsyncSession, tenant_id: str, key: str
) -> ResolvedDefinition:
    """Resolve a workflow definition by key for a tenant.

    Resolution order:

    1. Query the database for a ``WorkflowDefinitionRecord`` matching
       *tenant_id* and *key*.
    2. Look up the registry module via :func:`~src.agents.registry.get_registered`.
    3. Apply the disambiguation rules below.

    Rules:

    * **Both sources exist** → raise :exc:`DuplicateMAFKeyError` naming the
      key, tenant, and both sources.
    * **DB only** → build :class:`WorkflowDefinition` from the stored JSON and
      return with :attr:`DefinitionSource.DATABASE`.
    * **Module only** → call :func:`~src.agents.workflows.engine.load_workflow_definition`
      on the module and return with :attr:`DefinitionSource.MODULE`.  If that
      call raises :exc:`ValidationError` it is allowed to propagate.
    * **Neither exists** → raise :exc:`NotFoundError` naming the key and tenant.

    Args:
        db:       Database session (tenant-scoped).
        tenant_id: Tenant identifier.
        key:      The workflow definition / MAF key.

    Returns:
        A :class:`ResolvedDefinition` with the resolved definition and source.
    """
    # 1. Query the database for the tenant-scoped record.
    result = await db.execute(
        select(WorkflowDefinitionRecord)
        .where(
            WorkflowDefinitionRecord.tenant_id == tenant_id,
            WorkflowDefinitionRecord.key == key,
        )
    )
    record = result.scalars().first()

    # 2. Look up the shipped registry module (not scoped to tenant).
    module = get_registered(key)

    # 3. Disambiguate.
    if record is not None and module is not None:
        raise DuplicateMAFKeyError(
            f"Duplicate MAF key {key!r}: key is present in both "
            f"source 'database' (tenant '{tenant_id}') and "
            f"source 'module' ({module.__name__}); "
            f"precedence is not inferred — resolve the collision"
        )

    if record is not None:
        definition = WorkflowDefinition(**record.definition)
        return ResolvedDefinition(
            definition=definition,
            source=DefinitionSource.DATABASE,
            record=record,
        )

    if module is not None:
        definition = load_workflow_definition(module)
        return ResolvedDefinition(
            definition=definition,
            source=DefinitionSource.MODULE,
            record=None,
        )

    raise NotFoundError(
        f"Workflow definition '{key}' not found for tenant '{tenant_id}'"
    )


async def ensure_definition_enabled(
    db: AsyncSession, tenant_id: str, key: str
) -> None:
    """Refuse a disabled database definition for the given tenant and key.

    When a ``WorkflowDefinitionRecord`` exists and ``enabled`` is ``False``,
    raises :exc:`ValidationError` naming the key.  A module-only definition
    has no enable flag and always passes.  Returns ``None`` when the record
    does not exist or is enabled.

    Args:
        db:       Database session (tenant-scoped).
        tenant_id: Tenant identifier.
        key:      The workflow definition / MAF key.

    Raises:
        ValidationError: If the DB record exists and is disabled.
    """
    result = await db.execute(
        select(WorkflowDefinitionRecord)
        .where(
            WorkflowDefinitionRecord.tenant_id == tenant_id,
            WorkflowDefinitionRecord.key == key,
        )
    )
    record = result.scalars().first()

    if record is not None and not record.enabled:
        raise ValidationError(
            f"Workflow definition '{key}' is disabled for tenant '{tenant_id}'"
        )

    return None
