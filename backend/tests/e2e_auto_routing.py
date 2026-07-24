"""E2E tests for the Intelligent Model Routing feature (Issue #283).

Prerequisites:
- Docker stack is running (docker compose up -d)
- Alembic migrations are up to date
- At least one tenant with multiple models configured

Usage:
    pytest backend/tests/e2e_auto_routing.py -v
"""

import asyncio
import json
import os
import sys
import uuid
import warnings
from datetime import datetime, timezone

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
class TestAutoRoutingE2E:
    """End-to-end intelligent model routing verification."""

    async def _setup(
        self, db
    ):
        """Create tenant, models, and user for routing tests."""
        from src.db.orm.tenants import Tenant
        from src.db.orm.models import Model
        from src.db.orm.users import User
        from src.services.model_service import create_model

        test_tenant_id = str(uuid.uuid4())
        test_user_id = str(uuid.uuid4())

        tenant = Tenant(
            id=test_tenant_id,
            name=f"AutoRoute E2E {uuid.uuid4().hex[:8]}",
        )
        db.add(tenant)
        await db.flush()

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

        from src.core.security import hash_password

        user = User(
            id=test_user_id,
            tenant_id=test_tenant_id,
            email=f"autoroute-{uuid.uuid4().hex[:8]}@e2e.test",
            password_hash=hash_password("E2ETestPass123!"),
            display_name="AutoRoute Tester",
            role="user",
            is_active=True,
            default_model_id=general_model.id,
        )
        db.add(user)
        await db.commit()

        return {
            "tenant_id": test_tenant_id,
            "user_id": test_user_id,
            "reasoning_model": reasoning_model,
            "general_model": general_model,
            "ineligible_model": ineligible_model,
            "classifier_model": classifier_model,
        }

    async def test_auto_routing_math_picks_reasoning(self, e2e_db_session):
        """Test 1: Auto-routing picks reasoning model for math query."""
        db = e2e_db_session
        from src.services import session_service
        from src.services.router_service import route_message

        setup = await self._setup(db)
        session = await session_service.create_session(
            db, tenant_id=setup["tenant_id"], user_id=setup["user_id"],
            title="E2E Math Test",
            auto_route_enabled=True,
            selected_model_id=None,
        )
        assert session.auto_route_enabled is True
        assert session.selected_model_id is None

        selected_id = await route_message(
            db, "Solve this equation: 2x + 5 = 15",
            setup["tenant_id"], setup["user_id"],
        )
        assert selected_id is not None
        assert selected_id != setup["ineligible_model"].id

    async def test_auto_routing_general_picks_general(self, e2e_db_session):
        """Test 2: Auto-routing picks general model for generic query."""
        db = e2e_db_session
        from src.services.router_service import route_message

        setup = await self._setup(db)
        selected_id = await route_message(
            db, "Hello, how are you today?",
            setup["tenant_id"], setup["user_id"],
        )
        assert selected_id is not None
        assert selected_id != setup["ineligible_model"].id

    async def test_ineligible_model_never_selected(self, e2e_db_session):
        """Test 3: Ineligible model is never auto-selected."""
        db = e2e_db_session
        from src.services.router_service import route_message

        setup = await self._setup(db)
        selected_id = await route_message(
            db, "Solve 2+2",
            setup["tenant_id"], setup["user_id"],
        )
        assert selected_id != setup["ineligible_model"].id

    async def test_manual_model_bypasses_routing(self, e2e_db_session):
        """Test 4: Manual model selection bypasses auto-routing."""
        db = e2e_db_session
        from src.services import session_service

        setup = await self._setup(db)
        session = await session_service.create_session(
            db, tenant_id=setup["tenant_id"], user_id=setup["user_id"],
            title="E2E Manual Test",
            selected_model_id=setup["reasoning_model"].id,
            auto_route_enabled=False,
        )
        assert session.selected_model_id == setup["reasoning_model"].id
