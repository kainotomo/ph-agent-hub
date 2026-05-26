# =============================================================================
# PH Agent Hub — Demo Service Unit Tests
# =============================================================================
# Tests for demo tenant service, demo JWT creation, and DemoContext.
#
# Run inside the backend container:
#   docker compose exec backend python /app/tests/test_demo.py
# =============================================================================

import os
import sys
import uuid
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, "/app")

import asyncio
from unittest.mock import AsyncMock, patch

# Set env vars before importing app modules
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
)
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")
os.environ.setdefault("MINIO_ENDPOINT", "minio:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin")
os.environ.setdefault("MINIO_BUCKET_PREFIX", "phub")
os.environ.setdefault("JWT_SECRET", "test-secret-for-unit-tests")
os.environ.setdefault("ENCRYPTION_KEY", "test-encryption-key-for-unit-tests-32chr")
os.environ.setdefault("EMBED_GUEST_TOKEN_SECRET", "test-guest-secret")

from jose import JWTError

from src.core.config import settings
from src.core.dependencies import DemoContext
from src.core.exceptions import UnauthorizedError, NotFoundError
from src.core.jwt import create_demo_token, decode_guest_token, create_guest_token
from src.db.base import AsyncSessionLocal
from src.db.orm.tenants import Tenant
from src.services.tenant_service import get_demo_tenant, set_demo_tenant

PASS = 0
FAIL = 0


def ok(msg: str):
    global PASS
    PASS += 1
    print(f"  \u2713 {msg}")


def fail(msg: str):
    global FAIL
    FAIL += 1
    print(f"  \u2717 {msg}")


async def run():
    print("\n=== Unit: Demo Service ===\n")

    # ------------------------------------------------------------------
    # Test 1: get_demo_tenant returns None when no demo
    # ------------------------------------------------------------------
    print("1/9 get_demo_tenant with no demo tenant")
    async with AsyncSessionLocal() as db:
        tenant = await get_demo_tenant(db)
        if tenant is None:
            ok("returns None when no demo tenant")
        else:
            fail("expected None")

    # ------------------------------------------------------------------
    # Test 2: set_demo_tenant marks tenant as demo
    # ------------------------------------------------------------------
    print("2/9 set_demo_tenant basic")
    async with AsyncSessionLocal() as db:
        t = Tenant(name=f"Demo Unit {uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.flush()

        result = await set_demo_tenant(db, t.id)
        if result.is_demo:
            ok("marks tenant as demo")
        else:
            fail("is_demo should be True")

    # ------------------------------------------------------------------
    # Test 3: get_demo_tenant finds the demo tenant
    # ------------------------------------------------------------------
    print("3/9 get_demo_tenant finds demo")
    async with AsyncSessionLocal() as db:
        t = Tenant(name=f"Demo Unit {uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.flush()
        await set_demo_tenant(db, t.id)

        found = await get_demo_tenant(db)
        if found and found.id == t.id:
            ok("finds the demo tenant")
        else:
            fail("should find demo tenant")

    # ------------------------------------------------------------------
    # Test 4: Only one demo tenant
    # ------------------------------------------------------------------
    print("4/9 Only one demo tenant")
    async with AsyncSessionLocal() as db:
        t1 = Tenant(name=f"Demo Unit A {uuid.uuid4().hex[:8]}")
        t2 = Tenant(name=f"Demo Unit B {uuid.uuid4().hex[:8]}")
        db.add_all([t1, t2])
        await db.flush()

        await set_demo_tenant(db, t1.id)
        await set_demo_tenant(db, t2.id)

        fresh_t1 = await db.get(Tenant, t1.id)
        fresh_t2 = await db.get(Tenant, t2.id)
        if fresh_t1 and fresh_t2 and fresh_t2.is_demo and not fresh_t1.is_demo:
            ok("second set clears the first")
        else:
            fail(f"t1.is_demo={fresh_t1.is_demo if fresh_t1 else None}, t2.is_demo={fresh_t2.is_demo if fresh_t2 else None}")

    # ------------------------------------------------------------------
    # Test 5: set_demo_tenant raises NotFoundError
    # ------------------------------------------------------------------
    print("5/9 set_demo_tenant raises NotFoundError")
    async with AsyncSessionLocal() as db:
        try:
            await set_demo_tenant(db, str(uuid.uuid4()))
            fail("should have raised NotFoundError")
        except NotFoundError:
            ok("raises NotFoundError for nonexistent tenant")

    # ------------------------------------------------------------------
    # Test 6: create_demo_token
    # ------------------------------------------------------------------
    print("6/9 create_demo_token")
    tenant_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    token = create_demo_token({"sub": tenant_id, "session_id": session_id})

    payload = decode_guest_token(token)
    if payload["type"] == "demo" and payload["sub"] == tenant_id and payload["session_id"] == session_id:
        ok("creates valid demo token with correct claims")
    else:
        fail(f"claims mismatch: {payload}")

    # ------------------------------------------------------------------
    # Test 7: Demo token TTL <= 300s
    # ------------------------------------------------------------------
    print("7/9 Demo token TTL")
    ttl = payload["exp"] - payload["iat"]
    if 0 < ttl <= 300:
        ok(f"TTL is {ttl}s (max 300s)")
    else:
        fail(f"TTL should be <= 300s, got {ttl}s")

    # ------------------------------------------------------------------
    # Test 8: Demo token rejected by user JWT secret
    # ------------------------------------------------------------------
    print("8/9 Demo token rejected by user JWT secret")
    try:
        from jose import jwt as jose_jwt
        jose_jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        fail("should have raised JWTError")
    except JWTError:
        ok("demo token rejected by user JWT secret")

    # ------------------------------------------------------------------
    # Test 9: DemoContext
    # ------------------------------------------------------------------
    print("9/9 DemoContext")
    ctx = DemoContext(tenant_id="t1", session_id="s1")
    props_ok = ctx.tenant_id == "t1" and ctx.session_id == "s1" and ctx.id == "demo:t1" and ctx.is_guest
    if props_ok:
        ok("DemoContext created with correct properties")
    else:
        fail(f"DemoContext properties incorrect: tenant={ctx.tenant_id}, session={ctx.session_id}, id={ctx.id}, is_guest={ctx.is_guest}")

    # Summary
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
    if FAIL:
        print("SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(run())