# =============================================================================
# PH Agent Hub — RBAC Guard Tests
# =============================================================================
# Tests the require_admin and require_admin_or_manager dependency guards
# to ensure correct role-based access control.
# =============================================================================

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.dependencies import require_admin, require_admin_or_manager
from src.core.exceptions import ForbiddenError
from src.db.orm.users import User

pytestmark = [
    pytest.mark.security,
    pytest.mark.unit,
]


class TestRequireAdmin:
    """Tests for the require_admin guard."""

    async def test_admin_allowed(self, admin_user):
        """Verify admin is allowed through require_admin guard."""
        result = await require_admin(current_user=admin_user)
        assert result == admin_user

    async def test_manager_rejected(self, manager_user):
        """Verify manager is rejected by require_admin guard."""
        with pytest.raises(ForbiddenError, match="Admin access required"):
            await require_admin(current_user=manager_user)

    async def test_user_rejected(self, test_user):
        """Verify regular user is rejected by require_admin guard."""
        with pytest.raises(ForbiddenError, match="Admin access required"):
            await require_admin(current_user=test_user)


class TestRequireAdminOrManager:
    """Tests for the require_admin_or_manager guard."""

    async def test_admin_allowed(self, admin_user):
        """Verify admin is allowed through require_admin_or_manager guard."""
        result = await require_admin_or_manager(current_user=admin_user)
        assert result == admin_user

    async def test_manager_allowed(self, manager_user):
        """Verify manager is allowed through require_admin_or_manager guard."""
        result = await require_admin_or_manager(current_user=manager_user)
        assert result == manager_user

    async def test_user_rejected(self, test_user):
        """Verify regular user is rejected by require_admin_or_manager guard."""
        with pytest.raises(ForbiddenError, match="Admin or manager access required"):
            await require_admin_or_manager(current_user=test_user)
