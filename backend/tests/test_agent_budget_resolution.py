# =============================================================================
# PH Agent Hub — Agent Run Budget Resolution Tests (Issue #595)
# =============================================================================

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agents.budget import (
    BUDGET_MAX_INPUT_TOKENS_KEY,
    BUDGET_MAX_STEPS_KEY,
    RunBudget,
    budget_from_settings,
    coerce_int,
    normalize_agent_budget_config,
    resolve_run_budget,
)
from src.core.exceptions import ValidationError

pytestmark = [pytest.mark.unit]


# ===========================================================================
# coerce_int
# ===========================================================================


class TestCoerceInt:
    def test_int_values(self):
        assert coerce_int(70) == 70
        assert coerce_int(0) == 0

    def test_digit_strings(self):
        assert coerce_int("70") == 70
        assert coerce_int(" 70 ") == 70

    def test_bool_rejected(self):
        assert coerce_int(True) is None
        assert coerce_int(False) is None

    def test_none_rejected(self):
        assert coerce_int(None) is None

    def test_magicmock_rejected(self):
        assert coerce_int(MagicMock()) is None

    def test_float_rejected(self):
        assert coerce_int(70.0) is None


# ===========================================================================
# budget_from_settings
# ===========================================================================


class TestBudgetFromSettings:
    def test_defaults(self):
        settings = SimpleNamespace(
            AGENT_MAX_STEPS=50,
            AGENT_MAX_INPUT_TOKENS=0,
            AGENT_BUDGET_WARN_RATIO=0.6,
        )
        budget = budget_from_settings(settings)
        assert budget.max_steps == 50
        assert budget.max_input_tokens == 0
        assert budget.warn_ratio == 0.6
        assert budget.max_steps_source == "global"
        assert budget.max_input_tokens_source == "global"

    def test_explicit_values(self):
        settings = SimpleNamespace(
            AGENT_MAX_STEPS=50,
            AGENT_MAX_INPUT_TOKENS=12_000_000,
            AGENT_BUDGET_WARN_RATIO=0.5,
        )
        budget = budget_from_settings(settings)
        assert budget.max_steps == 50
        assert budget.max_input_tokens == 12_000_000
        assert budget.warn_ratio == 0.5
        assert budget.max_steps_source == "global"
        assert budget.max_input_tokens_source == "global"

    def test_invalid_values_fall_back(self):
        settings = SimpleNamespace(
            AGENT_MAX_STEPS=None,
            AGENT_MAX_INPUT_TOKENS=-1,
            AGENT_BUDGET_WARN_RATIO="not-a-number",
        )
        budget = budget_from_settings(settings)
        assert budget.max_steps == 50
        assert budget.max_input_tokens == 0
        assert budget.warn_ratio == 0.6


# ===========================================================================
# resolve_run_budget
# ===========================================================================


class TestResolveRunBudget:
    def test_global_fallback(self):
        budget = resolve_run_budget(
            skill_config=None,
            template_config=None,
            global_max_steps=50,
            global_max_input_tokens=0,
            warn_ratio=0.6,
        )
        assert budget.max_steps == 50
        assert budget.max_input_tokens == 0
        assert budget.warn_ratio == 0.6
        assert budget.max_steps_source == "global"
        assert budget.max_input_tokens_source == "global"

    def test_skill_override(self):
        budget = resolve_run_budget(
            skill_config={"max_steps": 70, "max_input_tokens": 12_000_000},
            template_config=None,
            global_max_steps=50,
            global_max_input_tokens=0,
        )
        assert budget.max_steps == 70
        assert budget.max_input_tokens == 12_000_000
        assert budget.max_steps_source == "skill"
        assert budget.max_input_tokens_source == "skill"

    def test_template_override_when_skill_none(self):
        budget = resolve_run_budget(
            skill_config=None,
            template_config={"max_steps": 70, "max_input_tokens": 12_000_000},
            global_max_steps=50,
            global_max_input_tokens=0,
        )
        assert budget.max_steps == 70
        assert budget.max_input_tokens == 12_000_000
        assert budget.max_steps_source == "template"
        assert budget.max_input_tokens_source == "template"

    def test_skill_beats_template(self):
        budget = resolve_run_budget(
            skill_config={"max_steps": 70, "max_input_tokens": 12_000_000},
            template_config={"max_steps": 80, "max_input_tokens": 20_000_000},
            global_max_steps=50,
            global_max_input_tokens=0,
        )
        assert budget.max_steps == 70
        assert budget.max_input_tokens == 12_000_000
        assert budget.max_steps_source == "skill"
        assert budget.max_input_tokens_source == "skill"

    def test_invalid_skill_falls_back_to_template(self):
        budget = resolve_run_budget(
            skill_config={"max_steps": 0, "max_input_tokens": -5},
            template_config={"max_steps": 70, "max_input_tokens": 12_000_000},
            global_max_steps=50,
            global_max_input_tokens=0,
        )
        assert budget.max_steps == 70
        assert budget.max_input_tokens == 12_000_000
        assert budget.max_steps_source == "template"
        assert budget.max_input_tokens_source == "template"

    def test_invalid_skill_falls_back_to_global(self):
        budget = resolve_run_budget(
            skill_config={"max_steps": 0, "max_input_tokens": -1},
            template_config=None,
            global_max_steps=50,
            global_max_input_tokens=0,
        )
        assert budget.max_steps == 50
        assert budget.max_input_tokens == 0
        assert budget.max_steps_source == "global"
        assert budget.max_input_tokens_source == "global"

    def test_ceiling_clamp(self):
        budget = resolve_run_budget(
            skill_config={"max_steps": 900},
            template_config=None,
            global_max_steps=50,
            step_ceiling=500,
        )
        assert budget.max_steps == 500
        assert budget.max_steps_source == "skill"

    def test_explicit_zero_max_input_tokens_accepted(self):
        budget = resolve_run_budget(
            skill_config={"max_input_tokens": 0},
            template_config=None,
            global_max_input_tokens=0,
        )
        assert budget.max_input_tokens == 0
        assert budget.max_input_tokens_source == "skill"

    def test_string_values_coerced(self):
        budget = resolve_run_budget(
            skill_config={"max_steps": "70", "max_input_tokens": "12000000"},
            template_config=None,
            global_max_steps=50,
            global_max_input_tokens=0,
        )
        assert budget.max_steps == 70
        assert budget.max_input_tokens == 12_000_000
        assert budget.max_steps_source == "skill"
        assert budget.max_input_tokens_source == "skill"


# ===========================================================================
# normalize_agent_budget_config
# ===========================================================================


class TestNormalizeAgentBudgetConfig:
    def test_none_returns_none(self):
        assert normalize_agent_budget_config(None) is None

    def test_valid_values_normalised(self):
        result = normalize_agent_budget_config(
            {"max_steps": 70, "max_input_tokens": 12_000_000, "max_turns": 5}
        )
        assert result["max_steps"] == 70
        assert result["max_input_tokens"] == 12_000_000
        assert result["max_turns"] == 5

    def test_max_turns_preserved(self):
        result = normalize_agent_budget_config(
            {"max_steps": 70, "max_turns": 12, "extra": "keep"}
        )
        assert result["max_turns"] == 12
        assert result["extra"] == "keep"

    def test_none_values_dropped(self):
        result = normalize_agent_budget_config(
            {"max_steps": None, "max_input_tokens": None, "max_turns": 3}
        )
        assert BUDGET_MAX_STEPS_KEY not in result
        assert BUDGET_MAX_INPUT_TOKENS_KEY not in result
        assert result["max_turns"] == 3

    def test_invalid_max_steps_raises(self):
        for bad in (0, -1, "abc", 501):
            with pytest.raises(ValidationError):
                normalize_agent_budget_config({"max_steps": bad})

    def test_invalid_max_input_tokens_raises(self):
        for bad in (-1, "abc"):
            with pytest.raises(ValidationError):
                normalize_agent_budget_config({"max_input_tokens": bad})

    def test_non_dict_raises(self):
        with pytest.raises(ValidationError):
            normalize_agent_budget_config("not-an-object")


# ===========================================================================
# RunBudget
# ===========================================================================


class TestRunBudget:
    def test_to_dict_shape(self):
        budget = RunBudget(
            max_steps=70,
            max_input_tokens=12_000_000,
            warn_ratio=0.6,
            max_steps_source="skill",
            max_input_tokens_source="skill",
        )
        payload = budget.to_dict()
        assert set(payload.keys()) == {
            "max_steps",
            "max_input_tokens",
            "warn_ratio",
            "max_steps_source",
            "max_input_tokens_source",
        }
        assert payload["max_steps"] == 70
        assert payload["max_input_tokens"] == 12_000_000
        assert payload["warn_ratio"] == 0.6
        assert payload["max_steps_source"] == "skill"
        assert payload["max_input_tokens_source"] == "skill"

    def test_post_init_rejects_zero_max_steps(self):
        with pytest.raises(ValueError):
            RunBudget(max_steps=0)

    def test_post_init_rejects_bad_warn_ratio(self):
        with pytest.raises(ValueError):
            RunBudget(max_steps=70, warn_ratio=0)
        with pytest.raises(ValueError):
            RunBudget(max_steps=70, warn_ratio=1)

    def test_post_init_rejects_negative_tokens(self):
        with pytest.raises(ValueError):
            RunBudget(max_steps=70, max_input_tokens=-1)
