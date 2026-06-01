# =============================================================================
# PH Agent Hub — MCP Server Service
# =============================================================================
# CRUD + test connection + sync tools for MCP server configurations.
# All env_vars and headers are encrypted at rest using the Fernet key.
# =============================================================================

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.encryption import encrypt, decrypt
from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.mcp_servers import McpServer
from ..db.orm.tools import Tool
from ..services.tool_service import derive_tool_category

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def list_mcp_servers(
    db: AsyncSession,
    tenant_id: str | None = None,
    *,
    search: str | None = None,
    transport: str | None = None,
    enabled: bool | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[McpServer], int]:
    """Return MCP servers with optional filtering and pagination."""
    stmt = select(McpServer)
    if tenant_id is not None:
        stmt = stmt.where(McpServer.tenant_id == tenant_id)
    if transport is not None:
        stmt = stmt.where(McpServer.transport == transport)
    if enabled is not None:
        stmt = stmt.where(McpServer.enabled == enabled)
    if search:
        stmt = stmt.where(McpServer.name.ilike(f"%{search}%"))

    stmt = stmt.order_by(McpServer.created_at.desc())

    from ..core.pagination import paginate
    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_mcp_server(
    db: AsyncSession, server_id: str
) -> McpServer | None:
    """Look up an MCP server by primary key."""
    result = await db.execute(
        select(McpServer).where(McpServer.id == server_id)
    )
    return result.scalar_one_or_none()


async def create_mcp_server(
    db: AsyncSession,
    tenant_id: str,
    name: str,
    transport: str,
    *,
    url: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    env_vars: dict | None = None,
    headers: dict | None = None,
    allowed_tools: list[str] | None = None,
    enabled: bool = True,
) -> McpServer:
    """Create a new MCP server config. Encrypts secrets at rest.

    Raises ValidationError if transport-specific required fields are missing.
    """
    _validate_transport_fields(transport, url=url, command=command)

    # Encrypt secrets
    encrypted_env = encrypt(json.dumps(env_vars)) if env_vars else None
    encrypted_headers = encrypt(json.dumps(headers)) if headers else None

    server = McpServer(
        tenant_id=tenant_id,
        name=name,
        transport=transport,
        url=url,
        command=command,
        args=args,
        env_vars=encrypted_env,
        headers=encrypted_headers,
        allowed_tools=allowed_tools,
        enabled=enabled,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    logger.info("Created MCP server '%s' (transport=%s) for tenant %s", name, transport, tenant_id)
    return server


async def update_mcp_server(
    db: AsyncSession,
    server_id: str,
    **fields,
) -> McpServer:
    """Update an MCP server's fields. Re-encrypts secrets if changed.

    Raises NotFoundError if missing, ValidationError if transport validation fails.
    """
    server = await get_mcp_server(db, server_id)
    if server is None:
        raise NotFoundError("MCP server not found")

    # If transport is being updated, validate required fields
    new_transport = fields.get("transport", server.transport)
    new_url = fields.get("url", server.url)
    new_command = fields.get("command", server.command)
    _validate_transport_fields(new_transport, url=new_url, command=new_command)

    # Encrypt secrets if provided
    if "env_vars" in fields:
        env = fields["env_vars"]
        fields["env_vars"] = encrypt(json.dumps(env)) if env else None
    if "headers" in fields:
        hdrs = fields["headers"]
        fields["headers"] = encrypt(json.dumps(hdrs)) if hdrs else None

    for key, value in fields.items():
        if hasattr(server, key):
            setattr(server, key, value)

    server.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(server)
    logger.info("Updated MCP server '%s' (id=%s)", server.name, server_id)
    return server


async def delete_mcp_server(
    db: AsyncSession,
    server_id: str,
) -> None:
    """Delete an MCP server and its associated tool records.

    Raises NotFoundError if missing.
    """
    server = await get_mcp_server(db, server_id)
    if server is None:
        raise NotFoundError("MCP server not found")

    # Delete associated Tool records with type="mcp" pointing to this server
    result = await db.execute(
        select(Tool).where(
            Tool.type == "mcp",
            Tool.config["mcp_server_id"].as_string() == server_id,
        )
    )
    for tool in result.scalars().all():
        await db.delete(tool)

    await db.delete(server)
    await db.commit()
    logger.info("Deleted MCP server '%s' (id=%s) and its tools", server.name, server_id)


# ---------------------------------------------------------------------------
# Decrypt helpers (for admin API responses — returns masked values)
# ---------------------------------------------------------------------------


def decrypt_env_vars(server: McpServer) -> dict | None:
    """Decrypt and parse env_vars. Returns None if not set."""
    if not server.env_vars:
        return None
    try:
        return json.loads(decrypt(server.env_vars))
    except Exception:
        logger.warning("Failed to decrypt env_vars for MCP server %s", server.id)
        return None


def decrypt_headers(server: McpServer) -> dict | None:
    """Decrypt and parse headers. Returns None if not set."""
    if not server.headers:
        return None
    try:
        return json.loads(decrypt(server.headers))
    except Exception:
        logger.warning("Failed to decrypt headers for MCP server %s", server.id)
        return None


def mask_secret_value(value: str) -> str:
    """Mask a secret value for API responses, showing only first 4 chars."""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****"


def mask_dict(d: dict | None) -> dict | None:
    """Return a masked copy of a dict for API responses."""
    if d is None:
        return None
    return {k: mask_secret_value(str(v)) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------


async def test_mcp_connection(
    db: AsyncSession,
    server_id: str,
) -> dict:
    """Test connection to an MCP server and return discovered tools.

    Returns a dict with keys:
      - connected (bool)
      - tools (list of {name, description})
      - error (str | None)
    """
    server = await get_mcp_server(db, server_id)
    if server is None:
        raise NotFoundError("MCP server not found")

    try:
        mcp_tool = _build_mcp_tool_instance(server)
        async with mcp_tool:
            functions = mcp_tool.functions
            tools = [
                {
                    "name": fn.name,
                    "description": fn.description or "",
                }
                for fn in functions
            ]
            return {"connected": True, "tools": tools, "error": None}
    except Exception as exc:
        logger.warning("MCP connection test failed for server %s: %s", server_id, exc)
        return {"connected": False, "tools": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Sync tools (discover MCP tools and upsert Tool records)
# ---------------------------------------------------------------------------


async def sync_mcp_tools(
    db: AsyncSession,
    server_id: str,
) -> dict:
    """Connect to an MCP server, discover tools, and upsert Tool records.

    For each discovered function:
      - If a Tool with matching (type='mcp', config.server_id, config.tool_name)
        exists, update its name/description.
      - Otherwise, create a new Tool record.
      - Tools no longer present on the server are soft-deprecated (enabled=False).

    Returns a dict with counts: created, updated, deprecated.
    Raises ValidationError if the MCP server is unreachable.
    """
    server = await get_mcp_server(db, server_id)
    if server is None:
        raise NotFoundError("MCP server not found")

    try:
        mcp_tool = _build_mcp_tool_instance(server)
        async with mcp_tool:
            functions = mcp_tool.functions
    except Exception as exc:
        logger.warning("MCP sync failed for server %s: %s", server_id, exc)
        raise ValidationError(
            f"Failed to connect to MCP server '{server.name}': {exc}"
        ) from exc

    discovered_names = {fn.name for fn in functions}

    # Fetch existing Tool records for this MCP server
    result = await db.execute(
        select(Tool).where(
            Tool.type == "mcp",
            Tool.config["mcp_server_id"].as_string() == server_id,
        )
    )
    existing_tools = {t.config.get("tool_name", ""): t for t in result.scalars().all() if t.config}

    created = 0
    updated = 0
    deprecated = 0

    for fn in functions:
        tool_name = fn.name
        tool_description = fn.description or ""
        config = {
            "mcp_server_id": server_id,
            "tool_name": tool_name,
        }

        if tool_name in existing_tools:
            # Update existing record
            tool = existing_tools[tool_name]
            tool.name = f"{server.name}: {tool_name}"
            tool.config = config
            tool.enabled = True
            updated += 1
            existing_tools.pop(tool_name)
        else:
            # Create new Tool record
            tool = Tool(
                tenant_id=server.tenant_id,
                name=f"{server.name}: {tool_name}",
                type="mcp",
                config=config,
                category="mcp",
                enabled=True,
                is_public=False,
            )
            db.add(tool)
            created += 1

    # Remaining existing tools are no longer on the server → soft-deprecate
    for tool_name, tool in existing_tools.items():
        if tool.enabled:
            tool.enabled = False
            deprecated += 1

    await db.commit()
    logger.info(
        "Synced MCP server '%s': %d created, %d updated, %d deprecated",
        server.name, created, updated, deprecated,
    )
    return {"created": created, "updated": updated, "deprecated": deprecated}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_transport_fields(
    transport: str,
    *,
    url: str | None = None,
    command: str | None = None,
) -> None:
    """Validate that the required fields are present for the given transport."""
    if transport in ("streamable_http", "websocket"):
        if not url:
            raise ValidationError(
                f"Transport '{transport}' requires a 'url' parameter."
            )
    elif transport == "stdio":
        if not command:
            raise ValidationError(
                "Transport 'stdio' requires a 'command' parameter."
            )
    else:
        raise ValidationError(f"Unsupported transport: '{transport}'")


def _build_mcp_tool_instance(server: McpServer):
    """Build the appropriate MAF MCP tool instance from a McpServer ORM record.

    Returns an instance of MCPStreamableHTTPTool, MCPStdioTool, or
    MCPWebsocketTool depending on the server's transport setting.
    """
    from agent_framework import (
        MCPStreamableHTTPTool,
        MCPStdioTool,
        MCPWebsocketTool,
    )

    env = decrypt_env_vars(server) or {}
    hdrs = decrypt_headers(server) or {}

    common_kwargs = {
        "name": server.name,
        "allowed_tools": server.allowed_tools,
        "approval_mode": "never_require",
    }

    if server.transport == "streamable_http":
        from httpx import AsyncClient, Timeout

        http_client = AsyncClient(
            follow_redirects=True,
            timeout=Timeout(30.0, read=300.0),
            headers=hdrs,
        )
        return MCPStreamableHTTPTool(
            **common_kwargs,
            url=server.url,
            http_client=http_client,
        )
    elif server.transport == "websocket":
        return MCPWebsocketTool(
            **common_kwargs,
            url=server.url,
        )
    elif server.transport == "stdio":
        return MCPStdioTool(
            **common_kwargs,
            command=server.command,
            args=server.args or [],
            env=env,
        )
    else:
        raise ValidationError(f"Unsupported transport: '{server.transport}'")
