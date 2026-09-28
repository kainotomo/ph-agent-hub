"""Unit tests for apply_tool_approval_modes in runner.py."""

from types import SimpleNamespace

import pytest

from src.agents.runner import apply_tool_approval_modes


def test_apply_tool_approval_modes_sets_matching_tool():
    """Matching tools get approval_mode='always_require'; non-matching stay untouched."""
    a = SimpleNamespace(name="a", approval_mode="never_require")
    b = SimpleNamespace(name="b", approval_mode="never_require")
    result = apply_tool_approval_modes([a, b], {"a"})
    assert a.approval_mode == "always_require"
    assert b.approval_mode == "never_require"
    assert result is not None


def test_apply_tool_approval_modes_ignores_missing_attribute():
    """An object without approval_mode must not raise."""
    a = SimpleNamespace(name="a", approval_mode="never_require")
    no_attr = SimpleNamespace(name="no_attr")  # no approval_mode
    result = apply_tool_approval_modes([a, no_attr], {"a", "no_attr"})
    assert a.approval_mode == "always_require"
    assert result is not None


def test_apply_tool_approval_modes_empty_set_is_noop():
    """With an empty approval_names set, nothing changes."""
    a = SimpleNamespace(name="a", approval_mode="never_require")
    result = apply_tool_approval_modes([a], set())
    assert a.approval_mode == "never_require"
    assert result is not None
