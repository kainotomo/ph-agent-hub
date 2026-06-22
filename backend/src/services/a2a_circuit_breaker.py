# =============================================================================
# PH Agent Hub — A2A Circuit Breaker
# =============================================================================
# Redis-backed circuit breaker for A2A remote server calls.
#
# State is stored in Redis hashes (key: ``a2a:cb:{server_id}``) with fields:
#
# - ``failures`` — consecutive failure count
# - ``degraded_at`` — ISO-8601 timestamp when the circuit was opened
# - ``last_failure_at`` — ISO-8601 timestamp of the most recent failure
# - ``last_probe_at`` — ISO-8601 timestamp of the most recent probe attempt
#
# The hash has a TTL of ``cooldown_seconds * 2`` so stale state auto-cleans.
# =============================================================================

import logging
from datetime import datetime, timezone

from ..core.exceptions import ServiceUnavailableError
from ..core.redis import get_redis

logger = logging.getLogger(__name__)

# Redis key prefix for circuit breaker state
CB_PREFIX = "a2a:cb:"


class A2ACircuitBreaker:
    """Circuit breaker for a single A2A server.

    Usage::

        cb = A2ACircuitBreaker(
            server_id="...",
            threshold=5,
            window_seconds=60,
            cooldown_seconds=300,
        )

        # Before making a call:
        verdict = await cb.before_call()
        if verdict == "circuit_open":
            # Return error, skip call
            ...

        # After a successful call:
        await cb.record_success()

        # After a failed call:
        await cb.record_failure()
    """

    def __init__(
        self,
        server_id: str,
        threshold: int = 5,
        window_seconds: int = 60,
        cooldown_seconds: int = 300,
    ) -> None:
        self.server_id = server_id
        self.threshold = threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._redis_key = f"{CB_PREFIX}{server_id}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def before_call(self) -> str:
        """Check whether a call to this server is allowed.

        Returns one of:
        - ``"ok"`` — call is allowed
        - ``"probe"`` — call is allowed as a probe (circuit was open,
          cooldown elapsed)
        - ``"circuit_open"`` — call is blocked; circuit is degraded

        Raises ``ServiceUnavailableError`` when the circuit is open.
        """
        r = await get_redis()
        state = await r.hgetall(self._redis_key)

        if not state:
            return "ok"

        degraded_at_str = state.get("degraded_at")
        if not degraded_at_str:
            return "ok"

        degraded_at = _parse_iso(degraded_at_str)

        # Check if cooldown has elapsed — allow a probe
        now = datetime.now(timezone.utc)
        elapsed = (now - degraded_at).total_seconds()
        if elapsed >= self.cooldown_seconds:
            logger.info(
                "Circuit breaker cooldown elapsed for server %s "
                "(degraded %.1fs ago). Allowing probe.",
                self.server_id,
                elapsed,
            )
            await r.hset(self._redis_key, "last_probe_at", now.isoformat())
            return "probe"

        # Circuit is still open
        logger.warning(
            "Circuit breaker open for server %s "
            "(degraded at %s, cooldown remaining %.0fs). "
            "Blocking call.",
            self.server_id,
            degraded_at_str,
            self.cooldown_seconds - elapsed,
        )
        raise ServiceUnavailableError(
            f"A2A server '{self.server_id}' is temporarily unavailable "
            f"(circuit breaker open, retry in ~{int(self.cooldown_seconds - elapsed)}s)."
        )

    async def record_success(self) -> None:
        """Record a successful call — reset failure count and clear degraded
        state."""
        r = await get_redis()
        await r.hset(
            self._redis_key,
            mapping={
                "failures": "0",
                # Clear degraded state
                "degraded_at": "",
                "last_failure_at": "",
            },
        )
        await self._refresh_ttl(r)
        logger.debug("Circuit breaker reset for server %s (success)", self.server_id)

    async def record_failure(self) -> dict:
        """Record a failed call.

        Increments the failure counter.  If the counter reaches
        ``threshold`` within ``window_seconds``, the circuit is opened
        (``degraded_at`` is set).

        Returns the current state dict (``{"failures": str, "degraded":
        bool}``).
        """
        now = datetime.now(timezone.utc)
        r = await get_redis()
        state = await r.hgetall(self._redis_key)

        # Read or initialise fields
        failures = int(state.get("failures", "0"))
        last_failure_at_str = state.get("last_failure_at", "")

        # If the last failure was outside the window, reset the counter
        if last_failure_at_str:
            last_failure_at = _parse_iso(last_failure_at_str)
            window_elapsed = (now - last_failure_at).total_seconds()
            if window_elapsed >= self.window_seconds:
                failures = 0

        failures += 1
        degraded = failures >= self.threshold

        mapping = {
            "failures": str(failures),
            "last_failure_at": now.isoformat(),
        }

        if degraded:
            mapping["degraded_at"] = now.isoformat()
            logger.warning(
                "Circuit breaker OPEN for server %s "
                "(%d consecutive failures, threshold=%d).",
                self.server_id,
                failures,
                self.threshold,
            )
        else:
            logger.debug(
                "Circuit breaker failure recorded for server %s "
                "(%d/%d).",
                self.server_id,
                failures,
                self.threshold,
            )

        await r.hset(self._redis_key, mapping=mapping)
        await self._refresh_ttl(r)

        return {"failures": str(failures), "degraded": degraded}

    async def get_state(self) -> dict:
        """Return current circuit breaker state for admin UI display.

        Returns a dict with keys: ``failures``, ``degraded_at``,
        ``last_failure_at``, ``last_probe_at``, ``threshold``,
        ``window_seconds``, ``cooldown_seconds``, ``degraded``.
        """
        r = await get_redis()
        state = await r.hgetall(self._redis_key)

        return {
            "failures": int(state.get("failures", "0")),
            "degraded_at": state.get("degraded_at") or None,
            "last_failure_at": state.get("last_failure_at") or None,
            "last_probe_at": state.get("last_probe_at") or None,
            "threshold": self.threshold,
            "window_seconds": self.window_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "degraded": bool(state.get("degraded_at")),
        }

    async def reset(self) -> None:
        """Manually reset the circuit breaker — clear all state."""
        r = await get_redis()
        await r.delete(self._redis_key)
        logger.info("Circuit breaker manually reset for server %s", self.server_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _refresh_ttl(self, r) -> None:
        """Refresh the Redis key TTL so state doesn't expire while the
        server is active."""
        ttl = self.cooldown_seconds * 2
        await r.expire(self._redis_key, ttl)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 timestamp string to a timezone-aware datetime.

    Handles both ``+00:00`` and ``Z`` suffixes as well as naive strings
    (assumed UTC).
    """
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
