# =============================================================================
# PH Agent Hub — Admin Template API Tests (Issue #595)
# =============================================================================
# The admin template endpoints must accept, persist and return the per-run
# budget block (``agent_config``) and reject invalid budget values.
# =============================================================================

import uuid

import httpx
import pytest

from src.db.orm.tenants import Tenant

pytestmark = [pytest.mark.integration]

BASE = "/api/admin/templates"


class TestAdminTemplateBudgets:
    async def test_create_with_budget_round_trips(
        self,
        async_client: httpx.AsyncClient,
        admin_user,
        auth_headers,
        test_tenant: Tenant,
    ):
        headers = auth_headers(admin_user)
        resp = await async_client.post(
            BASE,
            json={
                "tenant_id": test_tenant.id,
                "title": f"Budget Template {uuid.uuid4().hex[:6]}",
                "system_prompt": "You are a careful research agent.",
                "scope": "tenant",
                "agent_config": {
                    "max_steps": 70,
                    "max_input_tokens": 12_000_000,
                    "max_turns": 8,
                },
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["agent_config"] == {
            "max_steps": 70,
            "max_input_tokens": 12_000_000,
            "max_turns": 8,
        }

        listed = await async_client.get(BASE, headers=headers)
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert any(
            item["id"] == body["id"]
            and item["agent_config"]["max_steps"] == 70
            for item in items
        )

    async def test_update_budget_round_trips(
        self,
        async_client: httpx.AsyncClient,
        admin_user,
        auth_headers,
        test_tenant: Tenant,
    ):
        headers = auth_headers(admin_user)
        created = await async_client.post(
            BASE,
            json={
                "tenant_id": test_tenant.id,
                "title": f"Update Budget {uuid.uuid4().hex[:6]}",
                "system_prompt": "Prompt",
                "scope": "tenant",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        template_id = created.json()["id"]

        updated = await async_client.put(
            f"{BASE}/{template_id}",
            json={"agent_config": {"max_input_tokens": 3_000_000}},
            headers=headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["agent_config"] == {"max_input_tokens": 3_000_000}

    async def test_invalid_budget_rejected(
        self,
        async_client: httpx.AsyncClient,
        admin_user,
        auth_headers,
        test_tenant: Tenant,
    ):
        headers = auth_headers(admin_user)
        resp = await async_client.post(
            BASE,
            json={
                "tenant_id": test_tenant.id,
                "title": f"Bad Budget {uuid.uuid4().hex[:6]}",
                "system_prompt": "Prompt",
                "scope": "tenant",
                "agent_config": {"max_steps": 0},
            },
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

    async def test_create_without_budget_returns_none(
        self,
        async_client: httpx.AsyncClient,
        admin_user,
        auth_headers,
        test_tenant: Tenant,
    ):
        headers = auth_headers(admin_user)
        resp = await async_client.post(
            BASE,
            json={
                "tenant_id": test_tenant.id,
                "title": f"No Budget {uuid.uuid4().hex[:6]}",
                "system_prompt": "Prompt",
                "scope": "tenant",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["agent_config"] is None
