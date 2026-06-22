# =============================================================================
# PH Agent Hub — A2A Server API Tests
# =============================================================================
# Tests the Agent Card handler function directly (not through HTTP routing
# since the A2A server is disabled by default and requires explicit enablement).
# =============================================================================

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.api.a2a_server import get_agent_card


def _make_mock_request(base_url: str = "https://api.example.com"):
    """Create a mock FastAPI Request with a base_url."""
    request = MagicMock()
    request.base_url = base_url.rstrip("/") + "/"
    # Mock request.state.db as None so the skills DB query is skipped gracefully
    request.state = MagicMock()
    request.state.db = None
    return request


def _mock_settings(monkeypatch):
    """Set A2A settings for testing."""
    monkeypatch.setattr("src.api.a2a_server.settings.A2A_PUBLIC_URL", "https://api.example.com")
    monkeypatch.setattr("src.api.a2a_server.settings.A2A_ORGANIZATION_NAME", "Test Hub")
    monkeypatch.setattr("src.api.a2a_server.settings.A2A_ORGANIZATION_URL", "https://example.com")


class TestA2aAgentCard:
    """Tests for the /.well-known/agent-card.json handler."""

    async def test_returns_agent_card(self, monkeypatch):
        """Should return a valid AgentCard with required fields."""
        _mock_settings(monkeypatch)
        data = await get_agent_card(_make_mock_request())

        # Required AgentCard fields per A2A spec
        assert "name" in data
        assert "description" in data
        assert "supportedInterfaces" in data
        assert "version" in data
        assert "capabilities" in data
        assert "defaultInputModes" in data
        assert "defaultOutputModes" in data
        assert "skills" in data
        assert "securitySchemes" in data

    async def test_capabilities_structure(self, monkeypatch):
        """Should have valid capabilities."""
        _mock_settings(monkeypatch)
        data = await get_agent_card(_make_mock_request())

        caps = data["capabilities"]
        assert "streaming" in caps
        assert "pushNotifications" in caps
        assert "extendedAgentCard" in caps

    async def test_supported_interfaces(self, monkeypatch):
        """Should have at least one supported interface."""
        _mock_settings(monkeypatch)
        data = await get_agent_card(_make_mock_request())

        assert len(data["supportedInterfaces"]) >= 1
        interface = data["supportedInterfaces"][0]
        assert "url" in interface
        assert "protocolBinding" in interface
        assert "protocolVersion" in interface

    async def test_security_schemes(self, monkeypatch):
        """Should declare security schemes."""
        _mock_settings(monkeypatch)
        data = await get_agent_card(_make_mock_request())

        assert "bearer" in data["securitySchemes"]

    async def test_skills_is_list(self, monkeypatch):
        """Should have skills as a list (may be empty)."""
        _mock_settings(monkeypatch)
        data = await get_agent_card(_make_mock_request())

        assert isinstance(data["skills"], list)
