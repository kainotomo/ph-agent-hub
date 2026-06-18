# =============================================================================
# PH Agent Hub — Usage Service
# =============================================================================
# DB helpers for writing and querying usage log rows.
# =============================================================================

from datetime import datetime
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.orm.usage_logs import UsageLog


def _compute_cost(
    *,
    tokens_in: int,
    tokens_out: int,
    cache_hit_tokens: int,
    input_price: Decimal | None,
    output_price: Decimal | None,
    cache_hit_price: Decimal | None,
) -> Decimal | None:
    """Compute cost from token counts and per-1M pricing.

    cost = (cache_miss * input + cache_hit * cache_hit + tokens_out * output) / 1_000_000
    Returns None if pricing is not configured.
    """
    if input_price is None or output_price is None:
        return None
    cache_miss = max(0, tokens_in - cache_hit_tokens)
    hit_price = cache_hit_price if cache_hit_price is not None else input_price
    cost = (
        cache_miss * input_price
        + cache_hit_tokens * hit_price
        + tokens_out * output_price
    ) / Decimal("1000000")
    return cost


async def write_usage_log(
    db: AsyncSession,
    *,
    tenant_id: str,
    tenant_name: str,
    user_id: str,
    user_email: str,
    user_full_name: str,
    model_id: str,
    model_name: str,
    provider: str,
    tokens_in: int,
    tokens_out: int,
    cache_hit_tokens: int = 0,
    input_price: Decimal | None = None,
    output_price: Decimal | None = None,
    cache_hit_price: Decimal | None = None,
) -> UsageLog:
    """Create and persist a usage log row with denormalized snapshots and computed cost."""
    cost = _compute_cost(
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_hit_tokens=cache_hit_tokens,
        input_price=input_price,
        output_price=output_price,
        cache_hit_price=cache_hit_price,
    )
    log = UsageLog(
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        user_id=user_id,
        user_email=user_email,
        user_full_name=user_full_name,
        model_id=model_id,
        model_name=model_name,
        provider=provider,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_hit_tokens=cache_hit_tokens,
        cost=cost,
    )
    db.add(log)
    await db.commit()
    return log


async def list_usage_logs(
    db: AsyncSession,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    search: str | None = None,
    provider: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[UsageLog], int]:
    """Query usage logs with optional filtering, sorting, pagination."""
    from datetime import datetime as dt_type
    stmt = select(UsageLog)

    if tenant_id is not None:
        stmt = stmt.where(UsageLog.tenant_id == tenant_id)
    if user_id is not None:
        stmt = stmt.where(UsageLog.user_id == user_id)
    if provider is not None:
        stmt = stmt.where(UsageLog.provider == provider)
    if date_from is not None:
        stmt = stmt.where(UsageLog.created_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(UsageLog.created_at <= date_to)

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(
        stmt, search,
        [UsageLog.user_email, UsageLog.model_name],
    )
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "created_at": UsageLog.created_at,
            "tokens_in": UsageLog.tokens_in,
            "tokens_out": UsageLog.tokens_out,
            "cost": UsageLog.cost,
        },
        default_sort=UsageLog.created_at.desc(),
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_tenant_aggregates(
    db: AsyncSession,
) -> dict[str, dict]:
    """Get aggregate usage per tenant: total tokens in/out and cost."""
    stmt = (
        select(
            UsageLog.tenant_id,
            UsageLog.tenant_name,
            func.sum(UsageLog.tokens_in).label("total_tokens_in"),
            func.sum(UsageLog.tokens_out).label("total_tokens_out"),
            func.sum(UsageLog.cost).label("total_cost"),
        )
        .group_by(UsageLog.tenant_id, UsageLog.tenant_name)
    )
    result = await db.execute(stmt)
    aggregates: dict[str, dict] = {}
    for row in result:
        aggregates[row.tenant_id] = {
            "total_tokens_in": int(row.total_tokens_in or 0),
            "total_tokens_out": int(row.total_tokens_out or 0),
            "total_cost": float(row.total_cost or 0),
        }
    return aggregates


async def get_user_aggregates(
    db: AsyncSession,
) -> dict[str, dict]:
    """Get aggregate usage per user: total tokens in/out and cost."""
    stmt = (
        select(
            UsageLog.user_id,
            func.sum(UsageLog.tokens_in).label("total_tokens_in"),
            func.sum(UsageLog.tokens_out).label("total_tokens_out"),
            func.sum(UsageLog.cost).label("total_cost"),
        )
        .group_by(UsageLog.user_id)
    )
    result = await db.execute(stmt)
    aggregates: dict[str, dict] = {}
    for row in result:
        aggregates[row.user_id] = {
            "total_tokens_in": int(row.total_tokens_in or 0),
            "total_tokens_out": int(row.total_tokens_out or 0),
            "total_cost": float(row.total_cost or 0),
        }
    return aggregates
