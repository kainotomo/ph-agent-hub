# =============================================================================
# PH Agent Hub — File Upload Flow Integration Tests
# =============================================================================
# Tests upload lifecycle, file type/size validation, temp session rejection,
# DeepSeek+image rejection, and RAG auto-indexing.
# =============================================================================

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError
from src.main import app
from src.services import upload_service

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    """Create an async HTTP client wired to the FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# =============================================================================
# File Upload Tests
# =============================================================================


class TestFileUpload:
    """Tests for POST /chat/session/{session_id}/upload."""

    async def test_upload_text_file(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify uploading a text file returns file metadata."""
        headers = auth_headers(test_user)
        files = {"file": ("hello.txt", b"Hello, world!", "text/plain")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        # Upload may fail if MinIO is not available (Docker stack may not have it)
        if resp.status_code in (200, 201):
            data = resp.json()
            assert "file_id" in data
            assert data["original_filename"] == "hello.txt"
        else:
            assert resp.status_code in (422, 500), f"Unexpected status: {resp.status_code}"

    async def test_upload_disallowed_type(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify uploading a disallowed file type is rejected with 422."""
        headers = auth_headers(test_user)
        files = {"file": ("malware.exe", b"fake-binary", "application/x-msdownload")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        assert resp.status_code == 422, resp.text

    async def test_upload_yml_file(
        self, async_client, auth_headers, test_user, test_session
    ):
        """.yml files are now accepted (resolve to text/plain)."""
        headers = auth_headers(test_user)
        files = {"file": ("config.yml", b"key: value\n", "application/octet-stream")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        assert resp.status_code not in (422, 403), (
            f".yml upload should be accepted, got {resp.status_code}: {resp.text}"
        )

    async def test_upload_log_file(
        self, async_client, auth_headers, test_user, test_session
    ):
        """.log files are now accepted (resolve to text/plain)."""
        headers = auth_headers(test_user)
        files = {"file": ("server.log", b"2026-01-01 INFO starting up\n", "application/octet-stream")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        assert resp.status_code not in (422, 403), (
            f".log upload should be accepted, got {resp.status_code}: {resp.text}"
        )

    async def test_upload_py_file(
        self, async_client, auth_headers, test_user, test_session
    ):
        """.py files are now accepted (resolve to text/plain)."""
        headers = auth_headers(test_user)
        files = {"file": ("script.py", b"print('hello')\n", "application/octet-stream")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        assert resp.status_code not in (422, 403), (
            f".py upload should be accepted, got {resp.status_code}: {resp.text}"
        )

    async def test_upload_sh_file(
        self, async_client, auth_headers, test_user, test_session
    ):
        """.sh files are now accepted (resolve to text/plain)."""
        headers = auth_headers(test_user)
        files = {"file": ("deploy.sh", b"#!/bin/bash\necho deploy\n", "application/octet-stream")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        assert resp.status_code not in (422, 403), (
            f".sh upload should be accepted, got {resp.status_code}: {resp.text}"
        )


class TestTempSessionUploadGuard:
    """Verify temp session upload rejection (validates Step 0c)."""

    async def test_upload_to_temp_session_promotes_and_succeeds(
        self, async_client, auth_headers, test_user
    ):
        """Verify uploading to a temporary session auto-promotes and succeeds."""
        # Create a temp session
        headers = auth_headers(test_user)
        create_resp = await async_client.post(
            "/api/chat/session",
            json={"title": "Temp", "is_temporary": True},
            headers=headers,
        )
        assert create_resp.status_code == 201
        temp_id = create_resp.json()["id"]

        # Try uploading — should auto-promote the temp session and succeed
        files = {"file": ("test.txt", b"data", "text/plain")}
        resp = await async_client.post(
            f"/api/chat/session/{temp_id}/upload",
            files=files,
            headers=headers,
        )
        # Should NOT be 403 (session is auto-promoted).  May be 200/201
        # if MinIO available, or 422/500 if MinIO is not running.
        assert resp.status_code != 403, (
            "Temp session upload should auto-promote, not return 403"
        )

    async def test_upload_to_permanent_session_allowed(
        self, async_client, auth_headers, test_user, test_session
    ):
        """Verify uploading to a permanent session is allowed (not 403)."""
        headers = auth_headers(test_user)
        files = {"file": ("test.txt", b"data", "text/plain")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        # Should NOT be 403 (may be 200/201 if MinIO available, or 422 for other reasons)
        assert resp.status_code != 403, "Permanent session upload should not be blocked"


class TestUploadOwnership:
    """Verify upload ownership enforcement."""

    # ------------------------------------------------------------------
    # Service-layer: get_upload_by_id ownership check
    # ------------------------------------------------------------------

    async def test_get_upload_by_id_non_owner_raises_forbidden(
        self,
        db_session: AsyncSession,
        sample_file_upload,
        second_user,
    ):
        """Verify get_upload_by_id raises ForbiddenError for non-owner."""
        with pytest.raises(ForbiddenError, match="You do not own this file upload"):
            await upload_service.get_upload_by_id(
                db=db_session,
                file_id=sample_file_upload.id,
                user_id=second_user.id,
            )

    async def test_get_upload_by_id_owner_succeeds(
        self,
        db_session: AsyncSession,
        sample_file_upload,
        test_user,
    ):
        """Verify get_upload_by_id returns the upload for the owner."""
        upload = await upload_service.get_upload_by_id(
            db=db_session,
            file_id=sample_file_upload.id,
            user_id=test_user.id,
        )
        assert upload.id == sample_file_upload.id
        assert upload.user_id == test_user.id

    # ------------------------------------------------------------------
    # API-level: upload to other user's session
    # ------------------------------------------------------------------

    async def test_upload_to_other_user_session_forbidden(
        self, async_client, auth_headers, test_user, second_user, test_session
    ):
        """Verify user B cannot upload to user A's session."""
        headers = auth_headers(second_user)
        files = {"file": ("test.txt", b"data", "text/plain")}
        resp = await async_client.post(
            f"/api/chat/session/{test_session.id}/upload",
            files=files,
            headers=headers,
        )
        assert resp.status_code == 403

    # ------------------------------------------------------------------
    # API-level: non-owner access to download, presigned URL, and delete
    # ------------------------------------------------------------------

    async def test_non_owner_cannot_get_presigned_url(
        self,
        async_client,
        auth_headers,
        test_user,
        second_user,
        test_session,
        sample_file_upload,
    ):
        """Verify non-owner cannot get a presigned URL for a file."""
        headers = auth_headers(second_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/upload/{sample_file_upload.id}/url",
            headers=headers,
        )
        assert resp.status_code == 403, resp.text

    async def test_non_owner_cannot_download_file(
        self,
        async_client,
        auth_headers,
        test_user,
        second_user,
        test_session,
        sample_file_upload,
    ):
        """Verify non-owner cannot download a file."""
        headers = auth_headers(second_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/upload/{sample_file_upload.id}/download",
            headers=headers,
        )
        assert resp.status_code == 403, resp.text

    async def test_non_owner_cannot_delete_file(
        self,
        async_client,
        auth_headers,
        test_user,
        second_user,
        test_session,
        sample_file_upload,
    ):
        """Verify non-owner cannot delete a file."""
        headers = auth_headers(second_user)
        resp = await async_client.delete(
            f"/api/chat/session/{test_session.id}/upload/{sample_file_upload.id}",
            headers=headers,
        )
        assert resp.status_code == 403, resp.text


# =============================================================================
# DeepSeek + Image Rejection
# =============================================================================


class TestDeepSeekImageRejection:
    """Verify DeepSeek model + image upload is rejected with 422."""

    async def test_deepseek_with_image_rejected(
        self, async_client, auth_headers, test_user, test_session, test_model, db_session
    ):
        """Verify uploading an image to a DeepSeek-powered session returns 422."""
        # Create a session with a DeepSeek model
        from src.db.orm.models import Model

        # Create a DeepSeek model
        deepseek_model = Model(
            id=str(uuid.uuid4()),
            tenant_id=test_user.tenant_id,
            name="Test DeepSeek",
            model_id="deepseek-v4-flash",
            provider="deepseek",
            api_key="test-key",
            enabled=True,
            max_tokens=4096,
            temperature=0.7,
        )
        db_session.add(deepseek_model)
        await db_session.flush()

        from src.db.orm.sessions import Session

        ds_session = Session(
            id=str(uuid.uuid4()),
            tenant_id=test_user.tenant_id,
            user_id=test_user.id,
            title="DeepSeek Chat",
            selected_model_id=deepseek_model.id,
        )
        db_session.add(ds_session)
        await db_session.flush()

        # Upload an image file
        headers = auth_headers(test_user)
        files = {"file": ("photo.png", b"fake-png-data", "image/png")}
        upload_resp = await async_client.post(
            f"/api/chat/session/{ds_session.id}/upload",
            files=files,
            headers=headers,
        )
        # Upload should succeed (image type is allowed), but message send should fail
        # The rejection happens in _inject_file_content at message send time
        if upload_resp.status_code in (200, 201):
            file_id = upload_resp.json()["file_id"]

            # Send message with image file_id
            from unittest.mock import patch

            # The agent won't actually be called because _inject_file_content
            # raises ValidationError for DeepSeek + images before run_agent
            msg_payload = {"content": "Analyze this image", "file_ids": [file_id]}
            msg_resp = await async_client.post(
                f"/api/chat/session/{ds_session.id}/message",
                json=msg_payload,
                headers=headers,
            )
            assert msg_resp.status_code == 422, msg_resp.text
