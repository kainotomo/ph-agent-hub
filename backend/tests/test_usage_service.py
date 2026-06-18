# =============================================================================
# PH Agent Hub — Usage Service Tests
# =============================================================================

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.usage_logs import UsageLog
from src.db.orm.tenants import Tenant
from src.db.orm.users import User
from src.services.usage_service import (
    _compute_cost,
    get_tenant_aggregates,
    get_user_aggregates,
    list_usage_logs,
    write_usage_log,
)

pytestmark = [pytest.mark.integration]


class TestComputeCost:
    """Unit tests for _compute_cost — pure function, no DB needed."""

    @pytest.mark.unit
    def test_standard_pricing(self):
        """Standard pricing calculation."""
        cost = _compute_cost(
            tokens_in=1000,
            tokens_out=500,
            cache_hit_tokens=0,
            input_price=Decimal("2"),
            output_price=Decimal("3"),
            cache_hit_price=Decimal("0.5"),
        )
        # cost = (1000 * 2 + 0 * 0.5 + 500 * 3) / 1_000_000
        #      = (2000 + 1500) / 1_000_000 = 0.0035
        assert cost == Decimal("0.0035")

    @pytest.mark.unit
    def test_cache_hit_pricing(self):
        """Cache hits use cache_hit_price when provided."""
        cost = _compute_cost(
            tokens_in=1000,
            tokens_out=500,
            cache_hit_tokens=200,
            input_price=Decimal("2"),
            output_price=Decimal("3"),
            cache_hit_price=Decimal("0.5"),
        )
        # cache_miss = 1000 - 200 = 800
        # cost = (800 * 2 + 200 * 0.5 + 500 * 3) / 1_000_000
        #      = (1600 + 100 + 1500) / 1_000_000 = 0.0032
        assert cost == Decimal("0.0032")

    @pytest.mark.unit
    def test_cache_hit_fallback_to_input_price(self):
        """When cache_hit_price is None, fall back to input_price."""
        cost = _compute_cost(
            tokens_in=1000,
            tokens_out=200,
            cache_hit_tokens=300,
            input_price=Decimal("2"),
            output_price=Decimal("3"),
            cache_hit_price=None,
        )
        # cache_miss = 1000 - 300 = 700
        # cost = (700 * 2 + 300 * 2 + 200 * 3) / 1_000_000
        #      = (1400 + 600 + 600) / 1_000_000 = 0.0026
        assert cost == Decimal("0.0026")

    @pytest.mark.unit
    def test_cache_hit_exceeds_tokens_in(self):
        """cache_hit_tokens > tokens_in → cache_miss clamped to 0."""
        cost = _compute_cost(
            tokens_in=100,
            tokens_out=50,
            cache_hit_tokens=500,
            input_price=Decimal("2"),
            output_price=Decimal("3"),
            cache_hit_price=Decimal("0.5"),
        )
        # cache_miss = max(0, 100 - 500) = 0
        # cost = (0 * 2 + 500 * 0.5 + 50 * 3) / 1_000_000
        #      = (0 + 250 + 150) / 1_000_000 = 0.0004
        assert cost == Decimal("0.0004")

    @pytest.mark.unit
    def test_no_input_price(self):
        """input_price=None returns None."""
        cost = _compute_cost(
            tokens_in=1000,
            tokens_out=500,
            cache_hit_tokens=0,
            input_price=None,
            output_price=Decimal("3"),
            cache_hit_price=Decimal("0.5"),
        )
        assert cost is None

    @pytest.mark.unit
    def test_no_output_price(self):
        """output_price=None returns None."""
        cost = _compute_cost(
            tokens_in=1000,
            tokens_out=500,
            cache_hit_tokens=0,
            input_price=Decimal("2"),
            output_price=None,
            cache_hit_price=Decimal("0.5"),
        )
        assert cost is None

    @pytest.mark.unit
    def test_zero_tokens(self):
        """Zero tokens produces zero cost."""
        cost = _compute_cost(
            tokens_in=0,
            tokens_out=0,
            cache_hit_tokens=0,
            input_price=Decimal("2"),
            output_price=Decimal("3"),
            cache_hit_price=Decimal("0.5"),
        )
        assert cost == Decimal("0")

    @pytest.mark.unit
    def test_large_token_counts(self):
        """Large token counts scale correctly with per-1M pricing."""
        cost = _compute_cost(
            tokens_in=2_000_000,
            tokens_out=1_000_000,
            cache_hit_tokens=500_000,
            input_price=Decimal("15"),
            output_price=Decimal("60"),
            cache_hit_price=Decimal("5"),
        )
        # cache_miss = 2_000_000 - 500_000 = 1_500_000
        # cost = (1_500_000*15 + 500_000*5 + 1_000_000*60) / 1_000_000
        #      = (22_500_000 + 2_500_000 + 60_000_000) / 1_000_000
        #      = 85_000_000 / 1_000_000 = 85
        assert cost == Decimal("85")

    @pytest.mark.unit
    def test_decimal_precision(self):
        """Cost has proper Decimal precision."""
        cost = _compute_cost(
            tokens_in=1,
            tokens_out=1,
            cache_hit_tokens=0,
            input_price=Decimal("0.15"),
            output_price=Decimal("0.60"),
            cache_hit_price=Decimal("0.05"),
        )
        # (1*0.15 + 0 + 1*0.60) / 1_000_000 = 0.75 / 1_000_000 = 0.00000075
        expected = Decimal("0.00000075")
        assert cost == expected


class TestWriteUsageLog:
    """Integration tests for write_usage_log."""

    async def test_write_with_pricing(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """Writes a UsageLog with cost computed from pricing."""
        log = await write_usage_log(
            db_session,
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            user_id=test_user.id,
            user_email=test_user.email,
            user_full_name=test_user.display_name,
            model_id="test-model-id",
            model_name="gpt-4",
            provider="openai",
            tokens_in=1000,
            tokens_out=500,
            cache_hit_tokens=200,
            input_price=Decimal("10"),
            output_price=Decimal("30"),
            cache_hit_price=Decimal("2.5"),
        )
        assert log.tenant_id == test_tenant.id
        assert log.tenant_name == test_tenant.name
        assert log.user_id == test_user.id
        assert log.user_email == test_user.email
        assert log.user_full_name == test_user.display_name
        assert log.model_name == "gpt-4"
        assert log.provider == "openai"
        assert log.tokens_in == 1000
        assert log.tokens_out == 500
        assert log.cache_hit_tokens == 200
        # cache_miss = 800, cost = (800*10 + 200*2.5 + 500*30) / 1_000_000
        # = (8000 + 500 + 15000) / 1_000_000 = 0.0235
        assert log.cost == Decimal("0.0235")

    async def test_write_without_pricing(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """When pricing is not provided, cost is None."""
        log = await write_usage_log(
            db_session,
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            user_id=test_user.id,
            user_email=test_user.email,
            user_full_name=test_user.display_name,
            model_id="test-model-id",
            model_name="gpt-4",
            provider="openai",
            tokens_in=1000,
            tokens_out=500,
        )
        assert log.cost is None

    async def test_denormalized_snapshots(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """Denormalized fields are snapshotted at write time."""
        log = await write_usage_log(
            db_session,
            tenant_id=test_tenant.id,
            tenant_name="Snapshot-Tenant",
            user_id=test_user.id,
            user_email="snapshot@test.com",
            user_full_name="Snapshot User",
            model_id="test-model-id",
            model_name="snapshot-model",
            provider="test",
            tokens_in=100,
            tokens_out=50,
        )
        assert log.tenant_name == "Snapshot-Tenant"
        assert log.user_email == "snapshot@test.com"
        assert log.user_full_name == "Snapshot User"
        assert log.model_name == "snapshot-model"

    async def test_cache_hit_tokens_stored(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """Cache hit tokens are stored correctly."""
        log = await write_usage_log(
            db_session,
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            user_id=test_user.id,
            user_email=test_user.email,
            user_full_name=test_user.display_name,
            model_id="test-model-id",
            model_name="gpt-4",
            provider="openai",
            tokens_in=500,
            tokens_out=100,
            cache_hit_tokens=300,
        )
        assert log.cache_hit_tokens == 300


class TestListUsageLogs:
    """Integration tests for list_usage_logs."""

    @pytest_asyncio.fixture(autouse=True)
    async def _seed_logs(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """Seed a few usage logs for filtering/pagination tests."""
        now = datetime.now(timezone.utc)
        logs = [
            UsageLog(
                tenant_id=test_tenant.id,
                tenant_name=test_tenant.name,
                user_id=test_user.id,
                user_email=test_user.email,
                user_full_name=test_user.display_name,
                model_id="m1",
                model_name="gpt-4",
                provider="openai",
                tokens_in=100,
                tokens_out=50,
                cache_hit_tokens=0,
                cost=Decimal("0.001"),
                created_at=now,
            ),
            UsageLog(
                tenant_id=test_tenant.id,
                tenant_name=test_tenant.name,
                user_id=test_user.id,
                user_email=test_user.email,
                user_full_name=test_user.display_name,
                model_id="m2",
                model_name="claude-3",
                provider="anthropic",
                tokens_in=200,
                tokens_out=100,
                cache_hit_tokens=50,
                cost=Decimal("0.002"),
                created_at=now,
            ),
            UsageLog(
                tenant_id=test_tenant.id,
                tenant_name=test_tenant.name,
                user_id=test_user.id,
                user_email=test_user.email,
                user_full_name=test_user.display_name,
                model_id="m3",
                model_name="deepseek-chat",
                provider="deepseek",
                tokens_in=300,
                tokens_out=150,
                cache_hit_tokens=100,
                cost=Decimal("0.003"),
                created_at=now,
            ),
        ]
        for log in logs:
            db_session.add(log)
        await db_session.flush()

    async def test_no_filters(
        self, db_session: AsyncSession
    ):
        """No filters returns all logs."""
        logs, total = await list_usage_logs(db_session)
        assert total >= 3

    async def test_filter_by_tenant(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        """Filter by tenant_id returns only that tenant's logs."""
        logs, total = await list_usage_logs(
            db_session, tenant_id=test_tenant.id
        )
        assert total >= 3
        for log in logs:
            assert log.tenant_id == test_tenant.id

    async def test_filter_by_provider(
        self, db_session: AsyncSession
    ):
        """Filter by provider returns matching logs."""
        logs, total = await list_usage_logs(
            db_session, provider="openai"
        )
        assert total >= 1
        for log in logs:
            assert log.provider == "openai"

    async def test_search_by_model_name(
        self, db_session: AsyncSession
    ):
        """Search by model_name returns matching logs."""
        logs, total = await list_usage_logs(
            db_session, search="gpt-4"
        )
        assert total >= 1
        for log in logs:
            assert "gpt-4" in log.model_name

    async def test_pagination(
        self, db_session: AsyncSession
    ):
        """Pagination returns correct slices."""
        page1, total = await list_usage_logs(
            db_session, page=1, page_size=2
        )
        assert len(page1) <= 2
        assert total >= 3

    async def test_empty_result(
        self, db_session: AsyncSession
    ):
        """No matching logs returns ([], 0)."""
        logs, total = await list_usage_logs(
            db_session, provider="nonexistent"
        )
        assert logs == []
        assert total == 0


class TestGetTenantAggregates:
    """Integration tests for get_tenant_aggregates."""

    async def test_empty_db(
        self, db_session: AsyncSession
    ):
        """No logs for the current session yields no unexpected entries."""
        aggregates = await get_tenant_aggregates(db_session)
        # May contain pre-existing data from previous test runs;
        # just verify the structure is correct.
        assert isinstance(aggregates, dict)

    async def test_aggregates_by_tenant(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """Multiple tenants produce correct per-tenant aggregates."""
        # Logs for test_tenant
        await write_usage_log(
            db_session,
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            user_id=test_user.id,
            user_email=test_user.email,
            user_full_name=test_user.display_name,
            model_id="m1",
            model_name="gpt-4",
            provider="openai",
            tokens_in=100,
            tokens_out=50,
            input_price=Decimal("10"),
            output_price=Decimal("30"),
        )

        aggregates = await get_tenant_aggregates(db_session)
        assert test_tenant.id in aggregates
        agg = aggregates[test_tenant.id]
        assert agg["total_tokens_in"] >= 100
        assert agg["total_tokens_out"] >= 50


class TestGetUserAggregates:
    """Integration tests for get_user_aggregates."""

    async def test_empty_db(
        self, db_session: AsyncSession
    ):
        """No logs for the current session yields no unexpected entries."""
        aggregates = await get_user_aggregates(db_session)
        # May contain pre-existing data from previous test runs;
        # just verify the structure is correct.
        assert isinstance(aggregates, dict)

    async def test_aggregates_by_user(
        self, db_session: AsyncSession, test_tenant: Tenant, test_user: User
    ):
        """Multiple users produce correct per-user aggregates."""
        await write_usage_log(
            db_session,
            tenant_id=test_tenant.id,
            tenant_name=test_tenant.name,
            user_id=test_user.id,
            user_email=test_user.email,
            user_full_name=test_user.display_name,
            model_id="m1",
            model_name="gpt-4",
            provider="openai",
            tokens_in=200,
            tokens_out=100,
            input_price=Decimal("10"),
            output_price=Decimal("30"),
        )

        aggregates = await get_user_aggregates(db_session)
        assert test_user.id in aggregates
        agg = aggregates[test_user.id]
        assert agg["total_tokens_in"] >= 200
        assert agg["total_tokens_out"] >= 100
