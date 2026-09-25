# =============================================================================
# PH Agent Hub — Per-step Tool Reference Acceptance Tests (Issue #550)
# =============================================================================
# End-to-end coverage, against the real test database, for per-step tool
# restriction and for the shipped definition resolving for a tenant that has
# satisfied the roles it references.
# =============================================================================

import types
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, patch

import src.agents.workflows.web_research_report as web_research_report
from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.engine import build_workflow, load_workflow_definition
from src.core.exceptions import ValidationError
from src.db.orm.models import Model
from src.db.orm.tenants import Tenant
from src.db.orm.tools import Tool
from src.services.model_role_service import set_role_bindings

pytestmark = [pytest.mark.integration]


def _tool_stub(name: str):
    return types.SimpleNamespace(name=name)


def _step_defn() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="tool_refs_acceptance",
        name="Tool Refs Acceptance",
        steps=[
            {
                "id": "research",
                "name": "Research",
                "type": "inline",
                "instructions": "I1",
                "tool_refs": ["@web_search"],
            },
            {
                "id": "report",
                "name": "Report",
                "type": "inline",
                "instructions": "I2",
            },
        ],
    )


class TestPerStepToolRestriction:
    """tool_refs restricts a step to a subset of the run's resolved pool."""

    async def test_restricted_step_and_inheriting_step(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        web = _tool_stub("web_search")
        calc = _tool_stub("calculator")

        with patch(
            "src.agents.workflows.engine.resolve_model", new=_model_stub(test_model)
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            await build_workflow(
                defn=_step_defn(),
                db=db_session,
                tenant_id=test_tenant.id,
                extra_tools=[web, calc],
            )

        assert mock_build.call_args_list[0][1]["tools"] == [web]
        assert mock_build.call_args_list[1][1]["tools"] == [web, calc]

    async def test_missing_tool_in_pool_is_rejected(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        with patch(
            "src.agents.workflows.engine.resolve_model", new=_model_stub(test_model)
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            with pytest.raises(ValidationError) as exc:
                await build_workflow(
                    defn=_step_defn(),
                    db=db_session,
                    tenant_id=test_tenant.id,
                    extra_tools=[_tool_stub("calculator")],
                )

        message = str(exc.value)
        assert "research" in message
        assert "@web_search" in message

    async def test_tenant_tool_row_does_not_widen_the_pool(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        """An enabled tenant Tool row is not enough: the tool must be in the
        run's resolved pool, otherwise the build fails loudly."""
        db_session.add(
            Tool(
                id=str(uuid.uuid4()),
                tenant_id=test_tenant.id,
                name="Tenant Web Search",
                type="web_search",
                category="general",
                config=None,
                enabled=True,
            )
        )
        await db_session.flush()

        with patch(
            "src.agents.workflows.engine.resolve_model", new=_model_stub(test_model)
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            with pytest.raises(ValidationError, match=r"@web_search"):
                await build_workflow(
                    defn=_step_defn(),
                    db=db_session,
                    tenant_id=test_tenant.id,
                    extra_tools=[_tool_stub("calculator")],
                )

    async def test_unrestricted_steps_inherit_whole_pool(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        pool = [
            _tool_stub("web_search"),
            _tool_stub("calculator"),
            _tool_stub("current_time"),
        ]

        defn = WorkflowDefinition(
            key="no_tool_refs",
            name="No Tool Refs",
            steps=[
                {"id": "a", "name": "A", "type": "inline", "instructions": "I1"},
                {"id": "b", "name": "B", "type": "inline", "instructions": "I2"},
            ],
        )

        with patch(
            "src.agents.workflows.engine.resolve_model", new=_model_stub(test_model)
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            await build_workflow(
                defn=defn,
                db=db_session,
                tenant_id=test_tenant.id,
                extra_tools=pool,
            )

        assert mock_build.call_args_list[0][1]["tools"] == pool
        assert mock_build.call_args_list[1][1]["tools"] == pool


class TestShippedDefinitionPortability:
    """web_research_report resolves for a tenant that satisfies its roles."""

    async def test_shipped_definition_builds_for_bound_tenant(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )

        defn = load_workflow_definition(web_research_report)
        web = _tool_stub("web_search")

        with patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            workflow = await build_workflow(
                defn=defn,
                db=db_session,
                tenant_id=test_tenant.id,
                extra_tools=[web],
            )

        executor_ids = [e.id for e in workflow.get_executors_list()]
        assert executor_ids == ["research", "report"]

        # Step 1 is restricted to @web_search; step 2 inherits the pool.
        assert mock_build.call_args_list[0][1]["tools"] == [web]
        assert mock_build.call_args_list[1][1]["tools"] == [web]

    async def test_shipped_definition_fails_without_its_tool(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        """The shipped definition is portable only to tenants that have the
        tool it references — it must not silently run without it."""
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )

        defn = load_workflow_definition(web_research_report)

        with patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            with pytest.raises(ValidationError, match=r"@web_search"):
                await build_workflow(
                    defn=defn,
                    db=db_session,
                    tenant_id=test_tenant.id,
                    extra_tools=[],
                )


def _model_stub(model: Model):
    """Return an async resolver returning *model* for any reference."""
    from unittest.mock import AsyncMock

    return AsyncMock(return_value=model)
