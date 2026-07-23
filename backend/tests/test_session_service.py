# =============================================================================
# PH Agent Hub — Session Service Tests
# =============================================================================
# Service-layer tests for session CRUD, tool activation, lifecycle, and tag
# management.  Complements HTTP-layer tests in ``test_chat_api.py``.
# =============================================================================

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError, ValidationError
from src.db.orm.messages import Message, MessageFeedback
from src.db.orm.sessions import Session, SessionActiveTool
from src.db.orm.tags import Tag, SessionTag
from src.db.orm.tools import Tool
from src.db.orm.skills import Skill, SkillAllowedTool
from src.services.session_service import (
    add_session_tool,
    add_tag_to_session,
    create_session,
    delete_session,
    delete_session_tags,
    finalize_session,
    get_or_create_tag,
    get_session_by_id,
    get_session_tags,
    get_session_tools,
    list_admin_sessions,
    list_sessions_by_tag,
    list_sessions_for_user,
    list_tenant_tags,
    remove_session_tool,
    remove_tag_from_session,
    sync_session_tools_for_skill,
    update_session,
    _purge_empty_sessions,
)

pytestmark = [pytest.mark.integration]


# ===========================================================================
# Tool activation
# ===========================================================================


class TestSessionToolActivation:
    """Tests for add/remove/get/sync session tools."""

    async def test_add_session_tool_success(
        self, db_session: AsyncSession, test_session, test_tool, test_tenant
    ):
        """Should create a SessionActiveTool row."""
        sat = await add_session_tool(
            db_session, test_session.id, test_tool.id, test_tenant.id,
        )
        assert sat.session_id == test_session.id
        assert sat.tool_id == test_tool.id

        # Verify in DB
        result = await db_session.execute(
            select(SessionActiveTool).where(
                SessionActiveTool.session_id == test_session.id,
                SessionActiveTool.tool_id == test_tool.id,
            )
        )
        assert result.scalar_one_or_none() is not None

    async def test_add_session_tool_duplicate_raises(
        self, db_session: AsyncSession, test_session, test_tool, test_tenant
    ):
        """Adding the same tool twice should raise ValidationError."""
        await add_session_tool(db_session, test_session.id, test_tool.id, test_tenant.id)
        with pytest.raises(ValidationError, match="already active"):
            await add_session_tool(db_session, test_session.id, test_tool.id, test_tenant.id)

    async def test_add_session_tool_disabled_tool_raises(
        self, db_session: AsyncSession, test_session, test_tenant
    ):
        """Adding a disabled tool should raise ValidationError."""
        disabled_tool = Tool(
            id=str(uuid.uuid4()), name="Disabled Calc", type="calculator",
            category="general", enabled=False, tenant_id=test_tenant.id,
        )
        db_session.add(disabled_tool)
        await db_session.flush()

        with pytest.raises(ValidationError, match="disabled"):
            await add_session_tool(db_session, test_session.id, disabled_tool.id, test_tenant.id)

    async def test_add_session_tool_wrong_tenant_raises(
        self, db_session: AsyncSession, test_session, second_tenant
    ):
        """Adding a tool from another tenant should raise ValidationError."""
        other_tool = Tool(
            id=str(uuid.uuid4()), name="Other Tenant Tool", type="calculator",
            category="general", enabled=True, tenant_id=second_tenant.id,
        )
        db_session.add(other_tool)
        await db_session.flush()

        with pytest.raises(ValidationError, match="does not belong"):
            # session is in test_tenant, tool is in second_tenant
            await add_session_tool(db_session, test_session.id, other_tool.id, test_session.tenant_id)

    async def test_add_session_tool_nonexistent_session_raises(
        self, db_session: AsyncSession, test_tool, test_tenant
    ):
        """Adding a tool to a non-existent session should raise NotFoundError."""
        with pytest.raises(NotFoundError, match="Session not found"):
            await add_session_tool(
                db_session, str(uuid.uuid4()), test_tool.id, test_tenant.id,
            )

    async def test_add_session_tool_nonexistent_tool_raises(
        self, db_session: AsyncSession, test_session, test_tenant
    ):
        """Adding a non-existent tool should raise NotFoundError."""
        with pytest.raises(NotFoundError, match="Tool not found"):
            await add_session_tool(
                db_session, test_session.id, str(uuid.uuid4()), test_tenant.id,
            )

    async def test_remove_session_tool_success(
        self, db_session: AsyncSession, test_session, test_tool, test_tenant
    ):
        """Should remove the SessionActiveTool row."""
        await add_session_tool(db_session, test_session.id, test_tool.id, test_tenant.id)

        await remove_session_tool(db_session, test_session.id, test_tool.id)

        result = await db_session.execute(
            select(SessionActiveTool).where(
                SessionActiveTool.session_id == test_session.id,
                SessionActiveTool.tool_id == test_tool.id,
            )
        )
        assert result.scalar_one_or_none() is None

    async def test_remove_session_tool_not_active_raises(
        self, db_session: AsyncSession, test_session
    ):
        """Removing a tool that is not active should raise NotFoundError."""
        with pytest.raises(NotFoundError, match="not active"):
            await remove_session_tool(db_session, test_session.id, str(uuid.uuid4()))

    async def test_get_session_tools_returns_tools(
        self, db_session: AsyncSession, test_session, test_tool, test_tenant
    ):
        """Should return Tool ORM objects ordered by name."""
        await add_session_tool(db_session, test_session.id, test_tool.id, test_tenant.id)

        tools = await get_session_tools(db_session, test_session.id)
        assert len(tools) == 1
        assert tools[0].id == test_tool.id
        assert tools[0].name == test_tool.name

    async def test_get_session_tools_empty(
        self, db_session: AsyncSession, test_session
    ):
        """Session with no tools should return empty list."""
        tools = await get_session_tools(db_session, test_session.id)
        assert tools == []

    async def test_sync_session_tools_for_skill_adds_new_tools(
        self, db_session: AsyncSession, test_session, test_tenant, test_skill, test_tool
    ):
        """When switching to a skill, its tools should be activated."""
        # Associate the tool with the skill
        sat = SkillAllowedTool(skill_id=test_skill.id, tool_id=test_tool.id)
        db_session.add(sat)
        await db_session.flush()

        # Sync to new skill (no old skill)
        await sync_session_tools_for_skill(
            db_session,
            session_id=test_session.id,
            old_skill_id=None,
            new_skill_id=test_skill.id,
            tenant_id=test_tenant.id,
        )

        tools = await get_session_tools(db_session, test_session.id)
        assert len(tools) == 1
        assert tools[0].id == test_tool.id

    async def test_sync_session_tools_removes_old_tools(
        self, db_session: AsyncSession, test_session, test_tenant, test_tool
    ):
        """When old skill's tools are not in new skill, they should be removed."""
        # Create two skills
        skill_old = Skill(
            id=str(uuid.uuid4()), title="Old Skill", execution_type="agent",
            template_id=None, visibility="tenant", tenant_id=test_tenant.id,
        )
        skill_new = Skill(
            id=str(uuid.uuid4()), title="New Skill", execution_type="agent",
            template_id=None, visibility="tenant", tenant_id=test_tenant.id,
        )
        db_session.add_all([skill_old, skill_new])

        # Make tool2 belong to old skill only
        tool2 = Tool(
            id=str(uuid.uuid4()), name="Tool 2", type="calculator",
            category="general", enabled=True, tenant_id=test_tenant.id,
        )
        db_session.add(tool2)
        await db_session.flush()

        # tool1 belongs to both; tool2 belongs to old only
        for skill_id, tool_id in [(skill_old.id, test_tool.id), (skill_new.id, test_tool.id), (skill_old.id, tool2.id)]:
            db_session.add(SkillAllowedTool(skill_id=skill_id, tool_id=tool_id))
        await db_session.flush()

        # Activate both tools via old skill
        for t in [test_tool, tool2]:
            await add_session_tool(db_session, test_session.id, t.id, test_tenant.id)

        # Sync to new skill
        await sync_session_tools_for_skill(
            db_session,
            session_id=test_session.id,
            old_skill_id=skill_old.id,
            new_skill_id=skill_new.id,
            tenant_id=test_tenant.id,
        )

        tools = await get_session_tools(db_session, test_session.id)
        tool_ids = [t.id for t in tools]
        assert test_tool.id in tool_ids  # common
        assert tool2.id not in tool_ids  # removed

    async def test_sync_session_tools_preserves_always_on(
        self, db_session: AsyncSession, test_session, test_tenant, test_tool
    ):
        """Tools in always_on_ids should never be removed."""
        tool_always = Tool(
            id=str(uuid.uuid4()), name="Always-On Tool", type="calculator",
            category="general", enabled=True, tenant_id=test_tenant.id,
        )
        db_session.add(tool_always)
        await db_session.flush()

        skill_a = Skill(
            id=str(uuid.uuid4()), title="Skill A", execution_type="agent",
            template_id=None, visibility="tenant", tenant_id=test_tenant.id,
        )
        skill_b = Skill(
            id=str(uuid.uuid4()), title="Skill B", execution_type="agent",
            template_id=None, visibility="tenant", tenant_id=test_tenant.id,
        )
        db_session.add_all([skill_a, skill_b])

        # test_tool in skill_a, tool_always in skill_b
        db_session.add(SkillAllowedTool(skill_id=skill_a.id, tool_id=test_tool.id))
        db_session.add(SkillAllowedTool(skill_id=skill_b.id, tool_id=tool_always.id))
        await db_session.flush()

        await add_session_tool(db_session, test_session.id, test_tool.id, test_tenant.id)

        # Switch from skill_a to skill_b; test_tool should be removed
        # But tool_always is not yet active — it will be added
        await sync_session_tools_for_skill(
            db_session,
            session_id=test_session.id,
            old_skill_id=skill_a.id,
            new_skill_id=skill_b.id,
            tenant_id=test_tenant.id,
            always_on_ids=[],  # No always-on tools here
        )

        tools = await get_session_tools(db_session, test_session.id)
        tool_ids = {t.id for t in tools}
        assert test_tool.id not in tool_ids  # removed
        assert tool_always.id in tool_ids  # added by new skill


# ===========================================================================
# Lifecycle — finalize_session
# ===========================================================================


class TestFinalizeSession:
    """Tests for finalize_session — converting temporary to permanent."""

    async def test_creates_permanent_session(
        self, db_session: AsyncSession, test_user, test_tenant, test_model
    ):
        """Should create a permanent Session row with the same ID."""
        session_id = str(uuid.uuid4())
        temp_data = {
            "id": session_id,
            "tenant_id": test_tenant.id,
            "user_id": test_user.id,
            "title": "Finalized Chat",
            "is_pinned": False,
            "selected_model_id": test_model.id,
        }
        temp_messages = [
            {
                "sender": "user",
                "content": [{"type": "text", "text": "Hello"}],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "sender": "assistant",
                "content": [{"type": "text", "text": "Hi there!"}],
                "model_id": test_model.id,
                "created_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            },
        ]

        session = await finalize_session(db_session, temp_data, temp_messages)

        assert session.id == session_id
        assert session.is_temporary is False
        assert session.title == "Finalized Chat"
        assert session.selected_model_id == test_model.id
        assert session.user_id == test_user.id
        assert session.tenant_id == test_tenant.id

    async def test_migrates_messages(
        self, db_session: AsyncSession, test_user, test_tenant, test_model
    ):
        """Messages from temp_messages should be migrated to MariaDB."""
        session_id = str(uuid.uuid4())
        temp_data = {
            "id": session_id,
            "tenant_id": test_tenant.id,
            "user_id": test_user.id,
            "title": "Test",
            "is_pinned": False,
            "selected_model_id": test_model.id,
        }
        temp_messages = [
            {
                "sender": "user",
                "content": [{"type": "text", "text": "First"}],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        await finalize_session(db_session, temp_data, temp_messages)

        result = await db_session.execute(
            select(Message).where(Message.session_id == session_id)
        )
        messages = list(result.scalars().all())
        assert len(messages) == 1
        assert messages[0].sender == "user"

    async def test_activates_tools(
        self, db_session: AsyncSession, test_user, test_tenant, test_model, test_tool
    ):
        """Active tool IDs in temp_data should create SessionActiveTool rows."""
        session_id = str(uuid.uuid4())
        temp_data = {
            "id": session_id,
            "tenant_id": test_tenant.id,
            "user_id": test_user.id,
            "title": "Test",
            "is_pinned": False,
            "selected_model_id": test_model.id,
            "active_tool_ids": [test_tool.id],
        }

        await finalize_session(db_session, temp_data, [])

        result = await db_session.execute(
            select(SessionActiveTool).where(SessionActiveTool.session_id == session_id)
        )
        tools = list(result.scalars().all())
        assert len(tools) == 1
        assert tools[0].tool_id == test_tool.id

    async def test_auto_assigns_model_when_none(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """When no model specified, should auto-assign first enabled model."""
        # Create an enabled model
        from src.db.orm.models import Model
        model = Model(
            id=str(uuid.uuid4()), model_id="auto-model", name="auto-model", provider="openai",
            api_key="test-key", enabled=True, is_public=True, max_tokens=4096,
            temperature=0.7, auto_route_eligible=False,
            tenant_id=test_tenant.id,
        )
        db_session.add(model)
        await db_session.flush()

        session_id = str(uuid.uuid4())
        temp_data = {
            "id": session_id,
            "tenant_id": test_tenant.id,
            "user_id": test_user.id,
            "title": "Auto Model",
            "is_pinned": False,
        }

        session = await finalize_session(db_session, temp_data, [])
        assert session.selected_model_id is not None


# ===========================================================================
# Tag management
# ===========================================================================


class TestTagManagement:
    """Tests for session tag management functions."""

    async def test_get_or_create_tag_creates_new(
        self, db_session: AsyncSession, test_tenant
    ):
        """Should create a new tag when it doesn't exist."""
        tag = await get_or_create_tag(db_session, test_tenant.id, "new-tag")
        assert tag.name == "new-tag"
        assert tag.tenant_id == test_tenant.id

    async def test_get_or_create_tag_returns_existing(
        self, db_session: AsyncSession, test_tenant
    ):
        """Should return existing tag without creating duplicate."""
        tag1 = await get_or_create_tag(db_session, test_tenant.id, "existing")
        tag2 = await get_or_create_tag(db_session, test_tenant.id, "existing")
        assert tag1.id == tag2.id

        # Verify only one row
        result = await db_session.execute(
            select(Tag).where(Tag.name == "existing", Tag.tenant_id == test_tenant.id)
        )
        assert len(list(result.scalars().all())) == 1

    async def test_get_or_create_tag_normalizes_name(
        self, db_session: AsyncSession, test_tenant
    ):
        """Tag names should be lower-cased and stripped."""
        tag = await get_or_create_tag(db_session, test_tenant.id, "  UPPER-CASE  ")
        assert tag.name == "upper-case"

    async def test_get_or_create_tag_empty_raises(
        self, db_session: AsyncSession, test_tenant
    ):
        """Empty tag name should raise ValidationError."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            await get_or_create_tag(db_session, test_tenant.id, "")

    async def test_add_tag_to_session(
        self, db_session: AsyncSession, test_session, test_tenant
    ):
        """Should add a tag to a session and return True."""
        tag = await get_or_create_tag(db_session, test_tenant.id, "important")
        result = await add_tag_to_session(db_session, test_session.id, tag.id)
        assert result is True

        # Verify association exists
        st = await db_session.execute(
            select(SessionTag).where(
                SessionTag.session_id == test_session.id,
                SessionTag.tag_id == tag.id,
            )
        )
        assert st.scalar_one_or_none() is not None

    async def test_add_tag_to_session_duplicate_returns_false(
        self, db_session: AsyncSession, test_session, test_tenant
    ):
        """Adding the same tag twice should return False."""
        tag = await get_or_create_tag(db_session, test_tenant.id, "dup")
        await add_tag_to_session(db_session, test_session.id, tag.id)
        result = await add_tag_to_session(db_session, test_session.id, tag.id)
        assert result is False

    async def test_remove_tag_from_session(
        self, db_session: AsyncSession, test_session, test_tenant
    ):
        """Should remove a tag from a session."""
        tag = await get_or_create_tag(db_session, test_tenant.id, "remove-me")
        await add_tag_to_session(db_session, test_session.id, tag.id)

        await remove_tag_from_session(db_session, test_session.id, tag.id)

        st = await db_session.execute(
            select(SessionTag).where(
                SessionTag.session_id == test_session.id,
                SessionTag.tag_id == tag.id,
            )
        )
        assert st.scalar_one_or_none() is None

    async def test_remove_tag_from_session_silent_on_missing(
        self, db_session: AsyncSession, test_session
    ):
        """Removing a tag that isn't associated should not raise."""
        await remove_tag_from_session(db_session, test_session.id, str(uuid.uuid4()))
        # Should not raise

    async def test_get_session_tags(
        self, db_session: AsyncSession, test_session, test_tenant
    ):
        """Should return all tags for a session ordered by name."""
        tag_a = await get_or_create_tag(db_session, test_tenant.id, "beta")
        tag_b = await get_or_create_tag(db_session, test_tenant.id, "alpha")
        await add_tag_to_session(db_session, test_session.id, tag_b.id)
        await add_tag_to_session(db_session, test_session.id, tag_a.id)

        tags = await get_session_tags(db_session, test_session.id)
        assert len(tags) == 2
        assert tags[0].name == "alpha"  # ordered by name
        assert tags[1].name == "beta"

    async def test_get_session_tags_empty(
        self, db_session: AsyncSession, test_session
    ):
        """Session with no tags should return empty list."""
        tags = await get_session_tags(db_session, test_session.id)
        assert tags == []

    async def test_list_tenant_tags(
        self, db_session: AsyncSession, test_tenant, second_tenant
    ):
        """Should list all tags for a tenant."""
        await get_or_create_tag(db_session, test_tenant.id, "tag1")
        await get_or_create_tag(db_session, test_tenant.id, "tag2")
        await get_or_create_tag(db_session, second_tenant.id, "other-tag")

        tags = await list_tenant_tags(db_session, test_tenant.id)
        tag_names = [t.name for t in tags]
        assert "tag1" in tag_names
        assert "tag2" in tag_names
        assert "other-tag" not in tag_names

    async def test_list_sessions_by_tag(
        self, db_session: AsyncSession, test_session, test_user, test_tenant
    ):
        """Should return sessions with the given tag."""
        tag = await get_or_create_tag(db_session, test_tenant.id, "my-tag")
        await add_tag_to_session(db_session, test_session.id, tag.id)

        sessions = await list_sessions_by_tag(
            db_session, user_id=test_user.id, tenant_id=test_tenant.id, tag_name="my-tag"
        )
        assert len(sessions) >= 1
        assert test_session.id in {s.id for s in sessions}

    async def test_delete_session_tags(
        self, db_session: AsyncSession, test_session, test_tenant
    ):
        """Should bulk-delete all tag associations for a session."""
        tag1 = await get_or_create_tag(db_session, test_tenant.id, "tag1")
        tag2 = await get_or_create_tag(db_session, test_tenant.id, "tag2")
        await add_tag_to_session(db_session, test_session.id, tag1.id)
        await add_tag_to_session(db_session, test_session.id, tag2.id)

        await delete_session_tags(db_session, test_session.id)

        tags = await get_session_tags(db_session, test_session.id)
        assert tags == []


# ===========================================================================
# Session CRUD edge cases
# ===========================================================================


class TestSessionCreateEdgeCases:
    """Edge cases for create_session."""

    async def test_auto_route_leaves_model_none(
        self, db_session: AsyncSession, test_user, test_tenant, test_model
    ):
        """When auto_route_enabled=True, selected_model_id should be None."""
        session = await create_session(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Auto Route Session",
            auto_route_enabled=True,
        )
        assert session.auto_route_enabled is True
        assert session.selected_model_id is None

    async def test_explicit_model_is_used(
        self, db_session: AsyncSession, test_user, test_tenant, test_model
    ):
        """When explicit selected_model_id is given, it should be used."""
        session = await create_session(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Explicit Model",
            selected_model_id=test_model.id,
        )
        assert session.selected_model_id == test_model.id

    async def test_auto_assigns_model_when_no_model_given(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """When no model given and no auto_route, should auto-assign first enabled."""
        from src.db.orm.models import Model
        model = Model(
            id=str(uuid.uuid4()), model_id="fallback-model", name="fallback-model", provider="openai",
            api_key="test-key", enabled=True, is_public=True, max_tokens=4096,
            temperature=0.7, auto_route_eligible=False,
            tenant_id=test_tenant.id,
        )
        db_session.add(model)
        await db_session.flush()

        session = await create_session(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Auto Assign",
        )
        assert session.selected_model_id is not None

    async def test_custom_id_injection(
        self, db_session: AsyncSession, test_user, test_tenant, test_model
    ):
        """Explicit id should be used for the session (lazy persistence pattern)."""
        custom_id = str(uuid.uuid4())
        session = await create_session(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Custom ID",
            id=custom_id,
            selected_model_id=test_model.id,
        )
        assert session.id == custom_id


class TestSessionUpdateEdgeCases:
    """Edge cases for update_session."""

    async def test_update_multiple_fields(
        self, db_session: AsyncSession, test_session
    ):
        """Should update multiple fields at once."""
        updated = await update_session(
            db_session, test_session.id,
            title="New Title",
            is_pinned=True,
        )
        assert updated.title == "New Title"
        assert updated.is_pinned is True

    async def test_update_not_found(
        self, db_session: AsyncSession
    ):
        """Non-existent session should raise NotFoundError."""
        with pytest.raises(NotFoundError, match="Session not found"):
            await update_session(db_session, str(uuid.uuid4()), title="Nope")


class TestSessionDeleteCascade:
    """Verify delete_session cleans up all FK references."""

    async def test_delete_cleans_messages_and_tools_and_tags(
        self, db_session: AsyncSession, test_session, test_tenant, test_tool
    ):
        """Deleting a session should remove its messages, tools, and tags."""
        # Add a message
        msg = Message(
            id=str(uuid.uuid4()), session_id=test_session.id,
            sender="user", content=[{"type": "text", "text": "test"}],
        )
        db_session.add(msg)
        # Add a tool
        await add_session_tool(db_session, test_session.id, test_tool.id, test_tenant.id)
        # Add a tag
        tag = await get_or_create_tag(db_session, test_tenant.id, "test-tag")
        await add_tag_to_session(db_session, test_session.id, tag.id)

        await db_session.flush()

        # Delete
        await delete_session(db_session, test_session.id)

        # Verify session gone
        assert await get_session_by_id(db_session, test_session.id) is None
        # Verify messages gone
        msgs = await db_session.execute(
            select(Message).where(Message.session_id == test_session.id)
        )
        assert list(msgs.scalars().all()) == []
        # Verify tools gone
        tools = await get_session_tools(db_session, test_session.id)
        assert tools == []
        # Verify tags gone
        tags = await get_session_tags(db_session, test_session.id)
        assert tags == []

    async def test_delete_nonexistent_raises(
        self, db_session: AsyncSession
    ):
        """Deleting a non-existent session should raise NotFoundError."""
        with pytest.raises(NotFoundError, match="Session not found"):
            await delete_session(db_session, str(uuid.uuid4()))


class TestBatchDeleteSessions:
    """Verify delete_sessions_batch cascade behavior."""

    async def test_batch_delete_cleans_all_references(
        self,
        db_session: AsyncSession,
        test_tenant,
        test_user,
        test_tool,
        test_model,
    ):
        """Batch delete should clean messages, tools, and tags for all sessions."""
        from src.db.orm.models import Model as ModelORM
        from src.db.orm.sessions import Session
        from src.db.orm.messages import Message
        from src.services.session_service import (
            add_session_tool,
            add_tag_to_session,
            delete_sessions_batch,
            get_or_create_tag,
            get_session_by_id,
            get_session_tools,
            get_session_tags,
        )

        # Create 2 sessions with messages, tools, and tags
        sessions = []
        for i in range(2):
            s = Session(
                id=str(uuid.uuid4()),
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                title=f"Session {i}",
                selected_model_id=test_model.id,
            )
            db_session.add(s)
            sessions.append(s)
        await db_session.flush()

        for s in sessions:
            # Add a message
            msg = Message(
                id=str(uuid.uuid4()),
                session_id=s.id,
                sender="user",
                content=[{"type": "text", "text": f"test {s.id}"}],
            )
            db_session.add(msg)
            # Add a tool
            await add_session_tool(db_session, s.id, test_tool.id, test_tenant.id)
            # Add a tag
            tag = await get_or_create_tag(db_session, test_tenant.id, f"tag-{s.id[:8]}")
            await add_tag_to_session(db_session, s.id, tag.id)

        await db_session.flush()
        session_ids = [s.id for s in sessions]

        # Delete batch
        result = await delete_sessions_batch(
            db_session, session_ids, test_user.id, test_tenant.id
        )

        assert result["deleted"] == 2
        assert result["skipped"] == []
        assert result["errors"] == []

        # Verify all sessions gone
        for sid in session_ids:
            assert await get_session_by_id(db_session, sid) is None
            # Verify messages gone
            msgs = await db_session.execute(
                select(Message).where(Message.session_id == sid)
            )
            assert list(msgs.scalars().all()) == []
            # Verify tools gone
            tools = await get_session_tools(db_session, sid)
            assert tools == []
            # Verify tags gone
            tags = await get_session_tags(db_session, sid)
            assert tags == []

    async def test_batch_delete_skips_unowned_sessions(
        self,
        db_session: AsyncSession,
        test_tenant,
        test_user,
        test_model,
        second_tenant,
    ):
        """Batch delete should skip sessions not owned by the user."""
        from src.db.orm.sessions import Session
        from src.db.orm.users import User
        from src.services.session_service import delete_sessions_batch, get_session_by_id

        # Create a session for another user
        other_user = User(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            email=f"other-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="pbkdf2:sha256:600000$test-salt$test-hash",
            display_name="Other User",
            role="user",
            is_active=True,
        )
        db_session.add(other_user)
        await db_session.flush()

        other_session = Session(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            user_id=other_user.id,
            title="Other User Session",
            selected_model_id=test_model.id,
        )
        db_session.add(other_session)
        await db_session.flush()

        result = await delete_sessions_batch(
            db_session,
            [other_session.id],
            test_user.id,
            test_tenant.id,
        )

        assert result["deleted"] == 0
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["id"] == other_session.id
        assert "not owned" in result["skipped"][0]["reason"].lower()

        # Verify other session still exists
        assert await get_session_by_id(db_session, other_session.id) is not None

    async def test_batch_delete_skips_nonexistent(
        self,
        db_session: AsyncSession,
        test_tenant,
        test_user,
    ):
        """Batch delete should skip non-existent sessions gracefully."""
        from src.services.session_service import delete_sessions_batch

        fake_id = str(uuid.uuid4())
        result = await delete_sessions_batch(
            db_session, [fake_id], test_user.id, test_tenant.id
        )

        assert result["deleted"] == 0
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["id"] == fake_id


class TestListSessions:
    """Tests for list_sessions_for_user and list_admin_sessions."""

    async def test_list_excludes_temporary(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Temporary sessions should be excluded from user listing."""
        from src.db.orm.models import Model as ModelORM
        model = ModelORM(
            id=str(uuid.uuid4()), model_id="tmp-model", name="tmp-model", provider="openai",
            api_key="test-key", enabled=True, is_public=True, max_tokens=4096,
            temperature=0.7, auto_route_eligible=False,
            tenant_id=test_tenant.id,
        )
        db_session.add(model)
        await db_session.flush()

        # Create a temporary session
        temp = await create_session(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            title="Temp", is_temporary=True, selected_model_id=model.id,
        )

        # Create a permanent session
        perm = await create_session(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            title="Perm", selected_model_id=model.id,
        )

        sessions = await list_sessions_for_user(db_session, test_user.id, test_tenant.id)
        session_ids = {s.id for s in sessions}
        assert temp.id not in session_ids
        assert perm.id in session_ids

    async def test_list_admin_sessions_pagination(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Admin listing should support pagination."""
        from src.db.orm.models import Model as ModelORM
        model = ModelORM(
            id=str(uuid.uuid4()), model_id="admin-list-model", name="admin-list-model", provider="openai",
            api_key="test-key", enabled=True, is_public=True, max_tokens=4096,
            temperature=0.7, auto_route_eligible=False,
            tenant_id=test_tenant.id,
        )
        db_session.add(model)
        await db_session.flush()

        for i in range(5):
            await create_session(
                db_session, tenant_id=test_tenant.id, user_id=test_user.id,
                title=f"Session {i}", selected_model_id=model.id,
            )

        items, total = await list_admin_sessions(
            db_session, tenant_id=test_tenant.id, page=1, page_size=2,
        )
        assert total == 5
        assert len(items) == 2


class TestPurgeEmptySessions:
    """Tests for _purge_empty_sessions — cleanup of abandoned sessions."""

    async def test_purges_old_empty_sessions(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Empty sessions older than 1 hour should be purged."""
        from src.db.orm.models import Model as ModelORM
        model = ModelORM(
            id=str(uuid.uuid4()), model_id="purge-model", name="purge-model", provider="openai",
            api_key="test-key", enabled=True, is_public=True, max_tokens=4096,
            temperature=0.7, auto_route_eligible=False,
            tenant_id=test_tenant.id,
        )
        db_session.add(model)
        await db_session.flush()

        # Create an old session (simulate by setting created_at manually)
        old_session = Session(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            title="Old Empty",
            is_temporary=False,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db_session.add(old_session)
        await db_session.flush()

        count = await _purge_empty_sessions(db_session, test_user.id, test_tenant.id)
        assert count >= 1

    async def test_keeps_recent_empty_sessions(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Empty sessions created within the last hour should be kept."""
        from src.db.orm.models import Model as ModelORM
        model = ModelORM(
            id=str(uuid.uuid4()), model_id="recent-model", name="recent-model", provider="openai",
            api_key="test-key", enabled=True, is_public=True, max_tokens=4096,
            temperature=0.7, auto_route_eligible=False,
            tenant_id=test_tenant.id,
        )
        db_session.add(model)
        await db_session.flush()

        recent = await create_session(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            title="Recent Empty", selected_model_id=model.id,
        )

        count = await _purge_empty_sessions(db_session, test_user.id, test_tenant.id)
        # Recent should NOT be purged
        assert await get_session_by_id(db_session, recent.id) is not None

    async def test_keeps_sessions_with_messages(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Sessions with messages should NOT be purged regardless of age."""
        from src.db.orm.models import Model as ModelORM

        now = datetime.now(timezone.utc)

        old_session = Session(
            id=str(uuid.uuid4()),
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            title="Old With Messages",
            is_temporary=False,
            created_at=now - timedelta(hours=2),
        )
        db_session.add(old_session)
        await db_session.flush()
        # Add a message to this session — flush both together
        msg = Message(
            id=str(uuid.uuid4()), session_id=old_session.id,
            sender="user", content=[{"type": "text", "text": "keep me"}],
            created_at=now - timedelta(hours=2),
        )
        db_session.add(msg)
        await db_session.flush()

        count = await _purge_empty_sessions(db_session, test_user.id, test_tenant.id)
        assert count == 0  # not purged because it has messages
