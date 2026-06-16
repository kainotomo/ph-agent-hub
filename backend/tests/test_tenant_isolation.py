# =============================================================================
# PH Agent Hub — Tenant Isolation Tests (Service Layer)
# =============================================================================
# Tests that services enforce tenant boundaries correctly:
# - Data created in Tenant A is not accessible from Tenant B
# - Cross-tenant operations are rejected
# =============================================================================

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.sessions import Session
from src.db.orm.memory import Memory
from src.db.orm.prompts import Prompt
from src.db.orm.skills import Skill
from src.db.orm.tools import Tool
from src.db.orm.models import Model
from src.services.session_service import create_session, list_sessions_for_user as list_sessions
from src.services.memory_service import create_memory, list_memory
from src.services.prompt_service import (
    create_prompt,
    list_prompts as svc_list_prompts,
    get_prompt_by_id,
)
from src.services.group_service import (
    add_member,
    assign_model_to_group,
    assign_tool_to_group,
    create_group,
)
from src.core.exceptions import ForbiddenError, NotFoundError

pytestmark = [
    pytest.mark.security,
    pytest.mark.tenant_isolation,
    pytest.mark.integration,
]


class TestSessionIsolation:
    """Verify sessions are tenant-scoped."""

    async def test_create_session_sets_tenant(self, db_session, test_tenant, test_user):
        """Verify session is created with the correct tenant_id."""
        session = await create_session(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Test Session",
        )
        assert session.tenant_id == test_tenant.id

    async def test_other_tenant_cannot_access_session(
        self, db_session, test_tenant, test_user, second_tenant
    ):
        """Verify list_sessions for Tenant B does not return sessions from Tenant A."""
        # Create session in Tenant A
        session = await create_session(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Secret Session",
        )

        # Query as Tenant B
        sessions = await list_sessions(
            db_session,
            user_id=test_user.id,
            tenant_id=second_tenant.id,
        )
        session_ids = [s.id for s in sessions]
        assert session.id not in session_ids


class TestMemoryIsolation:
    """Verify memories are tenant-scoped."""

    async def test_create_memory_sets_tenant(self, db_session, test_tenant, test_user):
        """Verify memory is created with the correct tenant_id."""
        memory = await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="test-key",
            value="test-value",
            source="automatic",
        )
        assert memory.tenant_id == test_tenant.id

    async def test_other_tenant_cannot_access_memory(
        self, db_session, test_tenant, test_user, second_tenant
    ):
        """Verify list_memory for Tenant B does not return memories from Tenant A."""
        memory = await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="secret-key",
            value="secret-value",
            source="automatic",
        )

        # Query as Tenant B
        memories, total = await list_memory(
            db_session,
            tenant_id=second_tenant.id,
            user_id=test_user.id,
        )
        memory_ids = [m.id for m in memories]
        assert memory.id not in memory_ids


class TestPromptIsolation:
    """Verify prompts are tenant-scoped."""

    async def test_create_prompt_sets_tenant(self, db_session, test_tenant, test_user):
        """Verify prompt is created with the correct tenant_id."""
        prompt = await create_prompt(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Test Prompt",
            description="A test prompt",
            content="Hello world",
        )
        assert prompt.tenant_id == test_tenant.id

    async def test_other_tenant_cannot_list_prompts(
        self, db_session, test_tenant, test_user, second_tenant
    ):
        """Verify list_prompts for Tenant B does not return prompts from Tenant A."""
        prompt = await create_prompt(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Secret Prompt",
            description="Secret",
            content="Secret content",
        )

        # List as Tenant B with same user_id (should be filtered by tenant_id)
        prompts = await svc_list_prompts(
            db_session,
            user_id=test_user.id,
            tenant_id=second_tenant.id,
        )
        prompt_ids = [p.id for p in prompts]
        assert prompt.id not in prompt_ids


class TestSkillIsolation:
    """Verify skills are tenant-scoped."""

    async def test_skill_has_tenant_id(self, db_session, test_tenant, test_user):
        """Verify skill is created with the correct tenant_id."""
        from src.services.skill_service import create_skill

        skill = await create_skill(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            title="Test Skill",
            execution_type="agent",
            visibility="user",
        )
        assert skill.tenant_id == test_tenant.id


class TestGroupTenantBoundary:
    """Verify group operations enforce tenant boundaries."""

    async def test_add_member_same_tenant(self, db_session, test_tenant, test_user):
        """Verify user from same tenant can be added to group."""
        group = await create_group(db_session, tenant_id=test_tenant.id, name="Test Group")
        member = await add_member(db_session, group.id, test_user.id)
        assert member.user_id == test_user.id

    async def test_add_member_cross_tenant_blocked(
        self, db_session, test_tenant, second_user
    ):
        """Verify adding user from different tenant to group raises ForbiddenError."""
        group = await create_group(db_session, tenant_id=test_tenant.id, name="Test Group")
        with pytest.raises(ForbiddenError, match="different tenant"):
            await add_member(db_session, group.id, second_user.id)

    async def test_assign_model_cross_tenant_blocked(
        self, db_session, test_tenant, second_tenant
    ):
        """Verify assigning model from different tenant to group raises ForbiddenError."""
        group = await create_group(db_session, tenant_id=test_tenant.id, name="Test Group")
        # Create model in second tenant
        model = Model(
            id=str(uuid.uuid4()),
            tenant_id=second_tenant.id,
            name="Cross-Tenant Model",
            model_id="test-model",
            provider="openai",
            api_key="test-key",
            max_tokens=4096,
            temperature=0.7,
        )
        db_session.add(model)
        await db_session.flush()

        with pytest.raises(ForbiddenError, match="different tenant"):
            await assign_model_to_group(db_session, group.id, model.id)

    async def test_assign_tool_cross_tenant_blocked(
        self, db_session, test_tenant, second_tenant
    ):
        """Verify assigning tool from different tenant to group raises ForbiddenError."""
        group = await create_group(db_session, tenant_id=test_tenant.id, name="Test Group")
        # Create tool in second tenant
        tool = Tool(
            id=str(uuid.uuid4()),
            tenant_id=second_tenant.id,
            name="Cross-Tenant Tool",
            type="datetime",
            category="general",
            config={},
        )
        db_session.add(tool)
        await db_session.flush()

        with pytest.raises(ForbiddenError, match="different tenant"):
            await assign_tool_to_group(db_session, group.id, tool.id)
