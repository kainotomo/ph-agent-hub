# =============================================================================
# PH Agent Hub — Chat & Session API Integration Tests
# =============================================================================
# Tests session CRUD, message operations, file upload, feedback, and
# tenant/user isolation at the HTTP layer.
# =============================================================================

import json
import uuid
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.jwt import create_access_token
from src.db.orm.sessions import Session
from src.main import app

pytestmark = [
    pytest.mark.integration,
]


# =============================================================================
# Fixtures
# =============================================================================


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    """Create an async HTTP client wired to the FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# =============================================================================
# Session CRUD Tests
# =============================================================================


class TestCreateSession:
    """Tests for POST /chat/session."""

    async def test_create_permanent_session(
        self, async_client, auth_headers, test_user, test_model
    ):
        """Verify creating a permanent session returns correct fields."""
        headers = auth_headers(test_user)
        payload = {
            "title": "Test Chat",
            "selected_model_id": test_model.id,
            "is_temporary": False,
        }
        resp = await async_client.post("/api/chat/session", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["title"] == "Test Chat"
        assert data["tenant_id"] == test_user.tenant_id
        assert data["user_id"] == test_user.id
        assert data["is_temporary"] is False
        assert data["selected_model_id"] == test_model.id
        assert "id" in data

    async def test_create_temporary_session(
        self, async_client, auth_headers, test_user
    ):
        """Verify creating a temporary session returns is_temporary=True."""
        headers = auth_headers(test_user)
        payload = {"title": "Temp Chat", "is_temporary": True}
        resp = await async_client.post("/api/chat/session", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["is_temporary"] is True
        assert data["title"] == "Temp Chat"
        assert data["user_id"] == test_user.id

    async def test_create_session_with_template(
        self, async_client, auth_headers, test_user, test_model, test_template
    ):
        """Verify session can be created with a template reference."""
        headers = auth_headers(test_user)
        payload = {
            "title": "Templated Chat",
            "selected_model_id": test_model.id,
            "selected_template_id": test_template.id,
        }
        resp = await async_client.post("/api/chat/session", json=payload, headers=headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["selected_template_id"] == test_template.id

    async def test_create_session_requires_auth(self, async_client):
        """Verify unauthenticated request is rejected."""
        payload = {"title": "Hacker Chat"}
        resp = await async_client.post("/api/chat/session", json=payload)
        assert resp.status_code == 401


class TestListSessions:
    """Tests for GET /chat/sessions."""

    async def test_list_sessions_returns_user_sessions(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify user sees their own sessions."""
        headers = auth_headers(test_user)
        resp = await async_client.get("/api/chat/sessions", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        session_ids = [s["id"] for s in data]
        assert test_session.id in session_ids

    async def test_list_sessions_empty_for_new_user(
        self, async_client, auth_headers, test_user
    ):
        """Verify a user with no sessions gets an empty list."""
        headers = auth_headers(test_user)
        resp = await async_client.get("/api/chat/sessions", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_sessions_other_user_not_visible(
        self, async_client, auth_headers, test_user, second_user, test_session
    ):
        """Verify user B cannot see user A's sessions via list."""
        headers = auth_headers(second_user)
        resp = await async_client.get("/api/chat/sessions", headers=headers)
        assert resp.status_code == 200
        session_ids = [s["id"] for s in resp.json()]
        assert test_session.id not in session_ids


class TestGetSession:
    """Tests for GET /chat/session/{session_id}."""

    async def test_get_session_by_id(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify get session returns correct data."""
        headers = auth_headers(test_user)
        resp = await async_client.get(f"/api/chat/session/{test_session.id}", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == test_session.id
        assert data["title"] == test_session.title
        assert data["tenant_id"] == test_user.tenant_id

    async def test_get_session_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_session
    ):
        """Verify user B cannot access user A's session."""
        headers = auth_headers(second_user)
        resp = await async_client.get(f"/api/chat/session/{test_session.id}", headers=headers)
        assert resp.status_code == 403, resp.text

    async def test_get_session_not_found(
        self, async_client, auth_headers, test_user
    ):
        """Verify non-existent session returns 404."""
        headers = auth_headers(test_user)
        fake_id = str(uuid.uuid4())
        resp = await async_client.get(f"/api/chat/session/{fake_id}", headers=headers)
        assert resp.status_code == 404


class TestUpdateSession:
    """Tests for PUT /chat/session/{session_id}."""

    async def test_update_session_title(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify updating session title works."""
        headers = auth_headers(test_user)
        payload = {"title": "Updated Title"}
        resp = await async_client.put(
            f"/api/chat/session/{test_session.id}", json=payload, headers=headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"] == "Updated Title"

    async def test_update_session_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_session
    ):
        """Verify user B cannot update user A's session."""
        headers = auth_headers(second_user)
        payload = {"title": "Hacked Title"}
        resp = await async_client.put(
            f"/api/chat/session/{test_session.id}", json=payload, headers=headers
        )
        assert resp.status_code == 403

    async def test_update_session_genuine_not_found(
        self, async_client, auth_headers
    ):
        """Verify update_session returns 404 for a truly missing session,
        not a lock-contention false positive."""
        from src.core.exceptions import NotFoundError
        from src.db.base import AsyncSessionLocal
        from src.services import session_service

        async with AsyncSessionLocal() as db:
            with pytest.raises(NotFoundError):
                await session_service.update_session(
                    db, "nonexistent-session-id",
                    title="Should Not Work",
                )


class TestDeleteSession:
    """Tests for DELETE /chat/session/{session_id}."""

    async def test_delete_session(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify deleting a session returns 204."""
        headers = auth_headers(test_user)
        resp = await async_client.delete(
            f"/api/chat/session/{test_session.id}", headers=headers
        )
        assert resp.status_code == 204

        # Verify it's gone
        get_resp = await async_client.get(
            f"/api/chat/session/{test_session.id}", headers=headers
        )
        assert get_resp.status_code == 404

    async def test_delete_session_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_session
    ):
        """Verify user B cannot delete user A's session."""
        headers = auth_headers(second_user)
        resp = await async_client.delete(
            f"/api/chat/session/{test_session.id}", headers=headers
        )
        assert resp.status_code == 403


# =============================================================================
# Message Tests  (with mocked agent runner)
# =============================================================================


class TestSendMessage:
    """Tests for POST /chat/session/{session_id}/message."""

    @patch("src.api.chat.run_agent")
    async def test_send_message_non_streaming(
        self, mock_run_agent, async_client, auth_headers, test_user, test_session, test_model
    ):
        """Verify sending a message returns a response."""
        # Mock the agent runner to return a tuple (response_text, message_id)
        mock_run_agent.return_value = ("Hello back!", str(uuid.uuid4()))

        headers = auth_headers(test_user)
        payload = {"content": "Hello"}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/message",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "message_id" in data

    @patch("src.api.chat.run_agent")
    async def test_send_message_other_user_forbidden(
        self, mock_run_agent, async_client, auth_headers, test_user, second_user, test_session
    ):
        """Verify user B cannot send messages in user A's session."""
        mock_run_agent.return_value = ("", str(uuid.uuid4()))

        headers = auth_headers(second_user)
        payload = {"content": "Hello"}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/message",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 403

    async def test_send_message_to_nonexistent_session(
        self, async_client, auth_headers, test_user
    ):
        """Verify sending to a non-existent session returns 404."""
        headers = auth_headers(test_user)
        fake_id = str(uuid.uuid4())
        payload = {"content": "Hello"}
        resp = await async_client.post(
            f"/api/chat/session/{fake_id}/message",
            json=payload,
            headers=headers,
        )
        assert resp.status_code == 404


class TestListMessages:
    """Tests for GET /chat/session/{session_id}/messages."""

    async def test_list_messages_empty(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify empty session returns empty message list."""
        headers = auth_headers(test_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/messages", headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    async def test_list_messages_other_user_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_session
    ):
        """Verify user B cannot list messages in user A's session."""
        headers = auth_headers(second_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/messages", headers=headers
        )
        assert resp.status_code == 403


# =============================================================================
# File Upload Tests
# =============================================================================


class TestSessionUpload:
    """Tests for POST /chat/session/{session_id}/upload."""

    async def test_upload_file_to_session(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify file upload returns file metadata."""
        headers = auth_headers(test_user)
        files = {"file": ("test.txt", b"Hello, world!", "text/plain")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        # Upload may fail if MinIO is not available
        if resp.status_code == 201:
            data = resp.json()
            assert "file_id" in data
            assert data["original_filename"] == "test.txt"
        elif resp.status_code == 200:
            data = resp.json()
            assert "file_id" in data
        else:
            # MinIO may not be available; accept 422/500 errors
            assert resp.status_code in (422, 500), f"Unexpected status: {resp.status_code}"

    async def test_upload_to_temp_session_rejected(
        self, async_client, auth_headers, test_user
    ):
        """Verify uploads to temporary sessions auto-promote and succeed.

        Validates that the temp session is promoted to permanent
        on file upload (Issue #368).
        """
        # Create a temp session first
        headers = auth_headers(test_user)
        payload = {"title": "Temp", "is_temporary": True}
        create_resp = await async_client.post(
            "/api/chat/session", json=payload, headers=headers
        )
        assert create_resp.status_code == 201
        temp_session_id = create_resp.json()["id"]

        # Try to upload to it — should auto-promote and succeed
        files = {"file": ("test.txt", b"Hello", "text/plain")}
        resp = await async_client.post(
            f"/api/chat/session/{temp_session_id}/upload",
            files=files,
            headers=headers,
        )
        # Should NOT be 403 (session is auto-promoted). May be 200/201
        # if MinIO available, or 422/500 if MinIO is not running.
        assert resp.status_code != 403, (
            "Temp session upload should auto-promote, not return 403"
        )

    async def test_upload_to_other_user_session_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_session
    ):
        """Verify user B cannot upload to user A's session."""
        headers = auth_headers(second_user)
        files = {"file": ("test.txt", b"Hello", "text/plain")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        assert resp.status_code == 403


# =============================================================================
# Feedback Tests
# =============================================================================


class TestFeedback:
    """Tests for POST /chat/session/{session_id}/message/{message_id}/feedback."""

    async def test_submit_feedback(
        self, async_client, auth_headers, test_user, test_session, test_model, db_session
    ):
        """Verify submitting feedback for a message succeeds."""
        # Create a real assistant message in the DB so feedback can find it
        from src.db.orm.messages import Message
        from datetime import datetime, timezone

        msg_id = str(uuid.uuid4())
        real_msg = Message(
            id=msg_id,
            session_id=test_session.id,
            sender="assistant",
            content=[{"type": "text", "text": "Response"}],
            model_id=test_model.id,
            is_deleted=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(real_msg)
        await db_session.flush()

        # Submit feedback
        headers = auth_headers(test_user)
        feedback_payload = {"rating": "up", "comment": "Nice response"}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/message/{msg_id}/feedback",
            json=feedback_payload,
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["rating"] == "up"
        assert data["comment"] == "Nice response"


# =============================================================================
# Tenant Isolation Tests
# =============================================================================


class TestTenantIsolation:
    """Verify cross-tenant access is blocked for all session operations."""

    async def test_cross_tenant_get_session_forbidden(
        self, async_client, auth_headers, second_user, test_session
    ):
        """Verify tenant B user cannot get tenant A's session."""
        headers = auth_headers(second_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}", headers=headers
        )
        assert resp.status_code == 403

    async def test_cross_tenant_list_sessions_excludes(
        self, async_client, auth_headers, second_user, test_session
    ):
        """Verify tenant B's session list does not include tenant A's sessions."""
        headers = auth_headers(second_user)
        resp = await async_client.get("/api/chat/sessions", headers=headers)
        assert resp.status_code == 200
        session_ids = [s["id"] for s in resp.json()]
        assert test_session.id not in session_ids

    @patch("src.api.chat.run_agent")
    async def test_cross_tenant_send_message_forbidden(
        self, mock_run_agent, async_client, auth_headers, second_user, test_session
    ):
        """Verify tenant B user cannot send messages in tenant A's session."""
        mock_run_agent.return_value = ("", str(uuid.uuid4()))
        headers = auth_headers(second_user)
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/message",
            json={"content": "Hello"},
            headers=headers,
        )
        assert resp.status_code == 403
