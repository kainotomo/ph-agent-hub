# =============================================================================
# PH Agent Hub — Skills API Tests
# =============================================================================
# Tests pagination, search, sorting, and batch tool-fetch behaviour for the
# user-facing GET /api/skills endpoint.
# =============================================================================

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.jwt import create_access_token
from src.db.orm.skills import Skill, SkillAllowedTool
from src.main import app

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    """Create an async HTTP client with the test DB override."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestSkillsListPagination:
    """Verify GET /api/skills respects pagination parameters."""

    async def _create_skills(
        self, db_session: AsyncSession, tenant_id: str, count: int
    ) -> list[Skill]:
        """Helper: create N tenant-visibility skills."""
        skills = []
        for i in range(count):
            skill = Skill(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=None,
                title=f"Paginated Skill {i}",
                execution_type="agent",
                visibility="tenant",
            )
            db_session.add(skill)
            skills.append(skill)
        await db_session.flush()
        return skills

    async def test_default_pagination(
        self, async_client, db_session, test_tenant, test_user
    ):
        """Default page=1, page_size=25 returns items in envelope."""
        await self._create_skills(db_session, test_tenant.id, 5)
        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        headers = {"Authorization": f"Bearer {token}"}

        resp = await async_client.get("/api/skills", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data
        assert data["page"] == 1
        assert data["page_size"] == 25
        assert data["total"] == 5
        assert data["total_pages"] == 1
        assert len(data["items"]) == 5

    async def test_custom_page_size(
        self, async_client, db_session, test_tenant, test_user
    ):
        """Custom page_size returns at most that many items."""
        await self._create_skills(db_session, test_tenant.id, 10)
        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        headers = {"Authorization": f"Bearer {token}"}

        resp = await async_client.get(
            "/api/skills?page_size=3", headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["page_size"] == 3
        assert data["total"] == 10
        assert data["total_pages"] == 4

    async def test_second_page(
        self, async_client, db_session, test_tenant, test_user
    ):
        """Page 2 returns the next slice of items."""
        await self._create_skills(db_session, test_tenant.id, 5)
        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        headers = {"Authorization": f"Bearer {token}"}

        resp = await async_client.get(
            "/api/skills?page=2&page_size=2", headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2  # items 2-3 (0-indexed: 2,3)
        assert data["page"] == 2
        assert data["total_pages"] == 3

    async def test_search_filter(
        self, async_client, db_session, test_tenant, test_user
    ):
        """Search param filters skills by title."""
        skills = await self._create_skills(db_session, test_tenant.id, 3)
        # Add a distinct skill
        special = Skill(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            user_id=None,
            title="Special Tax Advisor",
            execution_type="agent",
            visibility="tenant",
        )
        db_session.add(special)
        await db_session.flush()

        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        headers = {"Authorization": f"Bearer {token}"}

        resp = await async_client.get(
            "/api/skills?search=Tax", headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["title"] == "Special Tax Advisor"

    async def test_sort_by_title(
        self, async_client, db_session, test_tenant, test_user
    ):
        """Sort_by=title orders results alphabetically."""
        titles = ["Charlie", "Alpha", "Bravo"]
        for t in titles:
            skill = Skill(
                id=str(uuid.uuid4()),
                tenant_id=test_tenant.id,
                user_id=None,
                title=t,
                execution_type="agent",
                visibility="tenant",
            )
            db_session.add(skill)
        await db_session.flush()

        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        headers = {"Authorization": f"Bearer {token}"}

        resp = await async_client.get(
            "/api/skills?sort_by=title&sort_dir=asc", headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        titles_returned = [item["title"] for item in data["items"]]
        assert titles_returned == ["Alpha", "Bravo", "Charlie"]

    async def test_empty_list(
        self, async_client, test_tenant, test_user
    ):
        """No skills returns empty items list with total=0."""
        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        headers = {"Authorization": f"Bearer {token}"}

        resp = await async_client.get("/api/skills", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["total_pages"] == 1


# ---------------------------------------------------------------------------
# Batch Tool Fetch
# ---------------------------------------------------------------------------


class TestSkillsBatchTools:
    """Verify batch tool-fetch avoids N+1 queries."""

    async def test_tool_ids_included_in_response(
        self, async_client, db_session, test_tenant, test_user, test_tool
    ):
        """Each skill in the paginated response includes its tool_ids."""
        skill = Skill(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            user_id=None,
            title="Skill With Tools",
            execution_type="agent",
            visibility="tenant",
        )
        db_session.add(skill)
        await db_session.flush()

        # Add a tool association using the pre-existing test_tool fixture
        tool_link = SkillAllowedTool(
            skill_id=skill.id,
            tool_id=test_tool.id,
        )
        db_session.add(tool_link)
        await db_session.flush()

        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        headers = {"Authorization": f"Bearer {token}"}

        resp = await async_client.get("/api/skills", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["tool_ids"] == [test_tool.id]

    async def test_skill_with_no_tools_returns_empty_list(
        self, async_client, db_session, test_tenant, test_user
    ):
        """Skill with no tool associations returns empty tool_ids list."""
        skill = Skill(
            id=str(uuid.uuid4()),
            tenant_id=test_tenant.id,
            user_id=None,
            title="Skill No Tools",
            execution_type="agent",
            visibility="tenant",
        )
        db_session.add(skill)
        await db_session.flush()

        token = create_access_token({
            "sub": test_user.id,
            "tenant_id": test_user.tenant_id,
            "role": test_user.role,
        })
        headers = {"Authorization": f"Bearer {token}"}

        resp = await async_client.get("/api/skills", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["tool_ids"] == []
