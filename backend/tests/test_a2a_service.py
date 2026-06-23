# =============================================================================
# PH Agent Hub — A2A Server Service Tests
# =============================================================================

import uuid
import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError, ValidationError
from src.core.encryption import encrypt
from src.db.orm.a2a_servers import A2aServer
from src.db.orm.tools import Tool
from src.services.a2a_service import (
    list_a2a_servers,
    get_a2a_server,
    create_a2a_server,
    update_a2a_server,
    delete_a2a_server,
    decrypt_auth_token,
    decrypt_headers as svc_decrypt_headers,
    mask_secret_value,
    mask_dict,
    test_a2a_connection as svc_test_a2a_connection,
    sync_a2a_tools,
)

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server_kwargs(tenant_id: str, **overrides) -> dict:
    """Build standard kwargs for create_a2a_server, with overrides."""
    kwargs = dict(
        tenant_id=tenant_id,
        name="Test A2A Server",
        protocol_binding="rest",
        url="https://agent.example.com",
    )
    kwargs.update(overrides)
    return kwargs


def _create_mock_agent_card(agent_name: str = "Test Agent", skill_list: list[dict] | None = None):
    """Create a mock AgentCard that mimics the protobuf AgentCard API."""
    if skill_list is None:
        skill_list = [{"id": "skill-1", "name": "Skill A", "description": "First skill"}]

    class MockAgentCapabilities:
        streaming = True
        push_notifications = False
        extended_agent_card = False

    class MockSkill:
        def __init__(self, data):
            self.id = data["id"]
            self.name = data.get("name", "")
            self.description = data.get("description", "")
            self.input_modes = data.get("inputModes") or data.get("input_modes", [])
            self.output_modes = data.get("outputModes") or data.get("output_modes", [])
            self.examples = data.get("examples", [])
            self.tags = data.get("tags", [])

    class MockAgentCard:
        def __init__(self):
            self.name = agent_name
            self.description = "A test A2A agent"
            self.capabilities = MockAgentCapabilities()
            self.skills = [MockSkill(s) for s in skill_list]

    return MockAgentCard()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListA2aServers:
    """Tests for list_a2a_servers."""

    async def test_empty_db(self, db_session: AsyncSession, test_tenant):
        """Should return empty list when no servers exist."""
        items, total = await list_a2a_servers(db_session, tenant_id=test_tenant.id)
        assert items == []
        assert total == 0

    async def test_multiple_servers(self, db_session: AsyncSession, test_tenant):
        """Should return all servers for the tenant."""
        s1 = A2aServer(
            tenant_id=test_tenant.id, name="Server A",
            protocol_binding="rest", url="https://a.example.com",
        )
        s2 = A2aServer(
            tenant_id=test_tenant.id, name="Server B",
            protocol_binding="jsonrpc", url="https://b.example.com",
        )
        db_session.add_all([s1, s2])
        await db_session.flush()

        items, total = await list_a2a_servers(db_session, tenant_id=test_tenant.id)
        assert total == 2
        assert {s.id for s in items} == {s1.id, s2.id}

    async def test_filter_by_binding(self, db_session: AsyncSession, test_tenant):
        """Should filter by protocol_binding."""
        db_session.add_all([
            A2aServer(
                tenant_id=test_tenant.id, name="REST",
                protocol_binding="rest", url="https://rest.example.com",
            ),
            A2aServer(
                tenant_id=test_tenant.id, name="JSON-RPC",
                protocol_binding="jsonrpc", url="https://jsonrpc.example.com",
            ),
        ])
        await db_session.flush()

        items, total = await list_a2a_servers(
            db_session, tenant_id=test_tenant.id, protocol_binding="rest",
        )
        assert total == 1
        assert items[0].name == "REST"

    async def test_search_by_name(self, db_session: AsyncSession, test_tenant):
        """Should search by name (case-insensitive)."""
        db_session.add_all([
            A2aServer(
                tenant_id=test_tenant.id, name="Production Agent",
                protocol_binding="rest", url="https://prod.example.com",
            ),
            A2aServer(
                tenant_id=test_tenant.id, name="Staging Agent",
                protocol_binding="rest", url="https://staging.example.com",
            ),
        ])
        await db_session.flush()

        items, total = await list_a2a_servers(
            db_session, tenant_id=test_tenant.id, search="production",
        )
        assert total == 1
        assert items[0].name == "Production Agent"

    async def test_search_by_url(self, db_session: AsyncSession, test_tenant):
        """Should search by URL (case-insensitive)."""
        db_session.add_all([
            A2aServer(
                tenant_id=test_tenant.id, name="Agent A",
                protocol_binding="rest", url="https://unique.example.com",
            ),
        ])
        await db_session.flush()

        items, total = await list_a2a_servers(
            db_session, tenant_id=test_tenant.id, search="unique",
        )
        assert total == 1

    async def test_pagination(self, db_session: AsyncSession, test_tenant):
        """Should paginate results."""
        for i in range(5):
            db_session.add(A2aServer(
                tenant_id=test_tenant.id, name=f"S{i}",
                protocol_binding="rest", url=f"https://s{i}.example.com",
            ))
        await db_session.flush()

        items, total = await list_a2a_servers(
            db_session, tenant_id=test_tenant.id, page=1, page_size=2,
        )
        assert total == 5
        assert len(items) == 2

    async def test_filter_by_enabled(self, db_session: AsyncSession, test_tenant):
        """Should filter by enabled status."""
        db_session.add_all([
            A2aServer(
                tenant_id=test_tenant.id, name="Enabled",
                protocol_binding="rest", url="https://enabled.example.com",
                enabled=True,
            ),
            A2aServer(
                tenant_id=test_tenant.id, name="Disabled",
                protocol_binding="rest", url="https://disabled.example.com",
                enabled=False,
            ),
        ])
        await db_session.flush()

        items, total = await list_a2a_servers(
            db_session, tenant_id=test_tenant.id, enabled=True,
        )
        assert total == 1
        assert items[0].name == "Enabled"


class TestGetA2aServer:
    """Tests for get_a2a_server."""

    async def test_existing(self, db_session: AsyncSession, test_tenant):
        """Should return the server when it exists."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Found",
            protocol_binding="rest", url="https://found.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        result = await get_a2a_server(db_session, server.id)
        assert result is not None
        assert result.name == "Found"

    async def test_nonexistent_returns_none(self, db_session: AsyncSession):
        """Should return None when server does not exist."""
        result = await get_a2a_server(db_session, str(uuid.uuid4()))
        assert result is None


class TestCreateA2aServer:
    """Tests for create_a2a_server."""

    async def test_rest_success(self, db_session: AsyncSession, test_tenant):
        """Should create a REST A2A server."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(test_tenant.id),
        )
        assert server.name == "Test A2A Server"
        assert server.protocol_binding == "rest"
        assert server.url == "https://agent.example.com"
        assert server.agent_card_path == "/.well-known/agent-card.json"
        assert server.auth_scheme == "none"
        assert server.enabled is True

    async def test_jsonrpc_success(self, db_session: AsyncSession, test_tenant):
        """Should create a JSON-RPC A2A server."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(
                test_tenant.id, protocol_binding="jsonrpc",
            ),
        )
        assert server.protocol_binding == "jsonrpc"

    async def test_grpc_success(self, db_session: AsyncSession, test_tenant):
        """Should create a gRPC A2A server."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(
                test_tenant.id, protocol_binding="grpc",
            ),
        )
        assert server.protocol_binding == "grpc"

    async def test_with_auth(self, db_session: AsyncSession, test_tenant):
        """Should encrypt auth_token on creation."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(
                test_tenant.id,
                auth_scheme="bearer",
                auth_token="sk-secret-token-12345",
            ),
        )
        assert server.auth_scheme == "bearer"
        assert server.auth_token is not None
        # Verify it's encrypted (not plaintext)
        assert "sk-secret-token" not in server.auth_token

    async def test_with_headers(self, db_session: AsyncSession, test_tenant):
        """Should encrypt headers on creation."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(
                test_tenant.id,
                headers={"X-API-Key": "abc123", "X-Custom": "value"},
            ),
        )
        assert server.headers is not None
        assert "X-API-Key" not in server.headers
        assert "abc123" not in server.headers

    async def test_with_allowed_skills(self, db_session: AsyncSession, test_tenant):
        """Should store allowed_skills list."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(
                test_tenant.id,
                allowed_skills=["skill-a", "skill-b"],
            ),
        )
        assert server.allowed_skills == ["skill-a", "skill-b"]

    async def test_custom_agent_card_path(self, db_session: AsyncSession, test_tenant):
        """Should use custom agent_card_path when provided."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(
                test_tenant.id,
                agent_card_path="/custom/agent-card.json",
            ),
        )
        assert server.agent_card_path == "/custom/agent-card.json"

    async def test_missing_url_raises(self, db_session: AsyncSession, test_tenant):
        """Should raise ValidationError when url is missing."""
        with pytest.raises(ValidationError, match="requires a 'url' parameter"):
            await create_a2a_server(
                db_session,
                **_make_server_kwargs(test_tenant.id, url=None),
            )

    async def test_disabled(self, db_session: AsyncSession, test_tenant):
        """Should create a disabled server."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(test_tenant.id, enabled=False),
        )
        assert server.enabled is False


class TestUpdateA2aServer:
    """Tests for update_a2a_server."""

    async def test_update_name(self, db_session: AsyncSession, test_tenant):
        """Should update the server name."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Original",
            protocol_binding="rest", url="https://original.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        updated = await update_a2a_server(db_session, server.id, name="Renamed")
        assert updated.name == "Renamed"

    async def test_update_auth_token(self, db_session: AsyncSession, test_tenant):
        """Should re-encrypt auth_token on update."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Secure",
            protocol_binding="rest", url="https://secure.example.com",
            auth_scheme="bearer",
            auth_token=encrypt("old-token"),
        )
        db_session.add(server)
        await db_session.flush()

        updated = await update_a2a_server(
            db_session, server.id, auth_token="new-token",
        )
        assert updated.auth_token is not None
        assert "new-token" not in updated.auth_token
        assert decrypt_auth_token(updated) == "new-token"

    async def test_update_missing_server_raises(self, db_session: AsyncSession):
        """Should raise NotFoundError for nonexistent server."""
        with pytest.raises(NotFoundError, match="A2A server not found"):
            await update_a2a_server(db_session, str(uuid.uuid4()), name="Nope")

    async def test_update_enabled(self, db_session: AsyncSession, test_tenant):
        """Should update enabled status."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Toggle",
            protocol_binding="rest", url="https://toggle.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        updated = await update_a2a_server(db_session, server.id, enabled=False)
        assert updated.enabled is False


class TestDeleteA2aServer:
    """Tests for delete_a2a_server."""

    async def test_delete_existing(self, db_session: AsyncSession, test_tenant):
        """Should delete an existing server."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Delete Me",
            protocol_binding="rest", url="https://delete.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        await delete_a2a_server(db_session, server.id)
        result = await get_a2a_server(db_session, server.id)
        assert result is None

    async def test_delete_missing_raises(self, db_session: AsyncSession):
        """Should raise NotFoundError for nonexistent server."""
        with pytest.raises(NotFoundError, match="A2A server not found"):
            await delete_a2a_server(db_session, str(uuid.uuid4()))

    async def test_delete_cascades_to_tools(self, db_session: AsyncSession, test_tenant):
        """Should delete associated Tool records with type='a2a'."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="With Tools",
            protocol_binding="rest", url="https://tools.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        tool = Tool(
            tenant_id=test_tenant.id,
            name="test: skill_a",
            type="a2a",
            config={"a2a_server_id": server.id, "skill_id": "skill-a"},
            category="communication",
        )
        db_session.add(tool)
        await db_session.flush()

        await delete_a2a_server(db_session, server.id)

        # Verify tool was also deleted
        result = await db_session.execute(
            select(Tool).where(Tool.id == tool.id)
        )
        assert result.scalar_one_or_none() is None


class TestDecryptHelpers:
    """Tests for decryption and masking helpers."""

    async def test_decrypt_auth_token(self, db_session: AsyncSession, test_tenant):
        """Should decrypt an encrypted auth_token."""
        encrypted = encrypt("my-secret-token")
        server = A2aServer(
            tenant_id=test_tenant.id, name="Secure",
            protocol_binding="rest", url="https://secure.example.com",
            auth_scheme="bearer", auth_token=encrypted,
        )
        db_session.add(server)
        await db_session.flush()

        result = decrypt_auth_token(server)
        assert result == "my-secret-token"

    async def test_decrypt_auth_token_none(self):
        """Should return None when auth_token is not set."""
        server = A2aServer(
            tenant_id="test", name="No Token",
            protocol_binding="rest", url="https://notoken.example.com",
        )
        assert decrypt_auth_token(server) is None

    async def test_mask_secret_value(self):
        """Should mask secrets showing first 4 chars."""
        assert mask_secret_value("abcdefghij") == "abcd****"
        assert mask_secret_value("short!") == "****"

    async def test_mask_dict(self):
        """Should mask all values in a dict."""
        # 'secret1' is 7 chars => "****", 'secret2' is 7 chars => "****"
        result = mask_dict({"key1": "secret1", "key2": "secret2"})
        assert result["key1"] == "****"
        assert result["key2"] == "****"


class TestTestA2aConnection:
    """Tests for test_a2a_connection."""

    async def test_successful_connection(self, db_session: AsyncSession, test_tenant):
        """Should return agent info on successful connection."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Test Agent",
            protocol_binding="rest", url="https://test-agent.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        mock_card = _create_mock_agent_card(
            agent_name="Remote Agent",
            skill_list=[
                {"id": "s1", "name": "Skill One", "description": "First skill",
                 "inputModes": ["text/plain"], "outputModes": ["text/plain"],
                 "examples": ["ex1"], "tags": ["tag1"]},
                {"id": "s2", "name": "Skill Two", "description": "Second skill",
                 "inputModes": ["application/json"], "outputModes": ["application/json"],
                 "examples": [], "tags": []},
            ],
        )

        with patch("src.services.a2a_service._resolve_agent_card", new=AsyncMock(return_value=mock_card)):
            result = await svc_test_a2a_connection(db_session, server.id)

        assert result["connected"] is True
        assert result["agent_name"] == "Remote Agent"
        assert result["agent_description"] == "A test A2A agent"
        assert len(result["skills"]) == 2
        assert result["skills"][0]["id"] == "s1"
        assert result["skills"][0]["inputModes"] == ["text/plain"]
        assert result["skills"][0]["outputModes"] == ["text/plain"]
        assert result["skills"][0]["examples"] == ["ex1"]
        assert result["skills"][0]["tags"] == ["tag1"]
        assert result["skills"][1]["inputModes"] == ["application/json"]

    async def test_failed_connection(self, db_session: AsyncSession, test_tenant):
        """Should return error info on failed connection."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Offline Agent",
            protocol_binding="rest", url="https://offline.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        with patch(
            "src.services.a2a_service._resolve_agent_card",
            new=AsyncMock(side_effect=ConnectionError("Connection refused")),
        ):
            result = await svc_test_a2a_connection(db_session, server.id)

        assert result["connected"] is False
        assert result["error"] is not None
        assert result["skills"] == []

    async def test_missing_server_raises(self, db_session: AsyncSession):
        """Should raise NotFoundError for nonexistent server."""
        with pytest.raises(NotFoundError, match="A2A server not found"):
            await svc_test_a2a_connection(db_session, str(uuid.uuid4()))


class TestSyncA2aTools:
    """Tests for sync_a2a_tools."""

    async def test_creates_new_tools(self, db_session: AsyncSession, test_tenant):
        """Should create Tool records for discovered skills."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Skillful Agent",
            protocol_binding="rest", url="https://skillful.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        mock_card = _create_mock_agent_card(
            agent_name="Skillful",
            skill_list=[
                {"id": "s1", "name": "Alpha", "description": "Alpha skill",
                 "inputModes": ["text/plain"], "outputModes": ["text/plain"],
                 "examples": ["try this"], "tags": ["alpha"]},
                {"id": "s2", "name": "Beta", "description": "Beta skill",
                 "inputModes": ["application/json"], "outputModes": ["application/json"],
                 "examples": [], "tags": ["beta", "json"]},
            ],
        )

        with patch("src.services.a2a_service._resolve_agent_card", new=AsyncMock(return_value=mock_card)):
            result = await sync_a2a_tools(db_session, server.id)

        assert result["created"] == 2
        assert result["updated"] == 0
        assert result["deprecated"] == 0

        # Verify Tool records exist for this server
        db_result = await db_session.execute(
            select(Tool).where(
                Tool.type == "a2a",
                Tool.config["a2a_server_id"].as_string() == server.id,
            )
        )
        tools = db_result.scalars().all()
        assert len(tools) == 2
        assert all(t.config["a2a_server_id"] == server.id for t in tools)

        # Verify enriched config fields (Issue #408)
        s1_tool = next(t for t in tools if t.config["skill_id"] == "s1")
        assert s1_tool.config["input_modes"] == ["text/plain"]
        assert s1_tool.config["output_modes"] == ["text/plain"]
        assert s1_tool.config["examples"] == ["try this"]
        assert s1_tool.config["tags"] == ["alpha"]

        s2_tool = next(t for t in tools if t.config["skill_id"] == "s2")
        assert s2_tool.config["input_modes"] == ["application/json"]
        assert s2_tool.config["output_modes"] == ["application/json"]
        assert s2_tool.config["examples"] == []
        assert s2_tool.config["tags"] == ["beta", "json"]

        # Verify Agent Card cache was populated (Issue #408)
        await db_session.refresh(server)
        assert server.agent_card_cache is not None
        assert server.agent_card_cache["name"] == "Skillful"
        assert server.agent_card_cached_at is not None

    async def test_updates_existing_tools(self, db_session: AsyncSession, test_tenant):
        """Should update existing Tool records for known skills."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Update Agent",
            protocol_binding="rest", url="https://update.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        existing_tool = Tool(
            tenant_id=test_tenant.id,
            name="Update Agent: Alpha",
            type="a2a",
            config={"a2a_server_id": server.id, "skill_id": "s1"},
            category="communication",
            enabled=True,
        )
        db_session.add(existing_tool)
        await db_session.flush()

        mock_card = _create_mock_agent_card(
            agent_name="Update Agent",
            skill_list=[
                {"id": "s1", "name": "Alpha Updated", "description": "Updated description"},
            ],
        )

        with patch("src.services.a2a_service._resolve_agent_card", new=AsyncMock(return_value=mock_card)):
            result = await sync_a2a_tools(db_session, server.id)

        assert result["created"] == 0
        assert result["updated"] == 1
        assert result["deprecated"] == 0

    async def test_deprecates_removed_skills(self, db_session: AsyncSession, test_tenant):
        """Should disable tools for skills no longer on the server."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="Dep Agent",
            protocol_binding="rest", url="https://dep.example.com",
        )
        db_session.add(server)
        await db_session.flush()

        old_tool = Tool(
            tenant_id=test_tenant.id,
            name="Dep Agent: Removed",
            type="a2a",
            config={"a2a_server_id": server.id, "skill_id": "removed-skill"},
            category="communication",
            enabled=True,
        )
        db_session.add(old_tool)
        await db_session.flush()

        mock_card = _create_mock_agent_card(
            agent_name="Dep Agent",
            skill_list=[],  # No skills returned
        )

        with patch("src.services.a2a_service._resolve_agent_card", new=AsyncMock(return_value=mock_card)):
            result = await sync_a2a_tools(db_session, server.id)

        assert result["created"] == 0
        assert result["updated"] == 0
        assert result["deprecated"] == 1

        # Verify the old tool was disabled
        await db_session.refresh(old_tool)
        assert old_tool.enabled is False


# =============================================================================
# OAuth2 Tests (Issue #418)
# =============================================================================


class TestCreateA2aServerWithOAuth2:
    """Tests for creating A2A servers with OAuth2 config."""

    async def test_create_with_oauth2_config(self, db_session: AsyncSession, test_tenant):
        """Should store OAuth2 config with client_secret encrypted."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(
                test_tenant.id,
                auth_scheme="oauth2",
                oauth2_client_id="my-client-id",
                oauth2_client_secret="my-client-secret",
                oauth2_authorize_url="https://provider.example.com/oauth2/authorize",
                oauth2_token_url="https://provider.example.com/oauth2/token",
                oauth2_scopes="openid profile email",
            ),
        )
        assert server.auth_scheme == "oauth2"
        assert server.oauth2_client_id == "my-client-id"
        assert server.oauth2_authorize_url == "https://provider.example.com/oauth2/authorize"
        assert server.oauth2_token_url == "https://provider.example.com/oauth2/token"
        assert server.oauth2_scopes == "openid profile email"
        # Client secret should be encrypted
        assert server.oauth2_client_secret is not None
        assert "my-client-secret" not in server.oauth2_client_secret

    async def test_create_without_oauth2(self, db_session: AsyncSession, test_tenant):
        """Should create server with no OAuth2 fields set."""
        server = await create_a2a_server(
            db_session, **_make_server_kwargs(test_tenant.id),
        )
        assert server.oauth2_client_id is None
        assert server.oauth2_client_secret is None


class TestDecryptOAuth2Helpers:
    """Tests for OAuth2 decryption and status helpers."""

    async def test_decrypt_oauth2_client_secret(self, db_session: AsyncSession, test_tenant):
        """Should decrypt an encrypted client_secret."""
        from src.services.a2a_service import decrypt_oauth2_client_secret
        encrypted = encrypt("my-secret-value")
        server = A2aServer(
            tenant_id=test_tenant.id, name="OAuth2 Test",
            protocol_binding="rest", url="https://oauth2.example.com",
            auth_scheme="oauth2", oauth2_client_secret=encrypted,
        )
        db_session.add(server)
        await db_session.flush()

        result = decrypt_oauth2_client_secret(server)
        assert result == "my-secret-value"

    async def test_decrypt_oauth2_client_secret_none(self):
        """Should return None when no client_secret is set."""
        from src.services.a2a_service import decrypt_oauth2_client_secret
        server = A2aServer(
            tenant_id="test", name="No Secret",
            protocol_binding="rest", url="https://nosecret.example.com",
        )
        assert decrypt_oauth2_client_secret(server) is None


class TestOAuth2TokenStatus:
    """Tests for get_oauth2_tokens_status."""

    async def test_not_oauth2_returns_none(self, db_session: AsyncSession, test_tenant):
        """Should return None when auth_scheme is not oauth2."""
        from src.services.a2a_service import get_oauth2_tokens_status
        server = A2aServer(
            tenant_id=test_tenant.id, name="Bearer",
            protocol_binding="rest", url="https://bearer.example.com",
            auth_scheme="bearer",
        )
        assert get_oauth2_tokens_status(server) is None

    async def test_no_tokens_returns_none(self, db_session: AsyncSession, test_tenant):
        """Should return 'none' when no oauth2_tokens stored."""
        from src.services.a2a_service import get_oauth2_tokens_status
        server = A2aServer(
            tenant_id=test_tenant.id, name="OAuth2 No Tokens",
            protocol_binding="rest", url="https://oauth2.example.com",
            auth_scheme="oauth2",
            oauth2_tokens=None,
        )
        assert get_oauth2_tokens_status(server) == "none"

    async def test_with_valid_tokens_returns_authorized(self, db_session: AsyncSession, test_tenant):
        """Should return 'authorized' when valid tokens exist."""
        from src.services.a2a_service import get_oauth2_tokens_status
        import time
        tokens = json.dumps({
            "access_token": "valid-access-token",
            "refresh_token": "valid-refresh-token",
            "expires_at": int(time.time()) + 3600,
        })
        server = A2aServer(
            tenant_id=test_tenant.id, name="OAuth2 Tokens",
            protocol_binding="rest", url="https://oauth2.example.com",
            auth_scheme="oauth2",
            oauth2_tokens=encrypt(tokens),
        )
        db_session.add(server)
        await db_session.flush()

        assert get_oauth2_tokens_status(server) == "authorized"

    async def test_empty_access_token_returns_none(self, db_session: AsyncSession, test_tenant):
        """Should return 'none' when access_token is empty."""
        from src.services.a2a_service import get_oauth2_tokens_status
        tokens = json.dumps({"access_token": "", "refresh_token": "", "expires_at": 0})
        server = A2aServer(
            tenant_id=test_tenant.id, name="OAuth2 Empty",
            protocol_binding="rest", url="https://oauth2.example.com",
            auth_scheme="oauth2",
            oauth2_tokens=encrypt(tokens),
        )
        db_session.add(server)
        await db_session.flush()

        assert get_oauth2_tokens_status(server) == "none"

    async def test_corrupted_tokens_returns_expired(self, db_session: AsyncSession, test_tenant):
        """Should return 'expired' when tokens cannot be decrypted."""
        from src.services.a2a_service import get_oauth2_tokens_status
        server = A2aServer(
            tenant_id=test_tenant.id, name="Corrupted",
            protocol_binding="rest", url="https://corrupted.example.com",
            auth_scheme="oauth2",
            oauth2_tokens="not-valid-fernet-data",
        )
        db_session.add(server)
        await db_session.flush()

        assert get_oauth2_tokens_status(server) == "expired"


class TestOAuth2TokenExchange:
    """Tests for exchange_oauth2_code and refresh_oauth2_token."""

    async def test_exchange_oauth2_code_success(self, db_session: AsyncSession, test_tenant):
        """Should exchange code for tokens via POST to token_url."""
        from src.services.a2a_service import exchange_oauth2_code
        import time

        server = A2aServer(
            tenant_id=test_tenant.id, name="Exchange Test",
            protocol_binding="rest", url="https://exchange.example.com",
            auth_scheme="oauth2",
            oauth2_client_id="test-client-id",
            oauth2_client_secret=encrypt("test-client-secret"),
            oauth2_token_url="https://provider.example.com/oauth2/token",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await exchange_oauth2_code(
                code="auth-code-123",
                redirect_uri="http://localhost:8000/api/a2a/oauth2/callback",
                server=server,
            )

        assert result is not None
        assert result["access_token"] == "new-access-token"
        assert result["refresh_token"] == "new-refresh-token"
        assert result["expires_at"] > int(time.time())

    async def test_exchange_oauth2_code_failure(self, db_session: AsyncSession, test_tenant):
        """Should return None when token exchange fails."""
        from src.services.a2a_service import exchange_oauth2_code

        server = A2aServer(
            tenant_id=test_tenant.id, name="Exchange Fail",
            protocol_binding="rest", url="https://fail.example.com",
            auth_scheme="oauth2",
            oauth2_client_id="test-client-id",
            oauth2_client_secret=encrypt("test-client-secret"),
            oauth2_token_url="https://provider.example.com/oauth2/token",
        )

        mock_response = MagicMock()
        mock_response.status_code = 400

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await exchange_oauth2_code(
                code="bad-code",
                redirect_uri="http://localhost:8000/api/a2a/oauth2/callback",
                server=server,
            )

        assert result is None

    async def test_refresh_oauth2_token_success(self, db_session: AsyncSession, test_tenant):
        """Should refresh token via POST to token_url."""
        from src.services.a2a_service import refresh_oauth2_token
        import time

        old_tokens = json.dumps({
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
            "expires_at": int(time.time()) - 100,  # expired
        })

        server = A2aServer(
            tenant_id=test_tenant.id, name="Refresh Test",
            protocol_binding="rest", url="https://refresh.example.com",
            auth_scheme="oauth2",
            oauth2_client_id="test-client-id",
            oauth2_client_secret=encrypt("test-client-secret"),
            oauth2_token_url="https://provider.example.com/oauth2/token",
            oauth2_tokens=encrypt(old_tokens),
        )
        db_session.add(server)
        await db_session.flush()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "access_token": "refreshed-access-token",
            "expires_in": 3600,
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await refresh_oauth2_token(server)

        assert result is not None
        assert result["access_token"] == "refreshed-access-token"
        # Should preserve existing refresh_token when provider doesn't rotate
        assert result["refresh_token"] == "old-refresh-token"
        assert result["expires_at"] > int(time.time())

    async def test_refresh_oauth2_token_invalid_grant(self, db_session: AsyncSession, test_tenant):
        """Should clear tokens when provider returns invalid_grant."""
        from src.services.a2a_service import refresh_oauth2_token
        import time

        old_tokens = json.dumps({
            "access_token": "dead-token",
            "refresh_token": "dead-refresh-token",
            "expires_at": int(time.time()) - 100,
        })

        server = A2aServer(
            tenant_id=test_tenant.id, name="Invalid Grant",
            protocol_binding="rest", url="https://invalid.example.com",
            auth_scheme="oauth2",
            oauth2_client_id="test-client-id",
            oauth2_client_secret=encrypt("test-client-secret"),
            oauth2_token_url="https://provider.example.com/oauth2/token",
            oauth2_tokens=encrypt(old_tokens),
        )
        db_session.add(server)
        await db_session.flush()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json = MagicMock(return_value={"error": "invalid_grant"})

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await refresh_oauth2_token(server)

        assert result is None
        # Tokens should be cleared
        assert server.oauth2_tokens is None

    async def test_refresh_oauth2_token_no_refresh_token(self, db_session: AsyncSession, test_tenant):
        """Should return None when no refresh_token available."""
        from src.services.a2a_service import refresh_oauth2_token
        import time

        tokens_no_rt = json.dumps({
            "access_token": "access-only",
            "refresh_token": "",
            "expires_at": int(time.time()) - 100,
        })

        server = A2aServer(
            tenant_id=test_tenant.id, name="No Refresh",
            protocol_binding="rest", url="https://norefresh.example.com",
            auth_scheme="oauth2",
            oauth2_client_id="test-client-id",
            oauth2_client_secret=encrypt("test-client-secret"),
            oauth2_token_url="https://provider.example.com/oauth2/token",
            oauth2_tokens=encrypt(tokens_no_rt),
        )

        result = await refresh_oauth2_token(server)
        assert result is None


class TestOAuth2IsTokenExpired:
    """Tests for is_oauth2_token_expired."""

    def test_fresh_token_not_expired(self):
        """Should return False for a fresh token."""
        from src.services.a2a_service import is_oauth2_token_expired
        import time
        tokens = json.dumps({
            "access_token": "fresh",
            "refresh_token": "fresh-rt",
            "expires_at": int(time.time()) + 3600,
        })
        server = A2aServer(
            tenant_id="test", name="Fresh",
            protocol_binding="rest", url="https://fresh.example.com",
            auth_scheme="oauth2",
            oauth2_tokens=encrypt(tokens),
        )
        assert is_oauth2_token_expired(server) is False

    def test_expired_token(self):
        """Should return True for an expired token."""
        from src.services.a2a_service import is_oauth2_token_expired
        import time
        tokens = json.dumps({
            "access_token": "stale",
            "refresh_token": "stale-rt",
            "expires_at": int(time.time()) - 100,
        })
        server = A2aServer(
            tenant_id="test", name="Stale",
            protocol_binding="rest", url="https://stale.example.com",
            auth_scheme="oauth2",
            oauth2_tokens=encrypt(tokens),
        )
        assert is_oauth2_token_expired(server) is True

    def test_no_tokens_returns_expired(self):
        """Should return True when no tokens stored."""
        from src.services.a2a_service import is_oauth2_token_expired
        server = A2aServer(
            tenant_id="test", name="No Tokens",
            protocol_binding="rest", url="https://notokens.example.com",
            auth_scheme="oauth2",
        )
        assert is_oauth2_token_expired(server) is True


class TestOAuth2Update:
    """Tests for updating OAuth2 config on existing servers."""

    async def test_update_oauth2_config(self, db_session: AsyncSession, test_tenant):
        """Should update OAuth2 fields with re-encryption."""
        server = A2aServer(
            tenant_id=test_tenant.id, name="OAuth2 Update",
            protocol_binding="rest", url="https://update.example.com",
            auth_scheme="oauth2",
            oauth2_client_id="old-client-id",
            oauth2_client_secret=encrypt("old-secret"),
        )
        db_session.add(server)
        await db_session.flush()

        updated = await update_a2a_server(
            db_session, server.id,
            oauth2_client_id="new-client-id",
            oauth2_client_secret="new-client-secret",
            oauth2_scopes="new-scope",
        )

        assert updated.oauth2_client_id == "new-client-id"
        assert updated.oauth2_scopes == "new-scope"
        # Secret should be re-encrypted
        assert "new-client-secret" not in updated.oauth2_client_secret
        from src.services.a2a_service import decrypt_oauth2_client_secret
        assert decrypt_oauth2_client_secret(updated) == "new-client-secret"

    async def test_revoke_tokens_by_setting_null(self, db_session: AsyncSession, test_tenant):
        """Should clear oauth2_tokens when set to None."""
        from src.core.encryption import encrypt
        import time

        tokens = json.dumps({
            "access_token": "to-revoke",
            "refresh_token": "to-revoke-rt",
            "expires_at": int(time.time()) + 3600,
        })
        server = A2aServer(
            tenant_id=test_tenant.id, name="OAuth2 Revoke",
            protocol_binding="rest", url="https://revoke.example.com",
            auth_scheme="oauth2",
            oauth2_tokens=encrypt(tokens),
        )
        db_session.add(server)
        await db_session.flush()

        # Simulate what the update endpoint does with oauth2_tokens=None
        from src.services.a2a_service import get_oauth2_tokens_status
        assert get_oauth2_tokens_status(server) == "authorized"

        updated = await update_a2a_server(
            db_session, server.id, oauth2_tokens=None,
        )
        assert updated.oauth2_tokens is None
        assert get_oauth2_tokens_status(updated) == "none"


class TestOAuth2GetAccessToken:
    """Tests for get_oauth2_access_token."""

    async def test_fresh_token_no_refresh(self, db_session: AsyncSession, test_tenant):
        """Should return fresh access token without calling refresh."""
        from src.services.a2a_service import get_oauth2_access_token
        import time

        tokens = json.dumps({
            "access_token": "still-fresh",
            "refresh_token": "still-fresh-rt",
            "expires_at": int(time.time()) + 3600,
        })
        server = A2aServer(
            tenant_id=test_tenant.id, name="Still Fresh",
            protocol_binding="rest", url="https://fresh.example.com",
            auth_scheme="oauth2",
            oauth2_client_id="test-client-id",
            oauth2_client_secret=encrypt("test-secret"),
            oauth2_token_url="https://provider.example.com/oauth2/token",
            oauth2_tokens=encrypt(tokens),
        )
        db_session.add(server)
        await db_session.flush()

        access_token = await get_oauth2_access_token(server, db_session)
        assert access_token == "still-fresh"

    async def test_expired_token_triggers_refresh(self, db_session: AsyncSession, test_tenant):
        """Should refresh an expired token and return the new one."""
        from src.services.a2a_service import get_oauth2_access_token
        import time

        old_tokens = json.dumps({
            "access_token": "expired-access",
            "refresh_token": "still-valid-rt",
            "expires_at": int(time.time()) - 100,
        })
        server = A2aServer(
            tenant_id=test_tenant.id, name="Expired",
            protocol_binding="rest", url="https://expired.example.com",
            auth_scheme="oauth2",
            oauth2_client_id="test-client-id",
            oauth2_client_secret=encrypt("test-secret"),
            oauth2_token_url="https://provider.example.com/oauth2/token",
            oauth2_tokens=encrypt(old_tokens),
        )
        db_session.add(server)
        await db_session.flush()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={
            "access_token": "refreshed-access",
            "expires_in": 3600,
        })

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            access_token = await get_oauth2_access_token(server, db_session)

        assert access_token == "refreshed-access"

        # Verify tokens were persisted
        await db_session.refresh(server)
        from src.services.a2a_service import _decrypt_oauth2_tokens
        persisted = _decrypt_oauth2_tokens(server)
        assert persisted["access_token"] == "refreshed-access"
