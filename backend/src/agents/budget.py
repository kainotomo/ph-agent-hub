# =============================================================================
# PH Agent Hub — Agent Run Budget Resolution (Issue #595)
# =============================================================================
# Pure module: no module-level import of ``settings`` — settings are injected
# by callers.  Budget resolution helpers are intentionally dependency-free so
# they can be unit-tested in isolation.
# =============================================================================

import logging
from dataclasses import dataclass

from ..core.exceptions import ValidationError

logger = logging.getLogger(__name__)

BUDGET_MAX_STEPS_KEY = "max_steps"
BUDGET_MAX_INPUT_TOKENS_KEY = "max_input_tokens"
DEFAULT_WARN_RATIO = 0.6
DEFAULT_MAX_INPUT_TOKENS = 0
DEFAULT_STEP_CEILING = 500


@dataclass(frozen=True)
class RunBudget:
    """Resolved per-invocation agent run budget (steps, token cap, warning
    ratio, and the source each value came from)."""

    max_steps: int
    max_input_tokens: int = 0
    warn_ratio: float = 0.6
    max_steps_source: str = "global"  # "skill" | "template" | "global"
    max_input_tokens_source: str = "global"  # "skill" | "template" | "global"

    def __post_init__(self):
        if self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if self.max_input_tokens < 0:
            raise ValueError("max_input_tokens must be >= 0")
        if not (0 < self.warn_ratio < 1):
            raise ValueError("warn_ratio must be in (0, 1)")

    def to_dict(self) -> dict:
        return {
            "max_steps": self.max_steps,
            "max_input_tokens": self.max_input_tokens,
            "warn_ratio": self.warn_ratio,
            "max_steps_source": self.max_steps_source,
            "max_input_tokens_source": self.max_input_tokens_source,
        }


def coerce_int(value) -> int | None:
    """Coerce a value to ``int`` when possible.

    Accepts ``int`` values (but not ``bool``) and whitespace-trimmed integer
    strings (e.g. ``"70"``).  Returns ``None`` for anything else (``None``,
    ``MagicMock``, ``float``, ``bool``, empty strings, ...).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def budget_from_settings(settings_obj) -> RunBudget:
    """Build a ``RunBudget`` from a settings object using the global
    fallback values.  Both values come from the ``global`` source."""
    max_steps = coerce_int(getattr(settings_obj, "AGENT_MAX_STEPS", None))
    if max_steps is None or max_steps < 1:
        max_steps = 50

    max_input_tokens = coerce_int(
        getattr(settings_obj, "AGENT_MAX_INPUT_TOKENS", None)
    )
    if max_input_tokens is None or max_input_tokens < 0:
        max_input_tokens = DEFAULT_MAX_INPUT_TOKENS

    warn_ratio = getattr(settings_obj, "AGENT_BUDGET_WARN_RATIO", None)
    if (
        not isinstance(warn_ratio, (int, float))
        or isinstance(warn_ratio, bool)
        or not (0 < float(warn_ratio) < 1)
    ):
        warn_ratio = DEFAULT_WARN_RATIO

    return RunBudget(
        max_steps=max_steps,
        max_input_tokens=max_input_tokens,
        warn_ratio=warn_ratio,
        max_steps_source="global",
        max_input_tokens_source="global",
    )


def resolve_run_budget(
    skill_config: dict | None,
    template_config: dict | None,
    *,
    global_max_steps: int = 50,
    global_max_input_tokens: int = 0,
    warn_ratio: float = 0.6,
    step_ceiling: int = 500,
) -> RunBudget:
    """Resolve the run budget with per-dimension precedence:
    ``skill_config`` -> ``template_config`` -> globals.

    Only a config that is a ``dict`` is treated as a source.  ``max_steps``
    values below 1 are ignored (warning logged); values above ``step_ceiling``
    are clamped to the ceiling and still count as that source.  ``max_input_tokens``
    values below 0 are ignored (warning logged); ``0`` is a valid explicit value
    meaning unlimited.  Invalid stored values fall back to the next lower source
    without raising (``RunBudget`` construction never raises for invalid data).
    """
    # Defensive coercion of the global fallbacks: a MagicMock/settings typo
    # must never produce an invalid budget (Issue #595).
    coerced_steps = coerce_int(global_max_steps)
    max_steps = coerced_steps if coerced_steps is not None and coerced_steps >= 1 else 50
    coerced_input_tokens = coerce_int(global_max_input_tokens)
    max_input_tokens = (
        coerced_input_tokens
        if coerced_input_tokens is not None and coerced_input_tokens >= 0
        else DEFAULT_MAX_INPUT_TOKENS
    )
    if (
        not isinstance(warn_ratio, (int, float))
        or isinstance(warn_ratio, bool)
        or not (0 < float(warn_ratio) < 1)
    ):
        warn_ratio = DEFAULT_WARN_RATIO
    coerced_ceiling = coerce_int(step_ceiling)
    if coerced_ceiling is None or coerced_ceiling < 1:
        coerced_ceiling = DEFAULT_STEP_CEILING

    max_steps_source = "global"
    max_input_tokens_source = "global"

    def _apply_config(config, source):
        nonlocal max_steps, max_input_tokens, max_steps_source, max_input_tokens_source
        if not isinstance(config, dict):
            return
        if BUDGET_MAX_STEPS_KEY in config:
            value = coerce_int(config.get(BUDGET_MAX_STEPS_KEY))
            if value is not None:
                if value < 1:
                    logger.warning(
                        "Ignoring invalid max_steps=%r in %s config (must be >= 1)",
                        value,
                        source,
                    )
                else:
                    if value > coerced_ceiling:
                        logger.warning(
                            "Clamping max_steps=%r in %s config to ceiling %d",
                            value,
                            source,
                            coerced_ceiling,
                        )
                        value = coerced_ceiling
                    max_steps = value
                    max_steps_source = source
        if BUDGET_MAX_INPUT_TOKENS_KEY in config:
            value = coerce_int(config.get(BUDGET_MAX_INPUT_TOKENS_KEY))
            if value is not None:
                if value < 0:
                    logger.warning(
                        "Ignoring invalid max_input_tokens=%r in %s config "
                        "(must be >= 0)",
                        value,
                        source,
                    )
                else:
                    max_input_tokens = value
                    max_input_tokens_source = source

    # Lower precedence first: the skill overrides the template when both set
    # the same dimension.
    _apply_config(template_config, "template")
    _apply_config(skill_config, "skill")

    return RunBudget(
        max_steps=max_steps,
        max_input_tokens=max_input_tokens,
        warn_ratio=float(warn_ratio),
        max_steps_source=max_steps_source,
        max_input_tokens_source=max_input_tokens_source,
    )


def normalize_agent_budget_config(config: dict | None, *, step_ceiling: int = 500) -> dict | None:
    """Normalise an ``agent_config`` dict before persisting.

    Returns ``None`` for ``None`` input.  Raises ``ValidationError`` for a
    non-dict input or for invalid ``max_steps`` / ``max_input_tokens`` values.
    ``None`` values for either key are dropped from the returned dict; all
    other keys (e.g. ``max_turns``) are preserved.
    """
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValidationError("agent_config must be an object")

    coerced_ceiling = coerce_int(step_ceiling)
    if coerced_ceiling is None or coerced_ceiling < 1:
        coerced_ceiling = DEFAULT_STEP_CEILING

    normalized = dict(config)

    if BUDGET_MAX_STEPS_KEY in normalized:
        raw_steps = normalized[BUDGET_MAX_STEPS_KEY]
        if raw_steps is None:
            normalized.pop(BUDGET_MAX_STEPS_KEY)
        else:
            value = coerce_int(raw_steps)
            if value is None or value < 1 or value > coerced_ceiling:
                raise ValidationError(
                    f"agent_config.max_steps must be an integer between 1 and {coerced_ceiling}"
                )
            normalized[BUDGET_MAX_STEPS_KEY] = value

    if BUDGET_MAX_INPUT_TOKENS_KEY in normalized:
        raw_tokens = normalized[BUDGET_MAX_INPUT_TOKENS_KEY]
        if raw_tokens is None:
            normalized.pop(BUDGET_MAX_INPUT_TOKENS_KEY)
        else:
            value = coerce_int(raw_tokens)
            if value is None or value < 0:
                raise ValidationError(
                    "agent_config.max_input_tokens must be a non-negative integer"
                )
            normalized[BUDGET_MAX_INPUT_TOKENS_KEY] = value

    return normalized
