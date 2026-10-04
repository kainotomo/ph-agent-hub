import pytest

from src.agents.subagent_bus import (
    SubagentBus,
    get_bus,
    register_bus,
    remove_bus,
)

pytestmark = [pytest.mark.unit]


def test_register_get_remove_round_trip():
    bus = SubagentBus("sess-1")
    register_bus("sess-1", bus)
    assert get_bus("sess-1") is bus
    remove_bus("sess-1")
    assert get_bus("sess-1") is None


def test_get_bus_unknown_returns_none():
    assert get_bus("nope") is None


async def test_put_and_get():
    bus = SubagentBus("sess-1")
    await bus.put({"event": "subagent_start"})
    assert await bus.get() == {"event": "subagent_start"}


def test_drain_nowait_returns_all_and_empties():
    bus = SubagentBus("sess-1")
    bus.put_nowait({"event": "a"})
    bus.put_nowait({"event": "b"})
    bus.put_nowait({"event": "c"})
    assert bus.drain_nowait() == [{"event": "a"}, {"event": "b"}, {"event": "c"}]
    assert bus.drain_nowait() == []
    assert bus.qsize() == 0


def test_add_usage_accumulates():
    bus = SubagentBus("sess-1")
    bus.add_usage(10, 5)
    bus.add_usage(1, 2)
    assert bus.usage_in == 11
    assert bus.usage_out == 7
    assert bus.total_tokens() == 18


def test_add_usage_none_is_zero():
    bus = SubagentBus("sess-1")
    bus.add_usage(None, None)
    assert bus.total_tokens() == 0


def test_repr_contains_session_id():
    bus = SubagentBus("sess-1")
    assert "sess-1" in repr(bus)
