# =============================================================================
# PH Agent Hub — Communication Tools Unit Tests
# =============================================================================
# Tests for built-in communication tool factories: email, slack, calendar,
# tasks.
#
# All external API calls (httpx, smtplib, imaplib, Google APIs, Microsoft
# Graph, Slack API, SendGrid) are mocked — no real network requests.
# =============================================================================

import asyncio
import smtplib
from unittest.mock import AsyncMock, MagicMock, Mock, patch

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
    client.patch = AsyncMock(return_value=mock_response)
    client.delete = AsyncMock(return_value=mock_response)
    client.put = AsyncMock(return_value=mock_response)
    return client


def _make_mock_httpx_get_client(mock_response):
    """Return an AsyncMock httpx.AsyncClient with only ``.get``."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get = AsyncMock(return_value=mock_response)
    return client


def _make_mock_httpx_post_client(mock_response):
    """Return an AsyncMock httpx.AsyncClient with only ``.post``."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post = AsyncMock(return_value=mock_response)
    return client


def _make_mock_credential(
    provider: str = "gmail",
    email: str = "test@example.com",
    label: str = "Test Account",
    is_default: bool = True,
    status: str = "active",
    access_token: str = "mock-access-token",
    refresh_token: str = "mock-refresh-token",
    expires_at: int | None = None,
):
    """Return a MagicMock that behaves like a UserToolCredential ORM object.

    The returned mock exposes:
    - ``.label``, ``.provider``, ``.email_address``, ``.is_default``, ``.status``
    - ``.credentials`` → dict (decrypted)
    - ``.oauth_tokens`` → dict with access_token, refresh_token, expires_at
    """
    import time

    import json, time as _time
    cred = MagicMock()
    cred.label = label
    cred.provider = provider
    cred.email_address = email
    cred.is_default = is_default
    cred.status = status
    # The real ORM stores these as EncryptedString — when read from DB they
    # are decrypted to JSON strings.  _parse_credential calls json.loads()
    # on them, so we store JSON strings here.
    cred.credentials = json.dumps({})
    cred.oauth_tokens = json.dumps({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at or (int(_time.time()) + 3600),
    })
    return cred


# ===================================================================
# 1. Slack
# ===================================================================
class TestSlackTool:
    """Tests for ``build_slack_tools()`` — webhook and bot token paths."""

    @pytest.fixture
    def tool(self):
        """Return the ``send_slack_message`` callable."""
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        return mod.build_slack_tools()[0]

    FAKE_SLACK_OK = {
        "channel": "C123",
        "ts": "1712345678.000001",
    }

    # ------------------------------------------------------------------
    # Webhook path
    # ------------------------------------------------------------------

    async def test_webhook_success(self, tool):
        """Webhook: success path — 200 with plain text."""
        config = {"webhook_url": "https://hooks.slack.com/services/T00/B00/xxx"}
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_w,) = mod.build_slack_tools(config)

        mock_resp = _make_mock_httpx_response(status_code=200)
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.slack.httpx.AsyncClient", return_value=mock_client):
            result = await tool_w(channel="#general", text="Hello!")

        assert result["status"] == "ok"
        assert result["channel"] == "#general"
        assert result["method"] == "webhook"

        # Verify POST payload
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["text"] == "Hello!"

    async def test_webhook_http_error(self, tool):
        """Webhook: non-200 status code."""
        config = {"webhook_url": "https://hooks.slack.com/services/T00/B00/xxx"}
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_w,) = mod.build_slack_tools(config)

        mock_resp = _make_mock_httpx_response(status_code=400, text="bad request")
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.slack.httpx.AsyncClient", return_value=mock_client):
            result = await tool_w(channel="#general", text="Hello!")

        assert result["status"] == "error"
        assert "400" in result["error"]

    async def test_webhook_exception(self, tool):
        """Webhook: network-level exception."""
        config = {"webhook_url": "https://hooks.slack.com/services/T00/B00/xxx"}
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_w,) = mod.build_slack_tools(config)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))

        with patch("src.tools.slack.httpx.AsyncClient", return_value=mock_client):
            result = await tool_w(channel="#general", text="Hello!")

        assert result["status"] == "error"
        assert "Connection refused" in result["error"]

    # ------------------------------------------------------------------
    # Bot token path
    # ------------------------------------------------------------------

    async def test_bot_token_success(self, tool):
        """Bot token: success path — ok: true."""
        config = {"bot_token": "xoxb-test-token"}
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_b,) = mod.build_slack_tools(config)

        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"ok": True, "channel": "C123", "ts": "1712345678.000001"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.slack.httpx.AsyncClient", return_value=mock_client):
            result = await tool_b(channel="#general", text="Hello!")

        assert result["status"] == "ok"
        assert result["channel"] == "C123"
        assert result["ts"] == "1712345678.000001"
        assert result["method"] == "bot_token"

    async def test_bot_token_api_error(self, tool):
        """Bot token: API returns ok: false."""
        config = {"bot_token": "xoxb-test-token"}
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_b,) = mod.build_slack_tools(config)

        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"ok": False, "error": "channel_not_found"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.slack.httpx.AsyncClient", return_value=mock_client):
            result = await tool_b(channel="#nonexistent", text="Hello!")

        assert result["status"] == "error"
        assert "channel_not_found" in result["error"]

    async def test_bot_token_invalid_token(self, tool):
        """Bot token: token does not start with xoxb-."""
        config = {"bot_token": "invalid-token"}
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_b,) = mod.build_slack_tools(config)

        result = await tool_b(channel="#general", text="Hello!")
        assert result["status"] == "error"
        assert "xoxb-" in result["error"]

    async def test_bot_token_exception(self, tool):
        """Bot token: network-level exception."""
        config = {"bot_token": "xoxb-test-token"}
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_b,) = mod.build_slack_tools(config)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(side_effect=Exception("Timeout"))

        with patch("src.tools.slack.httpx.AsyncClient", return_value=mock_client):
            result = await tool_b(channel="#general", text="Hello!")

        assert result["status"] == "error"
        assert "Timeout" in result["error"]

    # ------------------------------------------------------------------
    # Channel allowlist
    # ------------------------------------------------------------------

    async def test_channel_allowlist_allowed(self, tool):
        """Allowlist: channel is in the allowed list."""
        config = {
            "bot_token": "xoxb-test-token",
            "allowed_channels": ["general", "random"],
        }
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_a,) = mod.build_slack_tools(config)

        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"ok": True, "channel": "C123", "ts": "ts1"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.slack.httpx.AsyncClient", return_value=mock_client):
            result = await tool_a(channel="#general", text="Hello!")

        assert result["status"] == "ok"

    async def test_channel_allowlist_blocked(self, tool):
        """Allowlist: channel is NOT in the allowed list → error."""
        config = {
            "bot_token": "xoxb-test-token",
            "allowed_channels": ["general"],
        }
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_a,) = mod.build_slack_tools(config)

        result = await tool_a(channel="#secret", text="Hello!")
        assert result["status"] == "error"
        assert "not in the allowed list" in result["error"]

    # ------------------------------------------------------------------
    # Default channel
    # ------------------------------------------------------------------

    async def test_default_channel(self, tool):
        """Default channel: no channel arg → uses config default."""
        config = {
            "bot_token": "xoxb-test-token",
            "default_channel": "#announcements",
        }
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_d,) = mod.build_slack_tools(config)

        mock_resp = _make_mock_httpx_response(
            status_code=200,
            json_data={"ok": True, "channel": "C123", "ts": "ts1"},
        )
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.slack.httpx.AsyncClient", return_value=mock_client):
            result = await tool_d(text="Hello!")

        assert result["status"] == "ok"
        # Verify the payload channel
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["channel"] == "#announcements"

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    async def test_empty_text(self, tool):
        """Empty text returns early error."""
        result = await tool(text="")
        assert result["status"] == "error"
        assert "No message text" in result["error"]

    async def test_no_config(self, tool):
        """No credentials configured returns error."""
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_n,) = mod.build_slack_tools({})
        result = await tool_n(channel="#general", text="Hello!")
        assert result["status"] == "error"
        assert "not configured" in result["error"]

    async def test_invalid_webhook_url(self, tool):
        """Non-hooks.slack.com webhook URL returns error."""
        config = {"webhook_url": "https://evil.com/webhook"}
        mod = __import__("src.tools.slack", fromlist=["build_slack_tools"])
        (tool_i,) = mod.build_slack_tools(config)
        result = await tool_i(channel="#general", text="Hello!")
        assert result["status"] == "error"
        assert "Invalid webhook URL" in result["error"]


# ===================================================================
# 2. Tasks — Google Tasks Provider
# ===================================================================
class TestTasksGoogleTool:
    """Tests for ``build_tasks_tools()`` with Google Tasks provider."""

    FAKE_TASK_LISTS = {
        "items": [
            {"id": "list1", "title": "My Tasks"},
            {"id": "list2", "title": "Work"},
        ]
    }
    FAKE_TASKS = {
        "items": [
            {"id": "task1", "title": "Buy groceries", "due": "2026-06-20T00:00:00.000Z",
             "notes": "", "status": "needsAction"},
            {"id": "task2", "title": "Write report", "due": "",
             "notes": "", "status": "needsAction"},
        ]
    }
    FAKE_CREATED_TASK = {"id": "task3", "title": "New task", "status": "needsAction"}

    # ------------------------------------------------------------------
    # Factory fixture — returns a tuple of (tools_list, tool_dict)
    # ------------------------------------------------------------------

    @pytest.fixture
    def goog_creds(self):
        """Return a list with one Google Tasks credential."""
        return [_make_mock_credential(provider="google", email="user@gmail.com", label="My Tasks")]

    @pytest.fixture
    def google_tools(self, goog_creds):
        """Build all tasks tools with Google credentials."""
        mod = __import__("src.tools.tasks", fromlist=["build_tasks_tools"])
        tools = mod.build_tasks_tools(tool_config={}, user_credentials=goog_creds)
        # Return a dict keyed by the @tool decorated name for convenience
        return {t.name: t for t in tools}

    # ------------------------------------------------------------------
    # list_task_lists
    # ------------------------------------------------------------------

    async def test_list_task_lists_success(self, google_tools):
        """list_task_lists: success path."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_TASK_LISTS)
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=mock_client):
            result = await google_tools["list_task_lists"]()

        assert result["total"] == 2
        assert result["task_lists"][0]["name"] == "My Tasks"
        assert result["task_lists"][1]["id"] == "list2"

    async def test_list_task_lists_empty(self, google_tools):
        """list_task_lists: empty response."""
        mock_resp = _make_mock_httpx_response(200, {"items": []})
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=mock_client):
            result = await google_tools["list_task_lists"]()

        assert result["total"] == 0
        assert result["task_lists"] == []

    async def test_list_task_lists_401_triggers_refresh(self, google_tools):
        """list_task_lists: 401 triggers token refresh then retry."""
        # First call returns 401, refresh succeeds, second returns 200
        fail_resp = _make_mock_httpx_response(401, {})
        success_resp = _make_mock_httpx_response(200, self.FAKE_TASK_LISTS)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[fail_resp, success_resp])

        with (
            patch("src.tools.tasks.httpx.AsyncClient", return_value=client),
            patch("src.tools.tasks.refresh_token_if_expired", return_value={"access_token": "new-token"}),
        ):
            result = await google_tools["list_task_lists"]()

        assert result["total"] == 2

    async def test_list_task_lists_api_error(self, google_tools):
        """list_task_lists: API error returns error dict."""
        mock_resp = _make_mock_httpx_response(500, {})
        mock_resp.raise_for_status.side_effect = Exception("HTTP 500 Server Error")
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=mock_client):
            result = await google_tools["list_task_lists"]()

        assert "error" in result

    # ------------------------------------------------------------------
    # list_tasks
    # ------------------------------------------------------------------

    async def test_list_tasks_success(self, google_tools):
        """list_tasks: success path."""
        # When list_name is None, _get_google_task_list_id returns "@default"
        # without making an HTTP call, so only the tasks GET is needed.
        tasks_resp = _make_mock_httpx_response(200, self.FAKE_TASKS)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=tasks_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await google_tools["list_tasks"]()

        assert result["total"] == 2
        assert result["tasks"][0]["title"] == "Buy groceries"
        assert result["tasks"][1]["id"] == "task2"

    async def test_list_tasks_include_completed(self, google_tools):
        """list_tasks: include_completed=True."""
        # Same as success — only the tasks GET is needed when list_name is None
        tasks_resp = _make_mock_httpx_response(200, self.FAKE_TASKS)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=tasks_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await google_tools["list_tasks"](include_completed=True)

        assert result["total"] == 2
        # Verify params — only 1 GET call (no list_name → no list ID resolution)
        _, kwargs = client.get.call_args
        params = kwargs.get("params", {})
        assert "showCompleted" not in params  # omitted when include_completed=True

    # ------------------------------------------------------------------
    # create_task
    # ------------------------------------------------------------------

    async def test_create_task_success(self, google_tools):
        """create_task: success path."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_TASK_LISTS)
        create_resp = _make_mock_httpx_response(200, self.FAKE_CREATED_TASK)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=list_resp)
        client.post = AsyncMock(return_value=create_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await google_tools["create_task"](title="New task")

        assert result["id"] == "task3"
        assert result["status"] == "created"

    async def test_create_task_with_priority(self, google_tools):
        """create_task: priority stored as note prefix."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_TASK_LISTS)
        create_resp = _make_mock_httpx_response(200, {"id": "t4", "title": "Urgent", "status": "needsAction"})

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=list_resp)
        client.post = AsyncMock(return_value=create_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await google_tools["create_task"](title="Urgent", priority="high")

        assert result["id"] == "t4"
        # Verify priority prefix in notes
        _, kwargs = client.post.call_args
        assert "[PRIORITY:high]" in kwargs["json"]["notes"]

    async def test_create_task_empty_title(self, google_tools):
        """create_task: empty title returns error."""
        result = await google_tools["create_task"](title="")
        assert "error" in result

    # ------------------------------------------------------------------
    # update_task
    # ------------------------------------------------------------------

    async def test_update_task_success(self, google_tools):
        """update_task: success path — mark completed."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_TASK_LISTS)
        update_resp = _make_mock_httpx_response(200, {})

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=list_resp)
        client.patch = AsyncMock(return_value=update_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await google_tools["update_task"](task_id="task1", completed=True)

        assert result["status"] == "updated"
        _, kwargs = client.patch.call_args
        assert kwargs["json"]["status"] == "completed"

    async def test_update_task_empty_id(self, google_tools):
        """update_task: empty task_id returns error."""
        result = await google_tools["update_task"](task_id="")
        assert "error" in result

    # ------------------------------------------------------------------
    # delete_task
    # ------------------------------------------------------------------

    async def test_delete_task_success(self, google_tools):
        """delete_task: success path."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_TASK_LISTS)
        delete_resp = _make_mock_httpx_response(204, {})

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=list_resp)
        client.delete = AsyncMock(return_value=delete_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await google_tools["delete_task"](task_id="task1")

        assert result["status"] == "ok"

    async def test_delete_task_empty_id(self, google_tools):
        """delete_task: empty task_id returns error."""
        result = await google_tools["delete_task"](task_id="")
        assert "error" in result

    # ------------------------------------------------------------------
    # list_task_accounts
    # ------------------------------------------------------------------

    async def test_list_task_accounts_success(self, google_tools):
        """list_task_accounts: returns connected accounts."""
        result = await google_tools["list_task_accounts"]()
        assert result["total"] == 1
        assert result["accounts"][0]["email"] == "user@gmail.com"
        assert result["accounts"][0]["provider"] == "google"
        assert result["accounts"][0]["is_default"] is True

    async def test_list_task_accounts_empty(self):
        """list_task_accounts: no credentials → empty."""
        mod = __import__("src.tools.tasks", fromlist=["build_tasks_tools"])
        tools = mod.build_tasks_tools(tool_config={})
        if not tools:
            # Factory returns empty list when no user_credentials
            return
        tool_dict = {t.name: t for t in tools}
        result = await tool_dict["list_task_accounts"]()
        assert result["total"] == 0

    # ------------------------------------------------------------------
    # No credentials → empty tool list
    # ------------------------------------------------------------------

    async def test_no_credentials_returns_empty(self):
        """Factory returns empty list without user_credentials."""
        mod = __import__("src.tools.tasks", fromlist=["build_tasks_tools"])
        tools = mod.build_tasks_tools(tool_config={})
        assert len(tools) == 0


# ===================================================================
# 3. Tasks — Microsoft To Do Provider
# ===================================================================
class TestTasksMicrosoftTool:
    """Tests for ``build_tasks_tools()`` with Microsoft To Do provider."""

    FAKE_MS_LISTS = {
        "value": [
            {"id": "list1", "displayName": "Tasks"},
        ]
    }
    FAKE_MS_TASKS = {
        "value": [
            {"id": "t1", "title": "Buy groceries", "status": "notStarted",
             "dueDateTime": {"dateTime": "2026-06-20T00:00:00", "timeZone": "UTC"}},
        ]
    }
    FAKE_MS_CREATED = {"id": "t2", "title": "New task", "status": "notStarted"}

    @pytest.fixture
    def ms_creds(self):
        """Return a list with one Microsoft Tasks credential."""
        return [_make_mock_credential(provider="microsoft", email="user@outlook.com", label="Work Tasks")]

    @pytest.fixture
    def ms_tools(self, ms_creds):
        """Build all tasks tools with Microsoft credentials."""
        mod = __import__("src.tools.tasks", fromlist=["build_tasks_tools"])
        tools = mod.build_tasks_tools(tool_config={}, user_credentials=ms_creds)
        return {t.name: t for t in tools}

    async def test_list_task_lists_success(self, ms_tools):
        """Microsoft: list_task_lists success."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_MS_LISTS)
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=mock_client):
            result = await ms_tools["list_task_lists"]()

        assert result["total"] == 1
        assert result["task_lists"][0]["name"] == "Tasks"

    async def test_list_tasks_success(self, ms_tools):
        """Microsoft: list_tasks success."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_MS_LISTS)
        tasks_resp = _make_mock_httpx_response(200, self.FAKE_MS_TASKS)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[list_resp, tasks_resp])

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await ms_tools["list_tasks"]()

        assert result["total"] == 1
        assert result["tasks"][0]["title"] == "Buy groceries"

    async def test_create_task_success(self, ms_tools):
        """Microsoft: create_task success."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_MS_LISTS)
        create_resp = _make_mock_httpx_response(201, self.FAKE_MS_CREATED)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=list_resp)
        client.post = AsyncMock(return_value=create_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await ms_tools["create_task"](title="New task")

        assert result["id"] == "t2"
        assert result["status"] == "created"

    async def test_update_task_mark_completed(self, ms_tools):
        """Microsoft: update_task mark as completed."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_MS_LISTS)
        update_resp = _make_mock_httpx_response(200, {})

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=list_resp)
        client.patch = AsyncMock(return_value=update_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await ms_tools["update_task"](task_id="t1", completed=True)

        assert result["status"] == "updated"
        _, kwargs = client.patch.call_args
        assert kwargs["json"]["status"] == "completed"

    async def test_delete_task_success(self, ms_tools):
        """Microsoft: delete_task success."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_MS_LISTS)
        delete_resp = _make_mock_httpx_response(204, {})

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(return_value=list_resp)
        client.delete = AsyncMock(return_value=delete_resp)

        with patch("src.tools.tasks.httpx.AsyncClient", return_value=client):
            result = await ms_tools["delete_task"](task_id="t1")

        assert result["status"] == "ok"

    async def test_401_triggers_refresh(self, ms_tools):
        """Microsoft: 401 triggers token refresh then retry."""
        fail_resp = _make_mock_httpx_response(401, {})
        success_resp = _make_mock_httpx_response(200, self.FAKE_MS_LISTS)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[fail_resp, success_resp])

        with (
            patch("src.tools.tasks.httpx.AsyncClient", return_value=client),
            patch("src.tools.tasks.refresh_token_if_expired", return_value={"access_token": "new"}),
        ):
            result = await ms_tools["list_task_lists"]()

        assert result["total"] == 1


# ===================================================================
# 4. Calendar — Google Calendar Provider
# ===================================================================
class TestCalendarGoogleTool:
    """Tests for ``build_calendar_tools()`` with Google Calendar provider."""

    FAKE_EVENTS = {
        "items": [
            {
                "id": "evt1",
                "summary": "Team Standup",
                "description": "Daily standup",
                "location": "Room A",
                "start": {"dateTime": "2026-06-18T09:00:00", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-18T09:30:00", "timeZone": "UTC"},
                "status": "confirmed",
                "attendees": [{"email": "alice@example.com"}],
                "htmlLink": "https://calendar.google.com/event?eid=evt1",
            },
            {
                "id": "evt2",
                "summary": "Lunch",
                "description": "",
                "location": "",
                "start": {"dateTime": "2026-06-18T12:00:00", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-18T13:00:00", "timeZone": "UTC"},
                "status": "confirmed",
            },
        ]
    }
    FAKE_CREATED = {
        "id": "evt3",
        "summary": "New Event",
        "start": {"dateTime": "2026-06-19T10:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-06-19T11:00:00", "timeZone": "UTC"},
        "htmlLink": "https://calendar.google.com/event?eid=evt3",
        "status": "confirmed",
    }
    FAKE_TIMEZONE = {"value": "America/New_York", "timeZone": "America/New_York"}

    @pytest.fixture
    def goog_creds(self):
        """Return one Google Calendar credential with long access token (>50 chars)."""
        return [_make_mock_credential(
            provider="google", email="user@gmail.com", label="My Calendar",
            access_token="x" * 60,
        )]

    @pytest.fixture
    async def calendar_tools(self, goog_creds):
        """Build all calendar tools with Google credentials (mocked timezone)."""
        mod = __import__("src.tools.calendar", fromlist=["build_calendar_tools"])
        with patch("src.tools.calendar._detect_timezone", return_value="UTC"):
            tools = await mod.build_calendar_tools(tool_config={}, user_credentials=goog_creds)
        return {t.name: t for t in tools}

    # ------------------------------------------------------------------
    # list_events
    # ------------------------------------------------------------------

    async def test_list_events_success(self, calendar_tools):
        """list_events: success path."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_EVENTS)
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="America/New_York"),
        ):
            result = await calendar_tools["list_events"](date_from="2026-06-18")

        assert result["total"] == 2
        assert result["events"][0]["summary"] == "Team Standup"
        assert result["events"][1]["id"] == "evt2"
        assert result["events"][0]["attendees"] == ["alice@example.com"]
        assert result["events"][0]["html_link"] == "https://calendar.google.com/event?eid=evt1"

    async def test_list_events_empty(self, calendar_tools):
        """list_events: empty response."""
        mock_resp = _make_mock_httpx_response(200, {"items": []})
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
        ):
            result = await calendar_tools["list_events"](date_from="2026-06-18")

        assert result["total"] == 0

    async def test_list_events_no_date(self, calendar_tools):
        """list_events: empty date_from returns error."""
        result = await calendar_tools["list_events"](date_from="")
        assert "error" in result

    async def test_list_events_401_triggers_refresh(self, calendar_tools):
        """list_events: 401 triggers token refresh then retry."""
        fail_resp = _make_mock_httpx_response(401, {})
        success_resp = _make_mock_httpx_response(200, self.FAKE_EVENTS)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[fail_resp, success_resp])

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
            patch("src.tools.calendar.refresh_token_if_expired", return_value={"access_token": "refreshed"}),
        ):
            result = await calendar_tools["list_events"](date_from="2026-06-18")

        assert result["total"] == 2

    async def test_list_events_404(self, calendar_tools):
        """list_events: 404 calendar not found."""
        mock_resp = _make_mock_httpx_response(404, {})
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
        ):
            result = await calendar_tools["list_events"](date_from="2026-06-18")

        assert "error" in result
        assert "not found" in result["error"]

    # ------------------------------------------------------------------
    # create_event
    # ------------------------------------------------------------------

    async def test_create_event_success(self, calendar_tools):
        """create_event: success path."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_CREATED)
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
        ):
            result = await calendar_tools["create_event"](
                summary="New Event",
                start="2026-06-19T10:00:00",
                end="2026-06-19T11:00:00",
            )

        assert result["id"] == "evt3"
        assert result["status"] == "confirmed"

    async def test_create_event_with_attendees(self, calendar_tools):
        """create_event: with attendees and location."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_CREATED)
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
        ):
            result = await calendar_tools["create_event"](
                summary="Meeting",
                start="2026-06-19T14:00:00",
                end="2026-06-19T15:00:00",
                description="Discuss project",
                location="Conference Room B",
                attendees=["bob@example.com"],
            )

        assert result["id"] == "evt3"
        # Verify request body
        _, kwargs = mock_client.post.call_args
        body = kwargs["json"]
        assert body["description"] == "Discuss project"
        assert body["location"] == "Conference Room B"
        assert body["attendees"] == [{"email": "bob@example.com"}]

    async def test_create_event_missing_summary(self, calendar_tools):
        """create_event: empty summary returns error."""
        result = await calendar_tools["create_event"](summary="", start="now", end="later")
        assert "error" in result

    async def test_create_event_missing_start(self, calendar_tools):
        """create_event: empty start returns error."""
        result = await calendar_tools["create_event"](summary="Test", start="", end="later")
        assert "error" in result

    # ------------------------------------------------------------------
    # delete_event
    # ------------------------------------------------------------------

    async def test_delete_event_success(self, calendar_tools):
        """delete_event: success path."""
        mock_resp = _make_mock_httpx_response(204, {})
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
        ):
            result = await calendar_tools["delete_event"](event_id="evt1")

        assert result["status"] in ("ok", "deleted")

    # ------------------------------------------------------------------
    # list_calendar_accounts
    # ------------------------------------------------------------------

    async def test_list_calendar_accounts(self, calendar_tools):
        """list_calendar_accounts: returns connected accounts."""
        result = await calendar_tools["list_calendar_accounts"]()
        assert result["total"] == 1
        assert result["accounts"][0]["email"] == "user@gmail.com"

    # ------------------------------------------------------------------
    # search_events
    # ------------------------------------------------------------------

    async def test_search_events_success(self, calendar_tools):
        """search_events: success path (Google)."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_EVENTS)
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
        ):
            result = await calendar_tools["search_events"](query="standup")

        assert result["total"] == 2


# ===================================================================
# 5. Calendar — Microsoft Graph Provider
# ===================================================================
class TestCalendarMicrosoftTool:
    """Tests for ``build_calendar_tools()`` with Microsoft Graph provider."""

    FAKE_MS_EVENTS = {
        "value": [
            {
                "id": "evt1",
                "subject": "Team Standup",
                "bodyPreview": "Daily sync",
                "location": {"displayName": "Teams"},
                "start": {"dateTime": "2026-06-18T09:00:00", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-18T09:30:00", "timeZone": "UTC"},
                "showAs": "busy",
                "attendees": [{"emailAddress": {"address": "alice@example.com"}}],
            },
        ]
    }
    FAKE_MS_CREATED = {
        "id": "evt2",
        "subject": "Meeting",
        "start": {"dateTime": "2026-06-19T10:00:00", "timeZone": "UTC"},
        "end": {"dateTime": "2026-06-19T11:00:00", "timeZone": "UTC"},
    }

    @pytest.fixture
    def ms_creds(self):
        """Return one Microsoft Calendar credential."""
        return [_make_mock_credential(provider="microsoft", email="user@outlook.com", label="Work Calendar")]

    @pytest.fixture
    async def calendar_tools(self, ms_creds):
        """Build all calendar tools with Microsoft credentials (mocked timezone)."""
        mod = __import__("src.tools.calendar", fromlist=["build_calendar_tools"])
        with patch("src.tools.calendar._detect_timezone", return_value="UTC"):
            tools = await mod.build_calendar_tools(tool_config={}, user_credentials=ms_creds)
        return {t.name: t for t in tools}

    async def test_list_events_success(self, calendar_tools):
        """Microsoft: list_events success."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_MS_EVENTS)
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="America/New_York"),
        ):
            result = await calendar_tools["list_events"](date_from="2026-06-18")

        assert result["total"] == 1
        assert result["events"][0]["summary"] == "Team Standup"
        assert result["events"][0]["attendees"] == ["alice@example.com"]

    async def test_create_event_success(self, calendar_tools):
        """Microsoft: create_event success."""
        mock_resp = _make_mock_httpx_response(201, self.FAKE_MS_CREATED)
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="America/New_York"),
        ):
            result = await calendar_tools["create_event"](
                summary="Meeting",
                start="2026-06-19T10:00:00",
                end="2026-06-19T11:00:00",
            )

        assert result["id"] == "evt2"

    async def test_delete_event_success(self, calendar_tools):
        """Microsoft: delete_event success."""
        mock_resp = _make_mock_httpx_response(204, {})
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
        ):
            result = await calendar_tools["delete_event"](event_id="evt1")

        assert result["status"] in ("ok", "deleted")

    async def test_401_triggers_refresh(self, calendar_tools):
        """Microsoft: 401 triggers token refresh then retry."""
        fail_resp = _make_mock_httpx_response(401, {})
        success_resp = _make_mock_httpx_response(200, self.FAKE_MS_EVENTS)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[fail_resp, success_resp])

        with (
            patch("src.tools.calendar.httpx.AsyncClient", return_value=client),
            patch("src.tools.calendar._detect_timezone", return_value="UTC"),
            patch("src.tools.calendar.refresh_token_if_expired", return_value={"access_token": "refreshed"}),
        ):
            result = await calendar_tools["list_events"](date_from="2026-06-18")

        assert result["total"] == 1


# ===================================================================
# 6. Email — SMTP Provider
# ===================================================================
class TestEmailSmtpTool:
    """Tests for ``build_email_tools()`` with SMTP provider.

    SMTP uses ``smtplib.SMTP`` inside ``asyncio.to_thread()``.
    We mock ``smtplib.SMTP`` at the module level.
    """

    SMTP_CONFIG = {
        "provider": "smtp",
        "smtp_host": "smtp.test.com",
        "smtp_port": 587,
        "smtp_username": "user",
        "smtp_password": "pass",
        "from_email": "from@test.com",
        "from_name": "Test Sender",
    }

    @pytest.fixture
    def email_tool(self):
        """Build email tools with SMTP config, return send_email callable."""
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config=self.SMTP_CONFIG)
        return {t.name: t for t in tools}

    # ------------------------------------------------------------------
    # send_email — SMTP success
    # ------------------------------------------------------------------

    async def test_send_email_smtp_plain(self, email_tool):
        """SMTP: send plain text email successfully."""
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=None)

        def _smtp_constructor(*args, **kwargs):
            return mock_smtp

        with (
            patch("smtplib.SMTP", side_effect=_smtp_constructor),
        ):
            result = await email_tool["send_email"](
                to="recipient@test.com",
                subject="Hello",
                body="Plain text body",
            )

        assert result["status"] == "ok"
        assert result["to"] == "recipient@test.com"
        assert result["subject"] == "Hello"
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("user", "pass")

    async def test_send_email_smtp_html(self, email_tool):
        """SMTP: send HTML email."""
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=None)

        with (
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await email_tool["send_email"](
                to="recipient@test.com",
                subject="HTML Email",
                body="<h1>Hello</h1>",
                is_html=True,
            )

        assert result["status"] == "ok"

    async def test_send_email_smtp_with_cc(self, email_tool):
        """SMTP: send email with CC."""
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=None)

        with (
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await email_tool["send_email"](
                to="recipient@test.com",
                subject="With CC",
                body="Body",
                cc="cc@test.com",
            )

        assert result["status"] == "ok"

    # ------------------------------------------------------------------
    # SMTP errors
    # ------------------------------------------------------------------

    async def test_send_email_smtp_auth_failure(self, email_tool):
        """SMTP: authentication error."""
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=None)
        mock_smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"auth failed")

        with (
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await email_tool["send_email"](
                to="recipient@test.com",
                subject="Auth",
                body="Body",
            )

        assert result["status"] == "error"
        assert "auth" in result["error"].lower()

    async def test_send_email_smtp_exception(self, email_tool):
        """SMTP: general SMTP error."""
        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=None)
        mock_smtp.send_message.side_effect = smtplib.SMTPException("Connection dropped")

        with (
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await email_tool["send_email"](
                to="recipient@test.com",
                subject="Error",
                body="Body",
            )

        assert result["status"] == "error"
        assert "Connection dropped" in result["error"]

    # ------------------------------------------------------------------
    # Validation errors (before SMTP is called)
    # ------------------------------------------------------------------

    async def test_send_email_missing_to(self, email_tool):
        """send_email: missing recipient → validation error."""
        result = await email_tool["send_email"](to="", subject="Test", body="Body")
        assert "error" in result

    async def test_send_email_missing_subject(self, email_tool):
        """send_email: missing subject → validation error."""
        result = await email_tool["send_email"](to="a@b.com", subject="", body="Body")
        assert "error" in result

    async def test_send_email_missing_body(self, email_tool):
        """send_email: missing body → validation error."""
        result = await email_tool["send_email"](to="a@b.com", subject="Test", body="")
        assert "error" in result

    async def test_send_email_invalid_recipient(self, email_tool):
        """send_email: invalid email format → validation error."""
        result = await email_tool["send_email"](to="not-an-email", subject="Test", body="Body")
        assert "error" in result

    # ------------------------------------------------------------------
    # Recipient allowlist
    # ------------------------------------------------------------------

    async def test_recipient_allowlist_allowed(self, email_tool):
        """Recipient allowlist: allowed recipient passes."""
        config = {
            **self.SMTP_CONFIG,
            "allowed_recipients": ["*@company.com"],
        }
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config=config)
        tool_dict = {t.name: t for t in tools}

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=None)

        with (
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await tool_dict["send_email"](
                to="user@company.com", subject="Test", body="Body",
            )

        assert result["status"] == "ok"

    async def test_recipient_allowlist_blocked(self, email_tool):
        """Recipient allowlist: blocked recipient returns error."""
        config = {
            **self.SMTP_CONFIG,
            "allowed_recipients": ["*@company.com"],
        }
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config=config)
        tool_dict = {t.name: t for t in tools}

        result = await tool_dict["send_email"](
            to="user@gmail.com", subject="Test", body="Body",
        )
        assert result["status"] == "error"
        assert "not in the allowed list" in result["error"]

    async def test_recipient_allowlist_wildcard(self, email_tool):
        """Recipient allowlist: wildcard allows all."""
        config = {
            **self.SMTP_CONFIG,
            "allowed_recipients": ["*"],
        }
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config=config)
        tool_dict = {t.name: t for t in tools}

        mock_smtp = MagicMock()
        mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp.__exit__ = MagicMock(return_value=None)

        with (
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await tool_dict["send_email"](
                to="anyone@anywhere.com", subject="Test", body="Body",
            )

        assert result["status"] == "ok"

    async def test_smtp_port_465(self, email_tool):
        """SMTP: port 465 uses SMTP_SSL (no starttls)."""
        config = {**self.SMTP_CONFIG, "smtp_port": 465}
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config=config)
        tool_dict = {t.name: t for t in tools}

        mock_smtp_ssl = MagicMock()
        mock_smtp_ssl.__enter__ = MagicMock(return_value=mock_smtp_ssl)
        mock_smtp_ssl.__exit__ = MagicMock(return_value=None)

        with (
            patch("smtplib.SMTP_SSL", return_value=mock_smtp_ssl),
        ):
            result = await tool_dict["send_email"](
                to="user@company.com", subject="Test", body="Body",
            )

        assert result["status"] == "ok"
        # No starttls for SMTP_SSL
        mock_smtp_ssl.starttls.assert_not_called()


# ===================================================================
# 7. Email — SendGrid Provider
# ===================================================================
class TestEmailSendGridTool:
    """Tests for ``build_email_tools()`` with SendGrid provider."""

    SENDGRID_CONFIG = {
        "provider": "sendgrid",
        "api_key": "SG.test-key",
        "from_email": "from@test.com",
        "from_name": "Test Sender",
    }

    @pytest.fixture
    def sendgrid_tool(self):
        """Build email tools with SendGrid config."""
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config=self.SENDGRID_CONFIG)
        return {t.name: t for t in tools}

    async def test_sendgrid_success(self, sendgrid_tool):
        """SendGrid: send email successfully."""
        mock_resp = _make_mock_httpx_response(202, {})  # SendGrid returns 202
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with patch("src.tools.email.httpx.AsyncClient", return_value=mock_client):
            result = await sendgrid_tool["send_email"](
                to="recipient@test.com",
                subject="Hello",
                body="Body",
            )

        assert result["status"] == "ok"
        assert result["provider"] == "sendgrid"
        # Verify auth header
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer SG.test-key"

    async def test_sendgrid_error(self, sendgrid_tool):
        """SendGrid: non-2xx response."""
        mock_resp = _make_mock_httpx_response(401, {})
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with patch("src.tools.email.httpx.AsyncClient", return_value=mock_client):
            result = await sendgrid_tool["send_email"](
                to="recipient@test.com",
                subject="Hello",
                body="Body",
            )

        assert result["status"] == "error"

    async def test_sendgrid_from_name(self, sendgrid_tool):
        """SendGrid: from_name included in payload."""
        mock_resp = _make_mock_httpx_response(202, {})
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with patch("src.tools.email.httpx.AsyncClient", return_value=mock_client):
            result = await sendgrid_tool["send_email"](
                to="recipient@test.com",
                subject="Test",
                body="Body",
            )

        assert result["status"] == "ok"
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["from"]["name"] == "Test Sender"


# ===================================================================
# 8. Email — Gmail API Provider
# ===================================================================
class TestEmailGmailTool:
    """Tests for ``build_email_tools()`` with Gmail API credentials."""

    FAKE_MESSAGES = {
        "messages": [
            {"id": "msg1", "threadId": "thread1"},
            {"id": "msg2", "threadId": "thread2"},
        ],
        "resultSizeEstimate": 2,
    }
    FAKE_MESSAGE_DETAIL = {
        "id": "msg1",
        "threadId": "thread1",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Date", "value": "2026-06-15T10:00:00Z"},
            ],
        },
        "snippet": "This is a preview...",
    }
    FAKE_LABELS = {
        "labels": [
            {"id": "LABEL1", "name": "INBOX", "type": "system"},
            {"id": "LABEL2", "name": "IMPORTANT", "type": "system"},
        ]
    }

    @pytest.fixture
    def gmail_creds(self):
        """Return a Gmail credential."""
        return [_make_mock_credential(provider="gmail", email="user@gmail.com", label="My Gmail")]

    @pytest.fixture
    def gmail_tools(self, gmail_creds):
        """Build email tools with Gmail credentials."""
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config={}, user_credentials=gmail_creds)
        return {t.name: t for t in tools}

    # ------------------------------------------------------------------
    # send_email
    # ------------------------------------------------------------------

    async def test_gmail_send_success(self, gmail_tools):
        """Gmail: send email successfully."""
        mock_resp = _make_mock_httpx_response(200, {})
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await gmail_tools["send_email"](
                to="recipient@example.com", subject="Hello", body="Body",
            )

        assert result["status"] == "ok"
        assert result["provider"] == "gmail"

    # ------------------------------------------------------------------
    # read_emails
    # ------------------------------------------------------------------

    async def test_gmail_read_success(self, gmail_tools):
        """Gmail: read emails successfully."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_MESSAGES)
        detail_resp = _make_mock_httpx_response(200, self.FAKE_MESSAGE_DETAIL)

        client = AsyncMock()
        client.__aenter__.return_value = client
        # 1 list call + 2 detail calls (one per message)
        client.get = AsyncMock(side_effect=[list_resp, detail_resp, detail_resp])

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await gmail_tools["read_emails"](limit=2)

        assert result["total"] == 2
        assert result["emails"][0]["subject"] == "Hello"

    async def test_gmail_read_unread_only(self, gmail_tools):
        """Gmail: read emails with unread_only=True."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_MESSAGES)
        detail_resp = _make_mock_httpx_response(200, self.FAKE_MESSAGE_DETAIL)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[list_resp, detail_resp, detail_resp])

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await gmail_tools["read_emails"](unread_only=True)

        assert result["total"] == 2
        # Verify query param includes "is:unread"
        call_args_list = client.get.call_args_list
        params = call_args_list[0].kwargs.get("params", {})
        assert "is:unread" in params.get("q", "")

    # ------------------------------------------------------------------
    # search_emails
    # ------------------------------------------------------------------

    async def test_gmail_search_success(self, gmail_tools):
        """Gmail: search emails successfully."""
        list_resp = _make_mock_httpx_response(200, self.FAKE_MESSAGES)
        detail_resp = _make_mock_httpx_response(200, self.FAKE_MESSAGE_DETAIL)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[list_resp, detail_resp, detail_resp])

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await gmail_tools["search_emails"](query="meeting")

        assert result["total"] == 2

    async def test_gmail_search_empty_query(self, gmail_tools):
        """Gmail: empty search query returns error."""
        result = await gmail_tools["search_emails"](query="")
        assert "error" in result

    # ------------------------------------------------------------------
    # list_folders
    # ------------------------------------------------------------------

    async def test_gmail_list_folders(self, gmail_tools):
        """Gmail: list folders/labels."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_LABELS)
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await gmail_tools["list_folders"]()

        assert result["total"] == 2

    # ------------------------------------------------------------------
    # list_email_accounts
    # ------------------------------------------------------------------

    async def test_gmail_list_email_accounts(self, gmail_tools):
        """Gmail: list connected email accounts."""
        result = await gmail_tools["list_email_accounts"]()
        assert result["total"] == 1
        assert result["accounts"][0]["email"] == "user@gmail.com"

    # ------------------------------------------------------------------
    # Token refresh on 401
    # ------------------------------------------------------------------

    async def test_gmail_401_triggers_refresh(self, gmail_tools):
        """Gmail: exception with 'expired' triggers token refresh then retry."""
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post = AsyncMock(side_effect=[
            Exception("Access token expired"),
            _make_mock_httpx_response(200, {}),
        ])

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
            patch("src.tools.email._refresh_token_if_expired", return_value={"access_token": "refreshed"}),
        ):
            result = await gmail_tools["send_email"](
                to="a@b.com", subject="Test", body="Body",
            )

        assert result["status"] == "ok"


# ===================================================================
# 9. Email — Outlook / Microsoft Graph Provider
# ===================================================================
class TestEmailOutlookTool:
    """Tests for ``build_email_tools()`` with Microsoft Graph API credentials."""

    FAKE_MS_MESSAGES = {
        "value": [
            {"id": "msg1", "from": {"emailAddress": {"address": "alice@example.com"}},
             "subject": "Hello", "receivedDateTime": "2026-06-15T10:00:00Z", "isRead": False},
        ]
    }

    @pytest.fixture
    def outlook_creds(self):
        """Return an Outlook credential."""
        return [_make_mock_credential(provider="outlook", email="user@outlook.com", label="Work Outlook")]

    @pytest.fixture
    def outlook_tools(self, outlook_creds):
        """Build email tools with Outlook credentials."""
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config={}, user_credentials=outlook_creds)
        return {t.name: t for t in tools}

    async def test_outlook_send_success(self, outlook_tools):
        """Outlook: send email successfully."""
        mock_resp = _make_mock_httpx_response(202, {})
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await outlook_tools["send_email"](
                to="recipient@example.com", subject="Hello", body="Body",
            )

        assert result["status"] == "ok"
        assert result["provider"] in ("outlook", "microsoft", "graph")

    async def test_outlook_read_success(self, outlook_tools):
        """Outlook: read emails successfully."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_MS_MESSAGES)
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await outlook_tools["read_emails"]()

        assert result["total"] == 1
        assert result["emails"][0]["subject"] == "Hello"

    async def test_outlook_read_unread_filter(self, outlook_tools):
        """Outlook: read emails with unread_only=True."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_MS_MESSAGES)
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            await outlook_tools["read_emails"](unread_only=True)

        _, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        assert params.get("$filter") == "isRead eq false"

    async def test_outlook_search_success(self, outlook_tools):
        """Outlook: search emails successfully."""
        mock_resp = _make_mock_httpx_response(200, self.FAKE_MS_MESSAGES)
        mock_client = _make_mock_httpx_client(mock_resp)

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await outlook_tools["search_emails"](query="meeting")

        assert result["total"] == 1
        _, kwargs = mock_client.get.call_args
        params = kwargs.get("params", {})
        assert "$search" in params

    async def test_outlook_401_triggers_refresh(self, outlook_tools):
        """Outlook: 401 triggers token refresh then retry."""
        fail_resp = _make_mock_httpx_response(401, {})
        success_resp = _make_mock_httpx_response(200, self.FAKE_MS_MESSAGES)

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get = AsyncMock(side_effect=[fail_resp, success_resp])

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
            patch("src.tools.email._refresh_token_if_expired", return_value={"access_token": "refreshed"}),
        ):
            result = await outlook_tools["read_emails"]()

        assert result["total"] == 1


# ===================================================================
# 10. Email — IMAP Provider
# ===================================================================
class TestEmailImapTool:
    """Tests for ``build_email_tools()`` with IMAP credentials.

    IMAP uses ``imaplib.IMAP4_SSL`` inside ``asyncio.to_thread()``.
    """

    IMAP_CONFIG = {
        "provider": "imap",
        "imap_host": "imap.test.com",
        "imap_port": 993,
        "imap_username": "user",
        "imap_password": "pass",
        "from_email": "user@test.com",
        "smtp_host": "smtp.test.com",
        "smtp_port": 587,
        "smtp_username": "user",
        "smtp_password": "pass",
    }

    @pytest.fixture
    def imap_tools(self):
        """Build email tools with IMAP user credentials."""
        import json
        cred = _make_mock_credential(
            provider="imap", email="user@test.com", label="My IMAP",
        )
        cred.credentials = json.dumps({
            "imap_host": "imap.test.com",
            "imap_port": 993,
            "username": "user",
            "password": "pass",
            "from_email": "user@test.com",
        })
        cred.oauth_tokens = json.dumps({})
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config={}, user_credentials=[cred])
        return {t.name: t for t in tools}

    async def test_imap_read_success(self, imap_tools):
        """IMAP: read emails successfully."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.search = MagicMock(return_value=("OK", [b"1 2"]))
        header_bytes = (
            b"From: Alice <alice@example.com>\r\n"
            b"Subject: Hello\r\n"
            b"Date: Mon, 15 Jun 2026 10:00:00 +0000\r\n"
        )
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] {0}", header_bytes)])
        )
        mock_conn.logout = MagicMock()

        def _imap_constructor(*args, **kwargs):
            return mock_conn

        with (
            patch("imaplib.IMAP4_SSL", side_effect=_imap_constructor),
        ):
            result = await imap_tools["read_emails"](limit=2)

        assert result["total"] == 2
        assert result["emails"][0]["subject"] == "Hello"

    async def test_imap_read_unread_only(self, imap_tools):
        """IMAP: read unread_only=True."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.search = MagicMock(return_value=("OK", [b"1"]))
        header_bytes = (
            b"From: Alice <alice@example.com>\r\n"
            b"Subject: Test\r\n"
            b"Date: Mon, 15 Jun 2026 10:00:00 +0000\r\n"
        )
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] {0}", header_bytes)])
        )
        mock_conn.logout = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
        ):
            result = await imap_tools["read_emails"](unread_only=True)

        assert result["total"] == 1
        mock_conn.search.assert_called_with(None, "UNSEEN")
        mock_conn.logout.assert_called_once()

    async def test_imap_folder_inaccessible(self, imap_tools):
        """IMAP: inaccessible folder falls back to INBOX."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(side_effect=[
            ("NO", [b"Folder not found"]),
            ("OK", [b"1"]),
        ])
        mock_conn.search = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.fetch = MagicMock(return_value=("OK", []))
        mock_conn.logout = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
        ):
            result = await imap_tools["read_emails"](folder="Custom")

        assert result["total"] == 0

    async def test_imap_empty_inbox(self, imap_tools):
        """IMAP: empty inbox."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.search = MagicMock(return_value=("OK", [None]))
        mock_conn.logout = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
        ):
            result = await imap_tools["read_emails"]()

        assert result["total"] == 0

    # ------------------------------------------------------------------
    # Forward via IMAP — SMTP host/port fix (Bug #1)
    # ------------------------------------------------------------------

    @pytest.fixture
    def imap_tools_with_smtp(self):
        """Build email tools with IMAP credentials that include smtp_host/port."""
        import json
        cred = _make_mock_credential(
            provider="imap", email="user@test.com", label="My IMAP",
        )
        cred.credentials = json.dumps({
            "imap_host": "imap.test.com",
            "imap_port": 993,
            "username": "user",
            "password": "pass",
            "from_email": "user@test.com",
            "smtp_host": "smtp.test.com",
            "smtp_port": 587,
        })
        cred.oauth_tokens = json.dumps({})
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config={}, user_credentials=[cred])
        return {t.name: t for t in tools}

    async def test_imap_forward_uses_smtp_host(self, imap_tools_with_smtp):
        """IMAP forward: uses smtp_host/smtp_port for SMTP, not imap_host/imap_port."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY[] {0}", b"From: sender@test.com\r\nSubject: Test\r\n")])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock()
        mock_smtp.send_message = MagicMock()
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp) as smtp_mock,
        ):
            result = await imap_tools_with_smtp["forward_email"](
                email_id="1", to="recipient@test.com",
            )

        assert result["status"] == "ok"
        # Verify SMTP was called with smtp_host/smtp_port, NOT imap_host/imap_port
        smtp_mock.assert_called_once_with("smtp.test.com", 587, timeout=30.0)
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once()
        mock_smtp.send_message.assert_called_once()
        mock_smtp.quit.assert_called_once()

    async def test_imap_forward_filename_not_eml(self, imap_tools_with_smtp):
        """IMAP forward: attachment filename uses .txt not .eml."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY[] {0}", b"From: sender@test.com\r\nSubject: Test\r\n")])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock()
        mock_smtp.send_message = MagicMock()
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await imap_tools_with_smtp["forward_email"](
                email_id="1", to="recipient@test.com",
            )

        assert result["status"] == "ok"
        # Verify the forwarded message doesn't contain ".eml" in the attachment
        call_args = mock_smtp.send_message.call_args
        sent_msg = call_args[0][0]
        msg_str = sent_msg.as_string()
        assert ".eml" not in msg_str, "Attached file should not use .eml extension"
        assert ".txt" in msg_str or "Forwarded message" in msg_str

    async def test_imap_forward_smtp_quit_on_error(self, imap_tools_with_smtp):
        """IMAP forward: SMTP quit() is called even on send error."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY[] {0}", b"From: sender@test.com\r\nSubject: Test\r\n")])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock()
        mock_smtp.send_message = MagicMock(side_effect=smtplib.SMTPException("Send failed"))
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await imap_tools_with_smtp["forward_email"](
                email_id="1", to="recipient@test.com",
            )

        assert result["status"] == "error"
        # quit() must be called even on failure
        mock_smtp.quit.assert_called_once()

    async def test_imap_forward_fallback_to_imap_host(self, imap_tools):
        """IMAP forward: falls back to imap_host when smtp_host not in credentials."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY[] {0}", b"From: sender@test.com\r\nSubject: Test\r\n")])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock()
        mock_smtp.send_message = MagicMock()
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp) as smtp_mock,
        ):
            result = await imap_tools["forward_email"](
                email_id="1", to="recipient@test.com",
            )

        assert result["status"] == "ok"
        # Without smtp_host, falls back to imap_host but with smtp default port 587
        smtp_mock.assert_called_once()
        args, kwargs = smtp_mock.call_args
        assert args[0] == "imap.test.com"  # fallback to imap_host
        assert args[1] == 587  # default SMTP port

    # ------------------------------------------------------------------
    # Reply via IMAP — SMTP host/port fix (Bug #1)
    # ------------------------------------------------------------------

    async def test_imap_reply_uses_smtp_host(self, imap_tools_with_smtp):
        """IMAP reply: uses smtp_host/smtp_port for SMTP, not imap_host/imap_port."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        header_bytes = (
            b"From: sender@test.com\r\n"
            b"Subject: Original Subject\r\n"
            b"Message-ID: <orig123@test.com>\r\n"
            b"To: user@test.com\r\n"
        )
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY.PEEK[HEADER] {0}", header_bytes)])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock()
        mock_smtp.send_message = MagicMock()
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp) as smtp_mock,
        ):
            result = await imap_tools_with_smtp["reply_email"](
                email_id="1", body="Reply body",
            )

        assert result["status"] == "ok"
        # Verify SMTP was called with smtp_host/smtp_port
        smtp_mock.assert_called_once_with("smtp.test.com", 587, timeout=30.0)
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once()
        mock_smtp.send_message.assert_called_once()
        mock_smtp.quit.assert_called_once()

    async def test_imap_reply_smtp_cleanup_on_auth_failure(self, imap_tools_with_smtp):
        """IMAP reply: quit() is called even when login fails."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        header_bytes = (
            b"From: sender@test.com\r\n"
            b"Subject: Test\r\n"
            b"Message-ID: <test@test.com>\r\n"
            b"To: user@test.com\r\n"
        )
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY.PEEK[HEADER] {0}", header_bytes)])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock(side_effect=smtplib.SMTPAuthenticationError(535, b"bad pass"))
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await imap_tools_with_smtp["reply_email"](
                email_id="1", body="Reply body",
            )

        assert result["status"] == "error"
        assert "auth" in result["error"].lower()
        # quit() must be called even on auth failure
        mock_smtp.quit.assert_called_once()

    async def test_imap_reply_with_reply_all(self, imap_tools_with_smtp):
        """IMAP reply: reply_all works correctly."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        header_bytes = (
            b"From: sender@test.com\r\n"
            b"Subject: Original\r\n"
            b"Message-ID: <orig@test.com>\r\n"
            b"To: user@test.com, other@test.com\r\n"
        )
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY.PEEK[HEADER] {0}", header_bytes)])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock()
        mock_smtp.send_message = MagicMock()
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await imap_tools_with_smtp["reply_email"](
                email_id="1", body="Reply", reply_all=True,
            )

        assert result["status"] == "ok"
        # Verify the message was sent
        mock_smtp.send_message.assert_called_once()
        sent_msg = mock_smtp.send_message.call_args[0][0]
        msg_str = sent_msg.as_string()
        # reply_all should include both original sender and original To
        assert "sender@test.com" in msg_str
        assert "other@test.com" in msg_str

    async def test_imap_reply_sets_from_header(self, imap_tools_with_smtp):
        """IMAP reply: From header is set (no SMTP 501 error)."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        header_bytes = (
            b"From: sender@test.com\r\n"
            b"Subject: Original\r\n"
            b"Message-ID: <orig@test.com>\r\n"
            b"To: user@test.com\r\n"
        )
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY.PEEK[HEADER] {0}", header_bytes)])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock()
        mock_smtp.send_message = MagicMock()
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await imap_tools_with_smtp["reply_email"](
                email_id="1", body="Reply body",
            )

        assert result["status"] == "ok"
        sent_msg = mock_smtp.send_message.call_args[0][0]
        msg_str = sent_msg.as_string()
        # From header must be present to avoid SMTP 501
        assert "From:" in msg_str, "Reply must have a From header"
        assert "user" in msg_str.split("From:")[1].split("\n")[0]

    async def test_imap_reply_reply_all_sets_from_header(self, imap_tools_with_smtp):
        """IMAP reply with reply_all: From header is set."""
        mock_conn = MagicMock()
        mock_conn.select = MagicMock(return_value=("OK", [b"1"]))
        mock_conn.login = MagicMock(return_value=("OK", [b"1"]))
        header_bytes = (
            b"From: sender@test.com\r\n"
            b"Subject: Original\r\n"
            b"Message-ID: <orig@test.com>\r\n"
            b"To: user@test.com, other@test.com\r\n"
        )
        mock_conn.fetch = MagicMock(
            return_value=("OK", [(b"1 (BODY.PEEK[HEADER] {0}", header_bytes)])
        )
        mock_conn.logout = MagicMock()

        mock_smtp = MagicMock()
        mock_smtp.starttls = MagicMock()
        mock_smtp.login = MagicMock()
        mock_smtp.send_message = MagicMock()
        mock_smtp.quit = MagicMock()

        with (
            patch("imaplib.IMAP4_SSL", return_value=mock_conn),
            patch("smtplib.SMTP", return_value=mock_smtp),
        ):
            result = await imap_tools_with_smtp["reply_email"](
                email_id="1", body="Reply all", reply_all=True,
            )

        assert result["status"] == "ok"
        sent_msg = mock_smtp.send_message.call_args[0][0]
        msg_str = sent_msg.as_string()
        assert "From:" in msg_str, "Reply must have a From header"


# ===================================================================
# 11. Email — Credential Resolution
# ===================================================================
class TestEmailCredentialResolution:
    """Tests for credential resolution logic in ``build_email_tools()``.

    Verifies how the factory selects between tenant config and per-user
    credentials, and how ``account_label`` filtering works.
    """

    @pytest.fixture
    def smtp_creds_tools(self):
        """Build tools with SMTP config + Gmail user credential."""
        config = {
            "provider": "smtp",
            "smtp_host": "smtp.tenant.com",
            "smtp_username": "tenant",
            "smtp_password": "tenant-pass",
            "from_email": "tenant@company.com",
        }
        creds = [_make_mock_credential(provider="gmail", email="user@gmail.com", label="My Gmail")]
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config=config, user_credentials=creds)
        return {t.name: t for t in tools}

    async def test_user_credential_takes_priority(self, smtp_creds_tools):
        """User OAuth credential is used over tenant SMTP config."""
        mock_resp = _make_mock_httpx_response(200, {})
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await smtp_creds_tools["send_email"](
                to="recipient@example.com", subject="Test", body="Body",
            )

        assert result["status"] == "ok"

    async def test_list_email_accounts_multiple(self, smtp_creds_tools):
        """list_email_accounts shows all active credentials."""
        result = await smtp_creds_tools["list_email_accounts"]()
        assert result["total"] == 1
        assert result["accounts"][0]["email"] == "user@gmail.com"

    async def test_account_label_selection(self):
        """account_label filters credentials correctly."""
        creds = [
            _make_mock_credential(provider="gmail", email="work@gmail.com", label="Work", is_default=True),
            _make_mock_credential(provider="outlook", email="personal@outlook.com", label="Personal", is_default=False),
        ]
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config={}, user_credentials=creds)
        tool_dict = {t.name: t for t in tools}

        mock_resp = _make_mock_httpx_response(200, {})
        mock_client = _make_mock_httpx_post_client(mock_resp)

        with (
            patch("src.tools.email.httpx.AsyncClient", return_value=mock_client),
            patch("src.tools.email._ensure_fresh_token", return_value=True),
        ):
            result = await tool_dict["send_email"](
                to="r@example.com", subject="Test", body="Body",
                account_label="Personal",
            )

        assert result["status"] == "ok"

    async def test_no_email_accounts_error(self):
        """No credentials and no tenant config → only send_email returned."""
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config={})
        # Without user_credentials, only send_email is returned
        assert len(tools) == 1
        assert tools[0].name == "send_email"

    async def test_invalid_provider(self):
        """Unsupported provider returns error."""
        config = {"provider": "nonexistent", "from_email": "a@b.com"}
        mod = __import__("src.tools.email", fromlist=["build_email_tools"])
        tools = mod.build_email_tools(tool_config=config)
        tool_dict = {t.name: t for t in tools}
        result = await tool_dict["send_email"](
            to="r@example.com", subject="Test", body="Body",
        )
        assert "error" in result
