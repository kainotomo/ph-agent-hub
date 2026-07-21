# =============================================================================
# PH Agent Hub — StreamBridge
# =============================================================================
# Decouples agent execution from SSE connections.  The agent runs as an
# independent background task writing events to a StreamBridge; SSE readers
# subscribe and consume from the bridge.  Multiple concurrent readers are
# supported (initial SSE + reconnect SSE), and the bridge replays the last N
# buffered events to new subscribers.
#
# Usage:
#   bridge = StreamBridge(session_id)
#   register_bridge(session_id, bridge)
#   background_task = asyncio.create_task(run_agent(bridge))
#   sse_response = EventSourceResponse(bridge.subscribe())
#
# Agent writes:
#   bridge.put({"event": "token", "data": '{"delta":"hello"}'})
#
# On completion:
#   bridge.close()
#   remove_bridge(session_id)
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StreamBridge
# ---------------------------------------------------------------------------

class StreamBridge:
    """An event buffer that decouples event producers (agent tasks) from
    event consumers (SSE streams).

    Events are stored in a deque of last N events for replay on new
    subscriptions.  Each subscriber gets its own :class:`asyncio.Queue`
    so all subscribers receive all events (broadcast, not competing
    consumers).

    Auto-emits heartbeat events if no events arrive for *heartbeat_interval*
    seconds.
    """

    def __init__(
        self,
        session_id: str,
        max_buffer: int = 200,
        heartbeat_interval: int = 15,
        autopilot: bool = False,
    ) -> None:
        self._session_id = session_id
        self._max_buffer = max_buffer
        self._heartbeat_interval = heartbeat_interval
        self._autopilot = autopilot

        #: Bounded buffer of past events for replay on new subscriptions.
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max_buffer)

        #: Per-subscriber queues — each subscriber gets ALL events.
        self._subscribers: list[asyncio.Queue[dict[str, Any] | None]] = []

        #: Total number of events produced (for monitoring / debugging).
        self._event_count: int = 0

        #: Set to True when ``close()`` is called — subscribers see ``None``
        #: and stop.
        self._closed: bool = False

        #: Guard to ensure :meth:`close` is idempotent.
        self._close_lock: asyncio.Lock = asyncio.Lock()

        logger.debug(
            "StreamBridge created for session %s (buffer=%d, heartbeat=%ds)",
            session_id, max_buffer, heartbeat_interval,
        )

    # ------------------------------------------------------------------
    # Producer API (called by the agent background task)
    # ------------------------------------------------------------------

    async def put(self, event: dict[str, Any]) -> None:
        """Write an event to the bridge.

        The event is appended to the replay buffer and pushed into ALL
        subscriber queues so every connected SSE client receives it.
        """
        if self._closed:
            logger.warning(
                "StreamBridge[%s] put() called on closed bridge — dropping event %s",
                self._session_id, event.get("event"),
            )
            return

        self._buffer.append(event)
        self._event_count += 1

        # Broadcast to all subscribers (each gets a copy).
        for sub in self._subscribers:
            await sub.put(event)

    async def close(self) -> None:
        """Close the bridge, signalling all subscribers to stop.

        Idempotent — safe to call multiple times.
        """
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True

        # Push ``None`` sentinel to ALL subscriber queues.
        for sub in self._subscribers:
            await sub.put(None)

        logger.debug(
            "StreamBridge[%s] closed — %d events produced, %d buffered",
            self._session_id, self._event_count, len(self._buffer),
        )

    # ------------------------------------------------------------------
    # Consumer API (called by SSE readers)
    # ------------------------------------------------------------------

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Return an async iterator that replays buffered events then yields
        live events from the queue.

        Each subscriber gets its own private queue, so all subscribers
        receive all events (broadcast).  When the bridge is closed the
        iterator stops (does NOT raise ``StopAsyncIteration``).
        """
        # Replay buffered events first.
        for event in self._buffer:
            yield event

        # Create a per-subscriber queue so this reader doesn't compete
        # with other readers for events.
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            while not self._closed:
                try:
                    # Use wait_for so we can emit heartbeats even if the queue
                    # is idle for a long time (e.g. during a slow tool call).
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=self._heartbeat_interval,
                    )
                except asyncio.TimeoutError:
                    # No event arrived — emit heartbeat so the SSE connection
                    # stays alive.
                    yield {"event": "heartbeat", "data": "{}"}
                    continue

                if event is None:
                    # Sentinel — bridge is closed.
                    return

                yield event
        finally:
            # Clean up this subscriber's queue on exit (aclose/disconnect).
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def is_autopilot(self) -> bool:
        return self._autopilot

    def __repr__(self) -> str:
        return (
            f"StreamBridge(session={self._session_id}, "
            f"events={self._event_count}, "
            f"buffered={len(self._buffer)}, "
            f"closed={self._closed})"
        )


# ---------------------------------------------------------------------------
# Active bridge registry
# ---------------------------------------------------------------------------

#: In-memory mapping of session_id → StreamBridge.
#: Bridges live only as long as the agent task is running.
_active_bridges: dict[str, StreamBridge] = {}


def register_bridge(session_id: str, bridge: StreamBridge) -> None:
    """Register a StreamBridge for *session_id*."""
    _active_bridges[session_id] = bridge
    logger.debug("Registered StreamBridge for session %s", session_id)


def get_bridge(session_id: str) -> StreamBridge | None:
    """Return the registered StreamBridge for *session_id*, or ``None``."""
    return _active_bridges.get(session_id)


def remove_bridge(session_id: str) -> None:
    """Remove and return the registered StreamBridge for *session_id*.

    Does NOT close the bridge — the agent task's ``finally`` block
    should call ``bridge.close()`` before this.
    """
    removed = _active_bridges.pop(session_id, None)
    if removed is not None:
        logger.debug("Removed StreamBridge for session %s", session_id)
