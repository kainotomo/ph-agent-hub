# =============================================================================
# Tests for WorkflowStep identity validation
# =============================================================================
# A step's ``id`` is executor identity: it must be non-empty and free of
# whitespace, otherwise MAF silently falls back to the freely editable display
# ``name``.
# =============================================================================

import pytest

from src.agents.workflows.definition import WorkflowDefinition, WorkflowStep


def _inline_step(step_id: str) -> dict:
    """Build a minimal valid inline step payload with the given id."""
    return {
        "id": step_id,
        "name": "Display Name",
        "type": "inline",
        "instructions": "Do something",
    }


def test_empty_id_raises():
    with pytest.raises(ValueError):
        WorkflowStep(**_inline_step(""))


def test_whitespace_only_id_raises():
    with pytest.raises(ValueError):
        WorkflowStep(**_inline_step("   "))


def test_id_with_space_raises():
    with pytest.raises(ValueError):
        WorkflowStep(**_inline_step("has space"))


def test_id_with_tab_raises():
    with pytest.raises(ValueError):
        WorkflowStep(**_inline_step("has\ttab"))


def test_colon_separated_id_is_accepted():
    step = WorkflowStep(**_inline_step("a:b"))
    assert step.id == "a:b"


def test_normal_definition_constructs():
    defn = WorkflowDefinition(
        key="research",
        name="Web Research",
        steps=[_inline_step("research"), _inline_step("report")],
    )
    assert [s.id for s in defn.steps] == ["research", "report"]


def test_empty_id_error_message_mentions_id():
    with pytest.raises(ValueError, match="id"):
        WorkflowStep(**_inline_step(""))
