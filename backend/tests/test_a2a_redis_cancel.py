# =============================================================================
# PH Agent Hub — A2A Redis Cancellation Helper Tests
# =============================================================================
# Unit tests for the A2A cancellation helpers in core/redis.py.
# Uses mocked Redis — no actual Redis connection required.
# =============================================================================

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from src.core.redis import (
    set_a2a_cancel,
    check_a2a_cancel,
    clear_a2a_cancel,
    A2A_CANCEL_PREFIX,
    STREAM_CANCEL_PREFIX,
)


pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _mock_redis(monkeypatch):
    """Mock get_redis() to return a fake async Redis client."""
    fake_redis = AsyncMock()
    fake_redis.setex = AsyncMock()
    fake_redis.get = AsyncMock()
    fake_redis.delete = AsyncMock()
    fake_redis.pipeline = MagicMock()

    monkeypatch.setattr("src.core.redis.get_redis", AsyncMock(return_value=fake_redis))
    return fake_redis


class TestSetA2aCancel:
    async def test_sets_both_keys(self, _mock_redis):
        """set_a2a_cancel writes both a2a:cancel and stream:cancel keys."""
        fake_pipeline = AsyncMock()
        fake_pipeline.setex = AsyncMock()
        fake_pipeline.execute = AsyncMock()
        _mock_redis.pipeline.return_value = fake_pipeline

        await set_a2a_cancel("task-1", "session-abc")

        # Check that pipeline was used with both keys
        fake_pipeline.setex.assert_any_call(
            f"{A2A_CANCEL_PREFIX}task-1",
            ANY,  # ttl value from settings
            "session-abc",
        )
        fake_pipeline.setex.assert_any_call(
            f"{STREAM_CANCEL_PREFIX}session-abc",
            ANY,
            "1",
        )

    async def test_uses_custom_ttl(self, _mock_redis):
        """Custom ttl is passed to setex."""
        fake_pipeline = AsyncMock()
        fake_pipeline.setex = AsyncMock()
        fake_pipeline.execute = AsyncMock()
        _mock_redis.pipeline.return_value = fake_pipeline

        await set_a2a_cancel("task-1", "session-abc", ttl=300)

        fake_pipeline.setex.assert_any_call(
            f"{A2A_CANCEL_PREFIX}task-1", 300, "session-abc",
        )


class TestCheckA2aCancel:
    async def test_returns_session_id_when_set(self, _mock_redis):
        """check_a2a_cancel returns the session ID if key exists."""
        _mock_redis.get.return_value = "session-abc"

        result = await check_a2a_cancel("task-1")

        assert result == "session-abc"
        _mock_redis.get.assert_awaited_once_with(
            f"{A2A_CANCEL_PREFIX}task-1",
        )

    async def test_returns_none_when_not_set(self, _mock_redis):
        """check_a2a_cancel returns None if key does not exist."""
        _mock_redis.get.return_value = None

        result = await check_a2a_cancel("nonexistent")

        assert result is None


class TestClearA2aCancel:
    async def test_clears_both_keys(self, _mock_redis):
        """clear_a2a_cancel deletes both a2a:cancel and stream:cancel keys."""
        _mock_redis.get.return_value = "session-abc"
        fake_pipeline = AsyncMock()
        fake_pipeline.delete = AsyncMock()
        fake_pipeline.execute = AsyncMock()
        _mock_redis.pipeline.return_value = fake_pipeline

        await clear_a2a_cancel("task-1")

        fake_pipeline.delete.assert_any_call(
            f"{A2A_CANCEL_PREFIX}task-1",
        )
        fake_pipeline.delete.assert_any_call(
            f"{STREAM_CANCEL_PREFIX}session-abc",
        )

    async def test_does_nothing_when_not_set(self, _mock_redis):
        """clear_a2a_cancel is a no-op if the cancel key doesn't exist."""
        _mock_redis.get.return_value = None

        # Should not raise
        await clear_a2a_cancel("nonexistent")

        _mock_redis.pipeline.assert_not_called()

