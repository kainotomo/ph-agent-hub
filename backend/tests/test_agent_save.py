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
from src.tools import _agent_save as agent_save_mod
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


@pytest.fixture(autouse=True)
def _fake_save_session(monkeypatch):
    """Keep every save-related unit test off the real DB (Issue #635)."""
    session = MagicMock(name="save-session")
    session.close = AsyncMock()
    monkeypatch.setattr(
        agent_save_mod,
        "_open_save_session",
        AsyncMock(return_value=session),
    )
    return session


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

    # ---- Issue #635: the save writes on its own session ------------------

    async def test_save_writes_on_an_isolated_session_not_the_runner(
        self, _fake_save_session
    ):
        runner_db = MagicMock(name="runner-db")
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(return_value=_fake_upload("file-9")),
        ) as create:
            result = await _call(db=runner_db)

        assert result["file_id"] == "file-9"
        assert create.await_args.args[0] is _fake_save_session
        assert create.await_args.args[0] is not runner_db
        runner_db.add.assert_not_called()
        runner_db.commit.assert_not_called()
        _fake_save_session.close.assert_awaited_once()

    async def test_save_succeeds_when_runner_commit_would_raise_stale_data(self):
        from sqlalchemy.orm.exc import StaleDataError

        runner_db = MagicMock(name="runner-db")
        runner_db.commit = AsyncMock(
            side_effect=StaleDataError(
                "Record has changed since last read in table 'file_uploads'"
            )
        )

        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(return_value=_fake_upload("file-1")),
        ):
            result = await _call(db=runner_db)

        assert result["saved"] is True
        assert result["file_id"] == "file-1"
        runner_db.commit.assert_not_called()

    async def test_isolated_session_is_closed_on_failure(self, _fake_save_session):
        with patch(
            "src.services.upload_service.create_text_upload",
            new=AsyncMock(side_effect=RuntimeError("minio down")),
        ):
            result = await _call()

        assert result["error_code"] == CODE_FAILED
        _fake_save_session.close.assert_awaited_once()

    async def test_parallel_saves_use_distinct_sessions(self, monkeypatch):
        first = MagicMock(name="save-session-1")
        first.close = AsyncMock()
        second = MagicMock(name="save-session-2")
        second.close = AsyncMock()
        monkeypatch.setattr(
            agent_save_mod,
            "_open_save_session",
            AsyncMock(side_effect=[first, second]),
        )

        create = AsyncMock(
            side_effect=[_fake_upload("file-a"), _fake_upload("file-b")]
        )
        with patch(
            "src.services.upload_service.create_text_upload",
            new=create,
        ):
            results = await asyncio.gather(
                _call(text="a" * 10),
                _call(text="b" * 10),
            )

        assert {r["file_id"] for r in results} == {"file-a", "file-b"}
        used_sessions = [call.args[0] for call in create.await_args_list]
        assert {id(s) for s in used_sessions} == {id(first), id(second)}
        first.close.assert_awaited_once()
        second.close.assert_awaited_once()


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
