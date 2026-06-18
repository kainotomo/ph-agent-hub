# =============================================================================
# PH Agent Hub — Balance Service Tests
# =============================================================================

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import InsufficientBalanceError, NotFoundError
from src.db.orm.balance_transactions import BalanceTransaction
from src.db.orm.tenants import Tenant
from src.services.balance_service import (
    add_funds,
    check_balance_or_raise,
    deduct_usage,
    disable_limit,
    get_balance,
    get_tenant_balance_row,
    get_transaction_history,
    get_warning_threshold,
    set_warning_threshold,
)

pytestmark = [pytest.mark.integration]


class TestGetBalance:
    """Tests for get_balance and get_tenant_balance_row."""

    async def test_get_balance_with_limit(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """Tenant with a balance limit returns Decimal."""
        balance = await get_balance(db_session, admin_tenant.id)
        assert balance == Decimal("100.00")

    async def test_get_balance_unlimited(
        self, db_session: AsyncSession, unlimited_tenant: Tenant
    ):
        """Unlimited tenant returns None."""
        balance = await get_balance(db_session, unlimited_tenant.id)
        assert balance is None

    async def test_get_balance_nonexistent(self, db_session: AsyncSession):
        """Non-existent tenant returns None."""
        balance = await get_balance(db_session, "nonexistent-id")
        assert balance is None

    async def test_get_tenant_balance_row(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """get_tenant_balance_row returns full Tenant row."""
        row = await get_tenant_balance_row(db_session, admin_tenant.id)
        assert row is not None
        assert row.id == admin_tenant.id
        assert row.balance_euros == Decimal("100.00")

    async def test_get_tenant_balance_row_nonexistent(
        self, db_session: AsyncSession
    ):
        """Non-existent tenant returns None."""
        row = await get_tenant_balance_row(db_session, "nonexistent-id")
        assert row is None


class TestAddFunds:
    """Tests for add_funds."""

    async def test_top_up_existing_balance(
        self, db_session: AsyncSession, admin_tenant: Tenant, admin_user
    ):
        """Positive top-up increases balance and records transaction."""
        txn = await add_funds(
            db_session,
            tenant_id=admin_tenant.id,
            amount_eur=Decimal("50.00"),
            admin_user_id=admin_user.id,
            reason="test top-up",
        )
        assert txn.tenant_id == admin_tenant.id
        assert txn.admin_user_id == admin_user.id
        assert txn.amount_eur == Decimal("50.00")
        assert txn.balance_after == Decimal("150.00")
        assert txn.reason == "test top-up"

        # Verify DB
        result = await db_session.execute(
            select(Tenant.balance_euros).where(Tenant.id == admin_tenant.id)
        )
        assert result.scalar_one() == Decimal("150.00")

    async def test_first_top_up_unlimited_tenant(
        self, db_session: AsyncSession, unlimited_tenant: Tenant, admin_user
    ):
        """First top-up on unlimited tenant transitions NULL -> amount via COALESCE."""
        txn = await add_funds(
            db_session,
            tenant_id=unlimited_tenant.id,
            amount_eur=Decimal("25.00"),
            admin_user_id=admin_user.id,
            reason="enable limit",
        )
        assert txn.amount_eur == Decimal("25.00")
        assert txn.balance_after == Decimal("25.00")

    async def test_admin_deduction(
        self, db_session: AsyncSession, admin_tenant: Tenant, admin_user
    ):
        """Negative amount decreases balance."""
        txn = await add_funds(
            db_session,
            tenant_id=admin_tenant.id,
            amount_eur=Decimal("-30.00"),
            admin_user_id=admin_user.id,
            reason="admin deduction",
        )
        assert txn.amount_eur == Decimal("-30.00")
        assert txn.balance_after == Decimal("70.00")

    async def test_add_funds_nonexistent_tenant(
        self, db_session: AsyncSession, admin_user
    ):
        """Non-existent tenant raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await add_funds(
                db_session,
                tenant_id="nonexistent-id",
                amount_eur=Decimal("10.00"),
                admin_user_id=admin_user.id,
                reason="test",
            )


class TestDisableLimit:
    """Tests for disable_limit."""

    async def test_disable_limit_on_bounded_tenant(
        self, db_session: AsyncSession, admin_tenant: Tenant, admin_user
    ):
        """Disabling limit sets balance to NULL and records audit entry."""
        # Disable the limit
        txn = await disable_limit(
            db_session,
            tenant_id=admin_tenant.id,
            admin_user_id=admin_user.id,
            reason="testing disable",
        )
        assert txn.amount_eur == Decimal("0")
        assert txn.balance_after == Decimal("0")
        assert txn.reason == "testing disable"

        # Verify balance became None
        result = await db_session.execute(
            select(Tenant.balance_euros).where(Tenant.id == admin_tenant.id)
        )
        assert result.scalar_one() is None

    async def test_disable_limit_on_unlimited_tenant(
        self, db_session: AsyncSession, unlimited_tenant: Tenant, admin_user
    ):
        """Disabling limit on already-unlimited tenant still works."""
        txn = await disable_limit(
            db_session,
            tenant_id=unlimited_tenant.id,
            admin_user_id=admin_user.id,
        )
        assert txn is not None
        assert txn.amount_eur == Decimal("0")

    async def test_disable_limit_nonexistent_tenant(
        self, db_session: AsyncSession, admin_user
    ):
        """Non-existent tenant raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await disable_limit(
                db_session,
                tenant_id="nonexistent-id",
                admin_user_id=admin_user.id,
            )


class TestDeductUsage:
    """Tests for deduct_usage."""

    async def test_deduct_from_bounded_tenant(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """Deduction decreases balance and records transaction."""
        txn = await deduct_usage(
            db_session,
            tenant_id=admin_tenant.id,
            cost_eur=Decimal("10.00"),
        )
        assert txn is not None
        assert txn.admin_user_id is None  # System deduction
        assert txn.amount_eur == Decimal("-10.00")
        assert txn.balance_after == Decimal("90.00")
        assert txn.reason == "usage_deduction"

    async def test_deduct_from_unlimited_tenant(
        self, db_session: AsyncSession, unlimited_tenant: Tenant
    ):
        """Unlimited tenant returns None — no deduction."""
        txn = await deduct_usage(
            db_session,
            tenant_id=unlimited_tenant.id,
            cost_eur=Decimal("10.00"),
        )
        assert txn is None

    async def test_deduct_to_zero(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """Deducting to exactly 0 works."""
        txn = await deduct_usage(
            db_session,
            tenant_id=admin_tenant.id,
            cost_eur=Decimal("100.00"),
        )
        assert txn is not None
        assert txn.balance_after == Decimal("0")

    async def test_deduct_negative_balance(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """Deduction can bring balance below 0."""
        txn = await deduct_usage(
            db_session,
            tenant_id=admin_tenant.id,
            cost_eur=Decimal("150.00"),
        )
        assert txn is not None
        assert txn.balance_after == Decimal("-50.00")

    async def test_deduct_nonexistent_tenant(
        self, db_session: AsyncSession
    ):
        """Non-existent tenant returns None (get_balance returns None first)."""
        result = await deduct_usage(
            db_session,
            tenant_id="nonexistent-id",
            cost_eur=Decimal("5.00"),
        )
        assert result is None


class TestCheckBalanceOrRaise:
    """Tests for check_balance_or_raise."""

    async def test_unlimited_tenant_noop(
        self, db_session: AsyncSession, unlimited_tenant: Tenant
    ):
        """Unlimited tenant — no-op."""
        await check_balance_or_raise(db_session, unlimited_tenant.id)
        # Should not raise

    async def test_positive_balance_noop(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """Positive balance — no-op."""
        await check_balance_or_raise(db_session, admin_tenant.id)
        # Should not raise

    async def test_zero_balance_raises(self, db_session: AsyncSession):
        """Zero balance raises InsufficientBalanceError."""
        from src.services.balance_service import get_balance, check_balance_or_raise

        # Create a tenant with zero balance inline
        tenant = Tenant(
            id="test-zero-bal-id",
            name="Zero Balance Tenant",
            balance_euros=Decimal("0"),
        )
        db_session.add(tenant)
        await db_session.flush()

        with pytest.raises(InsufficientBalanceError):
            await check_balance_or_raise(db_session, tenant.id)

    async def test_negative_balance_raises(self, db_session: AsyncSession):
        """Negative balance raises InsufficientBalanceError."""
        tenant = Tenant(
            id="test-negative-bal-id",
            name="Negative Balance Tenant",
            balance_euros=Decimal("-10.00"),
        )
        db_session.add(tenant)
        await db_session.flush()

        with pytest.raises(InsufficientBalanceError):
            await check_balance_or_raise(db_session, tenant.id)


class TestWarningThreshold:
    """Tests for get_warning_threshold and set_warning_threshold."""

    async def test_get_threshold(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """get_warning_threshold returns the stored value."""
        threshold = await get_warning_threshold(db_session, admin_tenant.id)
        assert threshold == Decimal("10.00")

    async def test_get_threshold_not_set(self, db_session: AsyncSession):
        """Tenant with no threshold returns None."""
        tenant = Tenant(
            id="test-no-threshold-id",
            name="No Threshold Tenant",
        )
        db_session.add(tenant)
        await db_session.flush()

        threshold = await get_warning_threshold(db_session, tenant.id)
        assert threshold is None

    async def test_set_threshold(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """set_warning_threshold updates the threshold."""
        updated = await set_warning_threshold(
            db_session,
            tenant_id=admin_tenant.id,
            threshold_eur=Decimal("25.00"),
        )
        assert updated.warning_threshold_eur == Decimal("25.00")

    async def test_clear_threshold(
        self, db_session: AsyncSession, admin_tenant: Tenant
    ):
        """Setting threshold to None clears it."""
        updated = await set_warning_threshold(
            db_session,
            tenant_id=admin_tenant.id,
            threshold_eur=None,
        )
        assert updated.warning_threshold_eur is None

    async def test_set_threshold_nonexistent(
        self, db_session: AsyncSession
    ):
        """Non-existent tenant raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await set_warning_threshold(
                db_session,
                tenant_id="nonexistent-id",
                threshold_eur=Decimal("10.00"),
            )


class TestTransactionHistory:
    """Tests for get_transaction_history."""

    async def test_empty_history(
        self, db_session: AsyncSession, unlimited_tenant: Tenant
    ):
        """Tenant with no transactions returns empty list."""
        txns, total = await get_transaction_history(
            db_session, unlimited_tenant.id
        )
        assert txns == []
        assert total == 0

    async def test_ordered_newest_first(
        self, db_session: AsyncSession, admin_tenant: Tenant, admin_user
    ):
        """Transactions are returned newest first."""
        # Create two transactions
        txn1 = await add_funds(
            db_session,
            tenant_id=admin_tenant.id,
            amount_eur=Decimal("10.00"),
            admin_user_id=admin_user.id,
            reason="first",
        )
        txn2 = await add_funds(
            db_session,
            tenant_id=admin_tenant.id,
            amount_eur=Decimal("20.00"),
            admin_user_id=admin_user.id,
            reason="second",
        )

        txns, total = await get_transaction_history(
            db_session, admin_tenant.id
        )
        assert total >= 2
        # Both transactions should be present
        txn_ids = {t.id for t in txns}
        assert txn1.id in txn_ids
        assert txn2.id in txn_ids

    async def test_tenant_isolation(
        self, db_session: AsyncSession, admin_tenant: Tenant,
        unlimited_tenant: Tenant, admin_user
    ):
        """Only returns transactions for the requested tenant."""
        await add_funds(
            db_session,
            tenant_id=admin_tenant.id,
            amount_eur=Decimal("50.00"),
            admin_user_id=admin_user.id,
            reason="for admin tenant",
        )
        await add_funds(
            db_session,
            tenant_id=unlimited_tenant.id,
            amount_eur=Decimal("25.00"),
            admin_user_id=admin_user.id,
            reason="for unlimited tenant",
        )

        txns, total = await get_transaction_history(
            db_session, admin_tenant.id
        )
        assert total == 1
        assert txns[0].tenant_id == admin_tenant.id

    async def test_pagination(
        self, db_session: AsyncSession, admin_tenant: Tenant, admin_user
    ):
        """Pagination returns correct slices."""
        for i in range(5):
            await add_funds(
                db_session,
                tenant_id=admin_tenant.id,
                amount_eur=Decimal(f"{i+1}.00"),
                admin_user_id=admin_user.id,
                reason=f"txn-{i}",
            )

        # Page 1 with page_size=2
        page1, total = await get_transaction_history(
            db_session, admin_tenant.id, page=1, page_size=2
        )
        assert total == 5
        assert len(page1) == 2

        # Page 3 with page_size=2 (last page with 1 item)
        page3, total = await get_transaction_history(
            db_session, admin_tenant.id, page=3, page_size=2
        )
        assert len(page3) == 1
