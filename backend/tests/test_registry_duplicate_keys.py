# =============================================================================
# PH Agent Hub — Duplicate MAF Key Detection Tests
# =============================================================================
# Tests for DuplicateMAFKeyError and register_module.  No database, no
# startup_scan required.
# =============================================================================

import pathlib
import sys
import types

import pytest

sys.path.insert(0, pathlib.Path(__file__).resolve().parent.parent / "src")

from src.agents.registry import DuplicateMAFKeyError, register_module


def test_register_module_registers_new_key():
    """register_module should insert a new key and return None."""
    registry: dict = {}
    mod = types.SimpleNamespace(__name__="src.agents.skills.alpha")
    result = register_module(registry, "alpha", mod, "src.agents.skills.alpha", "skill")
    assert result is None
    assert registry["alpha"] is mod


def test_register_module_rejects_duplicate_key():
    """register_module should raise DuplicateMAFKeyError on a duplicate key."""
    registry: dict = {}
    first = types.SimpleNamespace(__name__="src.agents.skills.alpha")
    second = types.SimpleNamespace(__name__="src.agents.workflows.beta")

    register_module(registry, "dup", first, "src.agents.skills.alpha", "skill")
    assert registry["dup"] is first

    with pytest.raises(DuplicateMAFKeyError) as exc_info:
        register_module(registry, "dup", second, "src.agents.workflows.beta", "workflow")

    exc_str = str(exc_info.value)
    assert "src.agents.skills.alpha" in exc_str
    assert "src.agents.workflows.beta" in exc_str
    # The registry must still map to the first module
    assert registry["dup"] is first


def test_duplicate_maf_key_error_is_runtime_error():
    """DuplicateMAFKeyError must be a subclass of RuntimeError."""
    assert issubclass(DuplicateMAFKeyError, RuntimeError)
