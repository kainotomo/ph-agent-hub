# =============================================================================
# PH Agent Hub — Model Role Binding Service Tests
# =============================================================================
# Tenant-scoped role-binding CRUD and deterministic role resolution
# (Issue #550).
# =============================================================================

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.workflows.roles import MODEL_ROLES
from src.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.db.orm.model_role_bindings import ModelRoleBinding
from src.db.orm.models import Model
from src.db.orm.tenants import Tenant
from src.services.model_role_service import (
    clear_role_bindings,
    list_role_bindings,
    resolve_role_model,
    set_role_bindings,
)

pytestmark = [pytest.mark.integration]


def _make_model(
    tenant_id: str,
    *,
    model_id: str = "other-model",
    enabled: bool = True,
) -> Model:
    """Build an unsaved Model row for a tenant."""
    return Model(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=f"Model {uuid.uuid4().hex[:8]}",
        model_id=model_id,
        provider="openai",
        api_key="test-key",
        enabled=enabled,
        is_public=True,
        max_tokens=4096,
        temperature=0.7,
    )


class TestListRoleBindings:
    """Tests for list_role_bindings()."""

    async def test_returns_every_role_empty(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        bindings = await list_role_bindings(db_session, test_tenant.id)
        assert set(bindings) >= set(MODEL_ROLES)
        for role in MODEL_ROLES:
            assert bindings[role] == []

    async def test_reflects_bound_models(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )
        bindings = await list_role_bindings(db_session, test_tenant.id)
        assert [m.id for m in bindings["@reasoning"]] == [test_model.id]


class TestSetRoleBindings:
    """Tests for set_role_bindings()."""

    async def test_set_is_idempotent(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        for _ in range(2):
            await set_role_bindings(
                db_session, test_tenant.id, "@reasoning", [test_model.id]
            )

        result = await db_session.execute(
            select(ModelRoleBinding).where(
                ModelRoleBinding.tenant_id == test_tenant.id,
                ModelRoleBinding.role == "@reasoning",
            )
        )
        assert len(list(result.scalars().all())) == 1

    async def test_replaces_previous_binding(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        replacement = _make_model(test_tenant.id, model_id="replacement-model")
        db_session.add(replacement)
        await db_session.flush()

        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [replacement.id]
        )

        bindings = await list_role_bindings(db_session, test_tenant.id)
        assert [m.id for m in bindings["@reasoning"]] == [replacement.id]

    async def test_empty_list_clears_role(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )
        await set_role_bindings(db_session, test_tenant.id, "@reasoning", [])

        bindings = await list_role_bindings(db_session, test_tenant.id)
        assert bindings["@reasoning"] == []

    async def test_unknown_role_raises(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        with pytest.raises(ValidationError, match=r"@nope"):
            await set_role_bindings(db_session, test_tenant.id, "@nope", [])

    async def test_unknown_model_raises(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        with pytest.raises(NotFoundError, match="not found"):
            await set_role_bindings(
                db_session, test_tenant.id, "@reasoning", ["missing-model-id"]
            )

    async def test_cross_tenant_model_raises(
        self, db_session: AsyncSession, test_tenant: Tenant, second_tenant: Tenant
    ):
        foreign = _make_model(second_tenant.id)
        db_session.add(foreign)
        await db_session.flush()

        with pytest.raises(ForbiddenError, match="different tenant"):
            await set_role_bindings(
                db_session, test_tenant.id, "@reasoning", [foreign.id]
            )


class TestClearRoleBindings:
    """Tests for clear_role_bindings()."""

    async def test_clears_bound_models(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )
        await clear_role_bindings(db_session, test_tenant.id, "@reasoning")

        bindings = await list_role_bindings(db_session, test_tenant.id)
        assert bindings["@reasoning"] == []

    async def test_unknown_role_raises(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        with pytest.raises(ValidationError, match=r"@nope"):
            await clear_role_bindings(db_session, test_tenant.id, "@nope")


class TestResolveRoleModel:
    """Tests for resolve_role_model()."""

    async def test_returns_bound_enabled_model(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )
        resolved = await resolve_role_model(
            db_session, test_tenant.id, "@reasoning"
        )
        assert resolved is not None
        assert resolved.id == test_model.id

    async def test_unbound_role_returns_none(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        assert (
            await resolve_role_model(db_session, test_tenant.id, "@reasoning")
            is None
        )

    async def test_disabled_bound_model_returns_none(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        disabled = _make_model(test_tenant.id, enabled=False)
        db_session.add(disabled)
        await db_session.flush()

        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [disabled.id]
        )

        assert (
            await resolve_role_model(db_session, test_tenant.id, "@reasoning")
            is None
        )

    async def test_another_tenants_binding_is_not_resolved(
        self,
        db_session: AsyncSession,
        test_tenant: Tenant,
        second_tenant: Tenant,
    ):
        foreign = _make_model(second_tenant.id)
        db_session.add(foreign)
        await db_session.flush()

        await set_role_bindings(
            db_session, second_tenant.id, "@reasoning", [foreign.id]
        )

        assert (
            await resolve_role_model(db_session, test_tenant.id, "@reasoning")
            is None
        )
