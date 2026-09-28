# =============================================================================
# PH Agent Hub - Workflow Tool Reference Resolution Acceptance Tests
# =============================================================================
# Against the real test database: a tool role resolves through the tenant's
# enabled tools.type rows, and a concrete ref through name-or-type.
# =============================================================================

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, patch

from src.agents.workflows import web_research_report
from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.engine import build_workflow, load_workflow_definition
from src.core.exceptions import ValidationError
from src.db.orm.models import Model
from src.db.orm.tenants import Tenant
from src.db.orm.tools import Tool
from src.services.model_role_service import set_role_bindings
from src.services.workflow_reference_service import validate_definition_references
from src.tools.web_search import build_web_search_tools

pytestmark = [pytest.mark.integration]

#: The two legitimate diagnostics for an unbound ``@web_search`` role.  Which
#: one is reported depends on the database, not on the code under test: when
#: another tenant owns a ``web_search`` tool (as in a shared development
#: database) the reference resolves to the cross-tenant diagnostic, otherwise
#: the role is simply unbound for this tenant.
_WEB_SEARCH_ROLE_UNRESOLVED = (
    "no enabled tool of type 'web_search' for role '@web_search' in this tenant",
    "belongs to a different tenant",
)


def _defn(tool_refs):
    return WorkflowDefinition(
        key="tool_reference_acceptance",
        name="Tool Reference Acceptance",
        steps=[
            {
                "id": "research",
                "name": "Research",
                "type": "inline",
                "instructions": "I1",
                "tool_refs": tool_refs,
            },
        ],
    )


def _tool(tenant_id, *, name, type, enabled=True):
    return Tool(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=name,
        type=type,
        category="general",
        config=None,
        enabled=enabled,
    )


class TestToolRoleResolution:
    """A tool role resolves through the tenant enabled tools.type rows."""

    async def test_role_resolves_via_tool_type_not_name(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        db_session.add(_tool(test_tenant.id, name="Web Search", type="web_search"))
        await db_session.flush()

        issues = await validate_definition_references(
            db_session, test_tenant.id, _defn(["@web_search"])
        )

        assert issues == []

    async def test_display_name_coincidence_is_not_enough(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        db_session.add(_tool(test_tenant.id, name="web_search", type="calculator"))
        await db_session.flush()

        issues = await validate_definition_references(
            db_session, test_tenant.id, _defn(["@web_search"])
        )

        assert [i.reference for i in issues] == ["@web_search"]
        assert issues[0].reason in _WEB_SEARCH_ROLE_UNRESOLVED

    async def test_missing_role_tool_reports_the_role(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        issues = await validate_definition_references(
            db_session, test_tenant.id, _defn(["@web_search"])
        )

        assert len(issues) == 1
        assert issues[0].reference == "@web_search"
        assert issues[0].reason in _WEB_SEARCH_ROLE_UNRESOLVED

    async def test_disabled_role_tool_reports_disabled(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        db_session.add(
            _tool(test_tenant.id, name="Web Search", type="web_search", enabled=False)
        )
        await db_session.flush()

        issues = await validate_definition_references(
            db_session, test_tenant.id, _defn(["@web_search"])
        )

        assert len(issues) == 1
        assert "disabled" in issues[0].reason

    async def test_role_tool_in_another_tenant_reports_foreign(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        other = Tenant(id=str(uuid.uuid4()), name=f"Other {uuid.uuid4().hex[:8]}")
        db_session.add(other)
        await db_session.flush()
        db_session.add(_tool(other.id, name="Web Search", type="web_search"))
        await db_session.flush()

        issues = await validate_definition_references(
            db_session, test_tenant.id, _defn(["@web_search"])
        )

        assert len(issues) == 1
        assert issues[0].reason == "belongs to a different tenant"


class TestConcreteToolResolution:
    """A concrete ref matches the tenant row name or its type."""

    async def test_concrete_ref_matches_name_or_type(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        db_session.add(_tool(test_tenant.id, name="Web Search", type="web_search"))
        await db_session.flush()

        assert (
            await validate_definition_references(
                db_session, test_tenant.id, _defn(["web_search"])
            )
            == []
        )
        assert (
            await validate_definition_references(
                db_session, test_tenant.id, _defn(["Web Search"])
            )
            == []
        )

    async def test_unknown_concrete_ref_is_reported(
        self, db_session: AsyncSession, test_tenant: Tenant
    ):
        issues = await validate_definition_references(
            db_session, test_tenant.id, _defn(["nope"])
        )

        assert [i.reference for i in issues] == ["nope"]


class TestShippedDefinitionEndToEnd:
    """The reported failure: the shipped workflow must build for a tenant
    that has the Web Search tool enabled, with reference validation live."""

    async def test_shipped_definition_builds_with_enabled_tool(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
        await set_role_bindings(
            db_session, test_tenant.id, "@reasoning", [test_model.id]
        )
        db_session.add(_tool(test_tenant.id, name="Web Search", type="web_search"))
        await db_session.flush()

        defn = load_workflow_definition(web_research_report)

        with patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()

            workflow = await build_workflow(
                defn=defn,
                db=db_session,
                tenant_id=test_tenant.id,
                extra_tools=build_web_search_tools(),
            )

        assert [e.id for e in workflow.get_executors_list()] == ["research", "report"]
        step_tools = mock_build.call_args_list[0][1]["tools"]
        assert [getattr(t, "name", None) for t in step_tools] == ["web_search"]

    async def test_shipped_definition_fails_without_enabled_tool(
        self, db_session: AsyncSession, test_tenant: Tenant, test_model: Model
    ):
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
                    extra_tools=build_web_search_tools(),
                )
