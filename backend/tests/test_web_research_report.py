# =============================================================================
# PH Agent Hub — Web Research Report Workflow Contract Tests
# =============================================================================
# Verifies that the shipped ``web_research_report`` module satisfies the
# step contract migrated in Session 4: role references replace hardcoded
# tenant model ids, step ids are stable, and the graph topology is intact.
# =============================================================================

import pathlib
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

import src.agents.workflows.web_research_report as mod


# =============================================================================
# Module-level exports
# =============================================================================


class TestModuleExports:
    """Verify the module exposes the expected MAF keys."""

    def test_maf_key(self):
        assert mod.MAF_KEY == "web_research_report"

    def test_name_exists(self):
        assert hasattr(mod, "NAME")
        assert isinstance(mod.NAME, str)
        assert mod.NAME

    def test_description_exists(self):
        assert hasattr(mod, "DESCRIPTION")
        assert isinstance(mod.DESCRIPTION, str)
        assert mod.DESCRIPTION


# =============================================================================
# Workflow definition contract (Step 3)
# =============================================================================


class TestWorkflowDefinitionContract:
    """Tests for load_workflow_definition and step identity stability."""

    def test_load_returns_correct_key_and_step_ids(self):
        from src.agents.workflows.engine import load_workflow_definition
        from src.agents.workflows.definition import WorkflowDefinition

        defn = load_workflow_definition(mod)

        assert isinstance(defn, WorkflowDefinition)
        assert defn.key == mod.MAF_KEY

        step_ids = [s.id for s in defn.steps]
        assert step_ids == ["research", "report"]


# =============================================================================
# Step shape contract (Step 4)
# =============================================================================


class TestStepShape:
    """Every step must be ``inline`` with instructions, empty input, stop."""

    def test_all_inline_type(self):
        from src.agents.workflows.engine import load_workflow_definition

        defn = load_workflow_definition(mod)

        for step in defn.steps:
            assert step.type == "inline"

    def test_non_empty_instructions(self):
        from src.agents.workflows.engine import load_workflow_definition

        defn = load_workflow_definition(mod)

        for step in defn.steps:
            assert step.instructions and len(step.instructions) > 0

    def test_input_empty(self):
        from src.agents.workflows.engine import load_workflow_definition

        defn = load_workflow_definition(mod)

        for step in defn.steps:
            assert step.input == ""

    def test_on_error_stop(self):
        from src.agents.workflows.engine import load_workflow_definition

        defn = load_workflow_definition(mod)

        for step in defn.steps:
            assert step.on_error == "stop"


# =============================================================================
# No tenant model references (Steps 5 and 6)
# =============================================================================


class TestNoTenantModelReferences:
    """Every step's model_ref must be an @-prefixed role reference."""

    def test_model_ref_is_role_reference(self):
        from src.agents.workflows.roles import MODEL_ROLES
        from src.agents.workflows.engine import load_workflow_definition

        defn = load_workflow_definition(mod)

        for step in defn.steps:
            assert step.model_ref.startswith(
                "@"
            ), f"Step '{step.id}' model_ref must be @-prefixed, got '{step.model_ref}'"
            assert (
                step.model_ref in MODEL_ROLES
            ), f"Step '{step.id}' model_ref '{step.model_ref}' not in MODEL_ROLES"

    def test_source_has_no_gpt_4o(self):
        """Regression: the shipped definition must not contain 'gpt-4o'."""
        source = pathlib.Path(mod.__file__).read_text()
        assert "gpt-4o" not in source


# =============================================================================
# WORKFLOW_DEFINITION dict shape (Step 7)
# =============================================================================


class TestWorkflowDefinitionDict:
    """The raw dict must not carry on_error at the top level."""

    def test_no_top_level_on_error_key(self):
        # Pydantic silently discards unknown keys; the dict should not
        # carry an on_error key at the workflow-definition level.
        wf_def = mod.WORKFLOW_DEFINITION
        assert "on_error" not in wf_def


# =============================================================================
# build_workflow integration (Step 8)
# =============================================================================


class TestBuildWorkflowIntegration:
    """The workflow must build successfully with mocked model/agent resolution."""

    async def test_builds_with_correct_executors(self):
        from src.agents.workflows.engine import build_workflow, load_workflow_definition

        defn = load_workflow_definition(mod)

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ) as mock_resolve, patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:

            mock_resolve.return_value = MagicMock(max_tokens=4096)
            mock_build.return_value = MagicMock()

            workflow = await build_workflow(defn=defn, db=MagicMock())

            executor_ids = [e.id for e in workflow.get_executors_list()]
            assert executor_ids == ["research", "report"]
