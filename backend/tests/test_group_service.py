# =============================================================================
# PH Agent Hub — Group Service Tests (Idempotent Operations)
# =============================================================================
# Tests that duplicate operations (adding the same member/model/tool to a
# group twice) return the existing row instead of crashing with a
# cursor-consumption error (Issue #348).
# =============================================================================

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.orm.groups import ModelGroup, ToolGroup, UserGroup, UserGroupMember
from src.db.orm.models import Model
from src.db.orm.tools import Tool
from src.db.orm.users import User
import src.services.group_service as group_service
from src.services.group_service import (
    add_member,
    assign_model_to_group,
    assign_tool_to_group,
    create_group,
)

pytestmark = [
    pytest.mark.regression,
    pytest.mark.integration,
]


class TestGroupIdempotentOperations:
    """Verify duplicate adds return existing rows without error."""

    async def test_add_member_idempotent(
        self, db_session: AsyncSession, test_tenant, test_user
    ):
        """Call add_member twice with same args — second call returns existing."""
        group = await create_group(
            db_session, tenant_id=test_tenant.id, name="Idempotent Group"
        )
        # First call — create
        first = await add_member(db_session, group.id, test_user.id)
        assert first.user_id == test_user.id
        assert first.group_id == group.id

        # Second call — should return existing, not raise
        second = await add_member(db_session, group.id, test_user.id)
        assert second.user_id == test_user.id
        assert second.group_id == group.id
        assert second.user_id == first.user_id
        assert second.group_id == first.group_id  # same composite-PK row

    async def test_assign_model_to_group_idempotent(
        self, db_session: AsyncSession, test_tenant
    ):
        """Call assign_model_to_group twice — second call returns existing."""
        group = await create_group(
            db_session, tenant_id=test_tenant.id, name="Model Group"
        )
        model = Model(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            name="Idempotent Test Model",
            model_id="test-model",
            provider="openai",
            api_key="test-key",
            max_tokens=4096,
            temperature=0.7,
        )
        db_session.add(model)
        await db_session.flush()

        # First call — create
        first = await assign_model_to_group(db_session, group.id, model.id)
        assert first.model_id == model.id
        assert first.group_id == group.id

        # Second call — should return existing, not raise
        second = await assign_model_to_group(db_session, group.id, model.id)
        assert second.model_id == model.id
        assert second.group_id == group.id
        assert second.model_id == first.model_id
        assert second.group_id == first.group_id  # same composite-PK row

    async def test_assign_tool_to_group_idempotent(
        self, db_session: AsyncSession, test_tenant
    ):
        """Call assign_tool_to_group twice — second call returns existing."""
        group = await create_group(
            db_session, tenant_id=test_tenant.id, name="Tool Group"
        )
        tool = Tool(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            name="Idempotent Test Tool",
            type="datetime",
            category="general",
            config={},
        )
        db_session.add(tool)
        await db_session.flush()

        # First call — create
        first = await assign_tool_to_group(db_session, group.id, tool.id)
        assert first.tool_id == tool.id
        assert first.group_id == group.id

        # Second call — should return existing, not raise
        second = await assign_tool_to_group(db_session, group.id, tool.id)
        assert second.tool_id == tool.id
        assert second.group_id == group.id
        assert second.tool_id == first.tool_id
        assert second.group_id == first.group_id  # same composite-PK row


# =============================================================================
# Group CRUD Tests
# =============================================================================


class TestGroupCRUD:
    """Tests for list_groups, get_group_by_id, create_group, update_group."""

    async def test_list_groups(
        self, db_session: AsyncSession, test_tenant, test_group: UserGroup
    ):
        """list_groups returns paginated groups, filterable by tenant."""
        # All groups
        groups, total = await group_service.list_groups(db_session)
        assert total >= 1

        # Filter by tenant
        groups, total = await group_service.list_groups(
            db_session, tenant_id=test_tenant.id
        )
        assert total >= 1
        for g in groups:
            assert g.tenant_id == test_tenant.id

    async def test_get_group_by_id(
        self, db_session: AsyncSession, test_group: UserGroup
    ):
        """get_group_by_id returns group or None."""
        found = await group_service.get_group_by_id(db_session, test_group.id)
        assert found is not None
        assert found.id == test_group.id

        missing = await group_service.get_group_by_id(db_session, "nonexistent-id")
        assert missing is None

    async def test_create_group(
        self, db_session: AsyncSession, test_tenant
    ):
        """create_group creates with correct tenant_id and name."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="New Group"
        )
        assert group.name == "New Group"
        assert group.tenant_id == test_tenant.id

    async def test_update_group(
        self, db_session: AsyncSession, test_group: UserGroup
    ):
        """update_group updates name; nonexistent raises NotFoundError."""
        updated = await group_service.update_group(
            db_session, test_group.id, "Updated Group Name"
        )
        assert updated.name == "Updated Group Name"

        with pytest.raises(NotFoundError):
            await group_service.update_group(
                db_session, "nonexistent-id", "Any Name"
            )

    async def test_create_group_duplicate_name(
        self, db_session: AsyncSession, test_tenant
    ):
        """Duplicate group names in the same tenant are allowed (no uniqueness enforcement)."""
        g1 = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Same Name"
        )
        g2 = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Same Name"
        )
        assert g1.id != g2.id
        assert g1.name == g2.name


class TestDeleteGroup:
    """Tests for delete_group — verifies cascade deletion of all junction tables."""

    async def test_delete_group_with_members_and_assignments(
        self, db_session: AsyncSession, test_tenant, test_user, test_model, test_tool
    ):
        """Deleting a group cascades members, model assignments, AND tool assignments."""
        # Create a fresh group
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Full Group"
        )

        # Add member
        await group_service.add_member(db_session, group.id, test_user.id)
        # Assign model
        await group_service.assign_model_to_group(
            db_session, group.id, test_model.id
        )
        # Assign tool
        await group_service.assign_tool_to_group(
            db_session, group.id, test_tool.id
        )

        # Verify all three exist
        member_count = await db_session.execute(
            select(UserGroupMember).where(UserGroupMember.group_id == group.id)
        )
        assert len(list(member_count.scalars().all())) == 1
        model_count = await db_session.execute(
            select(ModelGroup).where(ModelGroup.group_id == group.id)
        )
        assert len(list(model_count.scalars().all())) == 1
        tool_count = await db_session.execute(
            select(ToolGroup).where(ToolGroup.group_id == group.id)
        )
        assert len(list(tool_count.scalars().all())) == 1

        # Delete the group
        await group_service.delete_group(db_session, group.id)

        # Verify all three junction tables are cleaned up
        member_after = await db_session.execute(
            select(UserGroupMember).where(UserGroupMember.group_id == group.id)
        )
        assert len(list(member_after.scalars().all())) == 0
        model_after = await db_session.execute(
            select(ModelGroup).where(ModelGroup.group_id == group.id)
        )
        assert len(list(model_after.scalars().all())) == 0
        tool_after = await db_session.execute(
            select(ToolGroup).where(ToolGroup.group_id == group.id)
        )
        assert len(list(tool_after.scalars().all())) == 0

        # Group itself is gone
        group_after = await group_service.get_group_by_id(db_session, group.id)
        assert group_after is None

    async def test_delete_nonexistent_group(self, db_session: AsyncSession):
        """Non-existent group raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await group_service.delete_group(db_session, "nonexistent-id")


class TestMemberManagement:
    """Tests for add_member, remove_member, list_group_members, list_user_groups."""

    async def test_add_member(
        self, db_session: AsyncSession, test_tenant, test_user
    ):
        """add_member adds user to group."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Member Test Group"
        )
        member = await group_service.add_member(
            db_session, group.id, test_user.id
        )
        assert member.user_id == test_user.id
        assert member.group_id == group.id

    async def test_add_member_nonexistent_group(
        self, db_session: AsyncSession, test_user
    ):
        """Non-existent group raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await group_service.add_member(
                db_session, "nonexistent-id", test_user.id
            )

    async def test_add_member_nonexistent_user(
        self, db_session: AsyncSession, test_tenant
    ):
        """Non-existent user raises NotFoundError."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="User Check Group"
        )
        with pytest.raises(NotFoundError):
            await group_service.add_member(
                db_session, group.id, "nonexistent-user-id"
            )

    async def test_add_member_cross_tenant(
        self, db_session: AsyncSession, test_tenant, second_user
    ):
        """Cross-tenant user raises ForbiddenError."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Cross Tenant Group"
        )
        with pytest.raises(ForbiddenError, match="different tenant"):
            await group_service.add_member(
                db_session, group.id, second_user.id
            )

    async def test_remove_member(
        self, db_session: AsyncSession, test_tenant, test_user
    ):
        """remove_member removes user; no-op if not a member."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Remove Test Group"
        )
        await group_service.add_member(db_session, group.id, test_user.id)

        # Remove
        await group_service.remove_member(db_session, group.id, test_user.id)
        members = await group_service.list_group_members(db_session, group.id)
        assert test_user.id not in [m.id for m in members]

        # No-op on non-member
        await group_service.remove_member(
            db_session, group.id, "nonexistent-id"
        )
        # No error

    async def test_list_group_members(
        self, db_session: AsyncSession, test_tenant, test_user, admin_user
    ):
        """list_group_members returns users ordered by display_name."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Member List Group"
        )
        await group_service.add_member(db_session, group.id, test_user.id)
        await group_service.add_member(db_session, group.id, admin_user.id)

        members = await group_service.list_group_members(db_session, group.id)
        assert len(members) == 2
        # Ordered by display_name: "Admin User" < "Test User"
        assert members[0].id == admin_user.id
        assert members[1].id == test_user.id

        # Empty group
        empty_group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Empty Group"
        )
        empty_members = await group_service.list_group_members(
            db_session, empty_group.id
        )
        assert empty_members == []

    async def test_list_user_groups(
        self, db_session: AsyncSession, test_tenant, test_user
    ):
        """list_user_groups returns groups ordered by name."""
        g1 = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="B Group"
        )
        g2 = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="A Group"
        )
        await group_service.add_member(db_session, g1.id, test_user.id)
        await group_service.add_member(db_session, g2.id, test_user.id)

        groups = await group_service.list_user_groups(db_session, test_user.id)
        assert len(groups) == 2
        # Ordered by name: "A Group" < "B Group"
        assert groups[0].name == "A Group"
        assert groups[1].name == "B Group"

        # User in no groups
        no_groups = await group_service.list_user_groups(
            db_session, "nonexistent-id"
        )
        assert no_groups == []


class TestModelGroupAssignment:
    """Tests for model-to-group assignment functions."""

    async def test_assign_model_to_group(
        self, db_session: AsyncSession, test_tenant, test_model
    ):
        """Assigns a model to a group."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Model Assign Group"
        )
        mg = await group_service.assign_model_to_group(
            db_session, group.id, test_model.id
        )
        assert mg.model_id == test_model.id
        assert mg.group_id == group.id

    async def test_assign_model_nonexistent_group(
        self, db_session: AsyncSession, test_model
    ):
        """Non-existent group raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await group_service.assign_model_to_group(
                db_session, "nonexistent-group", test_model.id
            )

    async def test_assign_model_nonexistent_model(
        self, db_session: AsyncSession, test_tenant
    ):
        """Non-existent model raises NotFoundError."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Model Check Group"
        )
        with pytest.raises(NotFoundError):
            await group_service.assign_model_to_group(
                db_session, group.id, "nonexistent-model"
            )

    async def test_assign_model_cross_tenant(
        self, db_session: AsyncSession, test_tenant, second_tenant
    ):
        """Cross-tenant model raises ForbiddenError."""
        from src.db.orm.models import Model

        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Cross Tenant Model Group"
        )
        # Model in second_tenant
        other_model = Model(
            id=str(uuid.uuid4()),
            tenant_id=second_tenant.id,
            name="Other Tenant Model",
            model_id="other-model",
            provider="openai",
            api_key="test-key",
            max_tokens=4096,
            temperature=0.7,
        )
        db_session.add(other_model)
        await db_session.flush()

        with pytest.raises(ForbiddenError, match="different tenant"):
            await group_service.assign_model_to_group(
                db_session, group.id, other_model.id
            )

    async def test_remove_model_from_group(
        self, db_session: AsyncSession, test_tenant, test_model
    ):
        """Remove model from group; no-op if not assigned."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Model Remove Group"
        )
        await group_service.assign_model_to_group(
            db_session, group.id, test_model.id
        )
        await group_service.remove_model_from_group(
            db_session, group.id, test_model.id
        )
        models = await group_service.list_group_models(db_session, group.id)
        assert test_model.id not in [m.id for m in models]

        # No-op
        await group_service.remove_model_from_group(
            db_session, group.id, "nonexistent-model"
        )

    async def test_list_group_models(
        self, db_session: AsyncSession, test_tenant, test_model, test_deepseek_model
    ):
        """list_group_models returns models ordered by name."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Model List Group"
        )
        await group_service.assign_model_to_group(
            db_session, group.id, test_model.id
        )
        await group_service.assign_model_to_group(
            db_session, group.id, test_deepseek_model.id
        )

        models = await group_service.list_group_models(db_session, group.id)
        assert len(models) == 2
        # Ordered by name
        assert models[0].id == test_deepseek_model.id  # "Test DeepSeek"
        assert models[1].id == test_model.id  # "Test Model"

        # Empty group
        empty_group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Empty Model Group"
        )
        empty_models = await group_service.list_group_models(
            db_session, empty_group.id
        )
        assert empty_models == []

    async def test_list_model_groups(
        self, db_session: AsyncSession, test_tenant, test_model
    ):
        """list_model_groups returns groups ordered by name."""
        g1 = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Z Group"
        )
        g2 = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="A Group"
        )
        await group_service.assign_model_to_group(
            db_session, g1.id, test_model.id
        )
        await group_service.assign_model_to_group(
            db_session, g2.id, test_model.id
        )

        groups = await group_service.list_model_groups(
            db_session, test_model.id
        )
        assert len(groups) == 2
        assert groups[0].name == "A Group"
        assert groups[1].name == "Z Group"


class TestToolGroupAssignment:
    """Tests for tool-to-group assignment functions."""

    async def test_assign_tool_to_group(
        self, db_session: AsyncSession, test_tenant, test_tool
    ):
        """Assigns a tool to a group."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Tool Assign Group"
        )
        tg = await group_service.assign_tool_to_group(
            db_session, group.id, test_tool.id
        )
        assert tg.tool_id == test_tool.id
        assert tg.group_id == group.id

    async def test_assign_tool_nonexistent_group(
        self, db_session: AsyncSession, test_tool
    ):
        """Non-existent group raises NotFoundError."""
        with pytest.raises(NotFoundError):
            await group_service.assign_tool_to_group(
                db_session, "nonexistent-group", test_tool.id
            )

    async def test_assign_tool_nonexistent_tool(
        self, db_session: AsyncSession, test_tenant
    ):
        """Non-existent tool raises NotFoundError."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Tool Check Group"
        )
        with pytest.raises(NotFoundError):
            await group_service.assign_tool_to_group(
                db_session, group.id, "nonexistent-tool"
            )

    async def test_assign_tool_cross_tenant(
        self, db_session: AsyncSession, test_tenant, second_tenant
    ):
        """Cross-tenant tool raises ForbiddenError."""
        from src.db.orm.tools import Tool as ToolORM

        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Cross Tenant Tool Group"
        )
        other_tool = ToolORM(
            id=str(uuid.uuid4()),
            tenant_id=second_tenant.id,
            name="Other Tool",
            type="datetime",
            category="general",
            config={},
            enabled=True,
        )
        db_session.add(other_tool)
        await db_session.flush()

        with pytest.raises(ForbiddenError, match="different tenant"):
            await group_service.assign_tool_to_group(
                db_session, group.id, other_tool.id
            )

    async def test_remove_tool_from_group(
        self, db_session: AsyncSession, test_tenant, test_tool
    ):
        """Remove tool from group; no-op if not assigned."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Tool Remove Group"
        )
        await group_service.assign_tool_to_group(
            db_session, group.id, test_tool.id
        )
        await group_service.remove_tool_from_group(
            db_session, group.id, test_tool.id
        )
        tools = await group_service.list_group_tools(db_session, group.id)
        assert test_tool.id not in [t.id for t in tools]

        # No-op
        await group_service.remove_tool_from_group(
            db_session, group.id, "nonexistent-tool"
        )

    async def test_list_group_tools(
        self, db_session: AsyncSession, test_tenant, test_tool
    ):
        """list_group_tools returns tools ordered by name."""
        group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Tool List Group"
        )
        await group_service.assign_tool_to_group(
            db_session, group.id, test_tool.id
        )

        tools = await group_service.list_group_tools(db_session, group.id)
        assert len(tools) >= 1

        # Empty group
        empty_group = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Empty Tool Group"
        )
        empty_tools = await group_service.list_group_tools(
            db_session, empty_group.id
        )
        assert empty_tools == []

    async def test_list_tool_groups(
        self, db_session: AsyncSession, test_tenant, test_tool
    ):
        """list_tool_groups returns groups ordered by name."""
        g1 = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="Z Tool Group"
        )
        g2 = await group_service.create_group(
            db_session, tenant_id=test_tenant.id, name="A Tool Group"
        )
        await group_service.assign_tool_to_group(
            db_session, g1.id, test_tool.id
        )
        await group_service.assign_tool_to_group(
            db_session, g2.id, test_tool.id
        )

        groups = await group_service.list_tool_groups(
            db_session, test_tool.id
        )
        assert len(groups) == 2
        assert groups[0].name == "A Tool Group"
        assert groups[1].name == "Z Tool Group"
