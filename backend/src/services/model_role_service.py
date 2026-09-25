# =============================================================================
# PH Agent Hub — Model Role Binding Service
# =============================================================================
# Tenant-scoped storage and resolution for the workflow model-role vocabulary.
#
# Developers reference logical roles (``@reasoning``) in workflow definitions;
# admins bind each role to one or more of their own tenant's ``Model`` rows.
# Every function here is tenant-scoped on every query — there is no code path
# that resolves or mutates a binding without a tenant.
#
# Decision (issue #550): a role may bind several models, forming a pool.
# Resolution is deterministic and cost-agnostic — the earliest binding wins,
# tie-broken by ``Model.model_id`` — and only ``enabled`` models qualify.
# Selecting within a pool from a step is out of scope; automatic cost-aware
# routing is explicitly out of scope.
# =============================================================================

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.workflows.roles import MODEL_ROLES
from ..core.exceptions import ForbiddenError, NotFoundError, ValidationError
from ..db.orm.model_role_bindings import ModelRoleBinding
from ..db.orm.models import Model

logger = logging.getLogger(__name__)


def _validate_role(role: str) -> None:
    """Raise ``ValidationError`` unless *role* is in the closed vocabulary."""
    if role not in MODEL_ROLES:
        raise ValidationError(
            f"Unknown model role '{role}'. "
            f"Known roles: {', '.join(sorted(MODEL_ROLES))}"
        )


async def list_role_bindings(
    db: AsyncSession, tenant_id: str
) -> dict[str, list[Model]]:
    """Return every declared role mapped to its bound models for a tenant.

    One key is present for every role in ``MODEL_ROLES`` (in sorted order);
    unbound roles map to an empty list.  Models are tenant-scoped and ordered
    by name.
    """
    result = await db.execute(
        select(ModelRoleBinding.role, Model)
        .join(Model, Model.id == ModelRoleBinding.model_id)
        .where(
            ModelRoleBinding.tenant_id == tenant_id,
            Model.tenant_id == tenant_id,
        )
        .order_by(ModelRoleBinding.role, Model.name)
    )

    bindings: dict[str, list[Model]] = {role: [] for role in sorted(MODEL_ROLES)}
    for role, model in result.all():
        bindings.setdefault(role, []).append(model)
    return bindings


async def set_role_bindings(
    db: AsyncSession, tenant_id: str, role: str, model_ids: list[str]
) -> None:
    """Replace the set of models bound to *role* for a tenant.

    Every model must exist and belong to *tenant_id*.  An empty ``model_ids``
    clears the role.
    """
    _validate_role(role)

    # Deduplicate while preserving the caller's order.
    unique_ids = list(dict.fromkeys(model_ids))

    for model_id in unique_ids:
        result = await db.execute(select(Model).where(Model.id == model_id))
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"Model '{model_id}' not found")
        if model.tenant_id != tenant_id:
            raise ForbiddenError(
                f"Model '{model_id}' belongs to a different tenant"
            )

    await db.execute(
        delete(ModelRoleBinding).where(
            ModelRoleBinding.tenant_id == tenant_id,
            ModelRoleBinding.role == role,
        )
    )
    await db.flush()

    for model_id in unique_ids:
        db.add(
            ModelRoleBinding(tenant_id=tenant_id, role=role, model_id=model_id)
        )

    await db.commit()

    logger.info(
        "Role '%s' for tenant %s bound to %d model(s)",
        role,
        tenant_id,
        len(unique_ids),
    )


async def clear_role_bindings(
    db: AsyncSession, tenant_id: str, role: str
) -> None:
    """Remove every model binding for *role* in a tenant.  No-op if unbound."""
    _validate_role(role)

    await db.execute(
        delete(ModelRoleBinding).where(
            ModelRoleBinding.tenant_id == tenant_id,
            ModelRoleBinding.role == role,
        )
    )
    await db.commit()


async def resolve_role_model(
    db: AsyncSession, tenant_id: str, role: str
) -> Model | None:
    """Resolve *role* to one of the tenant's enabled models.

    Returns ``None`` when the role is unbound, when every bound model is
    disabled, or when the bindings point at another tenant's rows.  Callers
    must treat ``None`` as an error — never as a reason to fall back to an
    arbitrary model.
    """
    result = await db.execute(
        select(Model)
        .join(ModelRoleBinding, ModelRoleBinding.model_id == Model.id)
        .where(
            ModelRoleBinding.tenant_id == tenant_id,
            ModelRoleBinding.role == role,
            Model.tenant_id == tenant_id,
            Model.enabled.is_(True),
        )
        .order_by(ModelRoleBinding.created_at.asc(), Model.model_id.asc())
        .limit(1)
    )
    return result.scalars().first()
