# =============================================================================
# PH Agent Hub — Condition Vocabulary Tests
# =============================================================================
# Tests for the closed condition vocabulary and @-reference helpers.
# =============================================================================

import pytest

from src.agents.workflows.conditions import (
    CONDITION_PRIMITIVES,
    CONDITION_PREFIX,
    condition_names,
    is_condition_reference,
    resolve_condition,
)


# =============================================================================
# condition_names
# =============================================================================


class TestConditionNames:
    """Tests for the condition_names() helper."""

    def test_returns_tuple(self):
        result = condition_names()
        assert isinstance(result, tuple)

    def test_equals_sorted_keys(self):
        assert condition_names() == tuple(sorted(CONDITION_PRIMITIVES.keys()))

    def test_contains_expected_conditions(self):
        expected = ("@always", "@contains_keyword", "@never", "@non_empty")
        assert condition_names() == expected


# =============================================================================
# Vocabulary keys
# =============================================================================


class TestVocabularyKeys:
    """Verify every key in CONDITION_PRIMITIVES has the expected shape."""

    def test_every_key_starts_with_at(self):
        for key in CONDITION_PRIMITIVES:
            assert key.startswith(CONDITION_PREFIX)

    def test_every_key_matches_reference_regex(self):
        import re

        ref_re = re.compile(r"^@([a-z][a-z0-9_]*)$")
        for key in CONDITION_PRIMITIVES:
            assert ref_re.match(key), f"{key!r} does not match ^@[a-z][a-z0-9_]*$"

    def test_no_whitespace_in_keys(self):
        for key in CONDITION_PRIMITIVES:
            assert not any(ch.isspace() for ch in key)


# =============================================================================
# Vocabulary values
# =============================================================================


class TestVocabularyValues:
    """Verify every value in CONDITION_PRIMITIVES is a proper predicate."""

    def test_every_value_is_callable(self):
        for key, fn in CONDITION_PRIMITIVES.items():
            assert callable(fn), f"{key!r} value is not callable"

    def test_no_disallowed_names(self):
        for key, fn in CONDITION_PRIMITIVES.items():
            assert fn.__name__ not in ("<lambda>", "<callable>", ""), (
                f"{key!r} has disallowed __name__ {fn.__name__!r}"
            )


# =============================================================================
# resolve_condition — happy path
# =============================================================================


class TestResolveCondition:
    """Tests for resolve_condition on valid references."""

    def test_always_returns_true(self):
        fn = resolve_condition("@always")
        assert fn("") is True

    def test_never_returns_false(self):
        fn = resolve_condition("@never")
        assert fn("anything") is False

    def test_non_empty_empty_string(self):
        fn = resolve_condition("@non_empty")
        assert fn("") is False

    def test_non_empty_whitespace_only(self):
        fn = resolve_condition("@non_empty")
        assert fn("  ") is False

    def test_non_empty_non_empty(self):
        fn = resolve_condition("@non_empty")
        assert fn("x") is True

    def test_contains_keyword_found_uppercase(self):
        fn = resolve_condition("@contains_keyword")
        assert fn("ESCALATE Keyword now") is True

    def test_contains_keyword_found_lowercase(self):
        fn = resolve_condition("@contains_keyword")
        assert fn("has keyword here") is True

    def test_contains_keyword_not_found(self):
        fn = resolve_condition("@contains_keyword")
        assert fn("nothing here") is False


# =============================================================================
# resolve_condition — errors
# =============================================================================


class TestResolveConditionErrors:
    """Tests for resolve_condition on unknown references."""

    def test_unknown_ref_raises_valueerror(self):
        with pytest.raises(ValueError, match="@nope") as exc_info:
            resolve_condition("@nope")
        assert "Unknown condition" in str(exc_info.value)

    def test_message_lists_known_conditions(self):
        with pytest.raises(ValueError, match="@always") as exc_info:
            resolve_condition("@nope")
        msg = str(exc_info.value)
        # Must list all known conditions sorted
        assert "@always" in msg
        assert "@contains_keyword" in msg
        assert "@never" in msg
        assert "@non_empty" in msg


# =============================================================================
# is_condition_reference
# =============================================================================


class TestIsConditionReference:
    """Tests for the is_condition_reference helper."""

    def test_prefixed_is_condition(self):
        assert is_condition_reference("@always") is True

    def test_unprefixed_is_not_condition(self):
        assert is_condition_reference("always") is False

    def test_empty_string_is_not_condition(self):
        assert is_condition_reference("") is False


# =============================================================================
# Identity is deliberate, not accidental
# =============================================================================


class TestIdentity:
    """MAF's graph_signature records only the predicate's __name__,
    so two conditions sharing a name would be indistinguishable."""

    def test_different_predicates_have_different_names(self):
        # MAF's graph_signature records only the predicate's __name__,
        # so two conditions sharing a name would be indistinguishable
        # to edit classification.
        always_name = CONDITION_PRIMITIVES["@always"].__name__
        never_name = CONDITION_PRIMITIVES["@never"].__name__
        assert always_name != never_name
