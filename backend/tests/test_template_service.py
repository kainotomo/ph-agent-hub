# =============================================================================
# PH Agent Hub — Template Service Tests
# =============================================================================

import uuid
import pytest
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.orm.templates import Template
from src.db.orm.sessions import Session as SessionORM
from src.db.orm.skills import Skill as SkillORM
from src.db.orm.users import User
from src.services.template_service import (
    list_templates,
    get_template_by_id,
    create_template,
    update_template,
    delete_template,
)

pytestmark = [pytest.mark.integration]


class TestListTemplates:
    """Tests for list_templates."""

    async def test_empty_db(self, db_session: AsyncSession, test_tenant):
        """Should return empty list when no templates exist."""
        items, total = await list_templates(db_session, tenant_id=test_tenant.id)
        assert items == []
        assert total == 0

    async def test_multiple_templates(self, db_session: AsyncSession, test_tenant):
        """Should return all templates for the tenant."""
        t1 = Template(tenant_id=test_tenant.id, title="First", system_prompt="P1", scope="tenant")
        t2 = Template(tenant_id=test_tenant.id, title="Second", system_prompt="P2", scope="tenant")
        db_session.add_all([t1, t2])
        await db_session.flush()

        items, total = await list_templates(db_session, tenant_id=test_tenant.id)
        assert total == 2
        assert {t.id for t in items} == {t1.id, t2.id}

    async def test_search(self, db_session: AsyncSession, test_tenant):
        """Should filter templates by search term on title or description."""
        db_session.add_all([
            Template(tenant_id=test_tenant.id, title="Customer Support", system_prompt="Help", scope="tenant", description="Support team"),
            Template(tenant_id=test_tenant.id, title="Dev Assistant", system_prompt="Code", scope="tenant", description="Engineering"),
        ])
        await db_session.flush()

        items, total = await list_templates(db_session, tenant_id=test_tenant.id, search="Support")
        assert total == 1
        assert items[0].title == "Customer Support"

    async def test_pagination(self, db_session: AsyncSession, test_tenant):
        """Should paginate results."""
        for i in range(5):
            db_session.add(Template(tenant_id=test_tenant.id, title=f"T{i}", system_prompt=f"P{i}", scope="tenant"))
        await db_session.flush()

        items, total = await list_templates(db_session, tenant_id=test_tenant.id, page=1, page_size=2)
        assert total == 5
        assert len(items) == 2

    async def test_admin_sees_all(self, db_session: AsyncSession, test_tenant, admin_user: User):
        """Admin should see all templates regardless of scope."""
        db_session.add_all([
            Template(tenant_id=test_tenant.id, title="Tenant Wide", system_prompt="A", scope="tenant"),
            Template(tenant_id=test_tenant.id, title="User Specific", system_prompt="B", scope="user", assigned_user_id=admin_user.id),
        ])
        await db_session.flush()

        items, total = await list_templates(db_session, current_user=admin_user, tenant_id=test_tenant.id)
        assert total == 2

    async def test_user_sees_tenant_plus_own(self, db_session: AsyncSession, test_tenant, test_user: User, second_user: User):
        """Regular user should see tenant-scoped templates + their own user-scoped templates."""
        db_session.add_all([
            Template(tenant_id=test_tenant.id, title="Shared", system_prompt="A", scope="tenant"),
            Template(tenant_id=test_tenant.id, title="Mine", system_prompt="B", scope="user", assigned_user_id=test_user.id),
            Template(tenant_id=test_tenant.id, title="Theirs", system_prompt="C", scope="user", assigned_user_id=second_user.id),
        ])
        await db_session.flush()

        items, total = await list_templates(db_session, current_user=test_user, tenant_id=test_tenant.id)
        titles = {t.title for t in items}
        assert "Shared" in titles
        assert "Mine" in titles
        assert "Theirs" not in titles

    async def test_filter_by_scope(self, db_session: AsyncSession, test_tenant, test_user: User):
        """Should filter by scope parameter."""
        db_session.add_all([
            Template(tenant_id=test_tenant.id, title="Tenant", system_prompt="A", scope="tenant"),
            Template(tenant_id=test_tenant.id, title="User", system_prompt="B", scope="user", assigned_user_id=test_user.id),
        ])
        await db_session.flush()

        items, total = await list_templates(db_session, tenant_id=test_tenant.id, scope="user")
        assert total == 1
        assert items[0].title == "User"


class TestGetTemplateById:
    """Tests for get_template_by_id."""

    async def test_existing(self, db_session: AsyncSession, test_tenant):
        """Should return the template when it exists."""
        template = Template(tenant_id=test_tenant.id, title="Found", system_prompt="Prompt", scope="tenant")
        db_session.add(template)
        await db_session.flush()

        result = await get_template_by_id(db_session, template.id)
        assert result is not None
        assert result.title == "Found"

    async def test_nonexistent(self, db_session: AsyncSession):
        """Should return None when template does not exist."""
        result = await get_template_by_id(db_session, str(uuid.uuid4()))
        assert result is None


class TestCreateTemplate:
    """Tests for create_template."""

    async def test_success_tenant_scope(self, db_session: AsyncSession, test_tenant):
        """Should create a tenant-scoped template."""
        template = await create_template(
            db_session,
            tenant_id=test_tenant.id,
            title="New Template",
            system_prompt="You are a helpful assistant.",
            scope="tenant",
        )
        assert template.title == "New Template"
        assert template.scope == "tenant"
        assert template.tenant_id == test_tenant.id

    async def test_success_user_scope_with_assigned_user(self, db_session: AsyncSession, test_tenant, test_user: User):
        """Should create a user-scoped template with an assigned user."""
        template = await create_template(
            db_session,
            tenant_id=test_tenant.id,
            title="Personal",
            system_prompt="Custom prompt",
            scope="user",
            assigned_user_id=test_user.id,
        )
        assert template.scope == "user"
        assert template.assigned_user_id == test_user.id

    async def test_success_with_description(self, db_session: AsyncSession, test_tenant):
        """Should create a template with a description."""
        template = await create_template(
            db_session,
            tenant_id=test_tenant.id,
            title="Described",
            system_prompt="Prompt",
            scope="tenant",
            description="A useful template",
        )
        assert template.description == "A useful template"


class TestUpdateTemplate:
    """Tests for update_template."""

    async def test_update_title(self, db_session: AsyncSession, test_template: Template):
        """Should update the title."""
        updated = await update_template(db_session, test_template.id, title="Renamed")
        assert updated.title == "Renamed"

    async def test_update_system_prompt(self, db_session: AsyncSession, test_template: Template):
        """Should update the system prompt."""
        updated = await update_template(db_session, test_template.id, system_prompt="New prompt")
        assert updated.system_prompt == "New prompt"

    async def test_update_scope(self, db_session: AsyncSession, test_template: Template):
        """Should update the scope."""
        updated = await update_template(db_session, test_template.id, scope="user")
        assert updated.scope == "user"

    async def test_nonexistent(self, db_session: AsyncSession):
        """Should raise NotFoundError when template does not exist."""
        with pytest.raises(NotFoundError, match="Template not found"):
            await update_template(db_session, str(uuid.uuid4()), title="Nope")


class TestDeleteTemplate:
    """Tests for delete_template."""

    async def test_delete_existing(self, db_session: AsyncSession, test_template: Template):
        """Should delete the template."""
        await delete_template(db_session, test_template.id)

        result = await db_session.execute(
            select(Template).where(Template.id == test_template.id)
        )
        assert result.scalar_one_or_none() is None

    async def test_nonexistent(self, db_session: AsyncSession):
        """Should raise NotFoundError when template does not exist."""
        with pytest.raises(NotFoundError, match="Template not found"):
            await delete_template(db_session, str(uuid.uuid4()))

    async def test_cascade_nullifies_session_refs(self, db_session: AsyncSession, test_session: SessionORM, test_template: Template):
        """Should nullify selected_template_id in sessions referencing the template."""
        # Link the session to the template
        test_session.selected_template_id = test_template.id
        await db_session.flush()

        await delete_template(db_session, test_template.id)

        # Verify the session ref was nullified
        await db_session.refresh(test_session)
        assert test_session.selected_template_id is None

    async def test_cascade_nullifies_skill_refs(self, db_session: AsyncSession, test_skill: SkillORM, test_template: Template):
        """Should nullify template_id in skills referencing the template."""
        # Link the skill to the template
        test_skill.template_id = test_template.id
        await db_session.flush()

        await delete_template(db_session, test_template.id)

        # Verify the skill ref was nullified
        await db_session.refresh(test_skill)
        assert test_skill.template_id is None
