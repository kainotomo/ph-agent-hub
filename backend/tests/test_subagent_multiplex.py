# =============================================================================
# PH Agent Hub — Subagent Stream Multiplexer Tests (Issue #574)
# =============================================================================

import json

import pytest

from src.agents.runner import (
    _attach_subagent_payloads,
    _multiplex_with_subagents,
)
from src.agents.subagent_bus import SubagentBus
from src.agents.subagent_events import accumulate_subagent_event, new_state

pytestmark = [pytest.mark.unit]


async def _parent(events):
    for event in events:
        yield event


async def test_multiplex_interleaves_bus_and_parent():
    bus = SubagentBus("sess-1")
    bus.put_nowait({"event": "subagent_token", "data": '{"delta": "hi"}'})

    stream = _parent([{"event": "token", "data": '{"delta": "a"}'}])
    out = [e async for e in _multiplex_with_subagents(stream, bus)]

    names = [e["event"] for e in out]
    assert "token" in names
    assert "subagent_token" in names


async def test_multiplex_yields_all_bus_events_with_empty_parent():
    bus = SubagentBus("sess-1")
    for i in range(3):
        bus.put_nowait({"event": "subagent_token", "data": json.dumps({"i": i})})

    out = [e async for e in _multiplex_with_subagents(_parent([]), bus)]

    assert len(out) == 3
    assert [json.loads(e["data"])["i"] for e in out] == [0, 1, 2]


async def test_multiplex_passes_parent_events_through_unchanged():
    bus = SubagentBus("sess-1")
    parent_events = [
        {"event": "token", "data": '{"delta": "x"}'},
        {"event": "step_complete", "data": "{}"},
        {"event": "message_complete", "data": "{}"},
    ]

    out = [e async for e in _multiplex_with_subagents(_parent(parent_events), bus)]

    assert out == parent_events


def _state_with_completed_child(call_id="c1"):
    state = new_state()
    accumulate_subagent_event(
        {
            "event": "subagent_start",
            "data": json.dumps(
                {
                    "parent_call_id": call_id,
                    "name": "Researcher",
                    "tool_name": "delegate_to_researcher",
                    "model_role": "@general",
                    "tools": ["web_search"],
                }
            ),
        },
        state,
    )
    accumulate_subagent_event(
        {
            "event": "subagent_tool_result",
            "data": json.dumps(
                {
                    "parent_call_id": call_id,
                    "tool_call_id": "child-call",
                    "tool_name": "web_search",
                    "success": True,
                    "output": "found it",
                }
            ),
        },
        state,
    )
    accumulate_subagent_event(
        {
            "event": "subagent_complete",
            "data": json.dumps(
                {
                    "parent_call_id": call_id,
                    "success": True,
                    "duration_ms": 1500,
                    "tokens_in": 11,
                    "tokens_out": 4,
                }
            ),
        },
        state,
    )
    return state


def test_attach_payloads_by_call_id():
    state = _state_with_completed_child("c1")
    segments = [
        {"type": "function_call", "name": "delegate_to_researcher", "id": "c1"},
        {"type": "function_result", "name": "delegate_to_researcher", "id": "c1"},
    ]

    _attach_subagent_payloads(segments, state)

    assert "subagent" not in segments[0]
    payload = segments[1]["subagent"]
    assert payload["name"] == "Researcher"
    assert payload["status"] == "complete"
    assert payload["tokens_in"] == 11
    assert payload["tokens_out"] == 4
    assert payload["duration_ms"] == 1500
    assert [s["type"] for s in payload["steps"]] == ["function_result"]


def test_attach_payloads_ignores_unknown_call_id():
    state = _state_with_completed_child("c1")
    segments = [{"type": "function_result", "name": "web_search", "id": "other"}]

    _attach_subagent_payloads(segments, state)

    assert "subagent" not in segments[0]


def test_attach_payloads_does_not_overwrite_existing():
    state = _state_with_completed_child("c1")
    segments = [
        {
            "type": "function_result",
            "id": "c1",
            "subagent": {"name": "already-attached"},
        }
    ]

    _attach_subagent_payloads(segments, state)

    assert segments[0]["subagent"]["name"] == "already-attached"


def test_attach_payloads_skips_non_result_segments():
    state = _state_with_completed_child("c1")
    segments = [
        {"type": "reasoning", "text": "thinking"},
        {"type": "text", "text": "answer"},
    ]

    _attach_subagent_payloads(segments, state)

    assert all("subagent" not in s for s in segments)


def test_fold_subagent_usage_adds_child_tokens():
    from src.agents.runner import _fold_subagent_usage

    bus = SubagentBus("sess-1")
    bus.add_usage(7, 3)
    token_info = {"in": 100, "out": 20}

    _fold_subagent_usage(token_info, bus)

    assert token_info["in"] == 107
    assert token_info["out"] == 23


def test_fold_subagent_usage_is_noop_without_usage():
    from src.agents.runner import _fold_subagent_usage

    token_info = {"in": 100, "out": 20}
    _fold_subagent_usage(token_info, SubagentBus("sess-1"))
    _fold_subagent_usage(token_info, None)

    assert token_info == {"in": 100, "out": 20}


def test_fold_subagent_usage_tolerates_missing_keys():
    from src.agents.runner import _fold_subagent_usage

    bus = SubagentBus("sess-1")
    bus.add_usage(5, 0)
    token_info: dict = {}

    _fold_subagent_usage(token_info, bus)

    assert token_info == {"in": 5, "out": 0}
