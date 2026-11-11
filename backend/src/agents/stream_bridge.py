# =============================================================================
# PH Agent Hub — StreamBridge
# =============================================================================
# Decouples agent execution from SSE connections.  The agent runs as an
# independent background task writing events to a StreamBridge; SSE readers
# subscribe and consume from the bridge.  Multiple concurrent readers are
# supported (initial SSE + reconnect SSE), and the bridge replays the last N
# buffered events to new subscribers.
#
# Every event is stamped with a monotonic ``seq`` (1..N, per run) and an SSE
# ``id`` of the form ``<run_id>:<seq>`` so a reconnecting client can resume
# exactly where it stopped instead of re-applying events it already saw.
# Subscribers resume with ``from_seq``; when the requested cursor has already
# fallen out of the bounded replay buffer the bridge emits ``stream_resync``
# so the client can reload persisted state instead of silently continuing
# with a hole in the message.
#
# Usage:
#   bridge = StreamBridge(session_id, run_id=lease.run_id)
#   register_bridge(session_id, bridge)
#   background_task = asyncio.create_task(run_agent(bridge))
#   sse_response = EventSourceResponse(bridge.subscribe(from_seq=last_seq))
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

#: Default number of events kept for replay.  A run at ~40 tokens/s fills
#: this in about half a minute; older events are recovered from the persisted
#: message (``stream_resync``) rather than replayed.
DEFAULT_MAX_BUFFER = 1000


class _Subscriber:
    """One SSE reader attached to a bridge.

    ``overflowed`` is set by the producer when the reader is too slow to
    drain its queue.  The reader then emits ``stream_resync`` instead of
    silently skipping events, so a slow client converges back to the
    persisted message rather than showing a corrupt partial answer.
    """

    __slots__ = ("queue", "overflowed")

    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=maxsize
        )
        self.overflowed = False


# ---------------------------------------------------------------------------
# StreamBridge
# ---------------------------------------------------------------------------

class StreamBridge:
    """An event buffer that decouples event producers (agent tasks) from
    event consumers (SSE streams).

    Events are stored in a deque of last N events for replay on new
    subscriptions.  Each subscriber gets its own bounded
    :class:`asyncio.Queue` so all subscribers receive all events
    (broadcast, not competing consumers).

    Auto-emits heartbeat events if no events arrive for *heartbeat_interval*
    seconds.
    """

    def __init__(
        self,
        session_id: str,
        max_buffer: int = DEFAULT_MAX_BUFFER,
        heartbeat_interval: int = 15,
        autopilot: bool = False,
        run_id: str | None = None,
        subscriber_queue_size: int = 512,
    ) -> None:
        self._session_id = session_id
        self._max_buffer = max_buffer
        self._heartbeat_interval = heartbeat_interval
        self._autopilot = autopilot
        self._run_id = run_id
        self._subscriber_queue_size = subscriber_queue_size

        #: Bounded buffer of ``(seq, wire_event)`` for replay on new
        #: subscriptions.  The sequence lives beside the event rather than
        #: inside it: ``sse_starlette`` builds ``ServerSentEvent(**event)``
        #: and rejects keys it does not know, so only ``id`` may be added
        #: to the wire dict.
        self._buffer: deque[tuple[int, dict[str, Any]]] = deque(maxlen=max_buffer)

        #: Per-subscriber queues — each subscriber gets ALL events.
        self._subscribers: list[_Subscriber] = []

        #: Total number of events produced (for monitoring / debugging).
        self._event_count: int = 0

        #: Monotonic sequence assigned to each produced event.
        self._seq: int = 0

        #: Set to True when ``close()`` is called — subscribers see ``None``
        #: and stop.
        self._closed: bool = False

        #: Set to True once any SSE subscriber has attached (Issue #608:
        #: `client_attached` distinguishes "answer produced but no browser
        #: ever saw it" from a delivered answer).
        self._ever_attached: bool = False

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

        The event is stamped with its SSE ``id`` (``<run_id>:<seq>``),
        appended to the replay buffer, and pushed into ALL subscriber
        queues so every connected SSE client receives it.
        """
        if self._closed:
            logger.warning(
                "StreamBridge[%s] put() called on closed bridge — dropping event %s",
                self._session_id, event.get("event"),
            )
            return

        self._seq += 1
        seq = self._seq

        # Copy so we never mutate the producer's dict and so the same
        # identity is not shared across subscribers.  The SSE ``id`` is the
        # client's resume cursor (``<run_id>:<seq>``).
        wire = dict(event)
        wire["id"] = f"{self._run_id or self._session_id}:{seq}"

        self._buffer.append((seq, wire))
        self._event_count += 1

        # Broadcast to all subscribers.  A slow subscriber is flagged
        # instead of blocking the producer (which would stall the agent).
        for sub in list(self._subscribers):
            if sub.overflowed:
                continue
            try:
                sub.queue.put_nowait(wire)
            except asyncio.QueueFull:
                sub.overflowed = True
                logger.warning(
                    "StreamBridge[%s] subscriber overflowed at seq %d — "
                    "client will be asked to resync",
                    self._session_id, seq,
                )

    async def close(self) -> None:
        """Close the bridge, signalling all subscribers to stop.

        Idempotent — safe to call multiple times.
        """
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True

        # Push ``None`` sentinel to ALL subscriber queues.
        for sub in list(self._subscribers):
            try:
                sub.queue.put_nowait(None)
            except asyncio.QueueFull:
                # Reader is gone or wedged; it will observe ``_closed``.
                pass

        logger.debug(
            "StreamBridge[%s] closed — %d events produced, %d buffered",
            self._session_id, self._event_count, len(self._buffer),
        )

    # ------------------------------------------------------------------
    # Consumer API (called by SSE readers)
    # ------------------------------------------------------------------

    def _resync_event(self, reason: str, from_seq: int) -> dict[str, Any]:
        return {
            "event": "stream_resync",
            "data": json.dumps({
                "reason": reason,
                "from_seq": from_seq,
                "oldest_seq": self.oldest_seq,
                "last_seq": self._seq,
                "run_id": self._run_id,
            }),
            "id": f"{self._run_id or self._session_id}:{self._seq}",
        }

    async def subscribe(
        self,
        from_seq: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Return an async iterator that replays buffered events then yields
        live events from the queue.

        ``from_seq`` is the last sequence the consumer already applied
        (exclusive); events with ``seq <= from_seq`` are skipped.  When that
        cursor predates the oldest buffered event a ``stream_resync`` event
        is emitted first, because replay would otherwise leave a hole.

        Each subscriber gets its own private queue, so all subscribers
        receive all events (broadcast).  When the bridge is closed the
        iterator stops (does NOT raise ``StopAsyncIteration``).
        """
        # Register the queue BEFORE snapshotting the buffer: otherwise
        # events produced during the replay would be lost, and iterating a
        # live deque while the producer appends raises RuntimeError.
        sub = _Subscriber(self._subscriber_queue_size)
        self._subscribers.append(sub)
        self._ever_attached = True

        last_seq = from_seq or 0
        try:
            snapshot = list(self._buffer)

            # Gap repair: the consumer's cursor is older than anything we
            # can replay, so tell it to reload the persisted message.
            if snapshot and last_seq and snapshot[0][0] > last_seq + 1:
                yield self._resync_event("buffer_gap", last_seq)
                last_seq = snapshot[0][0] - 1

            for seq, event in snapshot:
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield event

            while not self._closed:
                if sub.overflowed:
                    sub.overflowed = False
                    # Drop whatever is stale; the client reloads from REST.
                    while not sub.queue.empty():
                        sub.queue.get_nowait()
                    yield self._resync_event("slow_consumer", last_seq)
                    continue

                try:
                    # Use wait_for so we can emit heartbeats even if the queue
                    # is idle for a long time (e.g. during a slow tool call).
                    event = await asyncio.wait_for(
                        sub.queue.get(),
                        timeout=self._heartbeat_interval,
                    )
                except asyncio.TimeoutError:
                    # No event arrived — emit heartbeat so the SSE connection
                    # stays alive (and the client can detect a dead link).
                    yield {"event": "heartbeat", "data": "{}"}
                    continue

                if event is None:
                    # Sentinel — bridge is closed.
                    return

                seq = int(str(event.get("id", "")).rsplit(":", 1)[-1] or 0)
                if seq <= last_seq:
                    # Already replayed from the buffer snapshot.
                    continue
                last_seq = seq
                yield event
        finally:
            # Clean up this subscriber's queue on exit (aclose/disconnect).
            if sub in self._subscribers:
                self._subscribers.remove(sub)

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
    def last_seq(self) -> int:
        """Sequence of the most recently produced event (0 if none)."""
        return self._seq

    @property
    def oldest_seq(self) -> int:
        """Sequence of the oldest event still available for replay."""
        if not self._buffer:
            return 0
        return self._buffer[0][0]

    @property
    def ever_attached(self) -> bool:
        """Whether any SSE subscriber has ever attached to this run."""
        return self._ever_attached

    @property
    def subscriber_count(self) -> int:
        """Number of currently attached SSE subscribers (Issue #608)."""
        return len(self._subscribers)

    @property
    def is_autopilot(self) -> bool:
        return self._autopilot

    @property
    def run_id(self) -> str | None:
        """The run lease this bridge belongs to (Issue #604)."""
        return self._run_id

    def __repr__(self) -> str:
        return (
            f"StreamBridge(session={self._session_id}, "
            f"run={self._run_id}, "
            f"events={self._event_count}, "
            f"seq={self._seq}, "
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


def remove_bridge(session_id: str, run_id: str | None = None) -> None:
    """Remove the registered StreamBridge for *session_id*.

    When *run_id* is given, only remove the bridge if it belongs to that
    run — an older run's cleanup must not evict a newer run's bridge.

    Does NOT close the bridge — the agent task's ``finally`` block
    should call ``bridge.close()`` before this.
    """
    existing = _active_bridges.get(session_id)
    if existing is None:
        return
    if run_id is not None and existing.run_id != run_id:
        logger.debug(
            "Not removing StreamBridge for session %s — it belongs to run %s, not %s",
            session_id, existing.run_id, run_id,
        )
        return
    _active_bridges.pop(session_id, None)
    logger.debug("Removed StreamBridge for session %s", session_id)
