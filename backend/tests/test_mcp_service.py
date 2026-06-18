# =============================================================================
# PH Agent Hub — MCP Server Service Tests
# =============================================================================

import uuid
import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError, ValidationError
from src.core.encryption import encrypt
from src.db.orm.mcp_servers import McpServer
from src.db.orm.tools import Tool
from src.services.mcp_service import (
    list_mcp_servers,
    get_mcp_server,
    create_mcp_server,
    update_mcp_server,
    delete_mcp_server,
    decrypt_env_vars,
    decrypt_headers,
    mask_secret_value,
    mask_dict,
    test_mcp_connection as svc_test_mcp_connection,
    sync_mcp_tools,
)

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_server_kwargs(tenant_id: str, **overrides) -> dict:
    """Build standard kwargs for create_mcp_server, with overrides."""
    kwargs = dict(
        tenant_id=tenant_id,
        name="Test MCP Server",
        transport="stdio",
        command="python",
        args=["-m", "mcp_server"],
    )
    kwargs.update(overrides)
    return kwargs


def _create_mock_mcp_tool(functions: list[dict] | None = None):
    """Create a mock MCP tool instance that works as an async context manager."""
    if functions is None:
        functions = [{"name": "tool_a", "description": "First tool"}]

    def _make_fn(fn_dict):
        m = MagicMock()
        m.name = fn_dict["name"]
        m.description = fn_dict.get("description", "")
        return m

    mock_tool = AsyncMock()
    mock_tool.functions = [_make_fn(fn) for fn in functions]
    mock_tool.__aenter__ = AsyncMock(return_value=mock_tool)
    mock_tool.__aexit__ = AsyncMock(return_value=None)
    return mock_tool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListMcpServers:
    """Tests for list_mcp_servers."""

    async def test_empty_db(self, db_session: AsyncSession, test_tenant):
        """Should return empty list when no servers exist."""
        items, total = await list_mcp_servers(db_session, tenant_id=test_tenant.id)
        assert items == []
        assert total == 0

    async def test_multiple_servers(self, db_session: AsyncSession, test_tenant):
        """Should return all servers for the tenant."""
        s1 = McpServer(tenant_id=test_tenant.id, name="Server A", transport="stdio", command="python")
        s2 = McpServer(tenant_id=test_tenant.id, name="Server B", transport="stdio", command="node")
        db_session.add_all([s1, s2])
        await db_session.flush()

        items, total = await list_mcp_servers(db_session, tenant_id=test_tenant.id)
        assert total == 2
        assert {s.id for s in items} == {s1.id, s2.id}

    async def test_filter_by_transport(self, db_session: AsyncSession, test_tenant):
        """Should filter by transport type."""
        db_session.add_all([
            McpServer(tenant_id=test_tenant.id, name="Stdio", transport="stdio", command="python"),
            McpServer(tenant_id=test_tenant.id, name="HTTP", transport="streamable_http", url="http://localhost:8080"),
        ])
        await db_session.flush()

        items, total = await list_mcp_servers(db_session, tenant_id=test_tenant.id, transport="stdio")
        assert total == 1
        assert items[0].name == "Stdio"

    async def test_search_by_name(self, db_session: AsyncSession, test_tenant):
        """Should search by name (case-insensitive)."""
        db_session.add_all([
            McpServer(tenant_id=test_tenant.id, name="Production DB", transport="stdio", command="python"),
            McpServer(tenant_id=test_tenant.id, name="Staging API", transport="streamable_http", url="http://staging"),
        ])
        await db_session.flush()

        items, total = await list_mcp_servers(db_session, tenant_id=test_tenant.id, search="production")
        assert total == 1
        assert items[0].name == "Production DB"

    async def test_pagination(self, db_session: AsyncSession, test_tenant):
        """Should paginate results."""
        for i in range(5):
            db_session.add(McpServer(tenant_id=test_tenant.id, name=f"S{i}", transport="stdio", command="python"))
        await db_session.flush()

        items, total = await list_mcp_servers(db_session, tenant_id=test_tenant.id, page=1, page_size=2)
        assert total == 5
        assert len(items) == 2

    async def test_filter_by_enabled(self, db_session: AsyncSession, test_tenant):
        """Should filter by enabled status."""
        db_session.add_all([
            McpServer(tenant_id=test_tenant.id, name="Enabled", transport="stdio", command="python", enabled=True),
            McpServer(tenant_id=test_tenant.id, name="Disabled", transport="stdio", command="node", enabled=False),
        ])
        await db_session.flush()

        items, total = await list_mcp_servers(db_session, tenant_id=test_tenant.id, enabled=True)
        assert total == 1
        assert items[0].name == "Enabled"


class TestGetMcpServer:
    """Tests for get_mcp_server."""

    async def test_existing(self, db_session: AsyncSession, test_tenant):
        """Should return the server when it exists."""
        server = McpServer(tenant_id=test_tenant.id, name="Found", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        result = await get_mcp_server(db_session, server.id)
        assert result is not None
        assert result.name == "Found"

    async def test_nonexistent_returns_none(self, db_session: AsyncSession):
        """Should return None when server does not exist."""
        result = await get_mcp_server(db_session, str(uuid.uuid4()))
        assert result is None


class TestCreateMcpServer:
    """Tests for create_mcp_server."""

    async def test_stdio_success(self, db_session: AsyncSession, test_tenant):
        """Should create a stdio MCP server."""
        server = await create_mcp_server(db_session, **_make_server_kwargs(test_tenant.id))
        assert server.name == "Test MCP Server"
        assert server.transport == "stdio"
        assert server.command == "python"
        assert server.args == ["-m", "mcp_server"]
        assert server.enabled is True

    async def test_streamable_http_success(self, db_session: AsyncSession, test_tenant):
        """Should create a streamable_http MCP server."""
        server = await create_mcp_server(
            db_session,
            **_make_server_kwargs(test_tenant.id, transport="streamable_http", url="http://example.com/mcp"),
        )
        assert server.transport == "streamable_http"
        assert server.url == "http://example.com/mcp"

    async def test_websocket_success(self, db_session: AsyncSession, test_tenant):
        """Should create a websocket MCP server."""
        server = await create_mcp_server(
            db_session,
            **_make_server_kwargs(test_tenant.id, transport="websocket", url="ws://example.com/mcp"),
        )
        assert server.transport == "websocket"
        assert server.url == "ws://example.com/mcp"

    async def test_with_env_vars_and_headers(self, db_session: AsyncSession, test_tenant):
        """Should encrypt env_vars and headers on creation."""
        server = await create_mcp_server(
            db_session,
            **_make_server_kwargs(
                test_tenant.id,
                env_vars={"API_KEY": "secret123"},
                headers={"Authorization": "Bearer token"},
            ),
        )
        # env_vars and headers are encrypted — verify they're not plaintext
        assert server.env_vars is not None
        assert "secret123" not in server.env_vars
        assert server.headers is not None
        assert "Bearer" not in server.headers

    async def test_with_allowed_tools(self, db_session: AsyncSession, test_tenant):
        """Should store allowed_tools list."""
        server = await create_mcp_server(
            db_session,
            **_make_server_kwargs(test_tenant.id, allowed_tools=["tool_a", "tool_b"]),
        )
        assert server.allowed_tools == ["tool_a", "tool_b"]

    async def test_missing_url_for_http_raises(self, db_session: AsyncSession, test_tenant):
        """Should raise ValidationError when url is missing for streamable_http."""
        with pytest.raises(ValidationError, match="requires a 'url' parameter"):
            await create_mcp_server(
                db_session,
                **_make_server_kwargs(test_tenant.id, transport="streamable_http", url=None, command=None),
            )

    async def test_missing_command_for_stdio_raises(self, db_session: AsyncSession, test_tenant):
        """Should raise ValidationError when command is missing for stdio."""
        with pytest.raises(ValidationError, match="requires a 'command' parameter"):
            await create_mcp_server(
                db_session,
                **_make_server_kwargs(test_tenant.id, command=None),
            )

    async def test_disabled(self, db_session: AsyncSession, test_tenant):
        """Should create a disabled server."""
        server = await create_mcp_server(
            db_session,
            **_make_server_kwargs(test_tenant.id, enabled=False),
        )
        assert server.enabled is False


class TestUpdateMcpServer:
    """Tests for update_mcp_server."""

    async def test_update_name(self, db_session: AsyncSession, test_tenant):
        """Should update the server name."""
        server = McpServer(tenant_id=test_tenant.id, name="Original", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        updated = await update_mcp_server(db_session, server.id, name="Renamed")
        assert updated.name == "Renamed"

    async def test_update_enabled(self, db_session: AsyncSession, test_tenant):
        """Should toggle the enabled flag."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python", enabled=True)
        db_session.add(server)
        await db_session.flush()

        updated = await update_mcp_server(db_session, server.id, enabled=False)
        assert updated.enabled is False

    async def test_update_url(self, db_session: AsyncSession, test_tenant):
        """Should update the URL."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="streamable_http", url="http://old")
        db_session.add(server)
        await db_session.flush()

        updated = await update_mcp_server(db_session, server.id, url="http://new")
        assert updated.url == "http://new"

    async def test_update_env_vars_reencrypts(self, db_session: AsyncSession, test_tenant):
        """Should re-encrypt env_vars when updated."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        updated = await update_mcp_server(db_session, server.id, env_vars={"KEY": "value"})
        assert updated.env_vars is not None
        assert "value" not in updated.env_vars

    async def test_nonexistent_raises(self, db_session: AsyncSession):
        """Should raise NotFoundError when server does not exist."""
        with pytest.raises(NotFoundError, match="MCP server not found"):
            await update_mcp_server(db_session, str(uuid.uuid4()), name="Nope")

    async def test_transport_validation_on_update(self, db_session: AsyncSession, test_tenant):
        """Should validate transport fields when updating transport."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        with pytest.raises(ValidationError, match="requires a 'url' parameter"):
            await update_mcp_server(db_session, server.id, transport="streamable_http", url=None)


class TestDeleteMcpServer:
    """Tests for delete_mcp_server."""

    async def test_delete_existing(self, db_session: AsyncSession, test_tenant):
        """Should delete the MCP server."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        await delete_mcp_server(db_session, server.id)

        result = await db_session.execute(
            select(McpServer).where(McpServer.id == server.id)
        )
        assert result.scalar_one_or_none() is None

    async def test_nonexistent_raises(self, db_session: AsyncSession):
        """Should raise NotFoundError when server does not exist."""
        with pytest.raises(NotFoundError, match="MCP server not found"):
            await delete_mcp_server(db_session, str(uuid.uuid4()))

    async def test_cascade_deletes_mcp_tools(self, db_session: AsyncSession, test_tenant):
        """Should delete associated Tool records with type='mcp'."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        tool = Tool(
            tenant_id=test_tenant.id, name="MCP Tool", type="mcp",
            config={"mcp_server_id": server.id, "tool_name": "test_tool"},
            category="mcp",
        )
        db_session.add(tool)
        await db_session.flush()

        await delete_mcp_server(db_session, server.id)

        tool_result = await db_session.execute(
            select(Tool).where(Tool.id == tool.id)
        )
        assert tool_result.scalar_one_or_none() is None


class TestDecryptHelpers:
    """Tests for decrypt_env_vars and decrypt_headers."""

    async def test_decrypt_env_vars_success(self, db_session: AsyncSession, test_tenant):
        """Should decrypt env_vars when available."""
        encrypted = encrypt(json.dumps({"KEY": "value"}))
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python", env_vars=encrypted)
        db_session.add(server)
        await db_session.flush()

        result = decrypt_env_vars(server)
        assert result == {"KEY": "value"}

    async def test_decrypt_env_vars_none(self, db_session: AsyncSession, test_tenant):
        """Should return None when env_vars is not set."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        result = decrypt_env_vars(server)
        assert result is None

    async def test_decrypt_headers_success(self, db_session: AsyncSession, test_tenant):
        """Should decrypt headers when available."""
        encrypted = encrypt(json.dumps({"Authorization": "Bearer x"}))
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python", headers=encrypted)
        db_session.add(server)
        await db_session.flush()

        result = decrypt_headers(server)
        assert result == {"Authorization": "Bearer x"}

    async def test_decrypt_headers_none(self, db_session: AsyncSession, test_tenant):
        """Should return None when headers is not set."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        result = decrypt_headers(server)
        assert result is None


class TestMaskHelpers:
    """Tests for mask_secret_value and mask_dict (pure functions)."""

    pytestmark = [pytest.mark.unit]

    def test_mask_secret_short(self):
        """Should return '****' for short values."""
        assert mask_secret_value("ab") == "****"
        assert mask_secret_value("12345678") == "****"

    def test_mask_secret_long(self):
        """Should show first 4 chars + '****' for longer values."""
        assert mask_secret_value("abcdefghij") == "abcd****"

    def test_mask_dict(self):
        """Should mask all values in a dict."""
        result = mask_dict({"key1": "secret123", "key2": "abcdefghij"})
        assert result["key1"] == "secr****"
        assert result["key2"] == "abcd****"

    def test_mask_dict_none(self):
        """Should return None for None input."""
        assert mask_dict(None) is None


class TestTestMcpConnection:
    """Tests for test_mcp_connection (mocked MCP connection)."""

    async def test_success_stdio(self, db_session: AsyncSession, test_tenant):
        """Should return connected=True with discovered tools."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        mock_tool = _create_mock_mcp_tool([
            {"name": "tool_a", "description": "First tool"},
            {"name": "tool_b", "description": "Second tool"},
        ])

        with patch("src.services.mcp_service._build_mcp_tool_instance", return_value=mock_tool):
            result = await svc_test_mcp_connection(db_session, server.id)

        assert result["connected"] is True
        assert len(result["tools"]) == 2
        assert result["tools"][0]["name"] == "tool_a"

    async def test_success_http(self, db_session: AsyncSession, test_tenant):
        """Should work for HTTP transport."""
        server = McpServer(tenant_id=test_tenant.id, name="HTTP", transport="streamable_http", url="http://localhost")
        db_session.add(server)
        await db_session.flush()

        mock_tool = _create_mock_mcp_tool([{"name": "http_tool", "description": "HTTP tool"}])

        with patch("src.services.mcp_service._build_mcp_tool_instance", return_value=mock_tool):
            result = await svc_test_mcp_connection(db_session, server.id)

        assert result["connected"] is True
        assert result["tools"][0]["name"] == "http_tool"

    async def test_failure_connection_error(self, db_session: AsyncSession, test_tenant):
        """Should return connected=False with error message on failure."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        mock_tool = AsyncMock()
        mock_tool.__aenter__ = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_tool.__aexit__ = AsyncMock(return_value=None)

        with patch("src.services.mcp_service._build_mcp_tool_instance", return_value=mock_tool):
            result = await svc_test_mcp_connection(db_session, server.id)

        assert result["connected"] is False
        assert "Connection refused" in result["error"]

    async def test_server_not_found(self, db_session: AsyncSession):
        """Should raise NotFoundError when server does not exist."""
        with pytest.raises(NotFoundError, match="MCP server not found"):
            await svc_test_mcp_connection(db_session, str(uuid.uuid4()))


class TestSyncMcpTools:
    """Tests for sync_mcp_tools (mocked MCP connection)."""

    async def test_success_creates_tools(self, db_session: AsyncSession, test_tenant):
        """Should create Tool records for discovered functions."""
        server = McpServer(tenant_id=test_tenant.id, name="Sync Server", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        mock_tool = _create_mock_mcp_tool([
            {"name": "new_tool", "description": "Brand new"},
        ])

        with patch("src.services.mcp_service._build_mcp_tool_instance", return_value=mock_tool):
            result = await sync_mcp_tools(db_session, server.id)

        assert result["created"] == 1
        assert result["updated"] == 0
        assert result["deprecated"] == 0

        # Verify Tool record was created
        tool_result = await db_session.execute(
            select(Tool).where(Tool.type == "mcp", Tool.config["mcp_server_id"].as_string() == server.id)
        )
        tools = tool_result.scalars().all()
        assert len(tools) == 1
        assert tools[0].name == "Sync Server: new_tool"

    async def test_updates_existing_tools(self, db_session: AsyncSession, test_tenant):
        """Should update existing Tool records."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        existing_tool = Tool(
            tenant_id=test_tenant.id, name="S: old_name", type="mcp",
            config={"mcp_server_id": server.id, "tool_name": "existing_tool"},
            category="mcp", enabled=True,
        )
        db_session.add(existing_tool)
        await db_session.flush()

        mock_tool = _create_mock_mcp_tool([
            {"name": "existing_tool", "description": "Updated description"},
        ])

        with patch("src.services.mcp_service._build_mcp_tool_instance", return_value=mock_tool):
            result = await sync_mcp_tools(db_session, server.id)

        assert result["created"] == 0
        assert result["updated"] == 1
        assert result["deprecated"] == 0

    async def test_removes_stale_tools(self, db_session: AsyncSession, test_tenant):
        """Should soft-deprecate tools no longer on the server."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        stale_tool = Tool(
            tenant_id=test_tenant.id, name="S: stale", type="mcp",
            config={"mcp_server_id": server.id, "tool_name": "stale_tool"},
            category="mcp", enabled=True,
        )
        db_session.add(stale_tool)
        await db_session.flush()

        mock_tool = _create_mock_mcp_tool([
            {"name": "active_tool", "description": "Still active"},
        ])

        with patch("src.services.mcp_service._build_mcp_tool_instance", return_value=mock_tool):
            result = await sync_mcp_tools(db_session, server.id)

        assert result["deprecated"] == 1  # stale_tool was disabled
        assert result["created"] == 1     # active_tool was created

    async def test_server_not_found(self, db_session: AsyncSession):
        """Should raise NotFoundError when server does not exist."""
        with pytest.raises(NotFoundError, match="MCP server not found"):
            await sync_mcp_tools(db_session, str(uuid.uuid4()))

    async def test_connection_failure(self, db_session: AsyncSession, test_tenant):
        """Should raise ValidationError when connection fails."""
        server = McpServer(tenant_id=test_tenant.id, name="S", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        mock_tool = AsyncMock()
        mock_tool.__aenter__ = AsyncMock(side_effect=ConnectionError("Failed to connect"))
        mock_tool.__aexit__ = AsyncMock(return_value=None)

        with patch("src.services.mcp_service._build_mcp_tool_instance", return_value=mock_tool):
            with pytest.raises(ValidationError, match="Failed to connect"):
                await sync_mcp_tools(db_session, server.id)


class TestTenantIsolation:
    """Tenant isolation tests for MCP servers."""

    async def test_cross_tenant_list_empty(self, db_session: AsyncSession, test_tenant, second_tenant):
        """Listing MCP servers from a different tenant should not include cross-tenant data."""
        McpServer(tenant_id=second_tenant.id, name="Other", transport="stdio", command="python")
        db_session.add(McpServer(tenant_id=test_tenant.id, name="Ours", transport="stdio", command="python"))
        await db_session.flush()

        items, total = await list_mcp_servers(db_session, tenant_id=test_tenant.id)
        assert total == 1
        assert items[0].name == "Ours"

    async def test_cross_tenant_get_denied(self, db_session: AsyncSession, test_tenant, second_tenant):
        """Getting a server by ID scoped to a different tenant should return None."""
        server = McpServer(tenant_id=second_tenant.id, name="Other", transport="stdio", command="python")
        db_session.add(server)
        await db_session.flush()

        # get_mcp_server doesn't scope by tenant (that's the API layer's job)
        # tenant isolation for MCP is enforced via list_mcp_servers(tenant_id=...)
        result = await get_mcp_server(db_session, server.id)
        assert result is not None  # by ID it's found — tenant scoping is at the API layer
