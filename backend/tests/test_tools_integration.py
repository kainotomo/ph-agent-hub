# =============================================================================
# PH Agent Hub — Integration Tools Unit Tests
# =============================================================================
# Tests for built-in integration tool factories: github, erpnext, browser,
# file_list, memory, rag_search, mcp, and the _oauth_refresh helper.
#
# All external API calls (httpx, Playwright, S3, DB) are mocked — no real
# network requests or database connections.
# =============================================================================

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Module markers — pure unit tests, no DB / no network
# ---------------------------------------------------------------------------
pytestmark = [pytest.mark.unit]


# ===========================================================================
# Shared mock helpers
# ===========================================================================


def _make_mock_httpx_response(
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
    headers: dict | None = None,
):
    """Return an AsyncMock that behaves like an httpx.Response.

    Note: ``response.json()``, ``raise_for_status()``, and ``.text`` are
    *synchronous* in httpx, so we use plain ``Mock`` for those.
    """
    mock = AsyncMock()
    mock.status_code = status_code
    mock.raise_for_status = Mock()
    mock.json = Mock(return_value=json_data or {})
    mock.text = text
    mock.headers = headers or {"content-type": "application/json"}
    mock.url = "http://example.com"
    return mock


def _make_mock_httpx_client(mock_response):
    """Return an AsyncMock that behaves like an async context-manager
    httpx.AsyncClient with ``.get`` returning *mock_response*."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get = AsyncMock(return_value=mock_response)
    client.post = AsyncMock(return_value=mock_response)
    client.put = AsyncMock(return_value=mock_response)
    client.delete = AsyncMock(return_value=mock_response)
    client.patch = AsyncMock(return_value=mock_response)
    return client


def _make_mock_db_session():
    """Return a mock object that behaves like an async SQLAlchemy session.

    ``await db.execute(...)`` returns a MagicMock whose ``.scalars()``,
    ``.scalar_one_or_none()``, ``.all()``, and ``.first()`` methods all
    return sensible defaults.  Individual tests can override the result
    by replacing ``db.execute`` with an ``async def``.

    We deliberately avoid ``AsyncMock`` for ``execute`` because ``await``
    on an AsyncMock returns ``return_value`` (not the mock itself), which
    breaks the chain.  However, ``commit``, ``flush``, and ``rollback``
    are awaited, so they use AsyncMock.
    """
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()

    async def default_execute(*args, **kwargs):
        result = MagicMock()
        result.scalars.return_value = result
        result.all.return_value = []
        result.first.return_value = None
        result.scalar_one_or_none.return_value = None
        return result

    session.execute = default_execute
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


def _make_mock_orm_row(**kwargs):
    """Return a MagicMock that behaves like a SQLAlchemy ORM row."""
    row = MagicMock()
    for key, value in kwargs.items():
        setattr(row, key, value)
    return row


# ===================================================================
# 1. _oauth_refresh.py — OAuth Token Refresh Helper
# ===================================================================
# Note: Both ``is_token_expired`` and ``refresh_oauth_token`` are
# imported *inside* function bodies (lazy imports), so we patch them
# at the source module (``src.core.oauth``) rather than at the target
# module (``src.tools._oauth_refresh``).
# ===================================================================
class TestEnsureFreshToken:
    """Tests for ``ensure_fresh_token()``."""

    async def test_token_not_expired_returns_true(self):
        with patch("src.core.oauth.is_token_expired", return_value=False):
            from src.tools._oauth_refresh import ensure_fresh_token

            result = await ensure_fresh_token(
                tokens={"access_token": "abc", "expires_at": 9999999999},
                provider="gmail",
            )
            assert result is True

    async def test_expired_with_refresh_succeeds(self):
        with (
            patch("src.core.oauth.is_token_expired", return_value=True),
            patch(
                "src.tools._oauth_refresh.refresh_token_if_expired",
                return_value={"access_token": "new_token", "expires_at": 9999999999},
            ),
        ):
            from src.tools._oauth_refresh import ensure_fresh_token

            result = await ensure_fresh_token(
                tokens={"access_token": "old", "refresh_token": "rt", "expires_at": 0},
                provider="gmail",
            )
            assert result is True

    async def test_expired_with_refresh_fails_returns_false(self):
        with (
            patch("src.core.oauth.is_token_expired", return_value=True),
            patch(
                "src.tools._oauth_refresh.refresh_token_if_expired",
                return_value=None,
            ),
        ):
            from src.tools._oauth_refresh import ensure_fresh_token

            result = await ensure_fresh_token(
                tokens={"access_token": "old", "refresh_token": "rt", "expires_at": 0},
                provider="gmail",
            )
            assert result is False

    async def test_expired_no_refresh_token_returns_false(self):
        with patch("src.core.oauth.is_token_expired", return_value=True):
            from src.tools._oauth_refresh import ensure_fresh_token

            result = await ensure_fresh_token(
                tokens={"access_token": "old", "expires_at": 0},
                provider="gmail",
            )
            assert result is False


class TestRefreshTokenIfExpired:
    """Tests for ``refresh_token_if_expired()``."""

    async def test_no_refresh_token_returns_none(self):
        from src.tools._oauth_refresh import refresh_token_if_expired

        result = await refresh_token_if_expired(
            tokens={"access_token": "old", "expires_at": 0},
            provider="gmail",
        )
        assert result is None

    async def test_gmail_provider_uses_google_creds(self):
        with (
            patch("src.core.config.settings", GOOGLE_CLIENT_ID="gid", GOOGLE_CLIENT_SECRET="gsecret"),
            patch(
                "src.core.oauth.refresh_oauth_token",
                return_value={"access_token": "new", "expires_at": 999},
            ),
        ):
            from src.tools._oauth_refresh import refresh_token_if_expired

            tokens = {"access_token": "old", "refresh_token": "rt", "expires_at": 0}
            result = await refresh_token_if_expired(tokens, provider="gmail")
            assert result is not None
            assert result["access_token"] == "new"

    async def test_outlook_provider_uses_ms_creds(self):
        with (
            patch("src.core.config.settings", MS_CLIENT_ID="msid", MS_CLIENT_SECRET="mssecret"),
            patch(
                "src.core.oauth.refresh_oauth_token",
                return_value={"access_token": "new_ms", "expires_at": 999},
            ),
        ):
            from src.tools._oauth_refresh import refresh_token_if_expired

            tokens = {"access_token": "old", "refresh_token": "rt", "expires_at": 0}
            result = await refresh_token_if_expired(tokens, provider="outlook")
            assert result is not None
            assert result["access_token"] == "new_ms"

    async def test_token_updated_in_place(self):
        with (
            patch("src.core.config.settings", GOOGLE_CLIENT_ID="x", GOOGLE_CLIENT_SECRET="y"),
            patch(
                "src.core.oauth.refresh_oauth_token",
                return_value={"access_token": "new_token", "expires_at": 999},
            ),
        ):
            from src.tools._oauth_refresh import refresh_token_if_expired

            tokens = {"access_token": "old", "refresh_token": "rt", "expires_at": 0}
            result = await refresh_token_if_expired(tokens, provider="gmail")
            assert tokens["access_token"] == "new_token"
            assert tokens["expires_at"] == 999
            assert result is tokens

    async def test_refresh_token_rotation(self):
        with (
            patch("src.core.config.settings", GOOGLE_CLIENT_ID="x", GOOGLE_CLIENT_SECRET="y"),
            patch(
                "src.core.oauth.refresh_oauth_token",
                return_value={
                    "access_token": "new_token",
                    "expires_at": 999,
                    "refresh_token": "new_rt",
                },
            ),
        ):
            from src.tools._oauth_refresh import refresh_token_if_expired

            tokens = {"access_token": "old", "refresh_token": "old_rt", "expires_at": 0}
            await refresh_token_if_expired(tokens, provider="gmail")
            assert tokens["refresh_token"] == "new_rt"

    async def test_persists_to_db(self):
        credential_orm = MagicMock()
        tokens_dict = {"access_token": "old", "refresh_token": "rt", "expires_at": 0}
        db = AsyncMock()

        with (
            patch("src.core.config.settings", GOOGLE_CLIENT_ID="x", GOOGLE_CLIENT_SECRET="y"),
            patch(
                "src.core.oauth.refresh_oauth_token",
                return_value={"access_token": "new", "expires_at": 999},
            ),
        ):
            from src.tools._oauth_refresh import refresh_token_if_expired

            tokens = dict(tokens_dict)
            await refresh_token_if_expired(
                tokens, provider="gmail", credential_orm=credential_orm,
                tokens_dict=tokens_dict, db=db,
            )
            db.add.assert_called_once_with(credential_orm)
            db.commit.assert_awaited_once()

    async def test_tokens_dict_synced_when_different(self):
        credential_orm = MagicMock()
        tokens = {"access_token": "old", "refresh_token": "rt", "expires_at": 0}
        tokens_dict = dict(tokens)  # different object

        with (
            patch("src.core.config.settings", GOOGLE_CLIENT_ID="x", GOOGLE_CLIENT_SECRET="y"),
            patch(
                "src.core.oauth.refresh_oauth_token",
                return_value={"access_token": "new", "expires_at": 999, "refresh_token": "new_rt"},
            ),
        ):
            from src.tools._oauth_refresh import refresh_token_if_expired

            await refresh_token_if_expired(
                tokens, provider="gmail", credential_orm=credential_orm,
                tokens_dict=tokens_dict, db=AsyncMock(),
            )
            assert tokens_dict["access_token"] == "new"
            assert tokens_dict["refresh_token"] == "new_rt"

    async def test_api_exception_returns_none(self):
        with (
            patch("src.core.config.settings", GOOGLE_CLIENT_ID="x", GOOGLE_CLIENT_SECRET="y"),
            patch(
                "src.core.oauth.refresh_oauth_token",
                side_effect=Exception("Network error"),
            ),
        ):
            from src.tools._oauth_refresh import refresh_token_if_expired

            result = await refresh_token_if_expired(
                {"access_token": "old", "refresh_token": "rt", "expires_at": 0},
                provider="gmail",
            )
            assert result is None

    async def test_db_commit_failure_graceful(self):
        db = AsyncMock()
        db.commit.side_effect = Exception("DB error")

        with (
            patch("src.core.config.settings", GOOGLE_CLIENT_ID="x", GOOGLE_CLIENT_SECRET="y"),
            patch(
                "src.core.oauth.refresh_oauth_token",
                return_value={"access_token": "new", "expires_at": 999},
            ),
        ):
            from src.tools._oauth_refresh import refresh_token_if_expired

            tokens = {"access_token": "old", "refresh_token": "rt", "expires_at": 0}
            result = await refresh_token_if_expired(
                tokens, provider="gmail", credential_orm=MagicMock(),
                tokens_dict=dict(tokens), db=db,
            )
            assert result is not None  # tokens still fresh in-memory


# ===================================================================
# 2. memory.py — Agent Memory Tools
# ===================================================================
class TestMemoryTools:
    """Tests for ``build_memory_tools()``."""

    async def test_save_memory_creates_new(self):
        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.memory import build_memory_tools
        tools = build_memory_tools(db=db, user_id="user-1", tenant_id="tenant-1")
        save_memory = tools[0]

        result = await save_memory(key="name", value="Alice")
        assert result["action"] == "created"
        assert result["key"] == "name"
        assert result["value"] == "Alice"

    async def test_save_memory_updates_existing(self):
        db = _make_mock_db_session()
        existing = MagicMock()
        existing.value = "Bob"
        existing.source = "automatic"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.memory import build_memory_tools
        tools = build_memory_tools(db=db, user_id="user-1", tenant_id="tenant-1")
        save_memory = tools[0]

        result = await save_memory(key="name", value="Alice")
        assert result["action"] == "updated"
        assert existing.value == "Alice"

    async def test_save_memory_db_error_returns_error(self):
        db = _make_mock_db_session()
        async def mock_execute(*args, **kwargs):
            raise Exception("DB down")
        db.execute = mock_execute

        from src.tools.memory import build_memory_tools
        tools = build_memory_tools(db=db, user_id="user-1", tenant_id="tenant-1")
        save_memory = tools[0]

        result = await save_memory(key="name", value="Alice")
        assert result["action"] == "error"
        assert "Failed to save memory" in result["message"]

    async def test_delete_memory_automatic_success(self):
        db = _make_mock_db_session()
        existing = MagicMock()
        existing.source = "automatic"
        existing.id = "mem-1"
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.memory import build_memory_tools
        tools = build_memory_tools(db=db, user_id="user-1", tenant_id="tenant-1")
        delete_memory = tools[1]

        result = await delete_memory(key="name")
        assert result["action"] == "deleted"
        assert "deleted" in result["message"]

    async def test_delete_memory_not_found(self):
        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.memory import build_memory_tools
        tools = build_memory_tools(db=db, user_id="user-1", tenant_id="tenant-1")
        delete_memory = tools[1]

        result = await delete_memory(key="nonexistent")
        assert result["action"] == "not_found"

    async def test_delete_memory_db_error(self):
        db = _make_mock_db_session()
        async def mock_execute(*args, **kwargs):
            raise Exception("DB down")
        db.execute = mock_execute

        from src.tools.memory import build_memory_tools
        tools = build_memory_tools(db=db, user_id="user-1", tenant_id="tenant-1")
        delete_memory = tools[1]

        result = await delete_memory(key="name")
        assert result["action"] == "error"

    async def test_list_memory_returns_entries(self):
        db = _make_mock_db_session()
        entry = MagicMock()
        entry.key = "name"
        entry.value = "Alice"
        entry.source = "automatic"
        entry.created_at = None
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = [entry]
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.memory import build_memory_tools
        tools = build_memory_tools(db=db, user_id="user-1", tenant_id="tenant-1")
        list_memory = tools[2]

        result = await list_memory()
        assert len(result) == 1
        assert result[0]["key"] == "name"
        assert result[0]["value"] == "Alice"

    async def test_list_memory_empty(self):
        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.memory import build_memory_tools
        tools = build_memory_tools(db=db, user_id="user-1", tenant_id="tenant-1")
        list_memory = tools[2]

        result = await list_memory()
        assert result == []


# ===================================================================
# 3. file_list.py — File List Tools
# ===================================================================
class TestFileListTools:
    """Tests for ``build_file_list_tool()``.

    Like the memory tests, we pass a pre-configured mock db session into
    the factory to avoid unreliable ``__globals__`` access through the
    ``@tool`` decorator wrapper.
    """

    async def test_list_uploaded_files_normal_session(self):
        db = _make_mock_db_session()
        upload = _make_mock_orm_row(
            id="file-1",
            original_filename="test.txt",
            content_type="text/plain",
            size_bytes=1024,
            extracted_text="Hello world",
        )
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = [upload]
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.file_list import build_file_list_tool
        tools = build_file_list_tool(
            db=db, session_id="session-1", tenant_id="tenant-1",
        )
        list_files = tools[0]

        result = await list_files()
        assert len(result) == 1
        assert result[0]["filename"] == "test.txt"
        assert result[0]["content_type"] == "text/plain"
        assert result[0]["preview"] == "Hello world"

    async def test_list_uploaded_files_empty(self):
        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalars.return_value = result_mock
        result_mock.all.return_value = []
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.file_list import build_file_list_tool
        tools = build_file_list_tool(
            db=db, session_id="session-1", tenant_id="tenant-1",
        )
        list_files = tools[0]

        result = await list_files()
        assert result == []

    async def test_read_file_content_success(self):
        db = _make_mock_db_session()
        upload = _make_mock_orm_row(
            id="file-1",
            original_filename="test.txt",
            content_type="text/plain",
            size_bytes=1024,
            extracted_text="Full text content here",
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = upload
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.file_list import build_file_list_tool
        tools = build_file_list_tool(
            db=db, session_id="session-1", tenant_id="tenant-1",
        )
        read_file = tools[1]

        result = await read_file(file_id="00000000-0000-0000-0000-000000000001")
        assert result["filename"] == "test.txt"
        assert result["text"] == "Full text content here"
        assert result["truncated"] is False

    async def test_read_file_content_not_found(self):
        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.file_list import build_file_list_tool
        tools = build_file_list_tool(
            db=db, session_id="session-1", tenant_id="tenant-1",
        )
        read_file = tools[1]

        result = await read_file(file_id="00000000-0000-0000-0000-000000000001")
        assert "error" in result

    async def test_read_file_content_invalid_uuid(self):
        db = _make_mock_db_session()
        from src.tools.file_list import build_file_list_tool
        tools = build_file_list_tool(
            db=db, session_id="session-1", tenant_id="tenant-1",
        )
        read_file = tools[1]

        result = await read_file(file_id="not-a-uuid")
        assert "error" in result
        assert "Invalid" in result["error"]

    async def test_download_file_for_attachment_success(self):
        db = _make_mock_db_session()
        upload = _make_mock_orm_row(
            id="file-1",
            original_filename="test.txt",
            content_type="text/plain",
            size_bytes=1024,
            bucket="bucket-1",
            storage_key="key-1",
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = upload
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.file_list import build_file_list_tool
        tools = build_file_list_tool(
            db=db, session_id="session-1", tenant_id="tenant-1",
        )
        download = tools[2]

        with patch("src.tools.file_list.download_object", return_value=b"file content"):
            result = await download(file_id="00000000-0000-0000-0000-000000000001")
            assert result["filename"] == "test.txt"
            assert "content_base64" in result
            assert result["content_base64"] != ""

    async def test_download_file_for_attachment_s3_failure(self):
        db = _make_mock_db_session()
        upload = _make_mock_orm_row(
            id="file-1",
            original_filename="test.txt",
            content_type="text/plain",
            size_bytes=1024,
            bucket="bucket-1",
            storage_key="key-1",
        )
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = upload
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.file_list import build_file_list_tool
        tools = build_file_list_tool(
            db=db, session_id="session-1", tenant_id="tenant-1",
        )
        download = tools[2]

        with patch("src.tools.file_list.download_object", side_effect=Exception("S3 error")):
            result = await download(file_id="00000000-0000-0000-0000-000000000001")
            assert "download_error" in result
            assert result["content_base64"] == ""


# ===================================================================
# 4. github.py — GitHub/GitLab Integration Tools
# ===================================================================
class TestGithubRepoAllowlist:
    """Tests for ``_check_repo_allowed()``."""

    def test_exact_match_allowed(self):
        from src.tools.github import _check_repo_allowed
        assert _check_repo_allowed("myorg/myrepo", ["myorg/myrepo"])

    def test_glob_pattern_allowed(self):
        from src.tools.github import _check_repo_allowed
        assert _check_repo_allowed("myorg/any-repo", ["myorg/*"])

    def test_wildcard_allowed(self):
        from src.tools.github import _check_repo_allowed
        assert _check_repo_allowed("anything/here", ["*"])

    def test_no_allowlist_all_allowed(self):
        from src.tools.github import _check_repo_allowed
        assert _check_repo_allowed("any/repo", None)
        assert _check_repo_allowed("any/repo", [])

    def test_non_matching_blocked(self):
        from src.tools.github import _check_repo_allowed
        assert not _check_repo_allowed("other/repo", ["allowed/repo"])


class TestGithubTools:
    """Tests for ``build_github_tools()`` — GitHub provider."""

    @pytest.fixture
    def tools(self):
        from src.tools.github import build_github_tools
        return build_github_tools({
            "token": "mock-tenant-token",
            "allowed_repos": ["owner/repo"],
        })

    async def test_search_code_success(self, tools):
        search_code = tools[0]
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={
                "total_count": 1,
                "items": [{
                    "path": "src/main.py",
                    "name": "main.py",
                    "repository": {"full_name": "owner/repo"},
                    "html_url": "https://github.com/owner/repo/src/main.py",
                    "score": 1.0,
                }],
            },
            headers={"X-RateLimit-Remaining": "42"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await search_code(query="def main", repo="owner/repo")
            assert result["total_count"] == 1
            assert len(result["items"]) == 1
            assert result["rate_limit_remaining"] == "42"

    async def test_search_code_repo_not_allowed(self, tools):
        search_code = tools[0]
        result = await search_code(query="def main", repo="unallowed/repo")
        assert "error" in result
        assert "not in the allowed list" in result["error"]

    async def test_search_code_empty_query(self, tools):
        search_code = tools[0]
        result = await search_code(query="", repo="owner/repo")
        assert "error" in result

    async def test_list_issues_success(self, tools):
        list_issues = tools[1]
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data=[{"number": 1, "title": "Bug", "state": "open"}],
            headers={"X-RateLimit-Remaining": "99"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await list_issues(repo="owner/repo")
            assert "issues" in result

    async def test_get_file_content_success(self, tools):
        get_file = tools[2]
        import base64
        content = base64.b64encode(b"print('hello')").decode()
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={
                "content": content,
                "name": "main.py",
                "path": "src/main.py",
                "size": 100,
            },
            headers={"X-RateLimit-Remaining": "50"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await get_file(repo="owner/repo", path="src/main.py")
            assert "content" in result

    async def test_get_file_content_404(self, tools):
        get_file = tools[2]
        mock_resp = _make_mock_httpx_response(
            status_code=404,
            json_data={"message": "Not Found"},
            headers={"X-RateLimit-Remaining": "50"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await get_file(repo="owner/repo", path="missing.py")
            assert "error" in result

    async def test_list_pull_requests_success(self, tools):
        list_prs = tools[3]
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data=[{"number": 5, "title": "Fix bug", "state": "open"}],
            headers={"X-RateLimit-Remaining": "75"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await list_prs(repo="owner/repo")
            assert "pull_requests" in result

    async def test_create_issue_success(self, tools):
        create_issue = tools[4]
        # GitHub API wraps the result in "data" via _github_request
        mock_resp = _make_mock_httpx_response(
            status_code=201,
            json_data={"number": 10, "title": "New bug", "html_url": "http://example.com/10"},
            headers={"X-RateLimit-Remaining": "80"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await create_issue(repo="owner/repo", title="New bug", body="Details")
            assert result["number"] == 10
            assert result["title"] == "New bug"

    # ------------------------------------------------------------------
    # New write tools (Issue #441)
    # ------------------------------------------------------------------

    async def test_create_pull_request_success(self, tools):
        create_pr = tools[5]
        mock_resp = _make_mock_httpx_response(
            status_code=201,
            json_data={
                "number": 42,
                "title": "Fix the bug",
                "html_url": "http://example.com/42",
                "state": "open",
                "draft": False,
            },
            headers={"X-RateLimit-Remaining": "80"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await create_pr(repo="owner/repo", title="Fix the bug", head="feature", base="main")
            assert result["number"] == 42
            assert result["title"] == "Fix the bug"
            assert result["state"] == "open"
            assert result["draft"] is False

    async def test_create_pull_request_repo_not_allowed(self, tools):
        create_pr = tools[5]
        result = await create_pr(repo="unallowed/repo", title="Fix", head="feature", base="main")
        assert "error" in result
        assert "not in the allowed list" in result["error"]

    async def test_create_pull_request_missing_head(self, tools):
        create_pr = tools[5]
        result = await create_pr(repo="owner/repo", title="Fix", head="", base="main")
        assert "error" in result

    async def test_create_pull_request_missing_base(self, tools):
        create_pr = tools[5]
        result = await create_pr(repo="owner/repo", title="Fix", head="feature", base="")
        assert "error" in result

    async def test_comment_on_issue_success(self, tools):
        comment = tools[6]
        mock_resp = _make_mock_httpx_response(
            status_code=201,
            json_data={
                "id": 12345,
                "html_url": "http://example.com/#issuecomment-12345",
                "body": "Thanks for the report!",
            },
            headers={"X-RateLimit-Remaining": "70"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await comment(repo="owner/repo", issue_number=5, body="Thanks for the report!")
            assert result["id"] == 12345
            assert "Thanks" in result["body"]

    async def test_comment_on_issue_missing_body(self, tools):
        comment = tools[6]
        result = await comment(repo="owner/repo", issue_number=5, body="")
        assert "error" in result

    async def test_comment_on_issue_invalid_number(self, tools):
        comment = tools[6]
        result = await comment(repo="owner/repo", issue_number=0, body="test")
        assert "error" in result

    async def test_create_or_update_file_create_success(self, tools):
        file_tool = tools[7]
        mock_resp = _make_mock_httpx_response(
            status_code=201,
            json_data={
                "content": {"path": "src/new.py", "html_url": "http://example.com/src/new.py"},
                "commit": {"sha": "abc123"},
            },
            headers={"X-RateLimit-Remaining": "60"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await file_tool(repo="owner/repo", path="src/new.py", message="Add new file", content="print('hello')")
            assert result["action"] == "created"
            assert result["commit_sha"] == "abc123"

    async def test_create_or_update_file_update_success(self, tools):
        file_tool = tools[7]
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={
                "content": {"path": "src/existing.py", "html_url": "http://example.com/src/existing.py"},
                "commit": {"sha": "def456"},
            },
            headers={"X-RateLimit-Remaining": "60"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await file_tool(
                repo="owner/repo", path="src/existing.py", message="Update file",
                content="print('updated')", sha="existing-sha-123",
            )
            assert result["action"] == "updated"
            assert result["commit_sha"] == "def456"

    async def test_create_or_update_file_missing_message(self, tools):
        file_tool = tools[7]
        result = await file_tool(repo="owner/repo", path="src/test.py", message="", content="data")
        assert "error" in result

    async def test_merge_pull_request_success(self, tools):
        merge_pr = tools[8]
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={
                "merged": True,
                "message": "Pull Request successfully merged",
                "sha": "merge-sha-789",
            },
            headers={"X-RateLimit-Remaining": "50"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await merge_pr(repo="owner/repo", pull_number=42)
            assert result["merged"] is True
            assert result["sha"] == "merge-sha-789"

    async def test_merge_pull_request_invalid_number(self, tools):
        merge_pr = tools[8]
        result = await merge_pr(repo="owner/repo", pull_number=-1)
        assert "error" in result

    async def test_merge_pull_request_invalid_method(self, tools):
        merge_pr = tools[8]
        result = await merge_pr(repo="owner/repo", pull_number=1, merge_method="invalid")
        assert "error" in result

    async def test_add_issue_labels_success(self, tools):
        label_tool = tools[9]
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data=[
                {"name": "bug"},
                {"name": "urgent"},
            ],
            headers={"X-RateLimit-Remaining": "40"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await label_tool(repo="owner/repo", issue_number=10, labels=["bug", "urgent"])
            assert "labels" in result
            assert "bug" in result["labels"]
            assert "urgent" in result["labels"]

    async def test_add_issue_labels_empty_list(self, tools):
        label_tool = tools[9]
        result = await label_tool(repo="owner/repo", issue_number=10, labels=[])
        assert "error" in result

    async def test_rate_limit_exceeded(self, tools):
        search_code = tools[0]
        mock_resp = _make_mock_httpx_response(
            status_code=403,
            json_data={},
            text="rate limit exceeded",
            headers={"X-RateLimit-Remaining": "0"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await search_code(query="test", repo="owner/repo")
            assert "error" in result
            assert "Rate limit" in result["error"]

    # ------------------------------------------------------------------
    # Per-user credential tests (Issue #434)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_mock_credential(access_token: str, status: str = "active") -> object:
        """Return a mock ``UserToolCredential``-like object."""
        import json

        cred = MagicMock()
        cred.status = status
        cred.credentials = json.dumps({"access_token": access_token})
        return cred

    async def test_per_user_token_overrides_tenant_config(self):
        """Per-user active credential takes precedence over tool_config.token."""
        from src.tools.github import build_github_tools

        user_cred = self._make_mock_credential("mock-user-pat-abc123")
        tools = build_github_tools(
            {"token": "mock-tenant-config", "allowed_repos": ["owner/repo"]},
            user_credentials=[user_cred],
        )
        search_code = tools[0]

        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"total_count": 1, "items": [{"path": "f.py", "name": "f.py"}]},
            headers={"X-RateLimit-Remaining": "42"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)) as mock_client:
            result = await search_code(query="test", repo="owner/repo")
            # Verify the per-user token was sent in the Authorization header
            call_kwargs = mock_client.return_value.get.call_args
            headers = call_kwargs[1].get("headers", {})
            assert headers.get("Authorization") == "Bearer mock-user-pat-abc123", (
                f"Expected per-user token, got: {headers.get('Authorization')}"
            )
            assert result["total_count"] == 1

    async def test_falls_back_to_tenant_token(self):
        """Empty user_credentials falls back to tool_config.token."""
        from src.tools.github import build_github_tools

        tools = build_github_tools(
            {"token": "mock-tenant-fallback", "allowed_repos": ["owner/repo"]},
            user_credentials=[],
        )
        search_code = tools[0]

        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"total_count": 1, "items": [{"path": "f.py", "name": "f.py"}]},
            headers={"X-RateLimit-Remaining": "42"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)) as mock_client:
            result = await search_code(query="test", repo="owner/repo")
            call_kwargs = mock_client.return_value.get.call_args
            headers = call_kwargs[1].get("headers", {})
            assert headers.get("Authorization") == "Bearer mock-tenant-fallback"
            assert result["total_count"] == 1

    async def test_skips_inactive_user_credentials(self):
        """Inactive credentials are skipped, falling back to tenant token."""
        from src.tools.github import build_github_tools

        expired_cred = self._make_mock_credential("mock-expired-token", status="expired")
        tools = build_github_tools(
            {"token": "mock-tenant-still-works", "allowed_repos": ["owner/repo"]},
            user_credentials=[expired_cred],
        )
        search_code = tools[0]

        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"total_count": 0, "items": []},
            headers={"X-RateLimit-Remaining": "42"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)) as mock_client:
            result = await search_code(query="test", repo="owner/repo")
            call_kwargs = mock_client.return_value.get.call_args
            headers = call_kwargs[1].get("headers", {})
            assert headers.get("Authorization") == "Bearer mock-tenant-still-works"
            assert "total_count" in result

    async def test_error_when_no_token_at_all(self):
        """No per-user credentials and no tenant token returns an auth error."""
        from src.tools.github import build_github_tools

        tools = build_github_tools(
            {"allowed_repos": ["owner/repo"]},
            user_credentials=[],
        )
        search_code = tools[0]

        mock_resp = _make_mock_httpx_response(
            status_code=401,
            json_data={"message": "Bad credentials"},
            headers={"X-RateLimit-Remaining": "0"},
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await search_code(query="test", repo="owner/repo")
            assert "error" in result
            assert "Authentication failed" in result["error"]


class TestGithubGitLab:
    """Tests for ``build_github_tools()`` — GitLab provider."""

    @pytest.fixture
    def tools(self):
        from src.tools.github import build_github_tools
        return build_github_tools({
            "token": "glpat_test",
            "provider": "gitlab",
            "allowed_repos": ["owner/repo"],
        })

    async def test_gitlab_search_code(self, tools):
        search_code = tools[0]
        with patch(
            "src.tools.github._gitlab_request",
            return_value={
                "data": {
                    "total_count": 1,
                    "items": [{"path": "src/main.py", "name": "main.py", "html_url": ""}],
                },
            },
        ):
            result = await search_code(query="def main", repo="owner/repo")
            assert "items" in result

    async def test_gitlab_list_issues(self, tools):
        list_issues = tools[1]
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data=[{"iid": 1, "title": "Bug", "state": "opened"}],
        )

        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await list_issues(repo="owner/repo")
            assert "issues" in result

    async def test_gitlab_create_issue_unsupported(self, tools):
        create_issue = tools[4]
        result = await create_issue(repo="owner/repo", title="Test")
        assert "error" in result

    async def test_gitlab_create_pull_request_unsupported(self, tools):
        create_pr = tools[5]
        result = await create_pr(repo="owner/repo", title="PR", head="feature", base="main")
        assert "error" in result

    async def test_gitlab_comment_on_issue_unsupported(self, tools):
        comment = tools[6]
        result = await comment(repo="owner/repo", issue_number=1, body="Nice work")
        assert "error" in result

    async def test_gitlab_create_or_update_file_unsupported(self, tools):
        file_tool = tools[7]
        result = await file_tool(repo="owner/repo", path="f.py", message="msg", content="data")
        assert "error" in result

    async def test_gitlab_merge_pull_request_unsupported(self, tools):
        merge_pr = tools[8]
        result = await merge_pr(repo="owner/repo", pull_number=1)
        assert "error" in result

    async def test_gitlab_add_issue_labels_unsupported(self, tools):
        label_tool = tools[9]
        result = await label_tool(repo="owner/repo", issue_number=1, labels=["bug"])
        assert "error" in result


class TestGithubHelpers:
    """Tests for internal helpers in github.py."""

    async def test_github_request_401(self):
        mock_resp = _make_mock_httpx_response(
            status_code=401,
            json_data={"message": "Bad credentials"},
            headers={"X-RateLimit-Remaining": "0"},
        )
        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            from src.tools.github import _github_request
            result = await _github_request("/repos/owner/repo", "bad-token")
            assert "error" in result
            assert "Authentication failed" in result["error"]

    async def test_github_request_422(self):
        mock_resp = _make_mock_httpx_response(
            status_code=422,
            json_data={
                "message": "Validation Failed",
                "errors": [
                    {"resource": "PullRequest", "code": "custom",
                     "field": "head", "message": "head is not a branch"},
                ],
            },
            headers={"X-RateLimit-Remaining": "50"},
        )
        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            from src.tools.github import _github_request
            result = await _github_request("/repos/owner/repo/pulls", "token", method="POST", json_data={})
            assert "error" in result
            assert "422" in result["error"]
            assert "head" in result["error"]

    async def test_github_request_timeout(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.side_effect = httpx.TimeoutException("Timed out")

        with patch("src.tools.github.httpx.AsyncClient", return_value=client):
            from src.tools.github import _github_request
            result = await _github_request("/repos/owner/repo", "token")
            assert "error" in result
            assert "timed out" in result["error"].lower()

    async def test_gitlab_request_404(self):
        mock_resp = _make_mock_httpx_response(
            status_code=404,
            json_data={},
        )
        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            from src.tools.github import _gitlab_request
            result = await _gitlab_request("/projects/unknown", token="glpat-test")
            assert "error" in result
            assert "not found" in result["error"].lower()

    async def test_gitlab_request_timeout(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.side_effect = httpx.TimeoutException("Timed out")

        with patch("src.tools.github.httpx.AsyncClient", return_value=client):
            from src.tools.github import _gitlab_request
            result = await _gitlab_request("/projects/test", token="glpat-test")
            assert "error" in result
            assert "timed out" in result["error"].lower()

    async def test_gitlab_list_pull_requests(self):
        from src.tools.github import build_github_tools
        tools = build_github_tools({
            "token": "glpat_test",
            "provider": "gitlab",
            "allowed_repos": ["owner/repo"],
        })
        list_prs = tools[3]
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data=[{"iid": 5, "title": "Fix", "state": "opened", "web_url": ""}],
        )
        with patch("src.tools.github.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)):
            result = await list_prs(repo="owner/repo")
            assert "pull_requests" in result


# ===================================================================
# 5. erpnext.py — ERPNext Integration Tools
# ===================================================================
class TestErpnextHelpers:
    """Tests for helper functions in erpnext.py."""

    def test_make_tool_slug(self):
        from src.tools.erpnext import _make_tool_slug
        assert _make_tool_slug("kainotomo.com") == "kainotomo_com"
        assert _make_tool_slug("My ERP Instance!") == "my_erp_instance"
        assert _make_tool_slug("test") == "test"

    def test_make_tool_name(self):
        from src.tools.erpnext import _make_tool_name
        assert _make_tool_name("My Site", "get_doc") == "erpnext_my_site__get_doc"
        assert _make_tool_name(None, "get_doc") == "get_doc"

    def test_build_auth_header(self):
        from src.tools.erpnext import _build_auth_header
        header = _build_auth_header("key", "secret")
        assert header == {"Authorization": "token key:secret"}

    async def test_safe_erpnext_response_success(self):
        from src.tools.erpnext import _safe_erpnext_response
        mock_resp = _make_mock_httpx_response(status_code=200, json_data={"data": [{"name": "SO-001"}]})
        result = await _safe_erpnext_response(mock_resp)
        assert result == {"data": [{"name": "SO-001"}]}

    async def test_safe_erpnext_response_error(self):
        from src.tools.erpnext import _safe_erpnext_response
        mock_resp = _make_mock_httpx_response(
            status_code=400,
            json_data={"exc_type": "ValidationError", "_server_messages": "[]"},
        )
        mock_resp.is_error = True
        result = await _safe_erpnext_response(mock_resp)
        assert "exc_type" in result


class TestErpnextTools:
    """Tests for ``build_erpnext_tools()``."""

    @pytest.fixture
    def mock_client(self):
        mock_response = _make_mock_httpx_response(
            status_code=200,
            json_data={"data": {"name": "SO-001"}},
        )
        client = AsyncMock(spec=["get", "post", "put", "delete"])
        client.get = AsyncMock(return_value=mock_response)
        client.post = AsyncMock(return_value=mock_response)
        client.put = AsyncMock(return_value=mock_response)
        client.delete = AsyncMock(return_value=mock_response)
        return client

    @pytest.fixture
    def tools(self, mock_client):
        from src.tools.erpnext import build_erpnext_tools
        return build_erpnext_tools(
            base_url="https://erp.example.com",
            api_key="api_key",
            api_secret="api_secret",
            httpx_client=mock_client,
        )

    async def test_get_doc_success(self, tools):
        get_doc = tools[0]
        result = await get_doc(doctype="Sales Order", name="SO-001")
        assert "data" in result
        assert result["data"]["name"] == "SO-001"

    async def test_get_list_success(self, tools):
        get_list = tools[1]
        # Mock the client to return response with data as a list
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"data": [{"name": "SO-001"}]},
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        from src.tools.erpnext import build_erpnext_tools
        tools = build_erpnext_tools(
            base_url="https://erp.example.com",
            api_key="key", api_secret="secret",
            httpx_client=mock_client,
        )
        get_list = tools[1]

        result = await get_list(doctype="Sales Order")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "SO-001"

    async def test_get_list_with_filters(self, tools):
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"data": [{"name": "SO-002", "status": "Draft"}]},
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        from src.tools.erpnext import build_erpnext_tools
        tools = build_erpnext_tools(
            base_url="https://erp.example.com",
            api_key="key", api_secret="secret",
            httpx_client=mock_client,
        )
        get_list = tools[1]

        result = await get_list(
            doctype="Sales Order",
            filters=[["status", "=", "Draft"]],
            fields=["name", "status"],
            limit_page_length=10,
            order_by="creation desc",
        )
        assert isinstance(result, list)
        assert result[0]["name"] == "SO-002"

    async def test_create_doc_success(self, tools):
        create_doc = tools[2]
        result = await create_doc(doctype="Customer", data={"customer_name": "Acme"})
        assert "data" in result

    async def test_update_doc_success(self, tools):
        update_doc = tools[3]
        result = await update_doc(doctype="Customer", name="CUST-001", data={"customer_name": "Acme Corp"})
        assert "data" in result

    async def test_delete_doc_success(self, tools):
        delete_doc = tools[4]
        result = await delete_doc(doctype="Customer", name="CUST-001")
        assert "message" in result

    async def test_submit_doc_success(self, tools):
        submit_doc = tools[5]
        result = await submit_doc(doctype="Sales Order", name="SO-001")
        assert "data" in result

    async def test_cancel_doc_success(self, tools):
        cancel_doc = tools[6]
        result = await cancel_doc(doctype="Sales Order", name="SO-001")
        assert "data" in result

    async def test_amend_doc_success(self, tools):
        amend_doc = tools[7]
        result = await amend_doc(doctype="Sales Invoice", name="SINV-001")
        assert "data" in result

    async def test_get_doctype_meta_success(self, tools):
        # Mock response with fields array
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={
                "data": {
                    "fields": [
                        {"fieldname": "customer", "fieldtype": "Link", "label": "Customer", "reqd": 1},
                        {"fieldname": "status", "fieldtype": "Select", "label": "Status"},
                    ]
                }
            },
        )
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        from src.tools.erpnext import build_erpnext_tools
        tools = build_erpnext_tools(
            base_url="https://erp.example.com",
            api_key="key", api_secret="secret",
            httpx_client=mock_client,
        )
        meta = tools[8]
        result = await meta(doctype="Sales Order")
        assert len(result) == 2
        assert result[0]["fieldname"] == "customer"
        assert result[0]["reqd"] == 1

    async def test_call_method_post(self, tools):
        call_method = tools[9]
        result = await call_method(method="frappe.get_list", args={"doctype": "Sales Order"})
        assert "data" in result

    async def test_call_method_get(self, tools):
        call_method = tools[9]
        result = await call_method(method="frappe.get_list", args={"doctype": "Sales Order"}, http_method="GET")
        assert "data" in result


class TestErpnextUploadFile:
    """Tests for the upload_file tool (only present with file_infos)."""

    @pytest.fixture
    def mock_client(self):
        mock_response = _make_mock_httpx_response(
            status_code=200,
            json_data={"message": {"file_url": "/files/test.pdf"}},
        )
        client = AsyncMock()
        client.get = AsyncMock(return_value=mock_response)
        client.post = AsyncMock(return_value=mock_response)
        client.put = AsyncMock(return_value=mock_response)
        client.delete = AsyncMock(return_value=mock_response)
        return client

    @pytest.fixture
    def tools(self, mock_client):
        from src.tools.erpnext import build_erpnext_tools
        return build_erpnext_tools(
            base_url="https://erp.example.com",
            api_key="key",
            api_secret="secret",
            httpx_client=mock_client,
            file_infos=[
                {
                    "original_filename": "report.pdf",
                    "bucket": "bucket-1",
                    "storage_key": "uploads/report.pdf",
                    "content_type": "application/pdf",
                    "id": "file-1",
                },
            ],
        )

    async def test_upload_file_matches_filename(self, tools, mock_client):
        upload_file = tools[10]  # 11th tool
        # upload_file creates its own httpx.AsyncClient for multipart upload
        mock_upload_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"message": {"file_url": "/files/report.pdf"}},
        )
        with (
            patch("src.tools.erpnext.httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_upload_resp)),
            patch("src.storage.s3.download_object", return_value=b"pdf content"),
        ):
            result = await upload_file(filename="report.pdf", doctype="Sales Order", docname="SO-001")
            assert "message" in result
            assert result["message"]["file_url"] == "/files/report.pdf"

    async def test_upload_file_not_found(self, tools):
        upload_file = tools[10]
        result = await upload_file(filename="missing.pdf")
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestErpnextNoFileInfos:
    """When no file_infos, upload_file tool is NOT included."""

    def test_no_upload_file_without_file_infos(self):
        from src.tools.erpnext import build_erpnext_tools
        client = AsyncMock()
        tools = build_erpnext_tools(
            base_url="https://erp.example.com",
            api_key="key", api_secret="secret",
            httpx_client=client,
        )
        assert len(tools) == 10  # No upload_file


# ===================================================================
# 6. browser.py — Browser Automation Tools
# ===================================================================
class TestBrowserUrlSafety:
    """Tests for ``_is_safe_url()``."""

    def test_https_url_safe(self):
        from src.tools.browser import _is_safe_url
        assert _is_safe_url("https://example.com")

    def test_http_url_safe(self):
        from src.tools.browser import _is_safe_url
        assert _is_safe_url("http://example.com")

    def test_file_url_blocked(self):
        from src.tools.browser import _is_safe_url
        assert not _is_safe_url("file:///etc/passwd")

    def test_localhost_blocked(self):
        from src.tools.browser import _is_safe_url
        assert not _is_safe_url("http://localhost:8080")
        assert not _is_safe_url("http://127.0.0.1")

    def test_private_ip_blocked(self):
        from src.tools.browser import _is_safe_url
        assert not _is_safe_url("http://192.168.1.1")
        assert not _is_safe_url("http://10.0.0.5")
        assert not _is_safe_url("http://172.16.0.1")
        assert not _is_safe_url("http://169.254.1.1")

    def test_docker_hostname_blocked(self):
        from src.tools.browser import _is_safe_url
        assert not _is_safe_url("http://host.docker.internal")


class TestBrowserTools:
    """Tests for ``build_browser_tools()``."""

    @pytest.fixture
    def tools(self):
        from src.tools.browser import build_browser_tools
        return build_browser_tools(tenant_id="tenant-1")

    async def test_take_screenshot_blocked_url(self, tools):
        take_screenshot = tools[0]
        result = await take_screenshot(url="http://localhost:8080")
        assert "error" in result
        assert "blocked" in result["error"]

    async def test_take_screenshot_playwright_not_installed(self, tools):
        import builtins
        original_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == 'playwright' or name.startswith('playwright.'):
                raise ImportError("No module named 'playwright'")
            return original_import(name, *args, **kwargs)

        take_screenshot = tools[0]
        with patch("builtins.__import__", side_effect=mock_import):
            result = await take_screenshot(url="https://example.com")
            assert "error" in result
            assert "not installed" in result["error"].lower()

    async def test_extract_text_blocked_url(self, tools):
        extract_text = tools[1]
        result = await extract_text(url="http://127.0.0.1")
        assert "error" in result

    async def test_extract_text_playwright_not_installed(self, tools):
        import builtins
        original_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == 'playwright' or name.startswith('playwright.'):
                raise ImportError("No module named 'playwright'")
            return original_import(name, *args, **kwargs)

        extract_text = tools[1]
        with patch("builtins.__import__", side_effect=mock_import):
            result = await extract_text(url="https://example.com")
            assert "error" in result
            assert "not installed" in result["error"].lower()

    async def test_take_screenshot_no_tenant_id(self, tools):
        from src.tools.browser import build_browser_tools
        no_tenant_tools = build_browser_tools(tenant_id="")

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"image_data")
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.stop = AsyncMock()

        with patch("playwright.async_api.async_playwright", return_value=mock_pw):
            result = await no_tenant_tools[0](url="https://example.com")
            assert "error" in result
            assert "Tenant ID" in result["error"]

    async def test_extract_table_blocked_url(self, tools):
        extract_table = tools[2]
        result = await extract_table(url="http://192.168.1.1")
        assert "error" in result

    async def test_empty_url_returns_error(self, tools):
        take_screenshot = tools[0]
        result = await take_screenshot(url="")
        assert "error" in result

    async def test_take_screenshot_no_tenant_id(self, tools):
        from src.tools.browser import build_browser_tools
        no_tenant_tools = build_browser_tools(tenant_id="")

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"image_data")
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.stop = AsyncMock()

        with patch("playwright.async_api.async_playwright", return_value=mock_pw):
            result = await no_tenant_tools[0](url="https://example.com")
            assert "error" in result
            assert "Tenant ID" in result["error"]

    async def test_take_screenshot_happy_path(self):
        from src.tools.browser import build_browser_tools
        bt = build_browser_tools(tenant_id="tenant-1")
        take_screenshot = bt[0]

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"fake_screenshot")
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.start = AsyncMock(return_value=mock_pw)  # start() returns self
        mock_pw.stop = AsyncMock()

        with (
            patch("playwright.async_api.async_playwright", return_value=mock_pw),
            patch("src.core.config.settings", MINIO_BUCKET_PREFIX="phhub-tenant"),
            patch("src.tools.browser._upload_and_get_url", return_value="https://presigned.url/screenshot.png"),
        ):
            result = await take_screenshot(url="https://example.com")
            assert "url" in result
            assert result["page_url"] == "https://example.com"
            assert result["size_bytes"] == len(b"fake_screenshot")

    async def test_extract_text_happy_path(self):
        from src.tools.browser import build_browser_tools
        bt = build_browser_tools(tenant_id="tenant-1")
        extract_text = bt[1]

        mock_page = AsyncMock()
        mock_page.title = AsyncMock(return_value="Test Page")
        mock_page.evaluate = AsyncMock(return_value="Hello world content")
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.start = AsyncMock(return_value=mock_pw)
        mock_pw.stop = AsyncMock()

        with patch("playwright.async_api.async_playwright", return_value=mock_pw):
            result = await extract_text(url="https://example.com")
            assert result["url"] == "https://example.com"
            assert result["title"] == "Test Page"
            assert "Hello world" in result["text"]
            assert "truncated" in result

    async def test_extract_table_happy_path(self):
        from src.tools.browser import build_browser_tools
        bt = build_browser_tools(tenant_id="tenant-1")
        extract_table = bt[2]

        mock_page = AsyncMock()
        mock_page.title = AsyncMock(return_value="Table Page")
        mock_page.evaluate = AsyncMock(return_value=[
            {"headers": ["Name", "Age"], "rows": [["Alice", "30"], ["Bob", "25"]]},
        ])
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.start = AsyncMock(return_value=mock_pw)
        mock_pw.stop = AsyncMock()

        with patch("playwright.async_api.async_playwright", return_value=mock_pw):
            result = await extract_table(url="https://example.com", table_index=0)
            assert result["page_title"] == "Table Page"
            assert len(result["headers"]) == 2
            assert len(result["rows"]) == 2
            assert result["rows"][0]["Name"] == "Alice"

    async def test_extract_table_no_tables(self):
        from src.tools.browser import build_browser_tools
        bt = build_browser_tools(tenant_id="tenant-1")
        extract_table = bt[2]

        mock_page = AsyncMock()
        mock_page.title = AsyncMock(return_value="No Tables")
        mock_page.evaluate = AsyncMock(return_value=[])
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.start = AsyncMock(return_value=mock_pw)
        mock_pw.stop = AsyncMock()

        with patch("playwright.async_api.async_playwright", return_value=mock_pw):
            result = await extract_table(url="https://example.com")
            assert "error" in result
            assert "No tables" in result["error"]

    async def test_screenshot_selector_not_found(self):
        from src.tools.browser import build_browser_tools
        bt = build_browser_tools(tenant_id="tenant-1")
        take_screenshot = bt[0]

        mock_page = AsyncMock()
        mock_page.query_selector = AsyncMock(return_value=None)
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.start = AsyncMock(return_value=mock_pw)
        mock_pw.stop = AsyncMock()

        with patch("playwright.async_api.async_playwright", return_value=mock_pw):
            result = await take_screenshot(url="https://example.com", selector=".missing")
            assert "error" in result
            assert "Element not found" in result["error"]

    async def test_extract_table_index_out_of_range(self):
        from src.tools.browser import build_browser_tools
        bt = build_browser_tools(tenant_id="tenant-1")
        extract_table = bt[2]

        mock_page = AsyncMock()
        mock_page.title = AsyncMock(return_value="Page")
        mock_page.evaluate = AsyncMock(return_value=[
            {"headers": ["A"], "rows": [["1"]]},
        ])
        mock_page.goto = AsyncMock()
        mock_page.wait_for_timeout = AsyncMock()
        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        mock_context = AsyncMock()
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)
        mock_pw = AsyncMock()
        mock_pw.chromium = mock_chromium
        mock_pw.start = AsyncMock(return_value=mock_pw)
        mock_pw.stop = AsyncMock()

        with patch("playwright.async_api.async_playwright", return_value=mock_pw):
            result = await extract_table(url="https://example.com", table_index=5)
            assert "error" in result
            assert "out of range" in result["error"]


# ===================================================================
# 7. rag_search.py — RAG Search Tools
# ===================================================================
class TestSimpleVectorStore:
    """Tests for the in-memory ``SimpleVectorStore``."""

    @pytest.fixture
    def store(self):
        from src.tools.rag_search import SimpleVectorStore
        return SimpleVectorStore()

    def test_add_and_search(self, store):
        store.add("doc_1", "hello world", [1.0, 0.0, 0.0], {"filename": "a.txt"})
        store.add("doc_2", "goodbye world", [0.0, 1.0, 0.0], {"filename": "b.txt"})
        results = store.search([1.0, 0.0, 0.0], top_k=2)
        assert len(results) == 2
        assert results[0]["id"] == "doc_1"
        assert results[0]["score"] > results[1]["score"]

    def test_search_empty_store(self, store):
        results = store.search([1.0, 0.0, 0.0])
        assert results == []

    def test_clear_store(self, store):
        store.add("doc_1", "text", [1.0, 0.0], None)
        assert store.document_count == 1
        store.clear()
        assert store.document_count == 0

    def test_document_count(self, store):
        assert store.document_count == 0
        store.add("doc_1", "text", [1.0, 0.0])
        assert store.document_count == 1


class TestRagSearchChunking:
    """Tests for ``_chunk_text()``."""

    def test_empty_text(self):
        from src.tools.rag_search import _chunk_text
        assert _chunk_text("") == []
        assert _chunk_text("   ") == []

    def test_short_text_no_split(self):
        from src.tools.rag_search import _chunk_text
        chunks = _chunk_text("Hello world.")
        assert len(chunks) == 1
        assert chunks[0] == "Hello world."

    def test_long_text_chunked(self):
        from src.tools.rag_search import _chunk_text
        long_text = "Hello. " * 200
        chunks = _chunk_text(long_text, chunk_size=100)
        assert len(chunks) > 1


class TestRagSearchHelpers:
    """Tests for helper functions in rag_search.py."""

    def test_cosine_similarity_identical(self):
        from src.tools.rag_search import _cosine_similarity
        sim = _cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0])
        assert sim == 1.0

    def test_cosine_similarity_orthogonal(self):
        from src.tools.rag_search import _cosine_similarity
        sim = _cosine_similarity([1.0, 0.0], [0.0, 1.0])
        assert sim == 0.0

    def test_cosine_similarity_empty(self):
        from src.tools.rag_search import _cosine_similarity
        assert _cosine_similarity([], []) == 0.0
        assert _cosine_similarity([1.0], []) == 0.0

    def test_fallback_embed_produces_vector(self):
        from src.tools.rag_search import _fallback_embed
        vec = _fallback_embed("hello world", dim=256)
        assert len(vec) == 256
        # Vector should be normalized (length ~1.0)
        norm = sum(v * v for v in vec) ** 0.5
        assert abs(norm - 1.0) < 0.01

    def test_fallback_embed_consistency(self):
        from src.tools.rag_search import _fallback_embed
        v1 = _fallback_embed("same text", dim=256)
        v2 = _fallback_embed("same text", dim=256)
        assert v1 == v2

    def test_fallback_embed_different_inputs(self):
        from src.tools.rag_search import _fallback_embed
        v1 = _fallback_embed("hello world", dim=256)
        v2 = _fallback_embed("goodbye world", dim=256)
        assert v1 != v2

    def test_check_embedding_available_no_key(self):
        with patch("src.core.config.settings", OPENAI_API_KEY=None):
            from src.tools.rag_search import _check_embedding_available
            available, reason = _check_embedding_available()
            assert available is False
            assert "No embedding API key configured" in reason

    def test_check_embedding_available_with_key(self):
        with patch("src.core.config.settings", OPENAI_API_KEY="sk-test"):
            from src.tools.rag_search import _check_embedding_available
            available, reason = _check_embedding_available()
            assert available is True
            assert reason is None

    async def test_get_embeddings_empty_texts(self):
        from src.tools.rag_search import _get_embeddings
        result = await _get_embeddings([])
        assert result == []

    async def test_get_embeddings_fallback_no_key(self):
        with patch("src.core.config.settings", OPENAI_API_KEY=None):
            from src.tools.rag_search import _get_embeddings
            result = await _get_embeddings(["hello world"], api_key=None)
            assert len(result) == 1
            assert len(result[0]) == 256

    async def test_get_embeddings_api_success(self):
        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={
                "data": [
                    {"embedding": [0.1, 0.2, 0.3]},
                    {"embedding": [0.4, 0.5, 0.6]},
                ]
            },
        )
        with (
            patch("src.core.config.settings", OPENAI_API_KEY="sk-test"),
            patch("httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)),
        ):
            from src.tools.rag_search import _get_embeddings
            result = await _get_embeddings(["hello", "world"], api_key="sk-test")
            assert len(result) == 2
            assert result[0] == [0.1, 0.2, 0.3]

    async def test_get_embeddings_api_401_falls_back(self):
        mock_resp = _make_mock_httpx_response(
            status_code=401,
            json_data={"error": "unauthorized"},
        )
        with (
            patch("src.core.config.settings", OPENAI_API_KEY="sk-bad"),
            patch("httpx.AsyncClient", return_value=_make_mock_httpx_client(mock_resp)),
        ):
            from src.tools.rag_search import _get_embeddings
            result = await _get_embeddings(["hello"], api_key="sk-bad")
            assert len(result) == 1
            assert len(result[0]) == 256  # fallback


class TestRagSearchTools:
    """Tests for ``build_rag_search_tools()``."""

    @pytest.fixture
    def tools(self):
        # Start fresh — clear the module-level _vector_store between tests
        from src.tools.rag_search import _vector_store
        _vector_store.clear()
        from src.tools.rag_search import build_rag_search_tools
        return build_rag_search_tools()

    async def test_index_document_success(self, tools):
        index_doc = tools[0]
        # _get_embeddings will use fallback TF-IDF (no API key) — no mock needed
        content = "word " * 600
        result = await index_doc(content=content)
        assert result["status"] == "ok", f"Expected ok got {result}"
        assert result["chunks_indexed"] >= 1

    async def test_index_document_empty_content(self, tools):
        index_doc = tools[0]
        result = await index_doc(content="")
        assert result["status"] == "error"

    async def test_search_documents_empty_store(self, tools):
        search_docs = tools[1]
        result = await search_docs(query="test query")
        assert result["total_results"] == 0

    async def test_search_documents_with_results(self, tools):
        search_docs = tools[1]
        index_doc = tools[0]
        content = "word " * 600
        await index_doc(content=content)
        result = await search_docs(query="test query")
        assert isinstance(result, dict)
        assert "results" in result

    async def test_clear_index(self, tools):
        clear_index = tools[2]
        await tools[0](content="word " * 600)
        result = await clear_index()
        assert result["status"] == "ok"
        assert result["documents_removed"] >= 1

    async def test_index_document_removes_duplicate(self, tools):
        index_doc = tools[0]
        content = "word " * 600
        result1 = await index_doc(content=content, doc_id="dup-1")
        assert result1["status"] == "ok"
        result2 = await index_doc(content=content, doc_id="dup-1")
        assert result2["status"] == "ok"

    async def test_search_documents_db_backed(self, tools):
        db = _make_mock_db_session()
        search_result_mock = [
            {"file_id": "f1", "text": "result text", "score": 0.95, "filename": "doc.txt", "chunk_index": 0},
        ]
        from src.tools.rag_search import build_rag_search_tools
        db_tools = build_rag_search_tools(db=db, tenant_id="tenant-1")
        search_docs = db_tools[1]

        with patch(
            "src.tools.rag_search._get_embeddings",
            return_value=[[0.1, 0.2]],
        ):
            with patch(
                "src.services.rag_service.search_documents",
                return_value=search_result_mock,
            ):
                result = await search_docs(query="test")
                assert result["total_results"] == 1
                assert result["results"][0]["text"] == "result text"


# ===================================================================
# 8. mcp.py — MCP Tool Callable Builder
# ===================================================================
class TestMcpTools:
    """Tests for ``build_mcp_tool_callables()``."""

    @pytest.fixture
    def mock_server(self):
        server = MagicMock()
        server.id = "server-1"
        server.name = "test-server"
        server.enabled = True
        server.transport = "streamable_http"
        server.url = "http://mcp.example.com"
        server.headers = None
        server.env_vars = None
        server.command = None
        server.args = None
        server.allowed_tools = None
        return server

    @pytest.fixture
    def mock_tool(self):
        tool = MagicMock()
        tool.id = "tool-1"
        tool.name = "mcp-tool"
        tool.config = {"mcp_server_id": "server-1"}
        return tool

    async def test_streamable_http_transport(self, mock_server, mock_tool):
        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_server
        # Use async function so await db.execute(...) returns result_mock
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        with (
            patch("src.tools.mcp.MCPStreamableHTTPTool") as MockMCPTool,
            patch("httpx.AsyncClient"),
        ):
            from src.tools.mcp import build_mcp_tool_callables
            result = await build_mcp_tool_callables(db=db, tool=mock_tool, tenant_id="tenant-1")
            assert len(result) == 1
            MockMCPTool.assert_called_once()

    async def test_websocket_transport(self, mock_tool):
        server = MagicMock()
        server.id = "server-ws"
        server.name = "ws-server"
        server.enabled = True
        server.transport = "websocket"
        server.url = "ws://mcp.example.com"
        server.headers = None
        server.env_vars = None
        server.allowed_tools = None

        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = server
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        with patch("agent_framework.MCPWebsocketTool") as MockMCPTool:
            from src.tools.mcp import build_mcp_tool_callables
            result = await build_mcp_tool_callables(db=db, tool=mock_tool, tenant_id="tenant-1")
            assert len(result) == 1
            MockMCPTool.assert_called_once()

    async def test_stdio_transport(self, mock_tool):
        server = MagicMock()
        server.id = "server-stdio"
        server.name = "stdio-server"
        server.enabled = True
        server.transport = "stdio"
        server.url = None
        server.headers = None
        server.env_vars = None
        server.command = "/usr/bin/mcp"
        server.args = ["--port", "8080"]
        server.allowed_tools = None

        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = server
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        with patch("agent_framework.MCPStdioTool") as MockMCPTool:
            from src.tools.mcp import build_mcp_tool_callables
            result = await build_mcp_tool_callables(db=db, tool=mock_tool, tenant_id="tenant-1")
            assert len(result) == 1
            MockMCPTool.assert_called_once_with(
                name="stdio-server",
                allowed_tools=None,
                approval_mode="never_require",
                command="/usr/bin/mcp",
                args=["--port", "8080"],
                env={},
            )

    async def test_mcp_server_not_found(self, mock_tool):
        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.mcp import build_mcp_tool_callables
        result = await build_mcp_tool_callables(db=db, tool=mock_tool, tenant_id="tenant-1")
        assert result == []

    async def test_missing_mcp_server_id(self):
        tool = MagicMock()
        tool.id = "tool-1"
        tool.config = {}

        db = _make_mock_db_session()
        from src.tools.mcp import build_mcp_tool_callables
        result = await build_mcp_tool_callables(db=db, tool=tool, tenant_id="tenant-1")
        assert result == []

    async def test_disabled_server_returns_empty(self, mock_tool):
        server = MagicMock()
        server.enabled = False

        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = server
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        from src.tools.mcp import build_mcp_tool_callables
        result = await build_mcp_tool_callables(db=db, tool=mock_tool, tenant_id="tenant-1")
        assert result == []

    async def test_cleanup_clients_tracking(self, mock_server, mock_tool):
        db = _make_mock_db_session()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = mock_server
        async def mock_execute(*args, **kwargs):
            return result_mock
        db.execute = mock_execute

        cleanup = []

        with (
            patch("src.tools.mcp.MCPStreamableHTTPTool") as MockMCPTool,
            patch("httpx.AsyncClient"),
        ):
            from src.tools.mcp import build_mcp_tool_callables
            result = await build_mcp_tool_callables(
                db=db, tool=mock_tool, tenant_id="tenant-1",
                cleanup_clients=cleanup,
            )
            assert len(result) == 1
            assert len(cleanup) == 1  # http_client appended
