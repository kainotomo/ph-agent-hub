# =============================================================================
# PH Agent Hub — Resilient A2A Client
# =============================================================================
# Production-grade resilience wrapper around a2a.Client.send_message():
#
# - Circuit breaker guard (server degraded → fast fail)
# - Retry with exponential backoff on transient errors
# - Configurable per-server timeouts (connect, read, stream)
# - Structured logging with trace context and latency
# - Call log persistence to A2aCallLog table
# =============================================================================

import asyncio
import logging
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.redis import get_redis
from ..db.orm.a2a_servers import A2aServer
from .a2a_circuit_breaker import A2ACircuitBreaker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def send_message_resilient(
    server: A2aServer,
    a2a_client,  # a2a.Client instance
    send_message_request,  # a2a.types.SendMessageRequest
    skill_id: str = "",
    skill_name: str = "",
    session_id: str = "",
    tenant_id: str = "",
    *,
    db: AsyncSession | None = None,
) -> tuple[list[str], dict]:
    """Send an A2A message with full resilience wrapping.

    Applies circuit breaker guard, configurable timeouts, retry with
    exponential backoff, structured logging, and optional call-log
    persistence.

    Args:
        server: The ``A2aServer`` ORM record (for per-server config).
        a2a_client: An active ``a2a.Client`` instance.
        send_message_request: The prepared ``SendMessageRequest``.
        skill_id: Remote skill identifier (for logging).
        skill_name: Human-readable skill name (for logging).
        session_id: Session UUID (for trace correlation).
        tenant_id: Tenant UUID (for call log denormalisation).
        db: Optional DB session — if provided, an ``A2aCallLog`` row is
             written after each call.

    Returns:
        A tuple of ``(result_parts: list[str], log_info: dict)`` where
        ``log_info`` contains structured metadata about the call.

    Raises:
        ``ServiceUnavailableError`` if the circuit breaker is open.
    """
    trace_id = str(uuid.uuid4())
    result_parts: list[str] = []

    # Resolve per-server config with global fallbacks
    retry_max = _resolve_or(server.retry_max_attempts, settings.A2A_DEFAULT_RETRY_MAX_ATTEMPTS)
    backoff_base = _resolve_or(server.retry_backoff_base_seconds, settings.A2A_DEFAULT_RETRY_BACKOFF_BASE_SECONDS)
    backoff_max = _resolve_or(server.retry_backoff_max_seconds, settings.A2A_DEFAULT_RETRY_BACKOFF_MAX_SECONDS)
    timeout_connect = _resolve_or(server.timeout_connect_seconds, settings.A2A_DEFAULT_TIMEOUT_CONNECT_SECONDS)
    timeout_read = _resolve_or(server.timeout_read_seconds, settings.A2A_DEFAULT_TIMEOUT_READ_SECONDS)
    timeout_stream = _resolve_or(server.timeout_stream_seconds, settings.A2A_DEFAULT_TIMEOUT_STREAM_SECONDS)
    cb_threshold = _resolve_or(server.circuit_breaker_threshold, settings.A2A_DEFAULT_CIRCUIT_BREAKER_THRESHOLD)
    cb_window = _resolve_or(server.circuit_breaker_window_seconds, settings.A2A_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS)
    cb_cooldown = _resolve_or(server.circuit_breaker_cooldown_seconds, settings.A2A_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS)

    # --- Circuit breaker guard ------------------------------------------
    cb = A2ACircuitBreaker(
        server_id=server.id,
        threshold=cb_threshold,
        window_seconds=cb_window,
        cooldown_seconds=cb_cooldown,
    )
    cb_verdict = await cb.before_call()  # raises ServiceUnavailableError if open

    # --- Attempt the call with retry ------------------------------------
    start_time = time.monotonic()
    last_error: str | None = None
    retry_count = 0
    status = "success"

    for attempt in range(retry_max + 1):
        is_streaming = _is_streaming_request(send_message_request)

        try:
            timeout = asyncio.wait_for(
                _do_send(a2a_client, send_message_request),
                timeout=timeout_stream if is_streaming else timeout_read,
            )
            result_parts = await timeout
            last_error = None
            break  # Success — exit retry loop

        except asyncio.TimeoutError:
            status = "timeout"
            last_error = f"A2A call timed out after {timeout_stream if is_streaming else timeout_read}s"
            logger.warning(
                "A2A call timeout: trace_id=%s server=%s skill=%s "
                "attempt=%d/%d timeout=%.1fs",
                trace_id, server.name, skill_id,
                attempt + 1, retry_max + 1,
                timeout_stream if is_streaming else timeout_read,
            )

        except Exception as exc:
            status = "error"
            last_error = str(exc)
            exc_name = type(exc).__name__

            # Determine if this is a transient error (eligible for retry)
            if _is_transient_error(exc):
                logger.warning(
                    "A2A transient error: trace_id=%s server=%s skill=%s "
                    "attempt=%d/%d error=%s: %s",
                    trace_id, server.name, skill_id,
                    attempt + 1, retry_max + 1,
                    exc_name, last_error,
                )
            else:
                # Non-transient — do not retry
                logger.error(
                    "A2A non-transient error: trace_id=%s server=%s skill=%s "
                    "error=%s: %s",
                    trace_id, server.name, skill_id,
                    exc_name, last_error,
                )
                break

        # If this was the last attempt, don't backoff
        if attempt >= retry_max:
            break

        # Exponential backoff
        sleep_seconds = min(backoff_base * (2 ** attempt), backoff_max)
        logger.debug(
            "A2A retry backoff: trace_id=%s server=%s attempt=%d "
            "sleep=%.1fs",
            trace_id, server.name, attempt + 1, sleep_seconds,
        )
        await asyncio.sleep(sleep_seconds)
        retry_count = attempt + 1

    # --- Post-call: record outcome --------------------------------------
    latency_ms = int((time.monotonic() - start_time) * 1000)

    if last_error is None:
        await cb.record_success()
    else:
        await cb.record_failure()

    # Structured log entry
    log_info = {
        "trace_id": trace_id,
        "server_name": server.name,
        "server_id": server.id,
        "skill_id": skill_id,
        "skill_name": skill_name,
        "session_id": session_id,
        "tenant_id": tenant_id,
        "latency_ms": latency_ms,
        "status": status,
        "retry_count": retry_count,
        "error_message": last_error,
    }

    _log_call(log_info)

    # Persist call log to DB if available
    if db is not None:
        await _persist_call_log(db, log_info)

    return result_parts, log_info


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _do_send(a2a_client, request):
    """Iterate the async generator from ``client.send_message()`` and
    collect response parts.

    This is extracted so ``asyncio.wait_for`` can wrap the entire send
    + iterate cycle.
    """
    from a2a.types import Part

    result_parts: list[str] = []
    async for stream_response in a2a_client.send_message(request):
        if stream_response.HasField("task"):
            task = stream_response.task
            if task.artifacts:
                for artifact in task.artifacts:
                    for r_part in artifact.parts:
                        formatted = _format_part(r_part)
                        if formatted:
                            result_parts.append(formatted)
        elif stream_response.HasField("message"):
            msg_resp = stream_response.message
            for r_part in msg_resp.parts:
                formatted = _format_part(r_part)
                if formatted:
                    result_parts.append(formatted)
    return result_parts


def _format_part(r_part) -> str | None:
    """Format a single A2A ``Part`` into a human-readable string.

    Mirrors the logic from ``tools/a2a._format_response_part`` so the
    resilient client can be used independently.
    """
    kind: str | None = None
    if hasattr(r_part, "WhichOneof") and callable(r_part.WhichOneof):
        try:
            raw_kind = r_part.WhichOneof("content")
            if isinstance(raw_kind, str) and raw_kind:
                kind = raw_kind
        except Exception:
            pass

    if not kind:
        for candidate in ("text", "data", "url", "raw"):
            val = getattr(r_part, candidate, None)
            if val is not None and val != "" and val != b"":
                kind = candidate
                break

    if kind == "text":
        return r_part.text
    elif kind == "data":
        try:
            import json
            from google.protobuf.json_format import MessageToDict
            return json.dumps(MessageToDict(r_part.data), indent=2)
        except Exception:
            return str(r_part.data)
    elif kind == "url":
        return str(r_part.url)
    elif kind == "raw":
        try:
            return r_part.raw.decode("utf-8", errors="replace")
        except Exception:
            return f"[raw binary, {len(r_part.raw)} bytes]"
    return None


def _is_transient_error(exc: Exception) -> bool:
    """Return ``True`` if the error is transient and worth retrying."""
    import httpx

    if isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.RemoteProtocolError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, httpx.RequestError):
        return True
    return False


def _is_streaming_request(request) -> bool:
    """Heuristic: if the request was sent via the streaming endpoint,
    use the longer streaming timeout."""
    return getattr(request, "_streaming", False)


def _resolve_or(value, default):
    """Return ``value`` if not ``None``, else ``default``."""
    return value if value is not None else default


def _log_call(info: dict) -> None:
    """Emit a structured log line for an A2A call."""
    logger.info(
        "A2A call: trace_id=%(trace_id)s server=%(server_name)s "
        "skill=%(skill_id)s session=%(session_id)s "
        "status=%(status)s latency_ms=%(latency_ms)d "
        "retries=%(retry_count)d error=%(error_message)s",
        info,
    )


async def _persist_call_log(db: AsyncSession, info: dict) -> None:
    """Write an ``A2aCallLog`` row to the database."""
    try:
        from ..db.orm.a2a_call_logs import A2aCallLog

        log_entry = A2aCallLog(
            tenant_id=info["tenant_id"] or "",
            a2a_server_id=info["server_id"],
            a2a_server_name=info["server_name"],
            skill_id=info["skill_id"],
            session_id=info["session_id"] or None,
            trace_id=info["trace_id"],
            status=info["status"],
            latency_ms=info["latency_ms"],
            retry_count=info["retry_count"],
            error_message=info["error_message"],
        )
        db.add(log_entry)
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to persist A2A call log: %s", exc)
