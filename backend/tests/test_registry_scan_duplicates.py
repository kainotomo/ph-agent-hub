# =============================================================================
# PH Agent Hub — Duplicate-key rejection tests for startup_scan
# =============================================================================
# Prove that startup_scan rejects a duplicate MAF_KEY within skills,
# within workflows, across skills and workflows, and within agent
# definitions, and that a scan with distinct keys still registers
# everything.
# =============================================================================

import types

import pytest

from src.agents import agent_defs as agent_defs_pkg
from src.agents import registry as reg
from src.agents import skills as skills_pkg
from src.agents import workflows as workflows_pkg
from src.agents.registry import DuplicateMAFKeyError, scan_agent_defs, startup_scan


class _FakeResult:
    def all(self):
        return []


class _FakeDB:
    async def execute(self, stmt):
        return _FakeResult()


def _install_fake_discovery(monkeypatch, keys):
    """keys: {full_dotted_name: MAF_KEY}"""
    def fake_iter_modules(path):
        path = list(path)
        for prefix, pkg in (
            ("src.agents.skills.", skills_pkg),
            ("src.agents.workflows.", workflows_pkg),
            ("src.agents.agent_defs.", agent_defs_pkg),
        ):
            if path == list(pkg.__path__):
                return [
                    (None, name.split(".")[-1], False)
                    for name in keys
                    if name.startswith(prefix)
                ]
        return []

    def fake_import_module(full_name):
        return types.SimpleNamespace(
            MAF_KEY=keys[full_name],
            __name__=full_name,
            NAME="fake",
            INSTRUCTIONS="fake instructions",
            MODEL_ROLE="@reasoning",
        )

    monkeypatch.setattr(reg, "pkgutil", types.SimpleNamespace(iter_modules=fake_iter_modules))
    monkeypatch.setattr(reg, "importlib", types.SimpleNamespace(import_module=fake_import_module))


@pytest.fixture(autouse=True)
def _restore_registries():
    saved_registry = dict(reg._registry)
    saved_agents = dict(reg._agents)
    yield
    reg._registry.clear()
    reg._registry.update(saved_registry)
    reg._agents.clear()
    reg._agents.update(saved_agents)


async def test_duplicate_within_skills_raises(monkeypatch):
    keys = {
        "src.agents.skills.alpha": "collide",
        "src.agents.skills.beta": "collide",
    }
    _install_fake_discovery(monkeypatch, keys)
    with pytest.raises(DuplicateMAFKeyError) as exc_info:
        await startup_scan(_FakeDB())
    msg = str(exc_info.value)
    assert "src.agents.skills.alpha" in msg
    assert "src.agents.skills.beta" in msg


async def test_duplicate_within_workflows_raises(monkeypatch):
    keys = {
        "src.agents.workflows.alpha": "collide",
        "src.agents.workflows.beta": "collide",
    }
    _install_fake_discovery(monkeypatch, keys)
    with pytest.raises(DuplicateMAFKeyError) as exc_info:
        await startup_scan(_FakeDB())
    msg = str(exc_info.value)
    assert "src.agents.workflows.alpha" in msg
    assert "src.agents.workflows.beta" in msg


async def test_duplicate_across_skills_and_workflows_raises(monkeypatch):
    keys = {
        "src.agents.skills.alpha": "collide",
        "src.agents.workflows.beta": "collide",
    }
    _install_fake_discovery(monkeypatch, keys)
    with pytest.raises(DuplicateMAFKeyError) as exc_info:
        await startup_scan(_FakeDB())
    msg = str(exc_info.value)
    assert "src.agents.skills.alpha" in msg
    assert "src.agents.workflows.beta" in msg


async def test_duplicate_within_agent_defs_raises(monkeypatch):
    keys = {
        "src.agents.agent_defs.alpha": "collide",
        "src.agents.agent_defs.beta": "collide",
    }
    _install_fake_discovery(monkeypatch, keys)
    with pytest.raises(DuplicateMAFKeyError) as exc_info:
        await startup_scan(_FakeDB())
    msg = str(exc_info.value)
    assert "src.agents.agent_defs.alpha" in msg
    assert "src.agents.agent_defs.beta" in msg


async def test_distinct_keys_register_without_error(monkeypatch):
    keys = {
        "src.agents.skills.s": "skill_key",
        "src.agents.workflows.w": "workflow_key",
        "src.agents.agent_defs.a": "agent_key",
    }
    _install_fake_discovery(monkeypatch, keys)
    assert await startup_scan(_FakeDB()) is None
