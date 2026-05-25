# =============================================================================
# PH Agent Hub — MCP Tool Callable Builder
# =============================================================================
# Given a Tool ORM record with type="mcp", resolves the associated MCP
# server config and returns MAF tool callables that can be passed to an
# Agent.
# =============================================================================

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework import MCPStreamableHTTPTool

from ..core.encryption import decrypt
from ..core.exceptions import NotFoundError
from ..db.orm.mcp_servers import McpServer
from ..db.orm.tools import Tool

logger = logging.getLogger(__name__)


async def build_mcp_tool_callables(
    db: AsyncSession,
    tool: Tool,
    tenant_id: str,
    session_id: str = "",
    cleanup_clients: list | None = None,
) -> list:
    """Build MAF callables for an MCP tool.

    Given a Tool record with type="mcp", loads the associated McpServer
    config, connects to it, and returns the discovered FunctionTool
    instances.

    Returns a list with a single MCPStreamableHTTPTool (or equivalent)
    whose .functions attribute contains the individual MCP-discovered
    callables.  The agent runner flattens these via .extend().

    NOTE: Connection is NOT established here — the caller (runner.py)
    is responsible for calling .connect() before the agent run and
    .close() after, via the cleanup_clients mechanism.
    """
    if cleanup_clients is None:
        cleanup_clients = []

    config = tool.config or {}
    mcp_server_id = config.get("mcp_server_id")
    if not mcp_server_id:
        logger.warning("MCP tool %s has no mcp_server_id in config", tool.id)
        return []

    # Load the McpServer record
    result = await db.execute(
        select(McpServer).where(McpServer.id == mcp_server_id)
    )
    server = result.scalar_one_or_none()
    if server is None:
        logger.warning("MCP server %s not found for tool %s", mcp_server_id, tool.id)
        return []

    if not server.enabled:
        logger.info("MCP server %s is disabled, skipping tool %s", server.name, tool.id)
        return []

    # Decrypt headers for auth
    headers = {}
    if server.headers:
        try:
            headers = json.loads(decrypt(server.headers))
        except Exception:
            logger.warning("Failed to decrypt headers for MCP server %s", server.id)

    # Decrypt env vars for stdio
    env = {}
    if server.env_vars:
        try:
            env = json.loads(decrypt(server.env_vars))
        except Exception:
            logger.warning("Failed to decrypt env_vars for MCP server %s", server.id)

    mcp_tool_name = config.get("tool_name", tool.name)

    common_kwargs = {
        "name": server.name,
        "allowed_tools": server.allowed_tools,
        "approval_mode": "never_require",
    }

    if server.transport == "streamable_http":
        mcp_tool = MCPStreamableHTTPTool(
            **common_kwargs,
            url=server.url,
            header_provider=lambda _ctx: headers,
        )
    elif server.transport == "websocket":
        from agent_framework import MCPWebsocketTool
        mcp_tool = MCPWebsocketTool(
            **common_kwargs,
            url=server.url,
        )
    elif server.transport == "stdio":
        from agent_framework import MCPStdioTool
        mcp_tool = MCPStdioTool(
            **common_kwargs,
            command=server.command,
            args=server.args or [],
            env=env,
        )
    else:
        logger.warning("Unsupported MCP transport: %s", server.transport)
        return []

    # The MCP tool will be connected by the runner's lifecycle management
    return [mcp_tool]
