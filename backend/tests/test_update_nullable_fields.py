# =============================================================================
# PH Agent Hub — Tests: Nullable Field Clearing in Update APIs
# =============================================================================
# Validates that PUT endpoints correctly distinguish between omitted fields
# (keep existing value) and explicit null (clear the DB column).
# =============================================================================

import uuid

import pytest
import pytest_asyncio
from pydantic import BaseModel

from src.core.schemas import collect_update_fields


# =============================================================================
# Unit Tests — collect_update_fields utility
# =============================================================================


class _SampleUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    template_id: str | None = None
    enabled: bool | None = None


class TestCollectUpdateFields:
    """Unit tests for the ``collect_update_fields`` helper."""

    def test_omitted_fields_are_excluded(self):
        """When a field is not sent, it must not appear in the return dict."""
        body = _SampleUpdate()
        result = collect_update_fields(body)
        assert result == {}

    def test_explicit_null_is_included(self):
        """When a field is sent as null, it must appear with value None."""
        body = _SampleUpdate(template_id=None)
        result = collect_update_fields(body)
        assert result == {"template_id": None}

    def test_explicit_value_is_included(self):
        """When a field is sent with a real value, it must be present."""
        body = _SampleUpdate(title="Hello")
        result = collect_update_fields(body)
        assert result == {"title": "Hello"}

    def test_explicit_false_is_included(self):
        """False is a valid explicit value and must not be treated as omitted."""
        body = _SampleUpdate(enabled=False)
        result = collect_update_fields(body)
        assert result == {"enabled": False}

    def test_skip_excludes_fields(self):
        """Fields listed in ``skip`` must be removed from the result."""
        body = _SampleUpdate(title="Hello", template_id=None)
        result = collect_update_fields(body, skip={"template_id"})
        assert result == {"title": "Hello"}

    def test_mixed_omitted_and_provided(self):
        """Mixed omitted, explicit-null, and explicit-value fields."""
        body = _SampleUpdate(title="Keep", template_id=None)
        result = collect_update_fields(body)
        assert result == {"title": "Keep", "template_id": None}
        assert "description" not in result
        assert "enabled" not in result


# =============================================================================
# Integration Tests — PUT /prompts/{id}
# =============================================================================


class TestUpdatePromptNullableFields:
    """Tests for PUT /prompts/{id} nullable field clearing."""

    async def test_clear_template_id(
        self, async_client, auth_headers, test_user, test_prompt, test_template
    ):
        """Set template_id, then clear it with explicit null."""
        headers = auth_headers(test_user)
        prompt_id = test_prompt.id

        # Step 1: set template_id to a real template
        resp = await async_client.put(
            f"/api/prompts/{prompt_id}",
            json={"template_id": test_template.id},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["template_id"] == test_template.id

        # Step 2: clear it with explicit null
        resp = await async_client.put(
            f"/api/prompts/{prompt_id}",
            json={"template_id": None},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["template_id"] is None

    async def test_omit_field_preserves_existing_value(
        self, async_client, auth_headers, test_user, test_prompt
    ):
        """Omitting a field in the PUT body must not change it."""
        headers = auth_headers(test_user)

        # Set description first
        resp = await async_client.put(
            f"/api/prompts/{test_prompt.id}",
            json={"description": "Original description"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Original description"

        # Now send only title — description must remain unchanged
        resp = await async_client.put(
            f"/api/prompts/{test_prompt.id}",
            json={"title": "New Title Only"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "New Title Only"
        assert data["description"] == "Original description"

    async def test_update_prompt_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_prompt
    ):
        """Verify cross-user update is rejected."""
        headers = auth_headers(second_user)
        resp = await async_client.put(
            f"/api/prompts/{test_prompt.id}",
            json={"title": "Hacked"},
            headers=headers,
        )
        assert resp.status_code == 403

    async def test_update_prompt_not_found(
        self, async_client, auth_headers, test_user
    ):
        """Verify 404 for a nonexistent prompt."""
        headers = auth_headers(test_user)
        resp = await async_client.put(
            f"/api/prompts/{uuid.uuid4()}",
            json={"title": "Ghost"},
            headers=headers,
        )
        assert resp.status_code == 404


# =============================================================================
# Integration Tests — PUT /skills/{id}
# =============================================================================


@pytest_asyncio.fixture
async def test_skill_with_defaults(
    db_session,
    test_tenant,
    test_user,
    test_prompt,
    test_model,
    test_template,
):
    """Create a skill that has template_id and default_prompt_id set."""
    from src.db.orm.skills import Skill

    skill = Skill(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Skill With Defaults",
        description="Has template and prompt references",
        execution_type="agent",
        template_id=test_template.id,
        default_prompt_id=test_prompt.id,
        default_model_id=test_model.id,
        visibility="user",
        enabled=True,
    )
    db_session.add(skill)
    await db_session.flush()
    return skill


class TestUpdateSkillNullableFields:
    """Tests for PUT /skills/{id} nullable field clearing."""

    async def test_clear_multiple_nullable_fields(
        self, async_client, auth_headers, test_user, test_skill_with_defaults
    ):
        """Clear template_id, default_prompt_id, and default_model_id at once."""
        headers = auth_headers(test_user)
        skill_id = test_skill_with_defaults.id

        resp = await async_client.put(
            f"/api/skills/{skill_id}",
            json={
                "template_id": None,
                "default_prompt_id": None,
                "default_model_id": None,
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["template_id"] is None
        assert data["default_prompt_id"] is None
        assert data["default_model_id"] is None

    async def test_omit_skill_fields_preserves_values(
        self, async_client, auth_headers, test_user, test_skill_with_defaults
    ):
        """Omitting fields must leave existing values intact."""
        headers = auth_headers(test_user)
        skill_id = test_skill_with_defaults.id

        resp = await async_client.put(
            f"/api/skills/{skill_id}",
            json={"title": "Renamed Only"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "Renamed Only"
        assert data["template_id"] == test_skill_with_defaults.template_id
        assert data["description"] == "Has template and prompt references"

    async def test_update_skill_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_skill_with_defaults
    ):
        """Verify cross-user skill update is rejected."""
        headers = auth_headers(second_user)
        resp = await async_client.put(
            f"/api/skills/{test_skill_with_defaults.id}",
            json={"title": "Hacked"},
            headers=headers,
        )
        assert resp.status_code == 403

    async def test_update_skill_not_found(
        self, async_client, auth_headers, test_user
    ):
        """Verify 404 for a nonexistent skill."""
        headers = auth_headers(test_user)
        resp = await async_client.put(
            f"/api/skills/{uuid.uuid4()}",
            json={"title": "Ghost"},
            headers=headers,
        )
        assert resp.status_code == 404
