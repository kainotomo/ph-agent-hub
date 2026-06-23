# =============================================================================
# PH Agent Hub — A2A Server Service
# =============================================================================
# CRUD + test connection + sync tools for A2A remote agent configurations.
# Auth tokens and headers are encrypted at rest using the Fernet key.
# Agent Cards are resolved via the a2a-sdk (A2ACardResolver).
# =============================================================================

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.encryption import encrypt, decrypt
from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.a2a_servers import A2aServer
from ..db.orm.tools import Tool
from ..services.tool_service import derive_tool_category

logger = logging.getLogger(__name__)

# A2A protocol version this agent hub supports
A2A_SUPPORTED_PROTOCOL_VERSION = "1.0"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def list_a2a_servers(
    db: AsyncSession,
    tenant_id: str | None = None,
    *,
    search: str | None = None,
    protocol_binding: str | None = None,
    enabled: bool | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[A2aServer], int]:
    """Return A2A servers with optional filtering and pagination."""
    stmt = select(A2aServer)
    if tenant_id is not None:
        stmt = stmt.where(A2aServer.tenant_id == tenant_id)
    if protocol_binding is not None:
        stmt = stmt.where(A2aServer.protocol_binding == protocol_binding)
    if enabled is not None:
        stmt = stmt.where(A2aServer.enabled == enabled)
    if search:
        stmt = stmt.where(
            or_(
                A2aServer.name.ilike(f"%{search}%"),
                A2aServer.url.ilike(f"%{search}%"),
            )
        )

    stmt = stmt.order_by(A2aServer.created_at.desc())

    from ..core.pagination import paginate
    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_a2a_server(
    db: AsyncSession, server_id: str
) -> A2aServer | None:
    """Look up an A2A server by primary key."""
    result = await db.execute(
        select(A2aServer).where(A2aServer.id == server_id)
    )
    return result.scalar_one_or_none()


async def create_a2a_server(
    db: AsyncSession,
    tenant_id: str,
    name: str,
    protocol_binding: str,
    *,
    url: str | None = None,
    agent_card_path: str = "/.well-known/agent-card.json",
    auth_scheme: str | None = None,
    auth_token: str | None = None,
    headers: dict | None = None,
    allowed_skills: list[str] | None = None,
    enabled: bool = True,
    # --- Resilience config (Issue #409) ---
    retry_max_attempts: int | None = None,
    retry_backoff_base_seconds: float | None = None,
    retry_backoff_max_seconds: float | None = None,
    timeout_connect_seconds: float | None = None,
    timeout_read_seconds: float | None = None,
    timeout_stream_seconds: float | None = None,
    circuit_breaker_threshold: int | None = None,
    circuit_breaker_window_seconds: int | None = None,
    circuit_breaker_cooldown_seconds: int | None = None,
    # --- OAuth2 config (Issue #418) ---
    oauth2_client_id: str | None = None,
    oauth2_client_secret: str | None = None,
    oauth2_authorize_url: str | None = None,
    oauth2_token_url: str | None = None,
    oauth2_scopes: str | None = None,
    oauth2_tokens: str | None = None,
) -> A2aServer:
    """Create a new A2A server config. Encrypts secrets at rest.

    Raises ValidationError if required fields are missing.
    """
    _validate_binding_fields(protocol_binding, url=url)

    # Encrypt secrets
    encrypted_token = encrypt(auth_token) if auth_token else None
    encrypted_headers = encrypt(json.dumps(headers)) if headers else None
    encrypted_oauth2_client_secret = encrypt(oauth2_client_secret) if oauth2_client_secret else None
    encrypted_oauth2_tokens = encrypt(oauth2_tokens) if oauth2_tokens else None

    server = A2aServer(
        tenant_id=tenant_id,
        name=name,
        url=url,
        agent_card_path=agent_card_path,
        protocol_binding=protocol_binding,
        auth_scheme=auth_scheme or "none",
        auth_token=encrypted_token,
        headers=encrypted_headers,
        allowed_skills=allowed_skills,
        enabled=enabled,
        # Resilience config
        retry_max_attempts=retry_max_attempts,
        retry_backoff_base_seconds=retry_backoff_base_seconds,
        retry_backoff_max_seconds=retry_backoff_max_seconds,
        timeout_connect_seconds=timeout_connect_seconds,
        timeout_read_seconds=timeout_read_seconds,
        timeout_stream_seconds=timeout_stream_seconds,
        circuit_breaker_threshold=circuit_breaker_threshold,
        circuit_breaker_window_seconds=circuit_breaker_window_seconds,
        circuit_breaker_cooldown_seconds=circuit_breaker_cooldown_seconds,
        # OAuth2 config
        oauth2_client_id=oauth2_client_id,
        oauth2_client_secret=encrypted_oauth2_client_secret,
        oauth2_authorize_url=oauth2_authorize_url,
        oauth2_token_url=oauth2_token_url,
        oauth2_scopes=oauth2_scopes,
        oauth2_tokens=encrypted_oauth2_tokens,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    logger.info(
        "Created A2A server '%s' (binding=%s) for tenant %s",
        name, protocol_binding, tenant_id,
    )
    return server


async def update_a2a_server(
    db: AsyncSession,
    server_id: str,
    **fields,
) -> A2aServer:
    """Update an A2A server's fields. Re-encrypts secrets if changed.

    Raises NotFoundError if missing, ValidationError if validation fails.
    """
    server = await get_a2a_server(db, server_id)
    if server is None:
        raise NotFoundError("A2A server not found")

    # If protocol_binding is being updated, validate required fields
    new_binding = fields.get("protocol_binding", server.protocol_binding)
    new_url = fields.get("url", server.url)
    _validate_binding_fields(new_binding, url=new_url)

    # Encrypt secrets if provided
    if "auth_token" in fields:
        token = fields["auth_token"]
        fields["auth_token"] = encrypt(token) if token else None
    if "headers" in fields:
        hdrs = fields["headers"]
        fields["headers"] = encrypt(json.dumps(hdrs)) if hdrs else None
    if "oauth2_client_secret" in fields:
        secret = fields["oauth2_client_secret"]
        fields["oauth2_client_secret"] = encrypt(secret) if secret else None
    if "oauth2_tokens" in fields:
        tokens = fields["oauth2_tokens"]
        fields["oauth2_tokens"] = encrypt(tokens) if tokens else None

    for key, value in fields.items():
        if hasattr(server, key):
            setattr(server, key, value)

    server.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(server)
    logger.info("Updated A2A server '%s' (id=%s)", server.name, server_id)
    return server


async def delete_a2a_server(
    db: AsyncSession,
    server_id: str,
) -> None:
    """Delete an A2A server and its associated tool records.

    Raises NotFoundError if missing.
    """
    server = await get_a2a_server(db, server_id)
    if server is None:
        raise NotFoundError("A2A server not found")

    # Clean up OAuth2 refresh lock if present
    _oauth2_refresh_locks.pop(server_id, None)

    # Delete associated Tool records with type="a2a" pointing to this server
    result = await db.execute(
        select(Tool).where(
            Tool.type == "a2a",
            Tool.config["a2a_server_id"].as_string() == server_id,
        )
    )
    for tool in result.scalars().all():
        await db.delete(tool)

    await db.delete(server)
    await db.commit()
    logger.info(
        "Deleted A2A server '%s' (id=%s) and its tools",
        server.name, server_id,
    )


# ---------------------------------------------------------------------------
# Decrypt helpers (for admin API responses)
# ---------------------------------------------------------------------------


def decrypt_auth_token(server: A2aServer) -> str | None:
    """Decrypt auth_token. Returns None if not set."""
    if not server.auth_token:
        return None
    try:
        return decrypt(server.auth_token)
    except Exception:
        logger.warning("Failed to decrypt auth_token for A2A server %s", server.id)
        return None


def decrypt_oauth2_client_secret(server: A2aServer) -> str | None:
    """Decrypt the stored OAuth2 client secret. Returns None if not set."""
    if not server.oauth2_client_secret:
        return None
    try:
        return decrypt(server.oauth2_client_secret)
    except Exception:
        logger.warning(
            "Failed to decrypt oauth2_client_secret for A2A server %s", server.id
        )
        return None


def decrypt_headers(server: A2aServer) -> dict | None:
    """Decrypt and parse headers. Returns None if not set."""
    if not server.headers:
        return None
    try:
        return json.loads(decrypt(server.headers))
    except Exception:
        logger.warning("Failed to decrypt headers for A2A server %s", server.id)
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
# Test connection (resolve Agent Card)
# ---------------------------------------------------------------------------


async def test_a2a_connection(
    db: AsyncSession,
    server_id: str,
) -> dict:
    """Test connection to an A2A server by resolving its Agent Card.

    Uses the a2a-sdk's A2ACardResolver to fetch and validate the card.

    Returns a dict with keys:
      - connected (bool)
      - agent_name (str | None)
      - agent_description (str | None)
      - capabilities (dict | None)
      - skills (list of {id, name, description})
      - error (str | None)
    """
    server = await get_a2a_server(db, server_id)
    if server is None:
        raise NotFoundError("A2A server not found")

    try:
        agent_card = await _resolve_agent_card(server, db)
        skills = [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "inputModes": _safe_list(getattr(s, "input_modes", None) or getattr(s, "inputModes", None)),
                "outputModes": _safe_list(getattr(s, "output_modes", None) or getattr(s, "outputModes", None)),
                "examples": _safe_list(getattr(s, "examples", None)),
                "tags": _safe_list(getattr(s, "tags", None)),
            }
            for s in agent_card.skills
        ] if agent_card.skills else []

        return {
            "connected": True,
            "agent_name": agent_card.name,
            "agent_description": agent_card.description,
            "capabilities": {
                "streaming": agent_card.capabilities.streaming if agent_card.capabilities else False,
                "pushNotifications": agent_card.capabilities.push_notifications if agent_card.capabilities else False,
            },
            "skills": skills,
            "error": None,
        }
    except Exception as exc:
        logger.warning("A2A connection test failed for server %s: %s", server_id, exc)
        return {
            "connected": False,
            "agent_name": None,
            "agent_description": None,
            "capabilities": None,
            "skills": [],
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Sync tools (discover A2A skills and upsert Tool records)
# ---------------------------------------------------------------------------


async def sync_a2a_tools(
    db: AsyncSession,
    server_id: str,
) -> dict:
    """Connect to an A2A server, discover skills via Agent Card, and upsert
    Tool records.

    For each discovered skill:
      - If a Tool with matching (type='a2a', config.server_id, config.skill_id)
        exists, update its name/description.
      - Otherwise, create a new Tool record.
      - Skills no longer on the server are soft-deprecated (enabled=False).

    Returns a dict with counts: created, updated, deprecated.
    Raises ValidationError if the A2A server is unreachable.
    """
    server = await get_a2a_server(db, server_id)
    if server is None:
        raise NotFoundError("A2A server not found")

    try:
        agent_card = await _resolve_agent_card(server, db)
    except Exception as exc:
        logger.warning("A2A sync failed for server %s: %s", server_id, exc)
        raise ValidationError(
            f"Failed to connect to A2A server '{server.name}': {exc}"
        ) from exc

    discovered_skills = agent_card.skills or []
    discovered_ids = {s.id for s in discovered_skills}

    # Cache the full Agent Card JSON for later use (media type negotiation, etc.)
    _cache_agent_card(server, agent_card)

    # Fetch existing Tool records for this A2A server
    result = await db.execute(
        select(Tool).where(
            Tool.type == "a2a",
            Tool.config["a2a_server_id"].as_string() == server_id,
        )
    )
    existing_tools = {
        t.config.get("skill_id", ""): t
        for t in result.scalars().all() if t.config
    }

    created = 0
    updated = 0
    deprecated = 0

    for skill in discovered_skills:
        skill_name = skill.name or skill.id
        config = {
            "a2a_server_id": server_id,
            "skill_id": skill.id,
            "skill_name": skill_name,
            "skill_description": skill.description or "",
            # A2A protocol metadata (Issue #408)
            "input_modes": _safe_list(getattr(skill, "input_modes", None) or getattr(skill, "inputModes", None)),
            "output_modes": _safe_list(getattr(skill, "output_modes", None) or getattr(skill, "outputModes", None)),
            "examples": _safe_list(getattr(skill, "examples", None)),
            "tags": _safe_list(getattr(skill, "tags", None)),
        }

        if skill.id in existing_tools:
            # Update existing record
            tool = existing_tools[skill.id]
            tool.name = f"{server.name}: {skill_name}"
            tool.config = config
            tool.enabled = True
            tool.is_public = True
            updated += 1
            existing_tools.pop(skill.id)
        else:
            # Create new Tool record
            tool = Tool(
                tenant_id=server.tenant_id,
                name=f"{server.name}: {skill_name}",
                type="a2a",
                config=config,
                category=derive_tool_category("a2a"),
                enabled=True,
                is_public=True,
            )
            db.add(tool)
            created += 1

    # Remaining existing skills are no longer on the server → soft-deprecate
    for skill_id, tool in existing_tools.items():
        if tool.enabled:
            tool.enabled = False
            deprecated += 1

    await db.commit()
    logger.info(
        "Synced A2A server '%s': %d created, %d updated, %d deprecated",
        server.name, created, updated, deprecated,
    )
    return {"created": created, "updated": updated, "deprecated": deprecated}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_list(value) -> list:
    """Return *value* as a list, or an empty list if it is None."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _cache_agent_card(server: A2aServer, agent_card) -> None:
    """Store a JSON-serializable snapshot of the Agent Card in
    ``server.agent_card_cache`` and update the timestamp."""
    try:
        # Try protobuf MessageToDict / to_json first
        if hasattr(agent_card, "to_json"):
            server.agent_card_cache = json.loads(agent_card.to_json())
        elif hasattr(agent_card, "MessageToDict"):
            from google.protobuf.json_format import MessageToDict
            server.agent_card_cache = MessageToDict(agent_card)
        else:
            # Fallback: build a minimal dict from known fields
            server.agent_card_cache = {
                "name": getattr(agent_card, "name", ""),
                "description": getattr(agent_card, "description", ""),
                "defaultInputModes": _safe_list(getattr(agent_card, "default_input_modes", None) or getattr(agent_card, "defaultInputModes", None)),
                "defaultOutputModes": _safe_list(getattr(agent_card, "default_output_modes", None) or getattr(agent_card, "defaultOutputModes", None)),
            }
    except Exception:
        logger.warning("Failed to cache Agent Card for server %s", server.id, exc_info=True)
        return
    server.agent_card_cached_at = datetime.now(timezone.utc)


def _validate_binding_fields(
    protocol_binding: str,
    *,
    url: str | None = None,
) -> None:
    """Validate that required fields are present for the given binding."""
    if not url:
        raise ValidationError(
            f"Protocol binding '{protocol_binding}' requires a 'url' parameter."
        )


async def _resolve_agent_card(server: A2aServer, db: AsyncSession | None = None):
    """Resolve an Agent Card from an A2A server using the a2a-sdk.

    Optionally accepts a db session so OAuth2 token refreshes can be
    persisted. Callers that have a session (test_a2a_connection,
    sync_a2a_tools) should pass it.

    Returns an AgentCard object from the a2a.types module.
    """
    import httpx
    from a2a.client import A2ACardResolver

    # Decrypt headers for auth
    headers = decrypt_headers(server) or {}
    auth_token = decrypt_auth_token(server)

    if auth_token and "authorization" not in {k.lower() for k in headers}:
        if server.auth_scheme == "bearer":
            headers["Authorization"] = f"Bearer {auth_token}"
        elif server.auth_scheme == "api_key":
            headers["Authorization"] = f"Bearer {auth_token}"

    # OAuth2: get a fresh access token (triggers refresh if expired)
    if server.auth_scheme == "oauth2" and "authorization" not in {k.lower() for k in headers}:
        oauth_token = await get_oauth2_access_token(server, db)
        if oauth_token:
            headers["Authorization"] = f"Bearer {oauth_token}"

    async with httpx.AsyncClient(headers=headers) as client:
        resolver = A2ACardResolver(
            httpx_client=client,
            base_url=server.url.rstrip("/"),
            agent_card_path=server.agent_card_path,
        )
        card = await resolver.get_agent_card()

        # --- A2A protocol version negotiation (Issue #409) ---------------
        _validate_supported_interfaces(card, server)

        return card


# ---------------------------------------------------------------------------
# A2A protocol version negotiation
# ---------------------------------------------------------------------------


def _validate_supported_interfaces(card, server: A2aServer) -> None:
    """Validate that the remote Agent Card declares at least one supported
    protocol version.

    Logs a warning if versions are mismatched but still compatible.
    Raises ``ValidationError`` if no compatible interface exists.
    """
    supported_interfaces = getattr(card, "supportedInterfaces", None) or []
    if not supported_interfaces:
        logger.warning(
            "Agent Card for server '%s' (%s) declares no supportedInterfaces. "
            "Proceeding — may indicate an older A2A implementation.",
            server.name, server.url,
        )
        return

    compatible = any(
        getattr(iface, "protocolVersion", None) == A2A_SUPPORTED_PROTOCOL_VERSION
        for iface in supported_interfaces
    )

    if compatible:
        return  # All good

    # Log all declared versions for debugging
    declared_versions = [
        getattr(iface, "protocolVersion", "unknown") for iface in supported_interfaces
    ]
    logger.warning(
        "Agent Card for server '%s' (%s) declares protocol versions %s. "
        "This hub supports %s. Proceeding optimistically.",
        server.name, server.url,
        declared_versions,
        A2A_SUPPORTED_PROTOCOL_VERSION,
    )


# ---------------------------------------------------------------------------
# OAuth2 token helpers (Issue #418)
# ---------------------------------------------------------------------------

# Per-server refresh lock to prevent duplicate concurrent refreshes
_oauth2_refresh_locks: dict[str, asyncio.Lock] = {}
_oauth2_refresh_locks_lock = asyncio.Lock()


def _decrypt_oauth2_tokens(server: A2aServer) -> dict | None:
    """Decrypt and parse the oauth2_tokens JSON blob.

    Returns None if not set or decrypt fails.
    """
    if not server.oauth2_tokens:
        return None
    try:
        return json.loads(decrypt(server.oauth2_tokens))
    except Exception:
        logger.warning("Failed to decrypt oauth2_tokens for server %s", server.id)
        return None


def _encrypt_oauth2_tokens(tokens: dict) -> str:
    """Encrypt OAuth2 tokens dict into a Fernet string."""
    return encrypt(json.dumps(tokens))


def _get_oauth2_client_secret(server: A2aServer) -> str | None:
    """Decrypt the stored OAuth2 client secret."""
    if not server.oauth2_client_secret:
        return None
    try:
        return decrypt(server.oauth2_client_secret)
    except Exception:
        logger.warning("Failed to decrypt oauth2_client_secret for server %s", server.id)
        return None


async def exchange_oauth2_code(
    code: str,
    redirect_uri: str,
    server: A2aServer,
) -> dict | None:
    """Exchange an authorization code for OAuth2 tokens.

    POST to the server's token_url with grant_type=authorization_code.
    Returns {access_token, refresh_token, expires_at} or None on failure.
    """
    import httpx

    client_secret = _get_oauth2_client_secret(server)
    if not client_secret:
        logger.warning("Cannot exchange OAuth2 code: no client_secret for server %s", server.id)
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                server.oauth2_token_url,
                data={
                    "code": code,
                    "client_id": server.oauth2_client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if response.status_code != 200:
                logger.warning(
                    "OAuth2 code exchange failed for server %s: HTTP %d",
                    server.id, response.status_code,
                )
                return None

            data = response.json()

        tokens: dict = {
            "access_token": data.get("access_token", ""),
            "refresh_token": data.get("refresh_token", ""),
            "expires_at": int(time.time()) + data.get("expires_in", 3600),
        }
        return tokens
    except Exception as exc:
        logger.error(
            "OAuth2 code exchange error for server %s: %s",
            server.id, exc,
        )
        return None


async def refresh_oauth2_token(
    server: A2aServer,
) -> dict | None:
    """Refresh an OAuth2 access token.

    POST to the server's token_url with grant_type=refresh_token.
    If the provider returns ``invalid_grant`` (refresh token expired/revoked),
    clear the stored tokens and return None.

    Returns {access_token, refresh_token?, expires_at} or None on failure.
    """
    import httpx

    client_secret = _get_oauth2_client_secret(server)
    if not client_secret:
        logger.warning("Cannot refresh OAuth2 token: no client_secret for server %s", server.id)
        return None

    existing_tokens = _decrypt_oauth2_tokens(server)
    if not existing_tokens:
        return None

    refresh_token = existing_tokens.get("refresh_token", "")
    if not refresh_token:
        logger.warning("No refresh_token available for server %s", server.id)
        return None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                server.oauth2_token_url,
                data={
                    "refresh_token": refresh_token,
                    "client_id": server.oauth2_client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                },
            )

            if response.status_code != 200:
                error_body = response.json()
                error = error_body.get("error", "")
                if error == "invalid_grant":
                    logger.warning(
                        "Refresh token expired/revoked for server %s — clearing tokens",
                        server.id,
                    )
                    # Clear stored tokens so the UI shows "expired"
                    server.oauth2_tokens = None
                    return None

                logger.warning(
                    "OAuth2 token refresh failed for server %s: HTTP %d",
                    server.id, response.status_code,
                )
                return None

            data = response.json()

        tokens: dict = {
            "access_token": data.get("access_token", ""),
            "expires_at": int(time.time()) + data.get("expires_in", 3600),
        }
        # Some providers rotate refresh tokens
        new_rt = data.get("refresh_token")
        if new_rt:
            tokens["refresh_token"] = new_rt
        else:
            # Preserve existing refresh token if not rotated
            tokens["refresh_token"] = existing_tokens.get("refresh_token", "")

        return tokens
    except Exception as exc:
        logger.error(
            "OAuth2 token refresh error for server %s: %s",
            server.id, exc,
        )
        return None


def is_oauth2_token_expired(server: A2aServer) -> bool:
    """Check if the stored OAuth2 access token is expired (5-minute buffer)."""
    tokens = _decrypt_oauth2_tokens(server)
    if not tokens:
        return True
    expires_at = tokens.get("expires_at", 0)
    if not expires_at:
        return True
    return int(time.time()) >= (int(expires_at) - 300)


def get_oauth2_tokens_status(server: A2aServer) -> str | None:
    """Return the OAuth2 token status for API responses.

    Returns:
        - ``"authorized"`` — valid tokens present
        - ``"expired"`` — tokens present but refresh token is dead
        - ``"none"`` — no tokens yet
        - ``None`` — server not using OAuth2
    """
    if server.auth_scheme != "oauth2":
        return None
    if not server.oauth2_tokens:
        return "none"
    tokens = _decrypt_oauth2_tokens(server)
    if not tokens:
        return "expired"
    if not tokens.get("access_token"):
        return "none"
    return "authorized"


async def get_oauth2_access_token(
    server: A2aServer,
    db: AsyncSession | None,
) -> str | None:
    """Get a valid OAuth2 access token for the given server.

    Checks expiry and refreshes transparently if needed.
    Uses a per-server concurrency lock to prevent duplicate refreshes.

    If ``db`` is None, the refreshed token will NOT be persisted to the DB
    (caller should handle persistence).

    Returns the access token string, or None if unavailable.
    """
    # Quick check without lock — most requests are fresh
    if not is_oauth2_token_expired(server):
        tokens = _decrypt_oauth2_tokens(server)
        if tokens:
            return tokens.get("access_token")

    # Token is expired — acquire per-server lock
    async with _oauth2_refresh_locks_lock:
        if server.id not in _oauth2_refresh_locks:
            _oauth2_refresh_locks[server.id] = asyncio.Lock()
        lock = _oauth2_refresh_locks[server.id]

    async with lock:
        # Double-check: another caller may have refreshed while we waited
        if not is_oauth2_token_expired(server):
            tokens = _decrypt_oauth2_tokens(server)
            if tokens:
                return tokens.get("access_token")

        # Actually needs refresh
        new_tokens = await refresh_oauth2_token(server)
        if new_tokens is None:
            # Refresh failed — tokens may have been cleared in refresh_oauth2_token
            return None

        # Persist updated tokens if db is available
        server.oauth2_tokens = _encrypt_oauth2_tokens(new_tokens)
        if db is not None:
            await db.commit()

        return new_tokens.get("access_token")

