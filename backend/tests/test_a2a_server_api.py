# =============================================================================
# PH Agent Hub — A2A Server API Tests
# =============================================================================

import pytest
from httpx import AsyncClient, ASGITransport

from src.main import app
from src.core.config import settings

pytestmark = [pytest.mark.integration]


class TestA2aAgentCard:
    """Tests for the /.well-known/agent-card.json endpoint."""

    @pytest.fixture(autouse=True)
    def _enable_a2a_server(self, monkeypatch):
        """Enable the A2A server for these tests."""
        monkeypatch.setattr(settings, "A2A_SERVER_ENABLED", True)
        monkeypatch.setattr(settings, "A2A_PUBLIC_URL", "https://api.example.com")
        monkeypatch.setattr(settings, "A2A_ORGANIZATION_NAME", "Test Hub")
        monkeypatch.setattr(settings, "A2A_ORGANIZATION_URL", "https://example.com")
        # Re-import to re-evaluate the conditional import
        import importlib
        import src.main as main_module
        importlib.reload(main_module)

    async def test_returns_agent_card(self):
        """Should return a valid AgentCard with required fields."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://api.example.com") as client:
            response = await client.get("/.well-known/agent-card.json")

        assert response.status_code == 200
        data = response.json()

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

    async def test_capabilities_structure(self):
        """Should have valid capabilities."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://api.example.com") as client:
            response = await client.get("/.well-known/agent-card.json")

        data = response.json()
        caps = data["capabilities"]
        assert "streaming" in caps
        assert "pushNotifications" in caps
        assert "extendedAgentCard" in caps

    async def test_supported_interfaces(self):
        """Should have at least one supported interface."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://api.example.com") as client:
            response = await client.get("/.well-known/agent-card.json")

        data = response.json()
        assert len(data["supportedInterfaces"]) >= 1
        interface = data["supportedInterfaces"][0]
        assert "url" in interface
        assert "protocolBinding" in interface
        assert "protocolVersion" in interface

    async def test_security_schemes(self):
        """Should declare security schemes."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://api.example.com") as client:
            response = await client.get("/.well-known/agent-card.json")

        data = response.json()
        assert "bearer" in data["securitySchemes"]

    async def test_skills_is_list(self):
        """Should have skills as a list (may be empty)."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://api.example.com") as client:
            response = await client.get("/.well-known/agent-card.json")

        data = response.json()
        assert isinstance(data["skills"], list)
