# =============================================================================
# PH Agent Hub — Agent Registry Tests
# =============================================================================
# Tests for the agent_defs scan and lookup functions.  No database, no
# startup_scan required.
# =============================================================================

import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, pathlib.Path(__file__).resolve().parent.parent / "src")

from src.agents.registry import (
    get_registered_agent,
    list_registered_agent_keys,
    scan_agent_defs,
)
from src.agents.workflows.roles import known_roles


def test_scan_agent_defs_returns_web_researcher():
    """scan_agent_defs() should return a dict containing 'web_researcher'."""
    agents = scan_agent_defs()
    assert "web_researcher" in agents


def test_web_researcher_model_role_known():
    """get_registered_agent('web_researcher').MODEL_ROLE must be '@reasoning'
    and be present in known_roles('model')."""
    agent = get_registered_agent("web_researcher")
    assert agent.MODEL_ROLE == "@reasoning"
    assert "@reasoning" in known_roles("model")


def test_get_registered_agent_nonexistent_returns_none():
    """get_registered_agent for an unknown key must return None."""
    assert get_registered_agent("does_not_exist") is None


def test_list_registered_agent_keys_contains_web_researcher():
    """list_registered_agent_keys() must include 'web_researcher'."""
    keys = list_registered_agent_keys()
    assert "web_researcher" in keys


def test_scan_agent_defs_is_idempotent():
    """Calling scan_agent_defs() twice must leave the keys unchanged."""
    scan_agent_defs()
    keys_after_first = list_registered_agent_keys()
    scan_agent_defs()
    keys_after_second = list_registered_agent_keys()
    assert keys_after_first == keys_after_second


def test_check_events_deleted():
    """Regression guard: check_events.py must not exist."""
    check_events_path = pathlib.Path(
        __file__
    ).resolve().parent.parent / "src" / "agents" / "workflows" / "check_events.py"
    assert check_events_path.exists() is False
