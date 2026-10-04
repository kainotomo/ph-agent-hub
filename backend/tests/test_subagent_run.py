# =============================================================================
# PH Agent Hub — Subagent Child Run Tests (Issue #574)
# =============================================================================

import json
from types import SimpleNamespace

import pytest

from src.agents.subagent_bus import SubagentBus
from src.tools.subagent import _run_child

pytestmark = [pytest.mark.unit]


class _FakeStream:
    """Minimal stand-in for MAF's streaming response object."""

    def __init__(self, updates, final):
        self._updates = list(updates)
        self._final = final
        self._index = 0
        self._final_result = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._updates):
            raise StopAsyncIteration
        update = self._updates[self._index]
        self._index += 1
        return update

    async def aclose(self):
        return None

    async def get_final_response(self):
        return self._final


class _FakeAgent:
    """Captures constructor kwargs and returns a canned stream."""

    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeAgent.instances.append(self)

    def run(self, task, stream=False):
        return _FakeAgent.next_stream

    next_stream = None


class _FakeSessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


def _update(*contents):
    return SimpleNamespace(contents=list(contents))


def _text(value):
    return SimpleNamespace(type="text", text=value)


def _reasoning(value):
    return SimpleNamespace(type="text_reasoning", text=value)


def _call(call_id, name, arguments):
    return SimpleNamespace(
        type="function_call", call_id=call_id, name=name, arguments=arguments
    )


def _result(call_id, name, output):
    return SimpleNamespace(
        type="function_result", call_id=call_id, name=name, output=output
    )


@pytest.fixture
def child_env(monkeypatch):
    """Patch every external dependency of ``_run_child``."""
    import agent_framework

    import src.agents.runner as runner_mod
    import src.core.redis as redis_mod
    import src.db.base as db_base
    import src.models.base as models_base
    import src.services.model_role_service as role_mod

    model = SimpleNamespace(
        name="Cheap Model",
        provider="openai",
        max_tokens=0,
        context_length=8000,
    )

    async def _resolve(db, tenant_id, role):
        return model

    async def _never_cancelled(session_id):
        return False

    monkeypatch.setattr(role_mod, "resolve_role_model", _resolve)
    monkeypatch.setattr(redis_mod, "check_stream_cancel", _never_cancelled)
    monkeypatch.setattr(
        db_base, "AsyncSessionLocal", lambda *a, **k: _FakeSessionCtx()
    )
    monkeypatch.setattr(models_base, "get_chat_client", lambda *a, **k: object())
    monkeypatch.setattr(
        runner_mod, "_build_compaction_strategy", lambda *a, **k: (None, None)
    )
    monkeypatch.setattr(
        runner_mod, "_build_session_context_block", lambda *a, **k: None
    )
    _FakeAgent.instances = []
    monkeypatch.setattr(agent_framework, "Agent", _FakeAgent)
    return model


def _child_tool():
    return SimpleNamespace(
        id="row-1",
        name="Researcher",
        description="Research things.",
        config={
            "instructions": "You research things.",
            "model_role": "@general",
        },
        approval_required=False,
    )


async def _run(bus, stream, tool=None, **overrides):
    _FakeAgent.next_stream = stream
    tool = tool or _child_tool()
    kwargs = dict(
        tool=tool,
        delegate_name="delegate_to_researcher",
        config=dict(tool.config or {}),
        task="Find the answer.",
        child_pool=[SimpleNamespace(name="web_search", approval_mode="never_require")],
        omitted=["send_email"],
        session_id="sess-1",
        tenant_id="tenant-1",
        session_data={"id": "sess-1"},
        parent_call_id="call-parent",
        bus=bus,
        parent_temperature=0.5,
    )
    kwargs.update(overrides)
    return await _run_child(**kwargs)


def _events(bus):
    return bus.drain_nowait()


async def test_happy_path_emits_and_returns(child_env):
    bus = SubagentBus("sess-1")
    final = SimpleNamespace(
        text="Hello world",
        usage_details={"input_token_count": 12, "output_token_count": 3},
    )
    stream = _FakeStream(
        [
            _update(_reasoning("Let me think")),
            _update(_text("Hello ")),
            _update(_call("c1", "web_search", '{"q":')),
            _update(_call("c1", None, '"x"}')),
            _update(_result("c1", "web_search", "result text")),
            _update(_text("world")),
        ],
        final,
    )

    text, usage = await _run(bus, stream)

    assert text == "Hello world"
    assert usage == {"tokens_in": 12, "tokens_out": 3}

    names = [e["event"] for e in _events(bus)]
    assert names == [
        "subagent_start",
        "subagent_reasoning_token",
        "subagent_token",
        "subagent_tool_start",
        "subagent_tool_result",
        "subagent_token",
        "subagent_complete",
    ]


async def test_happy_path_event_payloads(child_env):
    bus = SubagentBus("sess-1")
    final = SimpleNamespace(text="", usage_details={"input_token_count": 5, "output_token_count": 1})
    stream = _FakeStream(
        [
            _update(_call("c1", "web_search", '{"q":"x"}')),
            _update(_result("c1", "web_search", "result text")),
        ],
        final,
    )

    await _run(bus, stream)
    events = {e["event"]: json.loads(e["data"]) for e in _events(bus)}

    start = events["subagent_start"]
    assert start["parent_call_id"] == "call-parent"
    assert start["name"] == "Researcher"
    assert start["model_role"] == "@general"
    assert start["model_name"] == "Cheap Model"
    assert start["tools"] == ["web_search"]
    assert start["omitted"] == ["send_email"]
    assert start["depth"] == 0

    tool_start = events["subagent_tool_start"]
    assert tool_start["tool_name"] == "web_search"
    assert tool_start["arguments"] == {"q": "x"}
    assert tool_start["tool_call_id"] == "c1"

    tool_result = events["subagent_tool_result"]
    assert tool_result["success"] is True
    assert tool_result["output"] == "result text"

    complete = events["subagent_complete"]
    assert complete["success"] is True
    assert complete["tokens_in"] == 5
    assert complete["tokens_out"] == 1
    assert complete["duration_ms"] >= 0


async def test_agent_built_with_child_pool_and_instructions(child_env):
    bus = SubagentBus("sess-1")
    stream = _FakeStream([], SimpleNamespace(text="done", usage_details=None))

    await _run(bus, stream)

    kwargs = _FakeAgent.instances[-1].kwargs
    assert [t.name for t in kwargs["tools"]] == ["web_search"]
    assert "You research things." in kwargs["instructions"]
    assert "## Task" in kwargs["instructions"]
    assert "Find the answer." in kwargs["instructions"]
    assert kwargs["default_options"]["temperature"] == 0.5


async def test_unbound_model_role_returns_error(child_env, monkeypatch):
    import src.services.model_role_service as role_mod

    async def _unbound(db, tenant_id, role):
        return None

    monkeypatch.setattr(role_mod, "resolve_role_model", _unbound)

    bus = SubagentBus("sess-1")
    stream = _FakeStream([], SimpleNamespace(text="", usage_details=None))

    text, usage = await _run(bus, stream)

    payload = json.loads(text)
    assert "error" in payload
    assert "@general" in payload["error"]
    assert usage == {"tokens_in": 0, "tokens_out": 0}

    events = _events(bus)
    assert [e["event"] for e in events] == ["subagent_error"]
    assert "@general" in json.loads(events[0]["data"])["message"]


async def test_zero_timeout_reports_error(child_env):
    bus = SubagentBus("sess-1")
    tool = _child_tool()
    tool.config = dict(tool.config, timeout_s=0)
    stream = _FakeStream([], SimpleNamespace(text="", usage_details=None))

    text, _ = await _run(bus, stream, tool=tool)

    assert "error" in json.loads(text)
    events = _events(bus)
    assert [e["event"] for e in events] == ["subagent_start", "subagent_error"]
    assert "time limit" in json.loads(events[1]["data"])["message"]


async def test_tool_error_output_flagged(child_env):
    bus = SubagentBus("sess-1")
    stream = _FakeStream(
        [_update(_result("c1", "web_search", {"error": "boom"}))],
        SimpleNamespace(text="", usage_details=None),
    )

    await _run(bus, stream)
    result_event = [
        e for e in _events(bus) if e["event"] == "subagent_tool_result"
    ][0]
    assert json.loads(result_event["data"])["success"] is False


async def test_empty_child_text_has_fallback(child_env):
    bus = SubagentBus("sess-1")
    stream = _FakeStream([], SimpleNamespace(text="", usage_details=None))

    text, _ = await _run(bus, stream)

    assert text == "(sub-agent returned no output)"


class _TelemetryFailingStream:
    """Yields one chunk, then mimics MAF's cross-context ContextVar reset."""

    def __init__(self):
        self._index = 0
        self._final_result = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index == 0:
            self._index += 1
            return _update(_text("partial answer"))
        raise ValueError(
            "<Token var=<ContextVar name="
            "'inner_response_telemetry_captured_fields' default=None at 0x1>"
            " at 0x2> was created in a different Context"
        )

    async def aclose(self):
        return None

    async def get_final_response(self):
        return SimpleNamespace(text="partial answer", usage_details=None)


class _BoomStream:
    """Raises a genuine provider error."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise RuntimeError("provider exploded")

    async def aclose(self):
        return None

    async def get_final_response(self):
        return SimpleNamespace(text="", usage_details=None)


async def test_tolerates_telemetry_context_error(child_env):
    """A telemetry cleanup artifact must not fail the delegation."""
    bus = SubagentBus("sess-1")

    text, usage = await _run(bus, _TelemetryFailingStream())

    assert text == "partial answer"
    assert usage == {"tokens_in": 0, "tokens_out": 0}
    events = [e["event"] for e in _events(bus)]
    assert "subagent_complete" in events
    assert "subagent_error" not in events


async def test_real_stream_errors_still_propagate(child_env):
    """Genuine errors must not be swallowed as telemetry artifacts.

    ``_run_child`` re-raises them; the delegate wrapper converts the failure
    into a structured error dict and emits ``subagent_error`` (covered by
    tests/test_subagent_wiring.py).
    """
    bus = SubagentBus("sess-1")

    with pytest.raises(RuntimeError, match="provider exploded"):
        await _run(bus, _BoomStream())


async def test_final_response_text_takes_precedence(child_env):
    """Partial deltas must not win over the authoritative final response."""
    bus = SubagentBus("sess-1")
    final = SimpleNamespace(text="complete answer", usage_details=None)
    stream = _FakeStream([_update(_text("partial"))], final)

    text, _ = await _run(bus, stream)

    assert text == "complete answer"
