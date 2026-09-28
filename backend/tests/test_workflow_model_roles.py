# =============================================================================
# PH Agent Hub — Workflow Model Resolution Acceptance Tests (Issue #550)
# =============================================================================
# End-to-end coverage, against the real test database, for tenant-scoped
# role-based model resolution and its rejection paths.
# =============================================================================

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, patch

from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.engine import build_workflow, resolve_model
from src.core.exceptions import NotFoundError, ValidationError
from src.db.orm.models import Model
from src.db.orm.tenants import Tenant
from src.services.model_role_service import set_role_bindings

pytestmark = [pytest.mark.integration]


def _make_model(tenant_id: str, *, model_id: str, enabled: bool = True) -> Model:
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


class TestRoleResolution:
    """Roles resolve to the tenant's bound model."""

    async def test_bound_role_resolves_to_bound_model(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )

        resolved = await resolve_model(db_session, "@reasoning", test_tenant.id)
        assert resolved.id == test_model.id

    async def test_unbound_role_raises_naming_the_role(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        with pytest.raises(ValidationError, match=r"@reasoning"):
            await resolve_model(db_session, "@reasoning", test_tenant.id)


class TestConcreteReferenceTenantScoping:
    """Concrete references never resolve across a tenant boundary."""

    async def test_shared_model_id_resolves_within_tenant(
        self,
        db_session: AsyncSession,
        test_tenant: Tenant,
        second_tenant: Tenant,
    ):
        mine = _make_model(test_tenant.id, model_id="shared-model")
        theirs = _make_model(second_tenant.id, model_id="shared-model")
        db_session.add_all([mine, theirs])
        await db_session.flush()

        resolved = await resolve_model(db_session, "shared-model", test_tenant.id)
        assert resolved.id == mine.id

        other = await resolve_model(
            db_session, "shared-model", second_tenant.id
        )
        assert other.id == theirs.id

    async def test_another_tenants_model_is_rejected(
        self, db_session: AsyncSession, test_tenant: Tenant, second_tenant: Tenant
    ):
        foreign = _make_model(second_tenant.id, model_id="foreign-model")
        db_session.add(foreign)
        await db_session.flush()

        with pytest.raises(ValidationError, match="different tenant"):
            await resolve_model(db_session, "foreign-model", test_tenant.id)

    async def test_unknown_reference_is_not_found(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        with pytest.raises(NotFoundError):
            await resolve_model(db_session, "no-such-model", test_tenant.id)


class TestBuildWorkflowModelResolution:
    """build_workflow wires resolution through tenant-scoped role bindings."""

    @staticmethod
    def _role_definition() -> WorkflowDefinition:
        return WorkflowDefinition(
            key="role_acceptance",
            name="Role Acceptance",
            steps=[
                {
                    "id": "only",
                    "name": "Only",
                    "type": "inline",
                    "instructions": "I",
                    "model_ref": "@reasoning",
                }
            ],
        )

    async def test_bound_role_builds_with_bound_model(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )

        with patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            await build_workflow(
                defn=self._role_definition(),
                db=db_session,
                tenant_id=test_tenant.id,
            )

        built_model = mock_build.call_args_list[0][1]["model"]
        assert built_model.id == test_model.id

    async def test_unbound_role_is_not_rescued_by_default_model(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        """The skill-default fallback must not rescue an unbound role."""
        with patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            with pytest.raises(ValidationError, match=r"@reasoning"):
                await build_workflow(
                    defn=self._role_definition(),
                    db=db_session,
                    tenant_id=test_tenant.id,
                    default_model_id=test_model.id,
                )

        assert not mock_build.called
