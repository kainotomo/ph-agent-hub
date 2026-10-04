# =============================================================================
# PH Agent Hub — SubagentBus
# =============================================================================
# Side channel for delegated sub-agents running inside a tool call.  Sub-agents
# publish progress events and child token usage to the bus; the outer
# streaming generator drains the bus and feeds the events to the SSE output.
#
# Usage:
#   bus = SubagentBus(session_id)
#   register_bus(session_id, bus)
#   await bus.put({"event": "subagent_start"})
#   bus.add_usage(tokens_in=10, tokens_out=5)
#   remove_bus(session_id)
# =============================================================================

import asyncio
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SubagentBus
# ---------------------------------------------------------------------------


class SubagentBus:
    """Per-session side channel carrying delegated sub-agent progress events
    plus the child token usage accumulator.

    The outer ``run_agent_stream`` multiplexer is the single consumer that
    drains this bus and forwards events to the streaming output.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self.usage_in: int = 0
        self.usage_out: int = 0

    # ------------------------------------------------------------------
    # Producer API (called by the delegated sub-agent)
    # ------------------------------------------------------------------

    async def put(self, event: dict) -> None:
        """Publish a progress event to the bus queue."""
        await self._queue.put(event)

    def put_nowait(self, event: dict) -> None:
        """Enqueue a progress event without awaiting."""
        self._queue.put_nowait(event)

    # ------------------------------------------------------------------
    # Consumer API (called by the outer streaming generator)
    # ------------------------------------------------------------------

    async def get(self) -> dict:
        """Drain one event from the bus queue."""
        return await self._queue.get()

    def drain_nowait(self) -> list[dict]:
        """Return and remove every event currently queued.

        Stops at the first ``asyncio.QueueEmpty``; returns ``[]`` when
        the queue is empty.
        """
        events: list[dict] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def qsize(self) -> int:
        """Return the number of events currently queued."""
        return self._queue.qsize()

    def add_usage(self, tokens_in: int = 0, tokens_out: int = 0) -> None:
        """Accumulate child token usage, treating ``None`` as 0."""
        self.usage_in += tokens_in or 0
        self.usage_out += tokens_out or 0

    def total_tokens(self) -> int:
        """Return the total accumulated child token usage."""
        return self.usage_in + self.usage_out

    def __repr__(self) -> str:
        return (
            f"SubagentBus(session={self.session_id}, "
            f"queue_size={self._queue.qsize()}, "
            f"usage_in={self.usage_in}, "
            f"usage_out={self.usage_out})"
        )


# ---------------------------------------------------------------------------
# Active bus registry
# ---------------------------------------------------------------------------

#: In-memory mapping of session_id → SubagentBus.
#: Buses live only as long as one agent run is active.
_active_buses: dict[str, SubagentBus] = {}


def register_bus(session_id: str, bus: SubagentBus) -> None:
    """Register a SubagentBus for *session_id*."""
    _active_buses[session_id] = bus
    logger.debug("Registered SubagentBus for session %s", session_id)


def get_bus(session_id: str) -> SubagentBus | None:
    """Return the registered SubagentBus for *session_id*, or ``None``."""
    return _active_buses.get(session_id)


def remove_bus(session_id: str) -> None:
    """Remove the registered SubagentBus for *session_id*.

    Does NOT close the bus — the agent run's cleanup should remove it
    before the run ends.
    """
    removed = _active_buses.pop(session_id, None)
    if removed is not None:
        logger.debug("Removed SubagentBus for session %s", session_id)
