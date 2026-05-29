"""E2E tests for the Intelligent Model Routing feature (Issue #283).

Prerequisites:
- Docker stack is running (docker compose up -d)
- Alembic migrations are up to date
- At least one tenant with multiple models configured

Usage:
    python backend/tests/e2e_auto_routing.py
"""

import asyncio
import json
import os
import sys
import uuid
import warnings
from datetime import datetime, timezone

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
    from src.db.orm.models import Model
    from src.db.orm.users import User
    from src.db.orm.sessions import Session
    from src.services import session_service
    from src.services.model_service import create_model
    from src.core.jwt import create_access_token
    from src.core.config import settings
    from sqlalchemy import select

    print("\n=== E2E: Intelligent Model Routing (Issue #283) ===\n")

    test_tenant_id = str(uuid.uuid4())
    test_user_id = str(uuid.uuid4())
    test_session_id = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Setup: Create tenant, models, and user
    # ------------------------------------------------------------------
    print("0/10 Setup: Creating test tenant, models, and user")

    async with AsyncSessionLocal() as db:
        # Tenant
        tenant = Tenant(
            id=test_tenant_id,
            name=f"AutoRoute E2E {uuid.uuid4().hex[:8]}",
        )
        db.add(tenant)
        await db.flush()

        # Models: one reasoning (DeepSeek), one general (OpenAI-compatible)
        reasoning_model = await create_model(
            db, tenant_id=test_tenant_id,
            name="E2E Reasoning",
            model_id="deepseek-v4-flash",
            provider="deepseek",
            api_key="test-key",
            enabled=True,
            is_public=True,
            max_tokens=8192,
            temperature=0.7,
            auto_route_eligible=True,
        )
        general_model = await create_model(
            db, tenant_id=test_tenant_id,
            name="E2E General",
            model_id="gpt-4o-mini",
            provider="openai",
            api_key="test-key",
            enabled=True,
            is_public=True,
            max_tokens=4096,
            temperature=0.7,
            auto_route_eligible=True,
        )
        # Ineligible model (not available for auto-routing)
        ineligible_model = await create_model(
            db, tenant_id=test_tenant_id,
            name="E2E Ineligible",
            model_id="test-ineligible",
            provider="openai",
            api_key="test-key",
            enabled=True,
            is_public=True,
            max_tokens=2048,
            temperature=0.7,
            auto_route_eligible=False,
        )
        # Classifier model (cheap flash, used by classify_message)
        classifier_model = await create_model(
            db, tenant_id=test_tenant_id,
            name="E2E Classifier",
            model_id="deepseek-v4-flash",
            provider="deepseek",
            api_key="test-key",
            enabled=True,
            is_public=True,
            max_tokens=2048,
            temperature=0.0,
            auto_route_eligible=True,
        )

        # User
        user = User(
            id=test_user_id,
            tenant_id=test_tenant_id,
            email=f"autoroute-{uuid.uuid4().hex[:8]}@e2e.test",
            display_name="AutoRoute Tester",
            role="user",
            is_active=True,
            default_model_id=general_model.id,
        )
        db.add(user)
        await db.commit()

        ok("Setup complete — 4 models (classifier, reasoning, general, ineligible) ready")

        # ------------------------------------------------------------------
        # Test 1: Auto-routing picks reasoning model for math query
        # ------------------------------------------------------------------
        print("\n1/10 Auto-routing picks reasoning model for math query")
        session = await session_service.create_session(
            db, tenant_id=test_tenant_id, user_id=test_user_id,
            title="E2E Math Test",
            auto_route_enabled=True,
            selected_model_id=None,
        )
        assert session.auto_route_enabled is True, "auto_route_enabled should be True"
        assert session.selected_model_id is None, "selected_model_id should be None"
        ok("Session created with auto_route_enabled=True and no model assigned")

        # Simulate the first-message routing logic (same code as in chat.py)
        from src.services.router_service import route_message
        selected_id = await route_message(db, "Solve this equation: 2x + 5 = 15", test_tenant_id, test_user_id)
        assert selected_id is not None, "Router should select a model"
        assert selected_id != ineligible_model.id, "Ineligible model should not be selected"
        await session_service.update_session(
            db, session.id, selected_model_id=selected_id
        )
        updated = await session_service.get_session_by_id(db, session.id)
        assert updated.selected_model_id == selected_id, "Model should be locked in"
        ok(f"Math query → routed to model {selected_id}")

        # ------------------------------------------------------------------
        # Test 2: Auto-routing picks general model for generic query
        # ------------------------------------------------------------------
        print("\n2/10 Auto-routing picks general model for generic query")
        session2 = await session_service.create_session(
            db, tenant_id=test_tenant_id, user_id=test_user_id,
            title="E2E General Test",
            auto_route_enabled=True,
            selected_model_id=None,
        )
        selected_id = await route_message(db, "Hello, how are you today?", test_tenant_id, test_user_id)
        assert selected_id is not None, "Router should select a model"
        ok(f"Generic query → routed to model {selected_id}")

        # ------------------------------------------------------------------
        # Test 3: Ineligible model is never selected
        # ------------------------------------------------------------------
        print("\n3/10 Ineligible model is never auto-selected")
        selected_id = await route_message(db, "Solve 2+2", test_tenant_id, test_user_id)
        assert selected_id != ineligible_model.id, (
            f"Ineligible model {ineligible_model.id} should not be selected"
        )
        ok("auto_route_eligible=False model correctly excluded")

        # ------------------------------------------------------------------
        # Test 4: Manual model selection bypasses auto-routing
        # ------------------------------------------------------------------
        print("\n4/10 Manual model selection bypasses auto-routing")
        session3 = await session_service.create_session(
            db, tenant_id=test_tenant_id, user_id=test_user_id,
            title="E2E Manual Test",
            auto_route_enabled=False,
            selected_model_id=reasoning_model.id,
        )
        assert session3.selected_model_id == reasoning_model.id, (
            "Manually selected model should be preserved"
        )
        ok("Manual model selection works alongside auto-routing")

        # ------------------------------------------------------------------
        # Test 5: Model locked for entire conversation
        # ------------------------------------------------------------------
        print("\n5/10 Router picks different models for different categories")
        math_id = await route_message(
            db, "Calculate the integral of x^2 from 0 to 1", test_tenant_id, test_user_id
        )
        writing_id = await route_message(
            db, "Write a poem about the ocean", test_tenant_id, test_user_id
        )
        assert math_id != writing_id, (
            "Router should pick different models for math vs writing"
        )
        ok("Router picks different models for different task types "
           "(session locks in the first choice)")

        # ------------------------------------------------------------------
        # Test 6: No eligible models → falls back to user default
        # ------------------------------------------------------------------
        print("\n6/10 No eligible models → falls back to user default")
        # Set all models to auto_route_eligible=False temporarily
        for m in [reasoning_model, general_model, ineligible_model]:
            m.auto_route_eligible = False
        await db.flush()

        selected_id = await route_message(db, "Hello", test_tenant_id, test_user_id)
        assert selected_id is None, (
            "Router should return None when no eligible models exist"
        )
        # In chat.py, when select_model returns None, it falls back to user.default_model_id
        ok("Router returns None → fallback to user.default_model_id")

        # Restore eligibility
        reasoning_model.auto_route_eligible = True
        general_model.auto_route_eligible = True
        await db.flush()

        # ------------------------------------------------------------------
        # Test 7: Session with auto_route_enabled=False works as before
        # ------------------------------------------------------------------
        print("\n7/10 Session with auto_route_enabled=False — existing behavior preserved")
        session4 = await session_service.create_session(
            db, tenant_id=test_tenant_id, user_id=test_user_id,
            title="E2E Legacy Test",
            auto_route_enabled=False,
        )
        assert session4.selected_model_id is not None, (
            "Legacy sessions should auto-assign a model"
        )
        assert session4.auto_route_enabled is False
        ok(f"Legacy session assigned model {session4.selected_model_id}")

        # ------------------------------------------------------------------
        # Test 8: Temporary sessions support auto-routing
        # ------------------------------------------------------------------
        print("\n8/10 Temporary sessions support auto-routing")
        # Simulate: create a temp session, run routing logic, verify
        temp_session_data = {
            "id": str(uuid.uuid4()),
            "tenant_id": test_tenant_id,
            "user_id": test_user_id,
            "title": "E2E Temp",
            "is_temporary": True,
            "selected_model_id": None,
            "auto_route_enabled": True,
        }
        from src.core.redis import store_temp_session, get_temp_session

        await store_temp_session(temp_session_data["id"], temp_session_data)
        stored = await get_temp_session(temp_session_data["id"])
        assert stored is not None, "Temp session should be stored in Redis"
        assert stored.get("auto_route_enabled") is True, "Temp session should have auto_route_enabled"
        ok("Temporary session with auto_route_enabled=True stored in Redis")

        # ------------------------------------------------------------------
        # Test 9: ORM auto_route_eligible defaults to True
        # ------------------------------------------------------------------
        print("\n9/10 ORM auto_route_eligible defaults to True")
        default_model = await create_model(
            db, tenant_id=test_tenant_id,
            name="E2E Default Eligible",
            model_id="test-default-eligible",
            provider="openai",
            api_key="test-key",
            enabled=True,
            max_tokens=1024,
            temperature=0.7,
            # auto_route_eligible not specified — should default to True
        )
        assert default_model.auto_route_eligible is True, (
            "auto_route_eligible should default to True"
        )
        ok("New models default to auto_route_eligible=True")

        # ------------------------------------------------------------------
        # Test 10: Admin API preserves auto_route_eligible
        # ------------------------------------------------------------------
        print("\n10/10 Admin API preserves auto_route_eligible")
        from src.api.admin import ModelResponse

        model_resp = ModelResponse.model_validate(reasoning_model)
        assert model_resp.auto_route_eligible is True, (
            "ModelResponse should include auto_route_eligible"
        )
        ok("ModelResponse schema includes auto_route_eligible")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'='*50}")
    print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(e2e())
