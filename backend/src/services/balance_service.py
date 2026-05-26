# =============================================================================
# PH Agent Hub — Balance Service
# =============================================================================
#
# Manages per-tenant euro balances with atomic MariaDB operations and a full
# audit trail via the balance_transactions table.
#
# Key concepts:
#   - balance_euros = NULL  → no limit (unlimited usage)
#   - balance_euros = N     → limit enforced; cutoff when <= 0
#   - admin enables a limit by adding funds (COALESCE handles NULL→N)
#   - admin disables a limit by clearing the balance to NULL
# =============================================================================

from decimal import Decimal

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError, InsufficientBalanceError
from ..db.orm.tenants import Tenant
from ..db.orm.balance_transactions import BalanceTransaction
from ..core.pagination import paginate


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def get_balance(
    db: AsyncSession,
    tenant_id: str,
) -> Decimal | None:
    """Return the current balance for a tenant, or None if unlimited."""
    result = await db.execute(
        select(Tenant.balance_euros).where(Tenant.id == tenant_id)
    )
    return result.scalar_one_or_none()


async def get_tenant_balance_row(
    db: AsyncSession,
    tenant_id: str,
) -> Tenant | None:
    """Return the full Tenant ORM row for balance-aware operations."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Write — Admin operations
# ---------------------------------------------------------------------------

async def add_funds(
    db: AsyncSession,
    tenant_id: str,
    amount_eur: Decimal,
    admin_user_id: str,
    reason: str,
) -> BalanceTransaction:
    """Add funds to a tenant's balance.

    Positive amount = top-up (enable or increase balance).
    Negative amount = admin deduction (decrease balance).

    For a tenant currently unlimited (balance_euros IS NULL), this operation
    **enables** the limit by setting balance_euros = amount_eur.
    """
    # Atomic update: COALESCE handles NULL→N transition
    stmt = (
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(
            balance_euros=func.coalesce(Tenant.balance_euros, 0) + amount_eur
        )
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        raise NotFoundError("Tenant not found")

    # Read back the new balance
    new_balance = await db.execute(
        select(Tenant.balance_euros).where(Tenant.id == tenant_id)
    )
    balance_after = new_balance.scalar_one()

    # Audit log
    txn = BalanceTransaction(
        tenant_id=tenant_id,
        admin_user_id=admin_user_id,
        amount_eur=amount_eur,
        balance_after=balance_after,
        reason=reason,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


async def disable_limit(
    db: AsyncSession,
    tenant_id: str,
    admin_user_id: str,
    reason: str = "admin_disabled",
) -> BalanceTransaction:
    """Disable the balance limit for a tenant by setting balance to NULL.

    Creates a final audit log entry capturing the balance before it was cleared.
    """
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found")

    current_balance = tenant.balance_euros

    # Set balance to NULL (unlimited)
    tenant.balance_euros = None
    await db.flush()

    # Audit log with amount=0, balance_after=0 to indicate limit removed
    txn = BalanceTransaction(
        tenant_id=tenant_id,
        admin_user_id=admin_user_id,
        amount_eur=Decimal("0"),
        balance_after=Decimal("0"),
        reason=reason,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


# ---------------------------------------------------------------------------
# Write — System deduction (called by agent runner after usage)
# ---------------------------------------------------------------------------

async def deduct_usage(
    db: AsyncSession,
    tenant_id: str,
    cost_eur: Decimal,
    reference_type: str = "usage_log",
    reference_id: str | None = None,
) -> BalanceTransaction | None:
    """Deduct a usage cost from a tenant's balance.

    Only performs the deduction if the tenant has a numeric balance
    (i.e., has a limit enabled). Returns the transaction or None if
    the tenant is unlimited.
    """
    # Check current balance first
    current = await get_balance(db, tenant_id)
    if current is None:
        return None  # Unlimited tenant — nothing to deduct

    # Atomic decrement
    stmt = (
        update(Tenant)
        .where(Tenant.id == tenant_id)
        .values(balance_euros=Tenant.balance_euros - cost_eur)
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        raise NotFoundError("Tenant not found")

    # Read back new balance
    new_balance = await db.execute(
        select(Tenant.balance_euros).where(Tenant.id == tenant_id)
    )
    balance_after = new_balance.scalar_one()

    txn = BalanceTransaction(
        tenant_id=tenant_id,
        admin_user_id=None,  # System deduction
        amount_eur=-cost_eur,
        balance_after=balance_after,
        reason="usage_deduction",
        reference_type=reference_type,
        reference_id=reference_id,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


# ---------------------------------------------------------------------------
# Pre-flight check (called by agent runner before LLM call)
# ---------------------------------------------------------------------------

async def check_balance_or_raise(
    db: AsyncSession,
    tenant_id: str,
) -> None:
    """Raise InsufficientBalanceError if the tenant has a limit and balance <= 0.

    Does nothing if the tenant is unlimited (balance IS NULL).
    """
    balance = await get_balance(db, tenant_id)
    if balance is None:
        return  # Unlimited — no check
    if balance <= 0:
        raise InsufficientBalanceError()


# ---------------------------------------------------------------------------
# Warning threshold
# ---------------------------------------------------------------------------

async def get_warning_threshold(
    db: AsyncSession,
    tenant_id: str,
) -> Decimal | None:
    """Return the warning threshold for a tenant, or None."""
    result = await db.execute(
        select(Tenant.warning_threshold_eur).where(Tenant.id == tenant_id)
    )
    return result.scalar_one_or_none()


async def set_warning_threshold(
    db: AsyncSession,
    tenant_id: str,
    threshold_eur: Decimal | None,
) -> Tenant:
    """Set (or clear) the warning threshold for a tenant."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found")

    tenant.warning_threshold_eur = threshold_eur
    await db.commit()
    await db.refresh(tenant)
    return tenant


# ---------------------------------------------------------------------------
# Transaction history
# ---------------------------------------------------------------------------

async def get_transaction_history(
    db: AsyncSession,
    tenant_id: str,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[BalanceTransaction], int]:
    """Return paginated transaction history for a tenant, newest first."""
    stmt = (
        select(BalanceTransaction)
        .where(BalanceTransaction.tenant_id == tenant_id)
        .order_by(BalanceTransaction.created_at.desc())
    )
    return await paginate(db, stmt, page=page, page_size=page_size)
