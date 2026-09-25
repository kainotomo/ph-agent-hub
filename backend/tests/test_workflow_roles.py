# =============================================================================
# PH Agent Hub — Role Vocabulary Tests
# =============================================================================
# Tests for the closed role vocabulary and @-reference helpers.
# =============================================================================

import pytest

from src.agents.workflows.roles import (
    AGENT_ROLES,
    MODEL_ROLES,
    REFERENCE_PREFIX,
    TOOL_ROLES,
    TOOL_ROLE_TARGETS,
    known_roles,
    is_role_reference,
    validate_reference,
)


# =============================================================================
# Constants
# =============================================================================


class TestRoleConstants:
    """Verify the declared role constants have the expected shape."""

    def test_model_roles_is_frozenset(self):
        assert isinstance(MODEL_ROLES, frozenset)

    def test_tool_roles_is_frozenset(self):
        assert isinstance(TOOL_ROLES, frozenset)

    def test_agent_roles_is_frozenset(self):
        assert isinstance(AGENT_ROLES, frozenset)

    def test_all_members_start_with_at(self):
        for role in MODEL_ROLES | TOOL_ROLES | AGENT_ROLES:
            assert role.startswith(REFERENCE_PREFIX)


class TestToolRoleTargets:
    """Verify the tool-role to MAF callable-name mapping."""

    def test_every_tool_role_has_a_target(self):
        assert set(TOOL_ROLE_TARGETS) == set(TOOL_ROLES)

    def test_targets_are_non_empty_tuples_of_names(self):
        for role, names in TOOL_ROLE_TARGETS.items():
            assert isinstance(names, tuple), f"{role} target must be a tuple"
            assert names, f"{role} target must not be empty"
            for name in names:
                assert isinstance(name, str) and name.strip(), (
                    f"{role} target names must be non-empty strings"
                )

    def test_web_search_maps_to_web_search_callable(self):
        assert TOOL_ROLE_TARGETS["@web_search"] == ("web_search",)

    def test_every_target_key_starts_with_at(self):
        for role in TOOL_ROLE_TARGETS:
            assert role.startswith(REFERENCE_PREFIX)


# =============================================================================
# is_role_reference
# =============================================================================


class TestIsRoleReference:
    """Tests for the is_role_reference helper."""

    def test_prefixed_is_role(self):
        assert is_role_reference("@reasoning") is True

    def test_unprefixed_is_not_role(self):
        assert is_role_reference("deepseek-reasoner") is False

    def test_empty_string_is_not_role(self):
        assert is_role_reference("") is False


# =============================================================================
# validate_reference
# =============================================================================


class TestValidateReference:
    """Tests for the validate_reference helper."""

    def test_valid_model_role_raises_nothing(self):
        validate_reference("@reasoning", "model")

    def test_unprefixed_concrete_resource_raises_nothing(self):
        validate_reference("gpt-4o", "model")

    def test_unknown_model_role_raises_valueerror(self):
        with pytest.raises(ValueError, match=r"@nope") as exc_info:
            validate_reference("@nope", "model")
        assert "Unknown model role" in str(exc_info.value)

    def test_empty_agent_vocabulary_raises_for_any_role(self):
        with pytest.raises(ValueError, match="Unknown agent role"):
            validate_reference("@anything", "agent")

    def test_message_lists_known_roles_when_invalid(self):
        with pytest.raises(ValueError, match=r"@fast, @general, @reasoning") as exc_info:
            validate_reference("@nope", "model")
        assert "gpt-4o" not in str(exc_info.value)

    def test_message_shows_none_for_empty_vocabulary(self):
        with pytest.raises(ValueError, match=r"\(none\)") as exc_info:
            validate_reference("@anything", "agent")


# =============================================================================
# known_roles
# =============================================================================


class TestKnownRoles:
    """Tests for the known_roles helper."""

    def test_returns_expected_set(self):
        assert known_roles("model") == MODEL_ROLES
        assert known_roles("tool") == TOOL_ROLES
        assert known_roles("agent") == AGENT_ROLES

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="Unknown reference kind 'bogus'"):
            known_roles("bogus")
