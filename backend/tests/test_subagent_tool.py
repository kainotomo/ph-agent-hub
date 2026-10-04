import json
from types import SimpleNamespace

import pytest

from src.tools.subagent import (
    _build_child_pool,
    _slugify,
    build_delegate_tool_name,
    build_subagent_delegate,
)

pytestmark = [pytest.mark.unit]


def _tool(name, **kw):
    return SimpleNamespace(
        id=kw.pop("id", "abc12345"),
        name=name,
        description=kw.pop("description", f"{name} tool"),
        config=kw.pop("config", None),
        approval_required=kw.pop("approval_required", False),
        **kw
    )


def _member(name, **kw):
    return SimpleNamespace(name=name, **kw)


def _delegate_for(tool, **kw):
    """Build a delegate tool, filling in the required run context."""
    kw.setdefault("tenant_id", "t1")
    kw.setdefault("session_id", "s1")
    kw.setdefault("pool", [])
    return build_subagent_delegate(tool=tool, **kw)


def test_slugify_basic():
    assert _slugify("Web Researcher") == "web_researcher"
    assert _slugify("A/B  C!") == "a_b_c"
    assert _slugify("") == "subagent"
    assert _slugify("!!!") == "subagent"


def test_slugify_truncates():
    assert len(_slugify("x" * 100)) == 40


def test_delegate_name_no_used_names():
    assert build_delegate_tool_name("Web Researcher", "abc12345", None) == "delegate_to_web_researcher"


def test_delegate_name_free_when_not_used():
    assert build_delegate_tool_name("Web Researcher", "abc12345", {"other"}) == "delegate_to_web_researcher"


def test_delegate_name_collision_suffix():
    assert build_delegate_tool_name("Web Researcher", "abc12345", {"delegate_to_web_researcher"}) == "delegate_to_web_researcher_abc123"


def test_delegate_name_double_collision():
    used_names = {"delegate_to_web_researcher", "delegate_to_web_researcher_abc123"}
    assert build_delegate_tool_name("Web Researcher", "abc12345", used_names) == "delegate_to_web_researcher_abc123_2"


def test_child_pool_excludes_delegate_denied_and_approval():
    pool = [
        _member("keep_a"),
        _member("delegate_to_x", _delegate_tool=True),
        _member("denied_one"),
        _member("needs_approval", approval_mode="always_require"),
        _member("keep_b"),
    ]
    child, omitted = _build_child_pool(pool, ["denied_one"])
    assert [t.name for t in child] == ["keep_a", "keep_b"]
    assert omitted == ["denied_one", "needs_approval"]


def test_child_pool_unknown_deny_is_noop():
    child, omitted = _build_child_pool([_member("keep_a")], ["not_present"])
    assert [t.name for t in child] == ["keep_a"]
    assert omitted == []


def test_child_pool_ignores_non_string_deny():
    child, omitted = _build_child_pool([_member("keep_a")], [123, None, "keep_a"])
    assert child == []
    assert omitted == ["keep_a"]


def test_child_pool_empty_pool():
    assert _build_child_pool([], None) == ([], [])


def test_build_delegate_returns_marked_function_tool():
    obj = _delegate_for(tool=_tool("Researcher"))
    assert obj.name == "delegate_to_researcher"
    assert obj._delegate_tool is True
    assert obj._subagent_row_id == "abc12345"
    assert obj.description == "Researcher tool"


def test_build_delegate_description_fallback():
    tool = SimpleNamespace(
        id="abc12345",
        name="Researcher",
        description=None,
        config=None,
        approval_required=False,
    )
    obj = _delegate_for(tool=tool)
    assert "Researcher" in obj.description


def test_build_delegate_schema_requires_task():
    obj = _delegate_for(tool=_tool("Researcher"))
    assert "task" in json.dumps(obj.to_json_schema_spec())


def test_build_delegate_approval_mode_from_row():
    obj = _delegate_for(tool=_tool("Researcher", approval_required=True))
    assert obj.approval_mode == "always_require"
    obj = _delegate_for(tool=_tool("Researcher"))
    assert obj.approval_mode == "never_require"


def test_build_delegate_registers_name_in_used_names():
    used_names = set()
    _delegate_for(tool=_tool("Researcher"), used_names=used_names)
    assert "delegate_to_researcher" in used_names


def test_build_delegate_no_config_is_tolerated():
    obj = _delegate_for(tool=_tool("Researcher", config=None))
    assert obj.name == "delegate_to_researcher"
