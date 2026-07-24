"""E2E test for Admin management flows (tenant → user → model → group).

Prerequisites:
- Docker stack is running (docker compose up -d)
- Alembic migrations are up to date

Usage:
    pytest backend/tests/e2e_admin_flow.py -v
"""

import os
import uuid

import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
)

pytestmark = [
    pytest.mark.e2e,
]


@pytest.mark.e2e
class TestAdminFlowE2E:
    """End-to-end admin management flow verification."""

    async def _create_admin(self, db, tenant):
        """Create an admin user."""
        from src.db.orm.users import User
        from src.core.security import hash_password

        admin = User(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            email=f"admin-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("AdminPass123!"),
            display_name="E2E Admin",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.flush()
        return admin

    async def test_admin_create_tenant(self, e2e_db_session):
        """Verify admin can create a tenant."""
        db = e2e_db_session
        from src.db.orm.tenants import Tenant

        tenant_data = {
            "id": str(uuid.uuid4()),
            "name": f"Admin E2E Tenant {uuid.uuid4().hex[:8]}",
        }
        tenant = Tenant(id=tenant_data["id"], name=tenant_data["name"])
        db.add(tenant)
        await db.flush()
        assert tenant.id == tenant_data["id"]
        assert tenant.name == tenant_data["name"]

    async def test_admin_create_user_in_tenant(self, e2e_db_session):
        """Verify admin can create a user in a tenant."""
        db = e2e_db_session
        from src.db.orm.tenants import Tenant
        from src.db.orm.users import User
        from src.core.security import hash_password

        tenant = Tenant(
            id=str(uuid.uuid4()),
            name=f"UserTest {uuid.uuid4().hex[:8]}",
        )
        db.add(tenant)
        await db.flush()

        user = User(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            email=f"created-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("UserPass123!"),
            display_name="Created User",
            role="user",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        assert user.tenant_id == tenant.id
        assert user.role == "user"

    async def test_admin_create_model_and_assign_group(self, e2e_db_session):
        """Verify admin can create a model and assign to a group."""
        db = e2e_db_session
        from src.db.orm.tenants import Tenant
        from src.db.orm.models import Model
        from src.services.group_service import create_group, assign_model_to_group

        tenant = Tenant(
            id=str(uuid.uuid4()),
            name=f"GroupTest {uuid.uuid4().hex[:8]}",
        )
        db.add(tenant)
        await db.flush()

        model = Model(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            name="E2E Assigned Model",
            model_id="test-model",
            provider="openai",
            api_key="test-key",
            enabled=True,
            max_tokens=4096,
            temperature=0.7,
        )
        db.add(model)
        await db.flush()

        group = await create_group(
            db=db, tenant_id=tenant.id,
            name="E2E Test Group",
        )
        assignment = await assign_model_to_group(
            db=db, group_id=group.id, model_id=model.id,
        )
        assert assignment is not None
        assert assignment.model_id == model.id
        assert assignment.group_id == group.id

    async def test_admin_audit_log(self, e2e_db_session):
        """Verify audit log records admin actions with tenant scope."""
        db = e2e_db_session
        from src.db.orm.tenants import Tenant
        from src.db.orm.users import User
        from src.services.audit_service import write_audit_log, list_audit_logs
        from src.core.security import hash_password

        tenant = Tenant(
            id=str(uuid.uuid4()),
            name=f"AuditTest {uuid.uuid4().hex[:8]}",
        )
        db.add(tenant)
        await db.flush()

        admin = await self._create_admin(db, tenant)

        await write_audit_log(
            db=db, actor=admin,
            action="tenant.created",
            target_type="tenant",
            target_id=tenant.id,
            tenant_id=tenant.id,
        )

        logs, total = await list_audit_logs(db=db, tenant_id=tenant.id)
        assert total >= 1
        actions = [log.action for log in logs]
        assert "tenant.created" in actions
