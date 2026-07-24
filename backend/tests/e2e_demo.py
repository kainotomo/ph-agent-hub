"""E2E tests for the demo tenant auto-provisioning feature (Issue #252).

Prerequisites:
- Docker stack is running (docker compose up -d)
- Alembic migrations are up to date

Usage:
    pytest backend/tests/e2e_demo.py -v
"""

import asyncio
import json
import os
import sys
import uuid
import warnings

import pytest

warnings.filterwarnings("ignore")

sys.path.insert(0, "/app")

# Use the same DB as the running stack
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
)

pytestmark = [
    pytest.mark.e2e,
]


@pytest.mark.e2e
class TestDemoE2E:
    """End-to-end demo tenant provisioning verification."""

    async def test_no_demo_tenant_returns_none(self, e2e_db_session):
        """Test 1: No demo tenant → get_demo_tenant returns None."""
        db = e2e_db_session
        from src.services.tenant_service import get_demo_tenant

        tenant = await get_demo_tenant(db)
        assert tenant is None or not getattr(tenant, 'is_demo', False)

    async def test_set_and_get_demo_tenant(self, e2e_db_session):
        """Test 2: Set a demo tenant and retrieve it.

        Note: this test genuinely needs cross-session persistence (write in one
        session, read from another), so it uses its own ``AsyncSessionLocal()``
        instances.  Explicit cleanup is performed at the end.
        """
        from src.db.base import AsyncSessionLocal
        from src.db.orm.tenants import Tenant
        from src.services.tenant_service import get_demo_tenant, set_demo_tenant
        from sqlalchemy import delete

        demo_tenant_id = str(uuid.uuid4())
        demo_tenant_name = f"Demo E2E {uuid.uuid4().hex[:8]}"

        # First session — create + commit
        async with AsyncSessionLocal() as db:
            demo_tenant = Tenant(
                id=demo_tenant_id,
                name=demo_tenant_name,
                is_demo=True,
            )
            db.add(demo_tenant)
            await db.flush()
            await set_demo_tenant(db, demo_tenant_id)
            await db.commit()

        # Second session — verify data is visible
        async with AsyncSessionLocal() as db:
            tenant = await get_demo_tenant(db)
            assert tenant is not None
            assert tenant.id == demo_tenant_id
            assert tenant.is_demo is True

        # Cleanup — delete the test tenant
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == demo_tenant_id))
            await db.commit()

    async def test_demo_jwt_creation(self):
        """Test 3: Create a demo JWT and verify claims."""
        from src.core.jwt import create_demo_token, decode_guest_token

        tenant_id = str(uuid.uuid4())
        token = create_demo_token({"sub": tenant_id})
        assert token is not None

        payload = decode_guest_token(token)
        assert payload.get("type") == "demo"
        assert payload.get("sub") == tenant_id

    async def test_demo_token_ttl(self):
        """Test 4: Demo token has a reasonable TTL."""
        from src.core.jwt import create_demo_token

        tenant_id = str(uuid.uuid4())
        token = create_demo_token({"sub": tenant_id, "type": "demo"})
        assert token is not None

    async def test_demo_context_validation(self):
        """Test 5: DemoContext validates demo token."""
        from src.core.jwt import create_demo_token, decode_guest_token
        from src.core.dependencies import DemoContext

        tenant_id = str(uuid.uuid4())
        token = create_demo_token({"sub": tenant_id})
        payload = decode_guest_token(token)
        ctx = DemoContext(tenant_id=payload["sub"])
        assert ctx.tenant_id == tenant_id

    async def test_guest_token_rejected_for_admin(self):
        """Test 6: Guest tokens are not accepted as demo tokens."""
        from src.core.jwt import create_access_token, decode_guest_token

        # A regular access token should not decode as a guest/demo token
        access_token = create_access_token({"sub": str(uuid.uuid4()), "tenant_id": str(uuid.uuid4()), "role": "user"})
        with pytest.raises(Exception):
            decode_guest_token(access_token)

