# =============================================================================
# PH Agent Hub — A2A Resilience Tests (Issue #409)
# =============================================================================
# Tests for circuit breaker, retry logic, transient error classification,
# and call log persistence.
#
# Mark: unit (Redis mocked via monkeypatch + AsyncMock).
# Pattern: test_a2a_redis_cancel.py
# =============================================================================

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.services.a2a_circuit_breaker import A2ACircuitBreaker, _parse_iso
from src.services.a2a_client import _is_transient_error, _resolve_or

pytestmark = [pytest.mark.unit]


# =========================================================================
# _is_transient_error — pure logic, no mocking needed
# =========================================================================


class TestIsTransientError:
    """Classification of retryable vs non-retryable errors."""

    def test_timeout_error_is_transient(self):
        assert _is_transient_error(asyncio.TimeoutError()) is True

    def test_httpx_timeout_is_transient(self):
        assert _is_transient_error(httpx.TimeoutException("timeout")) is True

    def test_connect_error_is_transient(self):
        assert _is_transient_error(httpx.ConnectError("connection refused")) is True

    def test_remote_protocol_error_is_transient(self):
        assert _is_transient_error(httpx.RemoteProtocolError("broken pipe")) is True

    def test_http_429_is_transient(self):
        response = MagicMock(spec=httpx.Response)
        response.status_code = 429
        exc = httpx.HTTPStatusError("rate limited", request=MagicMock(), response=response)
        assert _is_transient_error(exc) is True

    def test_http_5xx_is_transient(self):
        for code in (500, 502, 503, 504):
            response = MagicMock(spec=httpx.Response)
            response.status_code = code
            exc = httpx.HTTPStatusError("server error", request=MagicMock(), response=response)
            assert _is_transient_error(exc) is True, f"5xx code {code} should be transient"

    def test_http_4xx_is_not_transient(self):
        for code in (400, 401, 403, 404, 405, 422):
            response = MagicMock(spec=httpx.Response)
            response.status_code = code
            exc = httpx.HTTPStatusError("client error", request=MagicMock(), response=response)
            assert _is_transient_error(exc) is False, f"4xx code {code} should NOT be transient"

    def test_generic_exception_is_not_transient(self):
        assert _is_transient_error(ValueError("bad data")) is False

    def test_httpx_request_error_is_transient(self):
        assert _is_transient_error(httpx.RequestError("request failed")) is True


# =========================================================================
# A2ACircuitBreaker — Redis state machine
# =========================================================================


@pytest.fixture(autouse=True)
def _mock_redis(monkeypatch):
    """Mock ``get_redis()`` with an ``AsyncMock`` that supports all
    Redis hash operations used by ``A2ACircuitBreaker``."""
    fake_redis = AsyncMock()
    fake_redis.hgetall = AsyncMock(return_value={})
    fake_redis.hset = AsyncMock()
    fake_redis.delete = AsyncMock()
    fake_redis.expire = AsyncMock()
    monkeypatch.setattr(
        "src.services.a2a_circuit_breaker.get_redis",
        AsyncMock(return_value=fake_redis),
    )
    return fake_redis


class TestA2ACircuitBreaker:
    """State machine transitions in the Redis-backed circuit breaker."""

    async def test_before_call_returns_ok_when_no_state(self, _mock_redis):
        """Empty Redis → circuit is closed → returns "ok"."""
        _mock_redis.hgetall.return_value = {}
        cb = A2ACircuitBreaker("server-1", threshold=3, cooldown_seconds=60)
        assert await cb.before_call() == "ok"

    async def test_before_call_returns_ok_when_not_degraded(self, _mock_redis):
        """State present but ``degraded_at`` is empty → closed."""
        _mock_redis.hgetall.return_value = {"failures": "2", "degraded_at": ""}
        cb = A2ACircuitBreaker("server-1", threshold=3, cooldown_seconds=60)
        assert await cb.before_call() == "ok"

    async def test_before_call_raises_when_degraded(self, _mock_redis):
        """``degraded_at`` set and cooldown not elapsed → raises."""
        now = datetime.now(timezone.utc)
        _mock_redis.hgetall.return_value = {
            "failures": "5",
            "degraded_at": now.isoformat(),
        }
        cb = A2ACircuitBreaker("server-1", threshold=5, cooldown_seconds=300)
        with pytest.raises(Exception) as exc_info:
            await cb.before_call()
        assert "circuit breaker" in str(exc_info.value).lower()

    async def test_before_call_returns_probe_when_cooldown_elapsed(self, _mock_redis):
        """``degraded_at`` older than cooldown → probe allowed."""
        old_time = datetime.now(timezone.utc).timestamp() - 305
        degraded_at = datetime.fromtimestamp(old_time, tz=timezone.utc).isoformat()
        _mock_redis.hgetall.return_value = {
            "failures": "5",
            "degraded_at": degraded_at,
        }
        cb = A2ACircuitBreaker("server-1", threshold=5, cooldown_seconds=300)
        result = await cb.before_call()
        assert result == "probe"
        # Should record the probe timestamp
        _mock_redis.hset.assert_called_once()

    async def test_record_success_resets_failures(self, _mock_redis):
        """After success, ``failures`` is reset to 0 and degraded cleared."""
        _mock_redis.hgetall.return_value = {}
        cb = A2ACircuitBreaker("server-1")
        await cb.record_success()
        _mock_redis.hset.assert_called_once()
        mapping = _mock_redis.hset.call_args[1]["mapping"]
        assert mapping["failures"] == "0"
        assert mapping["degraded_at"] == ""

    async def test_record_failure_increments(self, _mock_redis):
        """Consecutive failures increment the counter."""
        _mock_redis.hgetall.return_value = {"failures": "2", "degraded_at": ""}
        cb = A2ACircuitBreaker("server-1", threshold=5)
        await cb.record_failure()
        mapping = _mock_redis.hset.call_args[1]["mapping"]
        assert mapping["failures"] == "3"

    async def test_record_failure_opens_circuit_at_threshold(self, _mock_redis):
        """Failures reach threshold → ``degraded_at`` is set."""
        _mock_redis.hgetall.return_value = {
            "failures": "4",
            "degraded_at": "",
            "last_failure_at": "",
        }
        cb = A2ACircuitBreaker("server-1", threshold=5)
        result = await cb.record_failure()
        assert result["degraded"] is True
        mapping = _mock_redis.hset.call_args[1]["mapping"]
        assert "degraded_at" in mapping

    async def test_record_failure_window_reset(self, _mock_redis):
        """Old failure outside window → counter resets to 1."""
        old_time = datetime.now(timezone.utc).timestamp() - 120  # 2 min ago
        old_iso = datetime.fromtimestamp(old_time, tz=timezone.utc).isoformat()
        _mock_redis.hgetall.return_value = {
            "failures": "4",
            "degraded_at": "",
            "last_failure_at": old_iso,
        }
        cb = A2ACircuitBreaker("server-1", threshold=5, window_seconds=60)
        result = await cb.record_failure()
        # Window is 60s, old failure is 120s ago → counter reset
        assert result["failures"] == "1"
        assert result["degraded"] is False

    async def test_get_state_returns_full_dict(self, _mock_redis):
        """``get_state`` returns computed state with all fields."""
        now_iso = datetime.now(timezone.utc).isoformat()
        _mock_redis.hgetall.return_value = {
            "failures": "3",
            "degraded_at": now_iso,
            "last_failure_at": now_iso,
        }
        cb = A2ACircuitBreaker(
            "server-1", threshold=5, window_seconds=60, cooldown_seconds=300,
        )
        state = await cb.get_state()
        assert state["failures"] == 3
        assert state["threshold"] == 5
        assert state["window_seconds"] == 60
        assert state["cooldown_seconds"] == 300
        assert state["degraded"] is True
        assert state["degraded_at"] == now_iso
        assert state["last_failure_at"] == now_iso

    async def test_reset_deletes_key(self, _mock_redis):
        """``reset`` deletes the Redis hash."""
        cb = A2ACircuitBreaker("server-1")
        await cb.reset()
        _mock_redis.delete.assert_called_once_with("a2a:cb:server-1")


# =========================================================================
# _resolve_or helper
# =========================================================================


class TestResolveOr:
    """Utility: value if not None, else default."""

    def test_returns_value_when_not_none(self):
        assert _resolve_or(42, 100) == 42

    def test_returns_default_when_none(self):
        assert _resolve_or(None, 100) == 100


# =========================================================================
# _parse_iso helper
# =========================================================================


class TestParseIso:
    """ISO-8601 timestamp parsing."""

    def test_parses_utc_iso(self):
        dt = _parse_iso("2025-01-01T00:00:00+00:00")
        assert dt.tzinfo is not None
        assert dt.year == 2025

    def test_parses_z_suffix(self):
        dt = _parse_iso("2025-06-15T12:30:00Z")
        assert dt.tzinfo is not None
        assert dt.hour == 12

    def test_parses_naive_as_utc(self):
        dt = _parse_iso("2025-06-15T12:30:00")
        assert dt.tzinfo is not None  # defaulted to UTC
        assert dt.hour == 12
