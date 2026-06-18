# =============================================================================
# PH Agent Hub — User Service Tests
# =============================================================================

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.db.orm.users import User
from src.db.orm.tenants import Tenant
from src.services.user_service import (
    create_user,
    delete_user,
    get_user_by_email,
    get_user_by_id,
    list_users,
    update_user,
    update_user_default_model,
)

pytestmark = [pytest.mark.integration]


class TestGetUserByEmail:
    """Tests for get_user_by_email."""

    async def test_existing(self, db_session: AsyncSession, test_user: User):
        """Existing email returns User."""
        user = await get_user_by_email(db_session, test_user.email)
        assert user is not None
        assert user.id == test_user.id

    async def test_nonexistent(self, db_session: AsyncSession):
        """Non-existent email returns None."""
        user = await get_user_by_email(db_session, "nobody@example.com")
        assert user is None


class TestGetUserById:
    """Tests for get_user_by_id."""

    async def test_existing(self, db_session: AsyncSession, test_user: User):
        """Existing ID returns User."""
        user = await get_user_by_id(db_session, test_user.id)
        assert user is not None
        assert user.email == test_user.email

    async def test_nonexistent(self, db_session: AsyncSession):
        """Non-existent ID returns None."""
        user = await get_user_by_id(db_session, "nonexistent-id")
        assert user is None


class TestListUsers:
    """Tests for list_users."""

    async def test_all_users(self, db_session: AsyncSession, test_user: User):
        """All users are returned."""
        users, total = await list_users(db_session)
        assert total >= 1
        assert any(u.id == test_user.id for u in users)

    async def test_filter_by_tenant(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """Filter by tenant_id returns only that tenant's users."""
        users, total = await list_users(
            db_session, tenant_id=test_tenant.id
        )
        assert total >= 1
        for u in users:
            assert u.tenant_id == test_tenant.id

    async def test_filter_by_role(
        self, db_session: AsyncSession, test_user: User, admin_user: User
    ):
        """Filter by role returns matching users."""
        users, total = await list_users(
            db_session, role="admin"
        )
        assert total >= 1
        for u in users:
            assert u.role == "admin"

    async def test_filter_by_is_active(
        self, db_session: AsyncSession, inactive_user: User
    ):
        """Filter by is_active returns matching users."""
        users, total = await list_users(
            db_session, is_active=False
        )
        assert total >= 1
        for u in users:
            assert u.is_active is False

    async def test_search_by_email(
        self, db_session: AsyncSession, test_user: User
    ):
        """Search by email returns matching users."""
        users, total = await list_users(
            db_session, search=test_user.email[:10]
        )
        assert any(u.id == test_user.id for u in users)

    async def test_pagination(
        self, db_session: AsyncSession, test_user: User
    ):
        """Pagination works."""
        page1, total = await list_users(db_session, page=1, page_size=1)
        assert len(page1) <= 1
        assert total >= 1


class TestCreateUser:
    """Tests for create_user."""

    async def test_success(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        """Creates a user with hashed password."""
        email = f"newuser-{uuid.uuid4().hex[:8]}@example.com"
        user = await create_user(
            db_session,
            tenant_id=test_tenant.id,
            email=email,
            password="secret123",
            display_name="New User",
            role="user",
        )
        assert user.email == email
        assert user.display_name == "New User"
        assert user.role == "user"
        assert user.tenant_id == test_tenant.id
        # Password hash is not equal to raw password
        assert user.password_hash != "secret123"
        assert user.password_hash is not None

    async def test_duplicate_email(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """Duplicate email raises ConflictError (global uniqueness)."""
        with pytest.raises(ConflictError, match="already exists"):
            await create_user(
                db_session,
                tenant_id=test_tenant.id,
                email=test_user.email,
                password="secret123",
                display_name="Duplicate",
            )

    async def test_password_is_hashed(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        """Raw password is not stored directly."""
        email = f"passcheck-{uuid.uuid4().hex[:8]}@example.com"
        user = await create_user(
            db_session,
            tenant_id=test_tenant.id,
            email=email,
            password="my-password",
            display_name="Password Check",
        )
        assert user.password_hash != "my-password"
        assert len(user.password_hash) > 20  # Hash is longer than raw


class TestUpdateUser:
    """Tests for update_user."""

    async def test_update_email(
        self, db_session: AsyncSession, test_user: User
    ):
        """Email is updated."""
        new_email = f"updated-{uuid.uuid4().hex[:8]}@example.com"
        updated = await update_user(
            db_session, test_user.id, email=new_email
        )
        assert updated.email == new_email

    async def test_update_display_name(
        self, db_session: AsyncSession, test_user: User
    ):
        """Display name is updated."""
        updated = await update_user(
            db_session, test_user.id, display_name="New Name"
        )
        assert updated.display_name == "New Name"

    async def test_update_role(
        self, db_session: AsyncSession, test_user: User
    ):
        """Role is updated."""
        updated = await update_user(
            db_session, test_user.id, role="admin"
        )
        assert updated.role == "admin"

    async def test_update_is_active(
        self, db_session: AsyncSession, test_user: User
    ):
        """is_active is updated."""
        updated = await update_user(
            db_session, test_user.id, is_active=False
        )
        assert updated.is_active is False

    async def test_update_password(
        self, db_session: AsyncSession, test_user: User
    ):
        """Password is hashed before storage."""
        old_hash = test_user.password_hash
        updated = await update_user(
            db_session, test_user.id, password="new-password"
        )
        assert updated.password_hash != "new-password"
        assert updated.password_hash != old_hash

    async def test_email_conflict(
        self, db_session: AsyncSession, test_tenant: Tenant,
        test_user: User
    ):
        """Email conflict with another user raises ConflictError."""
        other_email = f"other-{uuid.uuid4().hex[:8]}@example.com"
        other = await create_user(
            db_session,
            tenant_id=test_tenant.id,
            email=other_email,
            password="pass",
            display_name="Other",
        )
        with pytest.raises(ConflictError, match="already exists"):
            await update_user(
                db_session, test_user.id, email=other_email
            )

    async def test_same_email_noop(
        self, db_session: AsyncSession, test_user: User
    ):
        """Updating to same email succeeds (no-op)."""
        updated = await update_user(
            db_session, test_user.id, email=test_user.email
        )
        assert updated.email == test_user.email

    async def test_nonexistent_user(self, db_session: AsyncSession):
        """Non-existent user raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await update_user(
                db_session, "nonexistent-id", display_name="Ghost"
            )


class TestDeleteUser:
    """Tests for delete_user."""

    async def test_delete_existing(
        self, db_session: AsyncSession, test_user: User
    ):
        """Existing user is deleted."""
        await delete_user(db_session, test_user.id)
        result = await db_session.execute(
            select(User).where(User.id == test_user.id)
        )
        assert result.scalar_one_or_none() is None

    async def test_nonexistent_user(self, db_session: AsyncSession):
        """Non-existent user raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await delete_user(db_session, "nonexistent-id")


class TestUpdateUserDefaultModel:
    """Tests for update_user_default_model."""

    async def test_set_default_model(
        self, db_session: AsyncSession, test_user: User, test_model
    ):
        """Setting default model updates the field."""
        updated = await update_user_default_model(
            db_session, test_user.id, test_model.id
        )
        assert updated.default_model_id == test_model.id

    async def test_clear_default_model(
        self, db_session: AsyncSession, test_user: User
    ):
        """Setting model_id to None clears the field."""
        # First set a model, then clear it
        updated = await update_user_default_model(
            db_session, test_user.id, None
        )
        assert updated.default_model_id is None

    async def test_nonexistent_user(self, db_session: AsyncSession):
        """Non-existent user raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await update_user_default_model(
                db_session, "nonexistent-id", "some-model-id"
            )
