# =============================================================================
# PH Agent Hub — StreamBridge unit tests
# =============================================================================
# Covers the ordering guarantees the SSE reconnect path depends on:
#   * every event is stamped with a monotonic seq / SSE id,
#   * a subscriber can resume after a cursor without re-applying events,
#   * a cursor that fell out of the replay buffer yields stream_resync,
#   * a silent producer still produces heartbeats (dead-link detection).
# =============================================================================

import asyncio
import json

import pytest

from src.agents.stream_bridge import StreamBridge


def seq_of(event: dict) -> int:
    """Read the sequence back from the SSE ``id`` the bridge stamps."""
    return int(str(event["id"]).rsplit(":", 1)[-1])


async def _collect(iterator, count: int, timeout: float = 2.0):
    """Pull *count* events from *iterator* or fail the test on timeout."""
    events = []
    for _ in range(count):
        events.append(await asyncio.wait_for(iterator.__anext__(), timeout))
    return events


async def test_put_stamps_monotonic_seq_and_id():
    bridge = StreamBridge("s1", run_id="run-1")

    await bridge.put({"event": "token", "data": json.dumps({"delta": "a"})})
    await bridge.put({"event": "token", "data": json.dumps({"delta": "b"})})

    assert bridge.last_seq == 2
    assert bridge.oldest_seq == 1

    events = await _collect(bridge.subscribe(), 2)
    assert [seq_of(e) for e in events] == [1, 2]
    assert [e["id"] for e in events] == ["run-1:1", "run-1:2"]
    # Producer payload is preserved untouched.
    assert json.loads(events[0]["data"]) == {"delta": "a"}


async def test_put_does_not_mutate_producer_event():
    bridge = StreamBridge("s1", run_id="run-1")
    original = {"event": "token", "data": "{}"}

    await bridge.put(original)

    assert original == {"event": "token", "data": "{}"}


async def test_subscribe_resumes_after_cursor():
    bridge = StreamBridge("s1", run_id="run-1")
    for index in range(5):
        await bridge.put({"event": "token", "data": json.dumps({"delta": index})})

    events = await _collect(bridge.subscribe(from_seq=3), 2)

    assert [seq_of(e) for e in events] == [4, 5]


async def test_subscribe_cursor_at_head_receives_only_new_events():
    bridge = StreamBridge("s1", run_id="run-1")
    await bridge.put({"event": "token", "data": "{}"})

    iterator = bridge.subscribe(from_seq=1)
    # Start the generator so its queue is registered before the live event.
    pending = asyncio.create_task(iterator.__anext__())
    await asyncio.sleep(0.01)
    await bridge.put({"event": "token", "data": '{"delta":"live"}'})

    event = await asyncio.wait_for(pending, 2.0)
    assert seq_of(event) == 2
    await iterator.aclose()


async def test_gap_beyond_buffer_emits_resync():
    # A three-event buffer drops the oldest events, so a client that only
    # saw seq 1 cannot be replayed from seq 2 onwards.
    bridge = StreamBridge("s1", max_buffer=3, run_id="run-1")
    for index in range(6):
        await bridge.put({"event": "token", "data": json.dumps({"delta": index})})

    assert bridge.oldest_seq == 4

    iterator = bridge.subscribe(from_seq=1)
    first = await asyncio.wait_for(iterator.__anext__(), 2.0)

    assert first["event"] == "stream_resync"
    payload = json.loads(first["data"])
    assert payload["reason"] == "buffer_gap"
    assert payload["from_seq"] == 1
    assert payload["oldest_seq"] == 4

    # Replay continues from the oldest available event, without a hole.
    rest = await _collect(iterator, 3)
    assert [seq_of(e) for e in rest] == [4, 5, 6]
    await iterator.aclose()


async def test_live_subscriber_receives_events_produced_during_replay():
    bridge = StreamBridge("s1", run_id="run-1")
    await bridge.put({"event": "token", "data": "{}"})

    iterator = bridge.subscribe()
    # Consume the replay, then produce while the subscriber is attached.
    replayed = await asyncio.wait_for(iterator.__anext__(), 2.0)
    assert seq_of(replayed) == 1

    await bridge.put({"event": "token", "data": "{}"})
    live = await asyncio.wait_for(iterator.__anext__(), 2.0)

    assert seq_of(live) == 2
    await iterator.aclose()


async def test_idle_bridge_emits_heartbeat():
    bridge = StreamBridge("s1", heartbeat_interval=0, run_id="run-1")
    iterator = bridge.subscribe()

    event = await asyncio.wait_for(iterator.__anext__(), 2.0)

    assert event["event"] == "heartbeat"
    assert "seq" not in event
    await iterator.aclose()


async def test_close_stops_subscribers():
    bridge = StreamBridge("s1", run_id="run-1")
    iterator = bridge.subscribe()

    await bridge.close()

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(iterator.__anext__(), 2.0)


async def test_put_after_close_is_ignored():
    bridge = StreamBridge("s1", run_id="run-1")
    await bridge.close()

    await bridge.put({"event": "token", "data": "{}"})

    assert bridge.last_seq == 0
    assert bridge.event_count == 0


async def test_slow_subscriber_is_flagged_for_resync():
    # A queue of one cannot absorb three events while the reader is parked
    # on the first one, so the producer flags the subscriber instead of
    # blocking the agent.  The reader then asks the client to resync.
    bridge = StreamBridge("s1", subscriber_queue_size=1, run_id="run-1")
    iterator = bridge.subscribe()

    pending = asyncio.create_task(iterator.__anext__())
    await asyncio.sleep(0.01)  # let the generator register and block

    await bridge.put({"event": "token", "data": '{"delta":"a"}'})
    await bridge.put({"event": "token", "data": '{"delta":"b"}'})
    await bridge.put({"event": "token", "data": '{"delta":"c"}'})

    first = await asyncio.wait_for(pending, 2.0)
    assert seq_of(first) == 1

    resync = await asyncio.wait_for(iterator.__anext__(), 2.0)
    assert resync["event"] == "stream_resync"
    assert json.loads(resync["data"])["reason"] == "slow_consumer"
    await iterator.aclose()
