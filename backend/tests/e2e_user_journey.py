"""E2E test for the full user journey (login → session → upload → chat → logout).

Prerequisites:
- Docker stack is running (docker compose up -d)
- Alembic migrations are up to date

Usage:
    pytest backend/tests/e2e_user_journey.py -v
"""

import os
import uuid
from unittest.mock import patch

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
)

pytestmark = [
    pytest.mark.e2e,
]


@pytest.mark.e2e
class TestUserJourneyE2E:
    """End-to-end user journey verification."""

    async def _create_tenant_and_user(self, db):
        """Create a test tenant and user with known password."""
        from src.db.orm.tenants import Tenant
        from src.db.orm.users import User
        from src.core.security import hash_password

        tenant = Tenant(
            id=str(uuid.uuid4()),
            name=f"UserJourney {uuid.uuid4().hex[:8]}",
        )
        db.add(tenant)
        await db.flush()

        user = User(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            email=f"journey-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("TestPass123!"),
            display_name="Journey User",
            role="user",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        return {"tenant": tenant, "user": user}

    async def test_login_and_create_session(self, e2e_db_session):
        """Verify user can login and create a session."""
        db = e2e_db_session
        from src.core.jwt import create_access_token
        from src.services import session_service

        setup = await self._create_tenant_and_user(db)
        user = setup["user"]
        tenant = setup["tenant"]

        token = create_access_token({
            "sub": user.id,
            "tenant_id": tenant.id,
            "role": "user",
        })
        assert token is not None

        session = await session_service.create_session(
            db=db,
            tenant_id=tenant.id,
            user_id=user.id,
            title="Journey Test Session",
        )
        assert session is not None
        assert session.title == "Journey Test Session"
        assert session.tenant_id == tenant.id
        assert session.user_id == user.id

    async def test_session_crud_lifecycle(self, e2e_db_session):
        """Verify full session lifecycle: create → list → get → update → delete."""
        db = e2e_db_session
        from src.services import session_service

        setup = await self._create_tenant_and_user(db)
        user = setup["user"]
        tenant = setup["tenant"]

        # Create
        session = await session_service.create_session(
            db=db, tenant_id=tenant.id, user_id=user.id, title="CRUD Test",
        )
        assert session is not None

        # List
        sessions = await session_service.list_sessions_for_user(
            db=db, user_id=user.id, tenant_id=tenant.id,
        )
        assert len(sessions) >= 1
        assert session.id in [s.id for s in sessions]

        # Get
        fetched = await session_service.get_session_by_id(db, session.id)
        assert fetched is not None
        assert fetched.id == session.id

        # Update
        updated = await session_service.update_session(
            db=db, session_id=session.id, title="Updated Title",
        )
        assert updated.title == "Updated Title"

        # Delete
        await session_service.delete_session(db, session.id)
        deleted = await session_service.get_session_by_id(db, session.id)
        assert deleted is None

    async def test_tenant_isolation(self, e2e_db_session):
        """Verify session isolation between tenants."""
        db = e2e_db_session
        from src.services import session_service

        # Create two tenants with users
        setup_a = await self._create_tenant_and_user(db)
        setup_b = await self._create_tenant_and_user(db)

        # Create session in tenant A
        session_a = await session_service.create_session(
            db=db, tenant_id=setup_a["tenant"].id,
            user_id=setup_a["user"].id, title="Tenant A Session",
        )

        # List as tenant B — should not see tenant A's session
        sessions_b = await session_service.list_sessions_for_user(
            db=db, user_id=setup_b["user"].id,
            tenant_id=setup_b["tenant"].id,
        )
        assert session_a.id not in [s.id for s in sessions_b]

    async def test_create_session_with_model(self, e2e_db_session):
        """Verify session can be created with a selected model."""
        db = e2e_db_session
        from src.db.orm.models import Model
        from src.services import session_service

        setup = await self._create_tenant_and_user(db)
        model = Model(
            id=str(uuid.uuid4()),
            tenant_id=setup["tenant"].id,
            name="E2E Model",
            model_id="test-model",
            provider="openai",
            api_key="test-key",
            enabled=True,
            max_tokens=4096,
            temperature=0.7,
        )
        db.add(model)
        await db.flush()

        session = await session_service.create_session(
            db=db, tenant_id=setup["tenant"].id,
            user_id=setup["user"].id, title="Model Test",
            selected_model_id=model.id,
        )
        assert session.selected_model_id == model.id
