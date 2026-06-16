# =============================================================================
# PH Agent Hub — Embed Widget Tenant Isolation Tests
# =============================================================================
# Tests that the embed/widget system correctly isolates guest tokens
# and embed configurations per tenant.
# =============================================================================

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.jwt import create_guest_token, decode_guest_token
from src.db.orm.embed_configs import EmbedConfig

pytestmark = [
    pytest.mark.security,
    pytest.mark.tenant_isolation,
    pytest.mark.integration,
]


class TestEmbedTokenTenantBinding:
    """Verify guest tokens carry the correct tenant context."""

    async def test_guest_token_contains_correct_tenant_id(self):
        """Verify guest token stores the embed config's tenant_id."""
        token = create_guest_token({
            "sub": "embed-config-id",
            "tenant_id": "tenant-xyz",
            "type": "guest",
            "session_id": "",
        })
        decoded = decode_guest_token(token)
        assert decoded["tenant_id"] == "tenant-xyz"
        assert decoded["sub"] == "embed-config-id"
        assert decoded["type"] == "guest"

    async def test_guest_token_type_check(self):
        """Verify guest token always has type='guest' (forced by create_guest_token)."""
        token = create_guest_token({
            "sub": "embed-config-id",
            "tenant_id": "tenant-xyz",
            "type": "demo",  # This will be overridden to "guest"
        })
        decoded = decode_guest_token(token)
        assert decoded["type"] == "guest"  # create_guest_token always sets type="guest"


class TestEmbedConfigTenantScoping:
    """Verify embed configs are isolated by tenant."""

    async def test_embed_config_has_tenant_id(
        self, db_session: AsyncSession, test_tenant
    ):
        """Verify embed config is created with the correct tenant_id."""
        config = EmbedConfig(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            name="Test Widget",
            is_active=True,
            guest_token_hash="dummy-hash",
        )
        db_session.add(config)
        await db_session.flush()
        assert config.tenant_id == test_tenant.id

    async def test_embed_config_list_filtered_by_tenant(
        self, db_session: AsyncSession, test_tenant, second_tenant
    ):
        """Verify embed configs are filtered by tenant in queries."""
        # Create configs for both tenants
        config_a = EmbedConfig(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            name="Tenant A Widget",
            is_active=True,
            guest_token_hash="hash-a",
        )
        config_b = EmbedConfig(
            id=str(uuid.uuid4()),
            tenant_id=second_tenant.id,
            name="Tenant B Widget",
            is_active=True,
            guest_token_hash="hash-b",
        )
        db_session.add_all([config_a, config_b])
        await db_session.flush()

        # Query for tenant A only
        from src.services.embed_service import list_embed_configs
        configs_a, total_a = await list_embed_configs(
            db_session, tenant_id=test_tenant.id
        )
        config_ids_a = [c.id for c in configs_a]
        assert config_a.id in config_ids_a
        assert config_b.id not in config_ids_a
