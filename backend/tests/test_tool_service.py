# =============================================================================
# PH Agent Hub — Tool Service Tests
# =============================================================================

import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError, ValidationError
from src.db.orm.tools import Tool
from src.db.orm.groups import ToolGroup, UserGroup, UserGroupMember
from src.db.orm.skills import SkillAllowedTool
from src.db.orm.sessions import SessionActiveTool
from src.db.orm.user_tool_preferences import UserToolPreference
from src.db.orm.users import User
from src.services.tool_service import (
    derive_tool_category,
    list_tools,
    get_tool_by_id,
    create_tool,
    update_tool,
    delete_tool,
    VALID_TOOL_TYPES,
)

pytestmark = [pytest.mark.integration]


class TestDeriveToolCategory:
    """Tests for derive_tool_category (pure function)."""

    pytestmark = [pytest.mark.unit]

    def test_known_type(self):
        """Should return the correct category for a known type."""
        assert derive_tool_category("stock_data") == "financial"
        assert derive_tool_category("email") == "communication"
        assert derive_tool_category("mcp") == "mcp"
        assert derive_tool_category("calculator") == "utility"
        assert derive_tool_category("web_search") == "web"
        assert derive_tool_category("slack") == "communication"

    def test_unknown_type_falls_to_general(self):
        """Should return 'general' for unknown types."""
        assert derive_tool_category("nonexistent_type") == "general"

    def test_all_valid_types_have_category(self):
        """Every type in VALID_TOOL_TYPES should map to a non-'general' category."""
        for t in VALID_TOOL_TYPES:
            cat = derive_tool_category(t)
            assert cat != "general", f"Type '{t}' maps to 'general' — add it to TOOL_TYPE_TO_CATEGORY"


class TestListTools:
    """Tests for list_tools."""

    async def test_empty_db(self, db_session: AsyncSession, test_tenant):
        """Should return empty list when no tools exist."""
        items, total = await list_tools(db_session, tenant_id=test_tenant.id)
        assert items == []
        assert total == 0

    async def test_multiple_tools(self, db_session: AsyncSession, test_tenant):
        """Should return all tools for the tenant."""
        t1 = Tool(tenant_id=test_tenant.id, name="Tool A", type="calculator", category="utility")
        t2 = Tool(tenant_id=test_tenant.id, name="Tool B", type="web_search", category="web")
        db_session.add_all([t1, t2])
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id)
        assert total == 2
        assert {t.id for t in items} == {t1.id, t2.id}

    async def test_search(self, db_session: AsyncSession, test_tenant):
        """Should filter tools by search term on name."""
        db_session.add_all([
            Tool(tenant_id=test_tenant.id, name="Weather Tool", type="weather", category="utility"),
            Tool(tenant_id=test_tenant.id, name="Stock Fetcher", type="stock_data", category="financial"),
        ])
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id, search="Weather")
        assert total == 1
        assert items[0].name == "Weather Tool"

    async def test_filter_by_type(self, db_session: AsyncSession, test_tenant):
        """Should filter by type."""
        db_session.add_all([
            Tool(tenant_id=test_tenant.id, name="Calc", type="calculator", category="utility"),
            Tool(tenant_id=test_tenant.id, name="Search", type="web_search", category="web"),
        ])
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id, type="calculator")
        assert total == 1
        assert items[0].name == "Calc"

    async def test_filter_by_category(self, db_session: AsyncSession, test_tenant):
        """Should filter by category."""
        db_session.add_all([
            Tool(tenant_id=test_tenant.id, name="Calc", type="calculator", category="utility"),
            Tool(tenant_id=test_tenant.id, name="Weather", type="weather", category="utility"),
            Tool(tenant_id=test_tenant.id, name="Search", type="web_search", category="web"),
        ])
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id, category="utility")
        assert total == 2

    async def test_pagination(self, db_session: AsyncSession, test_tenant):
        """Should paginate results."""
        for i in range(5):
            db_session.add(Tool(tenant_id=test_tenant.id, name=f"T{i}", type="calculator", category="utility"))
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id, page=1, page_size=2)
        assert total == 5
        assert len(items) == 2

    async def test_filter_by_enabled(self, db_session: AsyncSession, test_tenant):
        """Should filter by enabled status."""
        db_session.add_all([
            Tool(tenant_id=test_tenant.id, name="Enabled", type="calculator", category="utility", enabled=True),
            Tool(tenant_id=test_tenant.id, name="Disabled", type="web_search", category="web", enabled=False),
        ])
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id, enabled=True)
        assert total == 1
        assert items[0].name == "Enabled"

    async def test_user_visibility_public_and_group(self, db_session: AsyncSession, test_tenant, test_user: User):
        """User should see public tools + tools in their groups."""
        group = UserGroup(tenant_id=test_tenant.id, name="Test Group")
        db_session.add(group)
        await db_session.flush()

        public_tool = Tool(tenant_id=test_tenant.id, name="Public", type="calculator", category="utility", is_public=True)
        group_tool = Tool(tenant_id=test_tenant.id, name="Group Only", type="web_search", category="web", is_public=False)
        hidden_tool = Tool(tenant_id=test_tenant.id, name="Hidden", type="weather", category="utility", is_public=False)
        db_session.add_all([public_tool, group_tool, hidden_tool])
        await db_session.flush()

        # Add user to group and group_tool to group
        db_session.add(UserGroupMember(group_id=group.id, user_id=test_user.id))
        db_session.add(ToolGroup(group_id=group.id, tool_id=group_tool.id))
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id, user_id=test_user.id)
        names = {t.name for t in items}
        assert "Public" in names
        assert "Group Only" in names
        assert "Hidden" not in names

    async def test_filter_by_is_public(self, db_session: AsyncSession, test_tenant):
        """Should filter by is_public flag."""
        db_session.add_all([
            Tool(tenant_id=test_tenant.id, name="Public", type="calculator", category="utility", is_public=True),
            Tool(tenant_id=test_tenant.id, name="Private", type="web_search", category="web", is_public=False),
        ])
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id, is_public=True)
        assert total == 1
        assert items[0].name == "Public"

    async def test_sort_by_name(self, db_session: AsyncSession, test_tenant):
        """Should sort tools by name."""
        db_session.add_all([
            Tool(tenant_id=test_tenant.id, name="Beta", type="calculator", category="utility"),
            Tool(tenant_id=test_tenant.id, name="Alpha", type="web_search", category="web"),
        ])
        await db_session.flush()

        items, total = await list_tools(db_session, tenant_id=test_tenant.id, sort_by="name", sort_dir="asc")
        assert total == 2
        assert items[0].name == "Alpha"
        assert items[1].name == "Beta"


class TestGetToolById:
    """Tests for get_tool_by_id."""

    async def test_existing(self, db_session: AsyncSession, test_tool: Tool):
        """Should return the tool when it exists."""
        result = await get_tool_by_id(db_session, test_tool.id)
        assert result is not None
        assert result.id == test_tool.id

    async def test_nonexistent(self, db_session: AsyncSession):
        """Should return None when tool does not exist."""
        result = await get_tool_by_id(db_session, str(uuid.uuid4()))
        assert result is None


class TestCreateTool:
    """Tests for create_tool."""

    async def test_success(self, db_session: AsyncSession, test_tenant):
        """Should create a tool with auto-derived category."""
        tool = await create_tool(
            db_session,
            tenant_id=test_tenant.id,
            name="My Tool",
            type="calculator",
        )
        assert tool.name == "My Tool"
        assert tool.type == "calculator"
        assert tool.category == "utility"
        assert tool.enabled is True
        assert tool.is_public is False

    async def test_invalid_type_raises_validation_error(self, db_session: AsyncSession, test_tenant):
        """Should raise ValidationError for invalid tool type."""
        with pytest.raises(ValidationError, match="Invalid tool type"):
            await create_tool(
                db_session,
                tenant_id=test_tenant.id,
                name="Bad Tool",
                type="nonexistent_type",
            )

    async def test_auto_derives_category(self, db_session: AsyncSession, test_tenant):
        """Should auto-derive category from type."""
        tool = await create_tool(
            db_session,
            tenant_id=test_tenant.id,
            name="Stock Tool",
            type="stock_data",
        )
        assert tool.category == "financial"

    async def test_with_config(self, db_session: AsyncSession, test_tenant):
        """Should create a tool with config."""
        config = {"api_key": "test123"}
        tool = await create_tool(
            db_session,
            tenant_id=test_tenant.id,
            name="Config Tool",
            type="web_search",
            config=config,
        )
        assert tool.config == config

    async def test_with_code(self, db_session: AsyncSession, test_tenant):
        """Should create a custom tool with code."""
        tool = await create_tool(
            db_session,
            tenant_id=test_tenant.id,
            name="Custom Tool",
            type="custom",
            code="def run(): pass",
        )
        assert tool.code == "def run(): pass"
        assert tool.category == "custom"


class TestUpdateTool:
    """Tests for update_tool."""

    async def test_update_name(self, db_session: AsyncSession, test_tool: Tool):
        """Should update the tool name."""
        updated = await update_tool(db_session, test_tool.id, name="Renamed")
        assert updated.name == "Renamed"

    async def test_update_enabled(self, db_session: AsyncSession, test_tool: Tool):
        """Should toggle the enabled flag."""
        updated = await update_tool(db_session, test_tool.id, enabled=False)
        assert updated.enabled is False

    async def test_update_type_changes_category(self, db_session: AsyncSession, test_tool: Tool):
        """Should auto-update category when type changes."""
        updated = await update_tool(db_session, test_tool.id, type="email")
        assert updated.type == "email"
        assert updated.category == "communication"

    async def test_update_config(self, db_session: AsyncSession, test_tool: Tool):
        """Should update the config."""
        config = {"key": "value"}
        updated = await update_tool(db_session, test_tool.id, config=config)
        assert updated.config == config

    async def test_invalid_type_raises(self, db_session: AsyncSession, test_tool: Tool):
        """Should raise ValidationError when setting an invalid type."""
        with pytest.raises(ValidationError, match="Invalid tool type"):
            await update_tool(db_session, test_tool.id, type="bad_type")

    async def test_nonexistent(self, db_session: AsyncSession):
        """Should raise NotFoundError when tool does not exist."""
        with pytest.raises(NotFoundError, match="Tool not found"):
            await update_tool(db_session, str(uuid.uuid4()), name="Nope")


class TestDeleteTool:
    """Tests for delete_tool."""

    async def test_delete_existing(self, db_session: AsyncSession, test_tool: Tool):
        """Should delete the tool."""
        await delete_tool(db_session, test_tool.id)

        result = await db_session.execute(
            select(Tool).where(Tool.id == test_tool.id)
        )
        assert result.scalar_one_or_none() is None

    async def test_nonexistent(self, db_session: AsyncSession):
        """Should raise NotFoundError when tool does not exist."""
        with pytest.raises(NotFoundError, match="Tool not found"):
            await delete_tool(db_session, str(uuid.uuid4()))

    async def test_cascade_group_assignments(self, db_session: AsyncSession, test_tool: Tool, test_tenant):
        """Should delete ToolGroup entries referencing the tool."""
        group = UserGroup(tenant_id=test_tenant.id, name="Group")
        db_session.add(group)
        await db_session.flush()
        db_session.add(ToolGroup(group_id=group.id, tool_id=test_tool.id))
        await db_session.flush()

        await delete_tool(db_session, test_tool.id)

        tg_result = await db_session.execute(
            select(ToolGroup).where(ToolGroup.tool_id == test_tool.id)
        )
        assert tg_result.scalar_one_or_none() is None

    async def test_cascade_skill_links(self, db_session: AsyncSession, test_tool: Tool, test_skill):
        """Should delete SkillAllowedTool entries referencing the tool."""
        db_session.add(SkillAllowedTool(skill_id=test_skill.id, tool_id=test_tool.id))
        await db_session.flush()

        await delete_tool(db_session, test_tool.id)

        sat_result = await db_session.execute(
            select(SkillAllowedTool).where(SkillAllowedTool.tool_id == test_tool.id)
        )
        assert sat_result.scalar_one_or_none() is None

    async def test_cascade_session_links(self, db_session: AsyncSession, test_tool: Tool, test_session):
        """Should delete SessionActiveTool entries referencing the tool."""
        db_session.add(SessionActiveTool(session_id=test_session.id, tool_id=test_tool.id))
        await db_session.flush()

        await delete_tool(db_session, test_tool.id)

        sat_result = await db_session.execute(
            select(SessionActiveTool).where(SessionActiveTool.tool_id == test_tool.id)
        )
        assert sat_result.scalar_one_or_none() is None

    async def test_cascade_user_preferences(self, db_session: AsyncSession, test_tool: Tool, test_user: User):
        """Should delete UserToolPreference entries referencing the tool."""
        db_session.add(UserToolPreference(user_id=test_user.id, tool_id=test_tool.id))
        await db_session.flush()

        await delete_tool(db_session, test_tool.id)

        utp_result = await db_session.execute(
            select(UserToolPreference).where(UserToolPreference.tool_id == test_tool.id)
        )
        assert utp_result.scalar_one_or_none() is None

class TestSubagentConfigValidation:
    """Tests for _validate_subagent_config and subagent tool create/update validation."""

    async def test_valid_config_accepted(self, db_session: AsyncSession, test_tenant):
        """Should accept a subagent tool with a valid config."""
        config = {
            "instructions": "You are a subagent.",
            "model_role": "@reasoning",
        }
        tool = await create_tool(
            db_session,
            tenant_id=test_tenant.id,
            name="Valid Subagent",
            type="subagent",
            config=config,
        )
        assert tool.type == "subagent"
        assert tool.config == config

    async def test_missing_config_rejected(self, db_session: AsyncSession, test_tenant):
        """Should reject a subagent tool with a missing config."""
        with pytest.raises(ValidationError, match="config object"):
            await create_tool(
                db_session,
                tenant_id=test_tenant.id,
                name="Missing Config",
                type="subagent",
                config=None,
            )

    async def test_empty_instructions_rejected(self, db_session: AsyncSession, test_tenant):
        """Should reject a subagent tool with empty instructions."""
        config = {
            "instructions": "",
            "model_role": "@reasoning",
        }
        with pytest.raises(ValidationError, match="non-empty 'instructions'"):
            await create_tool(
                db_session,
                tenant_id=test_tenant.id,
                name="Empty Instructions",
                type="subagent",
                config=config,
            )

    async def test_unknown_model_role_rejected(self, db_session: AsyncSession, test_tenant):
        """Should reject a subagent tool with an unknown model role."""
        config = {
            "instructions": "You are a subagent.",
            "model_role": "unknown_role",
        }
        with pytest.raises(ValidationError, match="model_role"):
            await create_tool(
                db_session,
                tenant_id=test_tenant.id,
                name="Unknown Role",
                type="subagent",
                config=config,
            )

    async def test_concrete_model_id_rejected(self, db_session: AsyncSession, test_tenant):
        """Should reject a subagent tool with a concrete model id."""
        config = {
            "instructions": "You are a subagent.",
            "model_role": "gpt-4o",
        }
        with pytest.raises(ValidationError, match="model_role"):
            await create_tool(
                db_session,
                tenant_id=test_tenant.id,
                name="Concrete Model",
                type="subagent",
                config=config,
            )

    async def test_tool_deny_must_be_string_list(self, db_session: AsyncSession, test_tenant):
        """Should reject a subagent tool with a non-string tool_deny list."""
        config = {
            "instructions": "You are a subagent.",
            "model_role": "@reasoning",
            "tool_deny": "not-a-list",
        }
        with pytest.raises(ValidationError, match="tool_deny"):
            await create_tool(
                db_session,
                tenant_id=test_tenant.id,
                name="Bad Tool Deny",
                type="subagent",
                config=config,
            )

    async def test_update_subagent_config_validated(self, db_session: AsyncSession, test_tenant):
        """Should validate subagent config on update and accept valid config."""
        tool = await create_tool(
            db_session,
            tenant_id=test_tenant.id,
            name="Subagent Tool",
            type="subagent",
            config={
                "instructions": "Original instructions.",
                "model_role": "@reasoning",
            },
        )
        await db_session.flush()

        updated = await update_tool(
            db_session,
            tool.id,
            config={
                "instructions": "Updated instructions.",
                "model_role": "@general",
            },
        )
        assert updated.type == "subagent"
        assert updated.config["instructions"] == "Updated instructions."
        assert updated.config["model_role"] == "@general"

        # Invalid config on update should be rejected.
        with pytest.raises(ValidationError, match="non-empty 'instructions'"):
            await update_tool(
                db_session,
                tool.id,
                config={"instructions": "", "model_role": "@reasoning"},
            )

    async def test_non_subagent_unaffected(self, db_session: AsyncSession, test_tenant):
        """Should not validate config for non-subagent tools."""
        tool = await create_tool(
            db_session,
            tenant_id=test_tenant.id,
            name="Regular Tool",
            type="calculator",
            config={"instructions": "", "model_role": "gpt-4o"},
        )
        await db_session.flush()

        # A calculator tool with a malformed config should not raise.
        updated = await update_tool(
            db_session,
            tool.id,
            config={"instructions": "", "model_role": "gpt-4o"},
        )
        assert updated.type == "calculator"
