import json

import pytest

from src.agents.subagent_events import (
    MAX_PROMPT_CHARS,
    MAX_STEP_TEXT,
    MAX_SUBAGENT_STEPS,
    accumulate_subagent_event,
    get_payload_for_call,
    new_state,
)

pytestmark = [pytest.mark.unit]


def _ev(event_name: str, **payload):
    return {"event": event_name, "data": json.dumps(payload)}


def _start(cid="c1", **overrides):
    base = {
        "parent_call_id": cid,
        "name": "Researcher",
        "tool_name": "delegate_to_researcher",
        "model_role": "@general",
        "model_name": "m1",
        "tools": ["web_search"],
        "omitted": ["send_email"],
        "depth": 0,
        "prompt": "Do research.",
    }
    base.update(overrides)
    return _ev("subagent_start", **base)


def test_non_subagent_event_returns_false():
    state = new_state()
    assert accumulate_subagent_event({"event": "token", "data": "{}"}, state) is False
    state = new_state()
    assert accumulate_subagent_event({"data": "{}"}, state) is False


def test_start_creates_record():
    state = new_state()
    event = _start(
        parent_call_id="c1",
        name="Researcher",
        tool_name="delegate_to_researcher",
        model_role="@general",
        model_name="m1",
        tools=["web_search"],
        omitted=["send_email"],
        depth=0,
        prompt="Do research.",
    )
    assert accumulate_subagent_event(event, state) is True
    payload = get_payload_for_call(state, "c1")
    assert payload["status"] == "running"
    assert payload["name"] == "Researcher"
    assert payload["tool_name"] == "delegate_to_researcher"
    assert payload["model_role"] == "@general"
    assert payload["model_name"] == "m1"
    assert payload["tools"] == ["web_search"]
    assert payload["omitted"] == ["send_email"]
    assert payload["depth"] == 0
    assert payload["prompt"] == "Do research."


def test_reasoning_flushes_before_tool_call():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    assert accumulate_subagent_event(
        _ev("subagent_reasoning_token", parent_call_id="c1", delta="Think"), state
    ) is True
    assert accumulate_subagent_event(
        _ev("subagent_reasoning_token", parent_call_id="c1", delta="ing"), state
    ) is True
    assert accumulate_subagent_event(
        _ev("subagent_tool_start", parent_call_id="c1", tool_call_id="call_1"), state
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert len(payload["steps"]) == 2
    assert payload["steps"][0] == {"type": "reasoning", "text": "Thinking"}
    assert payload["steps"][1]["type"] == "function_call"


def test_tool_result_is_error_flag():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    assert accumulate_subagent_event(
        _ev("subagent_tool_result", parent_call_id="c1", tool_call_id="call_1", output="ok", success=True),
        state,
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert payload["steps"][-1]["type"] == "function_result"
    assert payload["steps"][-1]["is_error"] is False
    assert accumulate_subagent_event(
        _ev("subagent_tool_result", parent_call_id="c1", tool_call_id="call_2", output="bad", success=False),
        state,
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert payload["steps"][-1]["is_error"] is True


def test_token_deltas_are_not_persisted():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    for delta in ("first", "second", "third"):
        assert accumulate_subagent_event(
            _ev("subagent_token", parent_call_id="c1", delta=delta), state
        ) is True
    assert accumulate_subagent_event(_ev("subagent_complete", parent_call_id="c1", success=True), state) is True
    payload = get_payload_for_call(state, "c1")
    assert all(step["type"] != "text" for step in payload["steps"])


def test_complete_sets_status_and_usage():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    assert accumulate_subagent_event(
        _ev("subagent_complete", parent_call_id="c1", success=True, duration_ms=1234, tokens_in=100, tokens_out=50),
        state,
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert payload["status"] == "complete"
    assert payload["duration_ms"] == 1234
    assert payload["tokens_in"] == 100
    assert payload["tokens_out"] == 50


def test_complete_failure_sets_error_status():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    assert accumulate_subagent_event(
        _ev("subagent_complete", parent_call_id="c1", success=False), state
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert payload["status"] == "error"


def test_error_event_sets_message():
    state = new_state()
    assert accumulate_subagent_event(
        _ev("subagent_error", parent_call_id="c1", message="boom"), state
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert payload["status"] == "error"
    assert "boom" in payload["error"]


def test_complete_flushes_trailing_reasoning():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    assert accumulate_subagent_event(
        _ev("subagent_reasoning_token", parent_call_id="c1", delta="thinking"), state
    ) is True
    assert accumulate_subagent_event(
        _ev("subagent_complete", parent_call_id="c1", success=True), state
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert payload["steps"][-1] == {"type": "reasoning", "text": "thinking"}


def test_malformed_json_returns_true():
    state = new_state()
    assert accumulate_subagent_event({"event": "subagent_complete", "data": "not json"}, state) is True
    assert state["subagents"] == {}


def test_missing_parent_call_id_is_ignored():
    state = new_state()
    event = _ev("subagent_start", name="Researcher")
    assert accumulate_subagent_event(event, state) is True
    assert state["subagents"] == {}


def test_output_is_truncated():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    assert accumulate_subagent_event(
        _ev(
            "subagent_tool_result",
            parent_call_id="c1",
            tool_call_id="call_1",
            output="x" * (MAX_STEP_TEXT + 500),
            success=True,
        ),
        state,
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert len(payload["steps"][-1]["output"]) <= MAX_STEP_TEXT


def test_step_cap_sets_truncated_flag():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    for i in range(MAX_SUBAGENT_STEPS + 5):
        assert accumulate_subagent_event(
            _ev("subagent_tool_start", parent_call_id="c1", tool_call_id=f"call_{i}"), state
        ) is True
    payload = get_payload_for_call(state, "c1")
    assert len(payload["steps"]) == MAX_SUBAGENT_STEPS
    assert payload["steps_truncated"] is True


def test_prompt_is_truncated():
    state = new_state()
    assert accumulate_subagent_event(
        _start(prompt="p" * (MAX_PROMPT_CHARS + 100)), state
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert len(payload["prompt"]) <= MAX_PROMPT_CHARS


def test_payload_excludes_internal_keys():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    assert accumulate_subagent_event(
        _ev("subagent_reasoning_token", parent_call_id="c1", delta="think"), state
    ) is True
    payload = get_payload_for_call(state, "c1")
    assert "pending_reasoning" not in payload
    assert "pending_calls" not in payload
    assert "parent_call_id" not in payload


def test_unknown_call_id_returns_none():
    state = new_state()
    assert get_payload_for_call(state, "nope") is None
    assert get_payload_for_call(state, "") is None


def test_payload_is_a_copy():
    state = new_state()
    assert accumulate_subagent_event(_start(), state) is True
    payload = get_payload_for_call(state, "c1")
    payload["steps"].append({})
    assert len(get_payload_for_call(state, "c1")["steps"]) == 0
