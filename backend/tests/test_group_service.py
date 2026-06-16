# =============================================================================
# PH Agent Hub — Group Service Tests (Idempotent Operations)
# =============================================================================
# Tests that duplicate operations (adding the same member/model/tool to a
# group twice) return the existing row instead of crashing with a
# cursor-consumption error (Issue #348).
# =============================================================================

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.models import Model
from src.db.orm.tools import Tool
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
