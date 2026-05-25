"""E2E tests for the demo tenant auto-provisioning feature (Issue #252).

Prerequisites:
- Docker stack is running (docker compose up -d)
- Alembic migrations are up to date

Usage:
    python backend/tests/e2e_demo.py
"""

import asyncio
import json
import os
import sys
import uuid
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, "/app")

# Use the same DB as the running stack
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
)

PASS = 0
FAIL = 0


def ok(msg: str):
    global PASS
    PASS += 1
    print(f"  ✓ {msg}")


def fail(msg: str):
    global FAIL
    FAIL += 1
    print(f"  ✗ {msg}")


async def e2e():
    from src.db.base import AsyncSessionLocal
    from src.db.orm.tenants import Tenant
    from src.services.settings_service import set_settings
    from src.services.tenant_service import get_demo_tenant, set_demo_tenant
    from src.core.jwt import create_demo_token, decode_guest_token
    from src.core.redis import get_redis
    from sqlalchemy import select

    print("\n=== E2E: Demo Tenant Auto-Provisioning ===\n")

    # ------------------------------------------------------------------
    # Test 1: No demo tenant → get_demo_tenant returns None
    # ------------------------------------------------------------------
    print("1/10 No demo tenant configured")
    async with AsyncSessionLocal() as db:
        tenant = await get_demo_tenant(db)
        if tenant is None:
            ok("get_demo_tenant returns None when no demo tenant")
        else:
            fail("expected None, got a tenant")

    # ------------------------------------------------------------------
    # Test 2: Create demo tenant
    # ------------------------------------------------------------------
    print("2/10 Create and verify demo tenant")
    async with AsyncSessionLocal() as db:
        new_tenant = Tenant(
            id=str(uuid.uuid4()),
            name=f"Demo E2E {uuid.uuid4().hex[:8]}",
        )
        db.add(new_tenant)
        await db.flush()

        demo = await set_demo_tenant(db, new_tenant.id)
        if demo.is_demo:
            ok(f"Tenant {new_tenant.name} marked as demo")
        else:
            fail("is_demo should be True")

        # Verify it's found
        found = await get_demo_tenant(db)
        if found and found.id == new_tenant.id:
            ok("get_demo_tenant finds the demo tenant")
        else:
            fail("get_demo_tenant should find the demo tenant")

        # Verify only one demo tenant
        t2 = Tenant(
            id=str(uuid.uuid4()),
            name=f"Demo E2E 2 {uuid.uuid4().hex[:8]}",
        )
        db.add(t2)
        await db.flush()
        await set_demo_tenant(db, t2.id)

        # Re-fetch both tenants fresh from DB
        fresh_new = await db.get(Tenant, new_tenant.id)
        fresh_t2 = await db.get(Tenant, t2.id)
        if fresh_t2 and fresh_t2.is_demo and fresh_new and not fresh_new.is_demo:
            ok("Only one demo tenant (second replaced first)")
        else:
            fail(f"Expected t2.is_demo=True, new_tenant.is_demo=False. Got t2={fresh_t2.is_demo if fresh_t2 else None}, new={fresh_new.is_demo if fresh_new else None}")

        # Clean up: remove demo flag
        await db.execute(Tenant.__table__.update().values(is_demo=False))
        await db.commit()

    # ------------------------------------------------------------------
    # Test 3: Demo token creation and validation
    # ------------------------------------------------------------------
    print("3/10 Demo token creation")
    tenant_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = create_demo_token({"sub": tenant_id, "session_id": session_id})

    payload = decode_guest_token(token)
    if payload["type"] == "demo" and payload["sub"] == tenant_id and payload["session_id"] == session_id:
        ok("Demo token created and decoded with correct claims")
    else:
        fail(f"Token claims mismatch: {payload}")

    # ------------------------------------------------------------------
    # Test 4: Demo token reject non-demo type
    # ------------------------------------------------------------------
    print("4/10 Demo token type validation")
    from src.core.jwt import create_guest_token

    guest_token = create_guest_token({
        "sub": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "session_id": session_id,
    })
    guest_payload = decode_guest_token(guest_token)
    if guest_payload["type"] == "guest":
        ok("Guest token has type=guest")
    else:
        fail("Guest token should have type=guest")

    # ------------------------------------------------------------------
    # Test 5: Demo token TTL is 5 minutes
    # ------------------------------------------------------------------
    print("5/10 Demo token expiry")
    import time

    exp = payload["exp"]
    iat = payload["iat"]
    ttl = exp - iat
    if 0 < ttl <= 300:
        ok(f"Demo token TTL is {ttl}s (max 300s)")
    else:
        fail(f"Demo token TTL should be <= 300s, got {ttl}s")

    # ------------------------------------------------------------------
    # Test 6: DemoContext creation
    # ------------------------------------------------------------------
    print("6/10 DemoContext")
    from src.core.dependencies import DemoContext

    ctx = DemoContext(tenant_id=tenant_id, session_id=session_id)
    if ctx.tenant_id == tenant_id and ctx.session_id == session_id and ctx.is_guest:
        ok("DemoContext created correctly")
    else:
        fail("DemoContext properties incorrect")

    # ------------------------------------------------------------------
    # Test 7: Demo session TTL in config
    # ------------------------------------------------------------------
    print("7/10 Demo session TTL config")
    from src.core.config import settings

    if settings.DEMO_SESSION_TTL_SECONDS == 3600:
        ok("DEMO_SESSION_TTL_SECONDS = 3600 (1 hour)")
    else:
        fail(f"Expected 3600, got {settings.DEMO_SESSION_TTL_SECONDS}")

    # ------------------------------------------------------------------
    # Test 8: verify is_demo column exists in DB schema
    # ------------------------------------------------------------------
    print("8/10 is_demo column exists")
    async with AsyncSessionLocal() as db:
        from sqlalchemy import text

        result = await db.execute(text("SHOW COLUMNS FROM tenants WHERE Field = 'is_demo'"))
        row = result.fetchone()
        if row and row.Field == "is_demo":
            ok("is_demo column exists in tenants table")
        else:
            fail("is_demo column not found in tenants table")

    # ------------------------------------------------------------------
    # Test 9: Demo status endpoint (simulated)
    # ------------------------------------------------------------------
    print("9/10 Demo enabled setting")
    async with AsyncSessionLocal() as db:
        from src.services.settings_service import get_setting

        # Default should be "false" (not enabled)
        val = await get_setting(db, "demo_enabled", "false")
        if val == "false":
            ok("demo_enabled setting defaults to false")
        else:
            fail(f"Expected false, got {val}")

        # Set it to true
        await set_settings(db, {"demo_enabled": "true"})
        val = await get_setting(db, "demo_enabled", "false")
        if val == "true":
            ok("demo_enabled setting can be set to true")
        else:
            fail(f"Expected true, got {val}")

        # Reset to false
        await set_settings(db, {"demo_enabled": "false"})

    # ------------------------------------------------------------------
    # Test 10: Demo guest rejection (user JWT should not work)
    # ------------------------------------------------------------------
    print("10/10 User JWT rejected by demo endpoints")
    from src.core.jwt import create_access_token

    user_jwt = create_access_token({"sub": str(uuid.uuid4()), "tenant_id": tenant_id, "role": "user"})
    try:
        decode_guest_token(user_jwt)
        fail("User JWT should NOT be decodable by guest token decoder")
    except Exception:
        ok("User JWT correctly rejected by guest token decoder")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
    if FAIL:
        print("SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(e2e())
