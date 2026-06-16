# =============================================================================
# PH Agent Hub — OAuth Token Exchange & Refresh Tests
# =============================================================================
# Tests Google and Microsoft OAuth code exchange and token refresh flows
# using mocked HTTP responses.
# =============================================================================

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.oauth import (
    exchange_google_code,
    exchange_microsoft_code,
    refresh_google_token,
    refresh_microsoft_token,
)

pytestmark = [
    pytest.mark.security,
    pytest.mark.unit,
]


def _make_mock_response(status_code: int, json_data: dict):
    """Create a properly configured AsyncMock for httpx responses.

    httpx.AsyncClient.post() returns an httpx.Response whose methods
    raise_for_status() and json() are synchronous.
    """
    mock = AsyncMock()
    mock.status_code = status_code
    mock.raise_for_status = Mock()
    mock.json = Mock(return_value=json_data)
    return mock


def _make_mock_client():
    """Create a properly configured AsyncMock for httpx.AsyncClient.

    Ensures the mock works as an async context manager (``async with``).
    """
    client = AsyncMock()
    client.__aenter__.return_value = client
    return client


class TestGoogleOAuth:
    """Tests for Google OAuth token exchange and refresh."""

    @patch("src.core.oauth.httpx.AsyncClient")
    async def test_exchange_google_code_success(self, mock_httpx):
        """Verify successful Google code exchange returns correct token structure."""
        mock_response = _make_mock_response(200, {
            "access_token": "ya29.google-access-token",
            "refresh_token": "1//google-refresh-token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
            "id_token": "header.payload.sig",
        })
        mock_client = _make_mock_client()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_client

        result = await exchange_google_code(
            code="auth-code-123",
            redirect_uri="http://localhost/callback",
            client_id="google-client-id",
            client_secret="google-client-secret",
        )

        assert result["access_token"] == "ya29.google-access-token"
        assert result["refresh_token"] == "1//google-refresh-token"
        assert result["token_type"] == "Bearer"
        assert result["expires_in"] == 3600
        assert result["expires_at"] > int(time.time())

    @patch("src.core.oauth.httpx.AsyncClient")
    async def test_exchange_google_code_http_error(self, mock_httpx):
        """Verify Google code exchange raises on HTTP error."""
        mock_response = _make_mock_response(400, {})
        mock_response.raise_for_status.side_effect = Exception("HTTP 400")
        mock_client = _make_mock_client()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_client

        with pytest.raises(Exception):
            await exchange_google_code(
                code="bad-code",
                redirect_uri="http://localhost/callback",
                client_id="client-id",
                client_secret="client-secret",
            )

    @patch("src.core.oauth.httpx.AsyncClient")
    async def test_refresh_google_token_success(self, mock_httpx):
        """Verify successful Google token refresh."""
        mock_response = _make_mock_response(200, {
            "access_token": "ya29.new-access-token",
            "expires_in": 3600,
        })
        mock_client = _make_mock_client()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_client

        result = await refresh_google_token(
            refresh_token="1//old-refresh-token",
            client_id="google-client-id",
            client_secret="google-client-secret",
        )

        assert result is not None
        assert result["access_token"] == "ya29.new-access-token"
        assert result["expires_at"] > int(time.time())

    @patch("src.core.oauth.httpx.AsyncClient")
    async def test_refresh_google_token_failure_returns_none(self, mock_httpx):
        """Verify Google token refresh returns None on HTTP error."""
        mock_response = _make_mock_response(400, {})
        mock_client = _make_mock_client()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_client

        result = await refresh_google_token(
            refresh_token="invalid-token",
            client_id="client-id",
            client_secret="client-secret",
        )
        assert result is None


class TestMicrosoftOAuth:
    """Tests for Microsoft OAuth token exchange and refresh."""

    @patch("src.core.oauth.httpx.AsyncClient")
    async def test_exchange_microsoft_code_success(self, mock_httpx):
        """Verify successful Microsoft code exchange returns correct token structure."""
        import jwt as pyjwt
        # Generate a fake ID token dynamically to avoid hardcoded JWT secrets
        fake_id_token = pyjwt.encode(
            {"email": "user@example.com", "sub": "ms-user-id"},
            key="test-key-for-testing-only",
            algorithm="HS256",
        )

        mock_response = _make_mock_response(200, {
            "access_token": "ms-access-token",
            "refresh_token": "ms-refresh-token",
            "expires_in": 3600,
            "scope": "User.Read Mail.Read",
            "token_type": "Bearer",
            "id_token": fake_id_token,
        })
        mock_client = _make_mock_client()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_client

        result = await exchange_microsoft_code(
            code="auth-code-456",
            redirect_uri="http://localhost/callback",
            client_id="ms-client-id",
            client_secret="ms-client-secret",
        )

        assert result["access_token"] == "ms-access-token"
        assert result["refresh_token"] == "ms-refresh-token"
        assert result["email"] == "user@example.com"
        assert result["expires_at"] > int(time.time())

    @patch("src.core.oauth.httpx.AsyncClient")
    async def test_exchange_microsoft_code_http_error(self, mock_httpx):
        """Verify Microsoft code exchange raises on HTTP error."""
        mock_response = _make_mock_response(400, {})
        mock_response.raise_for_status.side_effect = Exception("HTTP 400")
        mock_client = _make_mock_client()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_httpx.return_value = mock_client

        with pytest.raises(Exception):
            await exchange_microsoft_code(
                code="bad-code",
                redirect_uri="http://localhost/callback",
                client_id="client-id",
                client_secret="client-secret",
            )

    @patch("src.core.oauth.refresh_microsoft_token")
    async def test_refresh_microsoft_token_failure_returns_none(
        self, mock_refresh
    ):
        """Verify Microsoft token refresh returns None on failure."""
        mock_refresh.return_value = None
        result = await refresh_microsoft_token(
            refresh_token="invalid-token",
            client_id="client-id",
            client_secret="client-secret",
        )
        assert result is None
