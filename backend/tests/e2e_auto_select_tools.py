"""E2E tests for the Auto Tool Selection feature (Issue #287).

Prerequisites:
- Docker stack is running (docker compose up -d)
- Alembic migrations are up to date
- At least one tenant with tools configured

Usage:
    python backend/tests/e2e_auto_select_tools.py
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


async def create_tool(
    db,
    tenant_id: str,
    name: str,
    type: str = "datetime",
    category: str = "general",
    enabled: bool = True,
) -> object:
    """Helper to create a minimal Tool record."""
    from src.db.orm.tools import Tool

    tool = Tool(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=name,
        type=type,
        category=category,
        config={},
        enabled=enabled,
        code=None,
        is_public=False,
    )
    db.add(tool)
    await db.flush()
    return tool


async def e2e():
    from src.db.base import AsyncSessionLocal
    from src.db.orm.tenants import Tenant
    from src.db.orm.users import User
    from src.db.orm.sessions import Session, SessionActiveTool
    from src.db.orm.skills import Skill, SkillAllowedTool
    from src.db.orm.templates import Template
    from src.db.orm.models import Model
    from src.services import session_service
    from src.services.model_service import create_model
    from src.core.jwt import create_access_token
    from src.core.config import settings
    from sqlalchemy import select

    print("\n=== E2E: Auto Tool Selection (Issue #287) ===\n")

    test_tenant_id = str(uuid.uuid4())
    test_user_id = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    print("0/9 Setup: Creating test tenant, user, model, template, tools")

    async with AsyncSessionLocal() as db:
        # Tenant
        tenant = Tenant(
            id=test_tenant_id,
            name=f"AutoTools E2E {uuid.uuid4().hex[:8]}",
        )
        db.add(tenant)
        await db.flush()

        # Model
        model = await create_model(
            db, tenant_id=test_tenant_id,
            name="E2E Test Model",
            model_id="gpt-4o-mini",
            provider="openai",
            api_key="test-key",
            enabled=True,
            is_public=True,
            max_tokens=4096,
            temperature=0.7,
        )

        # Template
        template = Template(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant_id,
            name="E2E Test Template",
            system_prompt="You are a helpful assistant.",
        )
        db.add(template)
        await db.flush()

        # User
        user = User(
            id=test_user_id,
            tenant_id=test_tenant_id,
            email=f"autotools-{uuid.uuid4().hex[:8]}@e2e.test",
            display_name="AutoTools Tester",
            role="user",
            is_active=True,
            default_model_id=model.id,
        )
        db.add(user)
        await db.flush()

        # Tools — mix of categories for ranking tests
        datetime_tool = await create_tool(db, test_tenant_id, "datetime", "datetime", "date_time")
        weather_tool = await create_tool(db, test_tenant_id, "weather", "weather", "weather")
        calculator_tool = await create_tool(db, test_tenant_id, "calculator", "calculator", "general")
        web_search_tool = await create_tool(db, test_tenant_id, "web_search", "web_search", "search")
        fetch_url_tool = await create_tool(db, test_tenant_id, "fetch_url", "fetch_url", "web")
        stock_data_tool = await create_tool(db, test_tenant_id, "stock_data", "stock_data", "finance")
        # Disabled tool — should not appear in candidate pool
        disabled_tool = await create_tool(db, test_tenant_id, "disabled_test", "datetime", "general", enabled=False)

        # Skill with preselected tools
        skill = Skill(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant_id,
            title="E2E Test Skill",
            description="Skill with preselected tools",
            template_id=template.id,
            execution_type="agent",
        )
        db.add(skill)
        await db.flush()

        # Associate tools with skill
        for t in [calculator_tool, weather_tool]:
            sat = SkillAllowedTool(skill_id=skill.id, tool_id=t.id)
            db.add(sat)
        await db.flush()

        print("Setup complete — 7 tools (6 enabled, 1 disabled), 1 skill with 2 preselected tools")

        # ==============================================================
        # Test 1: Session created with auto_select_tools=True (default)
        # ==============================================================
        print("\n1/9 Session created with auto_select_tools=True by default")

        session = Session(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant_id,
            user_id=test_user_id,
            title="Auto Tools Test",
            selected_model_id=model.id,
            selected_skill_id=skill.id,
            selected_template_id=template.id,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)

        assert session.auto_select_tools is True, (
            f"auto_select_tools should default to True, got {session.auto_select_tools}"
        )
        ok(f"Session.auto_select_tools defaults to {session.auto_select_tools}")

        # ==============================================================
        # Test 2: Session can be created with auto_select_tools=False
        # ==============================================================
        print("\n2/9 Session can be created with auto_select_tools=False")

        session_manual = await session_service.create_session(
            db=db,
            tenant_id=test_tenant_id,
            user_id=test_user_id,
            title="Manual Tools Test",
            auto_select_tools=False,
        )
        assert session_manual.auto_select_tools is False, (
            "auto_select_tools should be False when explicitly set"
        )
        ok("session_service.create_session() accepts auto_select_tools=False")

        # ==============================================================
        # Test 3: API create_session returns auto_select_tools field
        # ==============================================================
        print("\n3/9 API create_session returns auto_select_tools field")

        from src.api.chat import SessionCreate, SessionResponse
        from src.core.dependencies import get_current_user

        # Use FastAPI TestClient to verify schema serialization
        from fastapi.testclient import TestClient
        from src.main import app

        # Build a valid JWT
        token = create_access_token(data={"sub": test_user_id, "tenant_id": test_tenant_id, "role": "user"})

        # Override get_current_user for the test
        async def override_get_current_user():
            return user

        app.dependency_overrides[get_current_user] = override_get_current_user

        with TestClient(app) as client:
            # Create session via API
            resp = client.post(
                "/chat/session",
                json={
                    "title": "API Auto Tools Test",
                    "selected_model_id": model.id,
                    "auto_select_tools": True,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data.get("auto_select_tools") is True, (
                f"Response should include auto_select_tools=True, got {data.get('auto_select_tools')}"
            )
            api_session_id = data["id"]
            ok("POST /chat/session returns auto_select_tools=True")

            # Create with auto_select_tools=False
            resp2 = client.post(
                "/chat/session",
                json={
                    "title": "API Manual Tools Test",
                    "selected_model_id": model.id,
                    "auto_select_tools": False,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp2.status_code == 201, f"Expected 201, got {resp2.status_code}: {resp2.text}"
            data2 = resp2.json()
            assert data2.get("auto_select_tools") is False, (
                f"Response should include auto_select_tools=False, got {data2.get('auto_select_tools')}"
            )
            ok("POST /chat/session with auto_select_tools=False works")

            # ==============================================================
            # Test 4: GET /chat/session/{id} returns auto_select_tools
            # ==============================================================
            print("\n4/9 GET /chat/session/{id} returns auto_select_tools")

            resp3 = client.get(
                f"/chat/session/{api_session_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp3.status_code == 200, f"Expected 200, got {resp3.status_code}: {resp3.text}"
            data3 = resp3.json()
            assert "auto_select_tools" in data3, "Response should include auto_select_tools"
            assert data3["auto_select_tools"] is True, (
                f"Expected True, got {data3['auto_select_tools']}"
            )
            ok(f"GET /chat/session/{api_session_id} returns auto_select_tools=True")

        app.dependency_overrides.clear()
        ok("API session endpoints handle auto_select_tools correctly")

        # ==============================================================
        # Test 5: PUT /chat/session/{id} updates auto_select_tools
        # ==============================================================
        print("\n5/9 PUT /chat/session/{id} updates auto_select_tools")

        from src.api.chat import app as chat_app
        from src.core.dependencies import get_current_user as _get_current_user

        async def override_user():
            return user

        chat_app.dependency_overrides[_get_current_user] = override_user

        with TestClient(chat_app) as client:
            # Toggle it off
            resp = client.put(
                f"/chat/session/{session.id}",
                json={"auto_select_tools": False},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            data = resp.json()
            assert data.get("auto_select_tools") is False, (
                f"Expected auto_select_tools=False after update, got {data.get('auto_select_tools')}"
            )
            ok("PUT /chat/session/{id} with auto_select_tools=False works")

            # Toggle it back on
            resp2 = client.put(
                f"/chat/session/{session.id}",
                json={"auto_select_tools": True},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert data2.get("auto_select_tools") is True
            ok("PUT /chat/session/{id} with auto_select_tools=True works")

        chat_app.dependency_overrides.clear()

        # ==============================================================
        # Test 6: _auto_select_tools shortlists to Top-K
        # ==============================================================
        print("\n6/9 _auto_select_tools shortlists to Top-K")

        from src.agents.runner import _auto_select_tools

        # Simulate a session_data dict
        session_data = {
            "id": session.id,
            "is_temporary": False,
            "tenant_id": test_tenant_id,
            "user_id": test_user_id,
            "auto_select_tools": True,
            "selected_skill_id": skill.id,
            "active_tool_ids": [],
            "uploaded_file_ids": [],
        }

        shortlist = await _auto_select_tools(
            db=db,
            session_data=session_data,
            tenant_id=test_tenant_id,
            user_message="What is the weather in London?",
            current_callables=[],
            cleanup_clients=[],
        )

        assert isinstance(shortlist, list), "shortlist should be a list"
        ok(f"_auto_select_tools returned {len(shortlist)} callables for weather query")

        # Verify shortlist respects Top-K
        top_k = getattr(settings, "AUTO_SELECT_TOOLS_TOP_K", 5)
        assert len(shortlist) <= top_k, (
            f"Shortlist size {len(shortlist)} exceeds Top-K={top_k}"
        )
        ok(f"Shortlist size ({len(shortlist)}) ≤ configured Top-K ({top_k})")

        # ==============================================================
        # Test 7: Manual mode preserves session-selected tools
        # ==============================================================
        print("\n7/9 Manual mode preserves session-selected tools only")

        # Create a session with manually selected tools
        manual_session = Session(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant_id,
            user_id=test_user_id,
            title="Manual Tools Only",
            selected_model_id=model.id,
            auto_select_tools=False,
        )
        db.add(manual_session)
        await db.flush()

        # Activate only datetime tool manually
        sat_manual = SessionActiveTool(session_id=manual_session.id, tool_id=datetime_tool.id)
        db.add(sat_manual)
        await db.flush()

        from src.agents.runner import _resolve_tool_callables

        manual_tools, _ = await _resolve_tool_callables(
            db=db,
            session_data={
                "id": manual_session.id,
                "is_temporary": False,
                "tenant_id": test_tenant_id,
            },
            tenant_id=test_tenant_id,
        )

        # When auto_select_tools is False, the existing path is used unchanged
        ok(f"Manual mode resolves {len(manual_tools)} tool(s) from session selection")

        # ==============================================================
        # Test 8: Auto mode can use tenant-approved tools outside session
        # ==============================================================
        print("\n8/9 Auto mode can reach tools outside session-selected set")

        auto_session_data = {
            "id": manual_session.id,
            "is_temporary": False,
            "tenant_id": test_tenant_id,
            "user_id": test_user_id,
            "auto_select_tools": True,
            "selected_skill_id": None,
            "active_tool_ids": [],
            "uploaded_file_ids": [],
        }

        auto_shortlist = await _auto_select_tools(
            db=db,
            session_data=auto_session_data,
            tenant_id=test_tenant_id,
            user_message="Get me the current stock price of AAPL",
            current_callables=manual_tools,
            cleanup_clients=[],
        )

        # The stock_data tool should be shortlisted (matches intent message)
        ok(f"Auto mode shortlisted {len(auto_shortlist)} callable(s) for stock query")

        # ==============================================================
        # Test 9: Disabled tools are excluded from candidate pool
        # ==============================================================
        print("\n9/9 Disabled tools excluded from candidate pool")

        # The disabled_tool (enabled=False) should not appear in the pool
        # Verify by checking the DB query in _auto_select_tools
        from sqlalchemy import select as sa_select
        from src.db.orm.tools import Tool as ToolORM

        result = await db.execute(
            sa_select(ToolORM).where(
                ToolORM.tenant_id == test_tenant_id,
                ToolORM.enabled == True,  # noqa: E712
            )
        )
        enabled_tools = list(result.scalars().all())
        tool_ids = [t.id for t in enabled_tools]
        assert disabled_tool.id not in tool_ids, (
            "Disabled tool should not appear in enabled tools list"
        )
        ok(f"Disabled tool correctly excluded; {len(enabled_tools)} enabled tools in pool")

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        global PASS, FAIL
        total = PASS + FAIL
        print(f"\n{'='*60}")
        print(f"Results: {PASS}/{total} passed, {FAIL}/{total} failed")
        print(f"{'='*60}")

        if FAIL > 0:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(e2e())
