# =============================================================================
# PH Agent Hub — Run lease heartbeat (Issue #632)
# =============================================================================
# One task per live run keeps that run's Redis lease fresh for as long as the
# process is working on it.
#
# Why this is a separate module instead of a local task in the background
# runner:
#
#   * The heartbeat must start when the run is admitted (`_session_run`), not
#     when the background agent task happens to start.  The request handler
#     still does pre-stream work (auto-routing, session promotion, attachment
#     validation) while holding the lease, and a long phase there used to let
#     the lease expire before the first heartbeat ever ran.
#   * A missed heartbeat is not proof that the run died.  If the key expired
#     while this process is alive (event-loop stall in a blocking tool call,
#     Redis restart, eviction), the loop re-creates the *same* run id.  It
#     must never mint a new id: a second run in the same session would write
#     into the same conversation.
#   * A `running` row whose lease key is missing is only resolved as `lost`
#     by the durable `chat_runs.last_heartbeat_at` written here
#     (see `run_record.touch_run_heartbeat`).  The DB write is scheduled
#     separately so a slow/saturated connection pool can never delay the
#     lease refresh itself.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from ..core.config import settings
from .run_registry import RunLease, heartbeat_run, reacquire_run

logger = logging.getLogger(__name__)


@dataclass
class _Heartbeat:
    lease: RunLease
    stop: asyncio.Event
    task: asyncio.Task


#: One heartbeat task per run id, owned by this process.
_heartbeats: dict[str, _Heartbeat] = {}

#: Strong references to in-flight durable-heartbeat writes so the event loop
#: cannot garbage-collect them before they finish.
_db_touch_tasks: set[asyncio.Task] = set()


async def start_run_heartbeat(lease: RunLease) -> None:
    """Start the heartbeat for *lease* (idempotent per run id)."""
    if lease.run_id in _heartbeats:
        return
    stop = asyncio.Event()
    task = asyncio.create_task(
        _heartbeat_loop(lease, stop),
        name=f"run-heartbeat-{lease.run_id}",
    )
    _heartbeats[lease.run_id] = _Heartbeat(lease=lease, stop=stop, task=task)
    logger.debug(
        "Started run heartbeat for run %s session %s",
        lease.run_id, lease.session_id,
    )


async def stop_run_heartbeat(run_id: str, timeout: float = 2.0) -> None:
    """Stop the heartbeat for *run_id* if one is running.

    Best-effort: the caller is usually already tearing a run down, so a
    heartbeat that does not stop in time is cancelled instead of raised.
    """
    handle = _heartbeats.pop(run_id, None)
    if handle is None:
        return
    handle.stop.set()
    try:
        await asyncio.wait_for(handle.task, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "Run heartbeat for run %s did not stop within %ss — cancelling it",
            run_id, timeout,
        )
        handle.task.cancel()
        try:
            await handle.task
        except (asyncio.CancelledError, Exception):
            pass
    except asyncio.CancelledError:
        handle.task.cancel()
        raise
    except Exception:
        logger.warning(
            "Run heartbeat for run %s ended with an error", run_id,
            exc_info=True,
        )
    logger.debug("Stopped run heartbeat for run %s", run_id)


async def stop_all_run_heartbeats() -> int:
    """Stop every heartbeat owned by this process (graceful shutdown)."""
    run_ids = list(_heartbeats)
    for run_id in run_ids:
        await stop_run_heartbeat(run_id)
    if run_ids:
        logger.info("Stopped %d run heartbeat(s)", len(run_ids))
    return len(run_ids)


def active_heartbeat_count() -> int:
    """Number of run heartbeats currently registered (tests/diagnostics)."""
    return len(_heartbeats)


def _schedule_db_touch(lease: RunLease) -> None:
    """Write the durable heartbeat in the background (never blocks the lease)."""
    task = asyncio.create_task(_touch_db_heartbeat(lease))
    _db_touch_tasks.add(task)
    task.add_done_callback(_db_touch_tasks.discard)


async def _touch_db_heartbeat(lease: RunLease) -> None:
    try:
        # Local import: `run_record` imports `run_registry`, and importing it
        # from this module's import graph would create a cycle at startup.
        from .run_record import touch_run_heartbeat

        await touch_run_heartbeat(lease.run_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "Failed to write durable heartbeat for run %s", lease.run_id,
            exc_info=True,
        )


async def _heartbeat_loop(lease: RunLease, stop: asyncio.Event) -> None:
    """Refresh *lease* until it is stopped or the run is superseded."""
    interval = max(0.1, float(settings.RUN_HEARTBEAT_INTERVAL_SECONDS))
    db_interval = max(interval, float(settings.RUN_DB_HEARTBEAT_INTERVAL_SECONDS))
    last_ok = time.monotonic()
    last_db = 0.0

    try:
        while not stop.is_set():
            try:
                if await heartbeat_run(lease):
                    gap = time.monotonic() - last_ok
                    if gap > float(settings.RUN_LEASE_TTL_SECONDS):
                        # The run is alive but the event loop was starved.
                        # Surfacing the gap is what makes a future report of
                        # issue #632 diagnosable from production logs.
                        logger.warning(
                            "Run lease heartbeat gap %.1fs exceeded TTL %ss "
                            "for run %s session %s (event loop blocked)",
                            gap, settings.RUN_LEASE_TTL_SECONDS,
                            lease.run_id, lease.session_id,
                        )
                    last_ok = time.monotonic()
                elif await reacquire_run(lease):
                    logger.warning(
                        "Re-acquired expired run lease %s for session %s",
                        lease.run_id, lease.session_id,
                    )
                    last_ok = time.monotonic()
                else:
                    logger.warning(
                        "Run lease %s for session %s is gone and another run "
                        "owns the session — stopping heartbeat",
                        lease.run_id, lease.session_id,
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Run lease heartbeat failed for run %s session %s",
                    lease.run_id, lease.session_id, exc_info=True,
                )

            now = time.monotonic()
            if now - last_db >= db_interval:
                last_db = now
                _schedule_db_touch(lease)

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
    finally:
        current = _heartbeats.get(lease.run_id)
        if current is not None and current.task is asyncio.current_task():
            _heartbeats.pop(lease.run_id, None)
        logger.debug(
            "Run heartbeat loop ended for run %s session %s",
            lease.run_id, lease.session_id,
        )
