# =============================================================================
# PH Agent Hub — Admin Model Role Binding API Tests
# =============================================================================
# Tests for the /api/admin/model-role-bindings endpoints: listing, binding,
# clearing, role-vocabulary validation, tenant scoping and auth guards
# (Issue #550).
# =============================================================================

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.workflows.roles import MODEL_ROLES
from src.db.orm.models import Model
from src.db.orm.tenants import Tenant
from src.db.orm.users import User

pytestmark = [pytest.mark.integration]

BASE = "/api/admin/model-role-bindings"


def _make_foreign_model(tenant_id: str) -> Model:
    return Model(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name="Foreign Model",
        model_id=f"foreign-{uuid.uuid4().hex[:8]}",
        provider="openai",
        api_key="test-key",
        enabled=True,
        is_public=True,
        max_tokens=4096,
        temperature=0.7,
    )


class TestListModelRoleBindings:
    """Tests for GET /api/admin/model-role-bindings."""

    async def test_lists_every_role_empty(
        self, async_client: httpx.AsyncClient, admin_user, auth_headers
    ):
        response = await async_client.get(BASE, headers=auth_headers(admin_user))
        assert response.status_code == 200
        body = response.json()
        assert [entry["role"] for entry in body] == sorted(MODEL_ROLES)
        assert all(entry["models"] == [] for entry in body)

    async def test_reflects_a_binding(
        self,
        async_client: httpx.AsyncClient,
        db_session: AsyncSession,
        test_tenant: Tenant,
        test_model: Model,
        admin_user,
        auth_headers,
    ):
        headers = auth_headers(admin_user)
        put = await async_client.put(
            f"{BASE}/@reasoning",
            json={"model_ids": [test_model.id]},
            headers=headers,
        )
        assert put.status_code == 200
        assert [m["id"] for m in put.json()["models"]] == [test_model.id]

        get = await async_client.get(BASE, headers=headers)
        reasoning = next(
            entry for entry in get.json() if entry["role"] == "@reasoning"
        )
        assert [m["id"] for m in reasoning["models"]] == [test_model.id]


class TestSetModelRoleBindings:
    """Tests for PUT /api/admin/model-role-bindings/{role}."""

    async def test_unknown_role_rejected(
        self, async_client: httpx.AsyncClient, admin_user, auth_headers
    ):
        response = await async_client.put(
            f"{BASE}/@nope",
            json={"model_ids": []},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 422
        assert "Known roles" in response.json()["detail"]

    async def test_unknown_model_rejected(
        self, async_client: httpx.AsyncClient, admin_user, auth_headers
    ):
        response = await async_client.put(
            f"{BASE}/@reasoning",
            json={"model_ids": ["does-not-exist"]},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 404

    async def test_other_tenants_model_rejected(
        self,
        async_client: httpx.AsyncClient,
        db_session: AsyncSession,
        second_tenant: Tenant,
        admin_user,
        auth_headers,
    ):
        foreign = _make_foreign_model(second_tenant.id)
        db_session.add(foreign)
        await db_session.flush()

        response = await async_client.put(
            f"{BASE}/@reasoning",
            json={"model_ids": [foreign.id]},
            headers=auth_headers(admin_user),
        )
        assert response.status_code == 403

    async def test_manager_cannot_write_another_tenant(
        self,
        async_client: httpx.AsyncClient,
        db_session: AsyncSession,
        test_tenant: Tenant,
        second_tenant: Tenant,
        test_model: Model,
    ):
        manager = User(
            id=str(uuid.uuid4()),
            tenant_id=second_tenant.id,
            email=f"manager-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="pbkdf2:sha256:600000$test-salt$test-hash",
            display_name="Other Manager",
            role="manager",
            is_active=True,
        )
        db_session.add(manager)
        await db_session.flush()

        headers = {
            "Authorization": f"Bearer {_token_for(manager)}",
        }
        response = await async_client.put(
            f"{BASE}/@reasoning?tenant_id={test_tenant.id}",
            json={"model_ids": [test_model.id]},
            headers=headers,
        )
        assert response.status_code == 403


class TestClearModelRoleBindings:
    """Tests for DELETE /api/admin/model-role-bindings/{role}."""

    async def test_clears_binding(
        self,
        async_client: httpx.AsyncClient,
        test_model: Model,
        admin_user,
        auth_headers,
    ):
        headers = auth_headers(admin_user)
        put = await async_client.put(
            f"{BASE}/@reasoning",
            json={"model_ids": [test_model.id]},
            headers=headers,
        )
        assert put.status_code == 200

        delete = await async_client.delete(f"{BASE}/@reasoning", headers=headers)
        assert delete.status_code == 204

        get = await async_client.get(BASE, headers=headers)
        reasoning = next(
            entry for entry in get.json() if entry["role"] == "@reasoning"
        )
        assert reasoning["models"] == []

    async def test_unknown_role_rejected(
        self, async_client: httpx.AsyncClient, admin_user, auth_headers
    ):
        response = await async_client.delete(
            f"{BASE}/@nope", headers=auth_headers(admin_user)
        )
        assert response.status_code == 422


class TestRoleBindingTenantScoping:
    """Role bindings must not leak across tenants."""

    async def test_admin_can_target_another_tenant(
        self,
        async_client: httpx.AsyncClient,
        db_session: AsyncSession,
        test_tenant: Tenant,
        second_tenant: Tenant,
        test_model: Model,
        admin_user,
        auth_headers,
    ):
        headers = auth_headers(admin_user)
        # Bind in the admin's own tenant.
        await async_client.put(
            f"{BASE}/@reasoning",
            json={"model_ids": [test_model.id]},
            headers=headers,
        )

        # Another tenant sees nothing.
        other = await async_client.get(
            f"{BASE}?tenant_id={second_tenant.id}", headers=headers
        )
        assert other.status_code == 200
        assert all(entry["models"] == [] for entry in other.json())


def _token_for(user: User) -> str:
    """Mint an access token for *user* (mirrors the auth_headers fixture)."""
    from src.core.jwt import create_access_token

    return create_access_token(
        {
            "sub": user.id,
            "tenant_id": user.tenant_id,
            "role": user.role,
        }
    )
