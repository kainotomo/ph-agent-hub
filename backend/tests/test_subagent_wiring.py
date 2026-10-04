# =============================================================================
# PH Agent Hub — Subagent Wiring Tests (Issue #574)
# =============================================================================

import asyncio
from types import SimpleNamespace

import pytest

import src.agents.runner as runner_mod
from src.agents.runner import (
    _append_subagent_delegates,
    _wrap_tools_with_output_cap,
)
from src.tools import subagent as subagent_mod

pytestmark = [pytest.mark.unit]


def _row(name="Researcher", row_id="row-1", config=None):
    return SimpleNamespace(
        id=row_id,
        name=name,
        description="Research things.",
        config=config
        or {"instructions": "You research.", "model_role": "@general"},
        approval_required=False,
    )


def test_append_delegates_appends_marked_tool():
    pool = [SimpleNamespace(name="web_search", approval_mode="never_require")]
    _append_subagent_delegates(
        pool,
        [_row()],
        tenant_id="t1",
        session_id="s1",
        session_data={"id": "s1"},
    )

    assert [c.name for c in pool] == ["web_search", "delegate_to_researcher"]
    delegate = pool[-1]
    assert delegate._delegate_tool is True
    assert delegate._subagent_row_id == "row-1"
    assert delegate._subagent_timeout_s > 0


def test_append_delegates_no_rows_is_noop():
    pool = [SimpleNamespace(name="web_search")]
    _append_subagent_delegates(
        pool, [], tenant_id="t1", session_id="s1", session_data={"id": "s1"},
    )
    assert len(pool) == 1


def test_append_delegates_uses_unique_names():
    pool: list = []
    _append_subagent_delegates(
        pool,
        [_row("Researcher", "row-1"), _row("Researcher", "row-2")],
        tenant_id="t1",
        session_id="s1",
        session_data={"id": "s1"},
    )
    names = [c.name for c in pool]
    assert names == ["delegate_to_researcher", "delegate_to_researcher_row_2"]
    assert len(set(names)) == 2


def test_append_delegates_row_timeout_is_honoured():
    pool: list = []
    _append_subagent_delegates(
        pool,
        [_row(config={"instructions": "i", "model_role": "@general", "timeout_s": 42})],
        tenant_id="t1",
        session_id="s1",
        session_data={"id": "s1"},
    )
    assert pool[0]._subagent_timeout_s == 42


async def test_delegate_child_pool_excludes_siblings_and_approval(monkeypatch):
    captured: dict = {}

    async def _fake_run_child(**kwargs):
        captured.update(kwargs)
        return "child answer", {"tokens_in": 1, "tokens_out": 2}

    monkeypatch.setattr(subagent_mod, "_run_child", _fake_run_child)

    approval_gated = SimpleNamespace(
        name="send_email", approval_mode="always_require"
    )
    sibling_delegate = SimpleNamespace(
        name="delegate_to_other",
        _delegate_tool=True,
        approval_mode="never_require",
    )
    pool = [
        SimpleNamespace(name="web_search", approval_mode="never_require"),
        approval_gated,
        sibling_delegate,
    ]

    _append_subagent_delegates(
        pool,
        [_row()],
        tenant_id="t1",
        session_id="s1",
        session_data={"id": "s1"},
    )
    delegate = pool[-1]

    ctx = SimpleNamespace(metadata={"call_id": "call-9"})
    result = await delegate.func(ctx, task="Do the thing")

    assert result == "child answer"
    assert [t.name for t in captured["child_pool"]] == ["web_search"]
    assert captured["omitted"] == ["send_email"]
    assert captured["parent_call_id"] == "call-9"
    assert captured["delegate_name"] == "delegate_to_researcher"
    assert captured["tenant_id"] == "t1"
    assert captured["session_id"] == "s1"


async def test_delegate_tool_deny_is_subtractive(monkeypatch):
    captured: dict = {}

    async def _fake_run_child(**kwargs):
        captured.update(kwargs)
        return "ok", {"tokens_in": 0, "tokens_out": 0}

    monkeypatch.setattr(subagent_mod, "_run_child", _fake_run_child)

    pool = [
        SimpleNamespace(name="web_search", approval_mode="never_require"),
        SimpleNamespace(name="fetch_url", approval_mode="never_require"),
    ]
    _append_subagent_delegates(
        pool,
        [_row(config={"instructions": "i", "model_role": "@general", "tool_deny": ["fetch_url", "absent_tool"]})],
        tenant_id="t1",
        session_id="s1",
        session_data={"id": "s1"},
    )

    await pool[-1].func(SimpleNamespace(metadata={"call_id": "c"}), task="t")

    assert [t.name for t in captured["child_pool"]] == ["web_search"]
    assert captured["omitted"] == ["fetch_url"]


async def test_wrap_gives_delegates_a_longer_timeout(monkeypatch):
    monkeypatch.setattr(runner_mod, "TOOL_EXECUTION_TIMEOUT", 0.01)

    async def _slow():
        await asyncio.sleep(0.05)
        return "done"

    plain = SimpleNamespace(name="plain", func=_slow)
    delegate = SimpleNamespace(
        name="delegate_to_x",
        func=_slow,
        _delegate_tool=True,
        _subagent_timeout_s=0,
    )

    wrapped = _wrap_tools_with_output_cap([plain, delegate], context_length=8000)
    plain_result = await wrapped[0].func()
    delegate_result = await wrapped[1].func()

    assert isinstance(plain_result, dict)
    assert "timed out" in plain_result["error"]
    assert delegate_result == "done"
