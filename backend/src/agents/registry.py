# =============================================================================
# PH Agent Hub — MAF Skill/Workflow Registry
# =============================================================================
# Scans src/agents/skills/ and src/agents/workflows/ on startup and registers
# any module that exposes a MAF_KEY attribute.
# =============================================================================

import importlib
import logging
import pkgutil
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# In-memory registry: maf_target_key → registered module/object
_registry: dict[str, Any] = {}

# Agent definitions: agent_key → agent module
_agents: dict[str, Any] = {}


class DuplicateMAFKeyError(RuntimeError):
    """Raised when two scanned modules declare the same MAF_KEY."""


def register_module(
    registry: dict[str, Any],
    key: str,
    module: Any,
    full_name: str,
    kind: str,
) -> None:
    """Register *module* under *key* in *registry*, raising
    ``DuplicateMAFKeyError`` if the key already exists.
    """
    existing = registry.get(key)
    if existing is not None:
        raise DuplicateMAFKeyError(
            f"Duplicate MAF key {key!r}: already registered "
            f"{getattr(existing, '__name__', repr(existing))}, "
            f"refused {full_name}"
        )
    registry[key] = module
    logger.info("Registered %s: %s → %s", kind, key, full_name)


def scan_agent_defs() -> dict[str, Any]:
    """Scan the agent_defs package and register any module exposing MAF_KEY,
    NAME, INSTRUCTIONS, and MODEL_ROLE.  Repeated calls are idempotent
    (stale modules are cleared first).

    Returns the ``_agents`` dict on success.
    """
    _agents.clear()

    from . import agent_defs as agent_defs_pkg
    from .workflows.roles import validate_reference

    for _, mod_name, _ in pkgutil.iter_modules(agent_defs_pkg.__path__):
        full_name = f"src.agents.agent_defs.{mod_name}"
        try:
            module = importlib.import_module(full_name)
        except Exception as exc:
            logger.warning("Failed to import agent module %s: %s", full_name, exc)
            continue

        for name in ("MAF_KEY", "NAME", "INSTRUCTIONS", "MODEL_ROLE"):
            if not hasattr(module, name):
                logger.warning(
                    "Agent module %s is missing %s — not registered",
                    full_name,
                    name,
                )
                break
        else:
            try:
                validate_reference(module.MODEL_ROLE, "model")
            except ValueError as exc:
                logger.warning(
                    "Agent module %s declares invalid MODEL_ROLE %r: %s — not registered",
                    full_name,
                    module.MODEL_ROLE,
                    exc,
                )
                continue

            register_module(_agents, module.MAF_KEY, module, full_name, "agent")

    return _agents


async def startup_scan(db: AsyncSession) -> None:
    """Scan skills and workflows packages, register modules with MAF_KEY,
    and validate against DB skill records. Called on FastAPI startup.

    Does NOT crash if a DB skill references an unregistered key — logs a
    WARNING instead.
    """
    # Import the packages so `pkgutil.iter_modules` can discover children
    from . import skills as skills_pkg
    from . import workflows as workflows_pkg

    _registry.clear()

    # ---- Scan skills -------------------------------------------------------
    for _, mod_name, _ in pkgutil.iter_modules(skills_pkg.__path__):
        full_name = f"src.agents.skills.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as exc:
            logger.warning("Failed to import skill module %s: %s", full_name, exc)
            continue

        key = getattr(mod, "MAF_KEY", None)
        if key is not None:
            register_module(_registry, key, mod, full_name, "skill")

    # ---- Scan workflows ----------------------------------------------------
    for _, mod_name, _ in pkgutil.iter_modules(workflows_pkg.__path__):
        full_name = f"src.agents.workflows.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as exc:
            logger.warning("Failed to import workflow module %s: %s", full_name, exc)
            continue

        key = getattr(mod, "MAF_KEY", None)
        if key is not None:
            register_module(_registry, key, mod, full_name, "workflow")

    # ---- Scan agent definitions --------------------------------------------
    scan_agent_defs()

    # ---- Validate DB skills against registry -------------------------------
    from ..db.orm.skills import Skill

    result = await db.execute(select(Skill.maf_target_key).distinct())
    db_keys = [row[0] for row in result.all()]

    for key in db_keys:
        if key is None:
            continue  # goal_based skills don't need a MAF target
        if key not in _registry:
            logger.warning(
                "Skill '%s' exists in DB but has no registered MAF target.", key
            )

    logger.info("MAF registry scan complete — %d target(s) registered.", len(_registry))


def get_registered(key: str) -> Any | None:
    """Look up a registered MAF target by key. Returns None if not found."""
    return _registry.get(key)


def list_registered_keys() -> list[str]:
    """Return all registered MAF target keys."""
    return list(_registry.keys())


def get_registered_agent(key: str) -> Any | None:
    """Look up a registered agent definition by key.  Returns ``None`` if
    not found."""
    return _agents.get(key)


def list_registered_agent_keys() -> list[str]:
    """Return all registered agent keys."""
    return list(_agents.keys())
