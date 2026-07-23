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


async def startup_scan(db: AsyncSession) -> None:
    """Scan skills and workflows packages, register modules with MAF_KEY,
    and validate against DB skill records. Called on FastAPI startup.

    Does NOT crash if a DB skill references an unregistered key — logs a
    WARNING instead.
    """
    # Import the packages so `pkgutil.iter_modules` can discover children
    from . import skills as skills_pkg
    from . import workflows as workflows_pkg

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
            _registry[key] = mod
            logger.info("Registered skill: %s → %s", key, full_name)

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
            _registry[key] = mod
            logger.info("Registered workflow: %s → %s", key, full_name)

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
