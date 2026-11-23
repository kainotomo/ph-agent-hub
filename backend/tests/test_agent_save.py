# =============================================================================
# PH Agent Hub — Agent "save instead of return" Helper Tests (Issue #626)
# =============================================================================
# Unit tests for ``src/tools/_agent_save.py``: filename derivation, the
# SHA-256 manifest field, and every named error path.
#
# The service call is patched — these tests never touch MinIO or the DB.
# =============================================================================

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import ValidationError
from src.tools._agent_save import (
    CODE_EMPTY_TEXT,
    CODE_FAILED,
    CODE_QUOTA,
    CODE_TOO_LARGE,
    CODE_UNAVAILABLE,
    filename_for_url,
    save_extracted_text,
    text_digest,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# filename_for_url
# ---------------------------------------------------------------------------


class TestFilenameForUrl:
    def test_derives_host_and_stem(self):
        assert (
            filename_for_url("https://www.sec.gov/Archives/data/report.pdf")
            == "sec.gov-report.txt"
        )

    def test_strips_www_and_extension(self):
        assert (
            filename_for_url("https://example.com/page.html")
            == "example.com-page.txt"
        )

    def test_ignores_query_and_trailing_slash(self):
        assert (
            filename_for_url("https://example.com/a/b/?q=1")
            == "example.com-b.txt"
        )

    def test_host_only_url(self):
        assert filename_for_url("https://example.com/") == "example.com.txt"

    def test_falls_back_without_host(self):
        assert filename_for_url("not-a-url") == "document.txt"

    def test_result_is_short_and_safe(self):
        name = filename_for_url("https://example.com/" + "x" * 400 + ".pdf")
        assert name.endswith(".txt")
        assert len(name) <= 85
        assert "/" not in name and " " not in name


# ---------------------------------------------------------------------------
# text_digest
# ---------------------------------------------------------------------------


class TestTextDigest:
    def test_matches_utf8_sha256(self):
        text = "Total revenue — 1,234\n"
        assert text_digest(text) == hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()

    def test_digest_is_stable_across_calls(self):
        assert text_digest("abc") == text_digest("abc")


# ---------------------------------------------------------------------------
# save_extracted_text — manifest and error paths
# ---------------------------------------------------------------------------


def _fake_upload(file_id: str = "file-1", filename: str = "doc.txt"):
    upload = MagicMock()
    upload.id = file_id
    upload.original_filename = filename
    return upload


def _call(**overrides):
    """Call save_extracted_text with sensible defaults plus *overrides*."""
    kwargs = {
        "db": MagicMock(name="db"),
        "text": "hello world",
        "url": "https://example.com/doc",
        "filename": "doc.txt",
        "session_id": "session-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
    }
    kwargs.update(overrides)
    return save_extracted_text(**kwargs)


class TestSaveExtractedText:
    async def test_returns_manifest_without_text(self):
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(return_value=_fake_upload()),
        ):
            result = await _call(text="x" * 10_000)

        assert "text" not in result
        assert result["saved"] is True
        assert result["file_id"] == "file-1"
        assert result["filename"] == "doc.txt"
        assert result["chars"] == 10_000
        assert result["url"] == "https://example.com/doc"

    async def test_preview_is_capped_at_500_chars(self):
        text = "y" * 12_000
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(return_value=_fake_upload()),
        ):
            result = await _call(text=text)

        assert len(result["preview"]) == 500
        assert result["preview"] == text[:500]

    async def test_sha256_matches_the_saved_text(self):
        text = "Total revenue 42\nNet income 7"
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(return_value=_fake_upload()),
        ):
            result = await _call(text=text)

        assert result["sha256"] == hashlib.sha256(
            text.encode("utf-8")
        ).hexdigest()

    async def test_extra_metadata_is_merged(self):
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(return_value=_fake_upload()),
        ):
            result = await _call(extra={"pages": 9, "status_code": 200})

        assert result["pages"] == 9
        assert result["status_code"] == 200

    async def test_no_db_context_is_unavailable(self):
        result = await _call(db=None)
        assert result["error_code"] == CODE_UNAVAILABLE
        assert "text" not in result

    async def test_missing_user_is_unavailable(self):
        result = await _call(user_id=None)
        assert result["error_code"] == CODE_UNAVAILABLE

    async def test_empty_text_is_rejected_locally(self):
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(return_value=_fake_upload()),
        ) as create:
            result = await _call(text="   \n  ")

        assert result["error_code"] == CODE_EMPTY_TEXT
        create.assert_not_awaited()

    async def test_quota_error_is_named(self):
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(
                side_effect=ValidationError(
                    "Save limit reached for this session (50 documents)."
                )
            ),
        ):
            result = await _call()

        assert result["error_code"] == CODE_QUOTA
        assert "Save limit reached" in result["error"]

    async def test_too_large_error_is_named(self):
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(
                side_effect=ValidationError(
                    "Document is 9 characters, above the 4-character save limit."
                )
            ),
        ):
            result = await _call()

        # Both messages mention a "save limit"; the size one must not be
        # reported as a quota failure.
        assert result["error_code"] == CODE_TOO_LARGE

    async def test_unexpected_failure_is_named(self):
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(side_effect=RuntimeError("minio down")),
        ):
            result = await _call()

        assert result["error_code"] == CODE_FAILED
        assert "minio down" in result["error"]


# ---------------------------------------------------------------------------
# Concurrency guard (parallel tool batches share one AsyncSession)
# ---------------------------------------------------------------------------


class TestSaveLock:
    async def test_lock_is_reused_within_a_loop(self):
        from src.tools._agent_save import _save_lock

        assert _save_lock() is _save_lock()

    async def test_concurrent_saves_are_serialised(self):
        order: list[str] = []

        async def slow_create(*args, **kwargs):
            order.append("enter")
            await asyncio.sleep(0.01)
            order.append("exit")
            return _fake_upload()

        with patch(
            "src.services.upload_service.create_text_upload",
            new=slow_create,
        ):
            await asyncio.gather(
                _call(text="a" * 10),
                _call(text="b" * 10),
            )

        # No interleaving: the second save waits for the first to finish.
        assert order == ["enter", "exit", "enter", "exit"]
