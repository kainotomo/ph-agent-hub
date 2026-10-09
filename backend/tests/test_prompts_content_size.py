# =============================================================================
# PH Agent Hub — Saved Prompt content size
# =============================================================================
# Regression: ``prompts.content`` was a MariaDB TEXT column (~64 KB), so saving
# a large prompt failed with error 1406 ("Data too long") and the API returned
# a 500 — which the frontend then swallowed silently. The column is now
# LONGTEXT; these tests assert a large prompt round-trips unchanged.
# =============================================================================

import httpx
import pytest
import pytest_asyncio

from src.main import app

pytestmark = [
    pytest.mark.regression,
    pytest.mark.integration,
]

# Comfortably above the old TEXT limit (65,535 bytes).
LARGE_CONTENT_SIZE = 200_000


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    """Create an async HTTP client wired to the FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        yield client


class TestPromptLargeContent:
    """A prompt larger than the legacy TEXT limit must persist in full."""

    async def test_create_prompt_larger_than_text_limit(
        self, async_client, auth_headers, test_user
    ):
        content = "x" * LARGE_CONTENT_SIZE
        headers = auth_headers(test_user)

        resp = await async_client.post(
            "/api/prompts",
            json={"title": "Big", "description": "", "content": content},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        prompt_id = resp.json()["id"]
        assert len(resp.json()["content"]) == LARGE_CONTENT_SIZE

        # Re-fetch to confirm the full value was stored, not truncated.
        listing = await async_client.get("/api/prompts", headers=headers)
        assert listing.status_code == 200
        stored = next(p for p in listing.json() if p["id"] == prompt_id)
        assert len(stored["content"]) == LARGE_CONTENT_SIZE

    async def test_update_prompt_larger_than_text_limit(
        self, async_client, auth_headers, test_user
    ):
        headers = auth_headers(test_user)
        create = await async_client.post(
            "/api/prompts",
            json={"title": "Small", "description": "", "content": "hi"},
            headers=headers,
        )
        prompt_id = create.json()["id"]

        content = "y" * LARGE_CONTENT_SIZE
        resp = await async_client.put(
            f"/api/prompts/{prompt_id}",
            json={"content": content},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["content"]) == LARGE_CONTENT_SIZE

    async def test_content_over_max_length_rejected_cleanly(
        self, async_client, auth_headers, test_user
    ):
        """A payload beyond the sanity cap returns a clean 422, not a DB error."""
        from src.api.prompts import PROMPT_CONTENT_MAX_LENGTH

        resp = await async_client.post(
            "/api/prompts",
            json={
                "title": "Huge",
                "description": "",
                "content": "z" * (PROMPT_CONTENT_MAX_LENGTH + 1),
            },
            headers=auth_headers(test_user),
        )
        assert resp.status_code == 422, resp.text
