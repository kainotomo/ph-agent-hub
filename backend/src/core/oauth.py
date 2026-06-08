# =============================================================================
# PH Agent Hub — Shared OAuth Helper Module
# =============================================================================
# Token exchange and refresh for Google and Microsoft OAuth flows.
# Used by the credential API (code→token exchange) and tool factories
# (token refresh at runtime).
# =============================================================================

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
DEFAULT_TIMEOUT = 15.0


# =============================================================================
# Google OAuth
# =============================================================================


async def exchange_google_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Exchange an authorization code for Google OAuth tokens.

    Returns:
        Dict with access_token, refresh_token (if offline access was granted),
        expires_in, scope, token_type, and id_token.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        data = response.json()

    expires_at = int(time.time()) + data.get("expires_in", 3600)

    return {
        "access_token": data.get("access_token", ""),
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": expires_at,
        "expires_in": data.get("expires_in", 3600),
        "scope": data.get("scope", ""),
        "token_type": data.get("token_type", "Bearer"),
        "id_token": data.get("id_token", ""),
    }


async def refresh_google_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any] | None:
    """Refresh an expired Google access token.

    Returns:
        Dict with new access_token, expires_at, or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                },
            )
            if response.status_code != 200:
                logger.warning("Google token refresh failed: HTTP %d", response.status_code)
                return None

            data = response.json()

        return {
            "access_token": data.get("access_token", ""),
            "expires_at": int(time.time()) + data.get("expires_in", 3600),
        }
    except Exception as exc:
        logger.error("Google token refresh error: %s", exc)
        return None


# =============================================================================
# Microsoft OAuth
# =============================================================================


async def exchange_microsoft_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Exchange an authorization code for Microsoft OAuth tokens.

    Returns:
        Dict with access_token, refresh_token, expires_at, and email.
    """
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.post(
            MICROSOFT_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        response.raise_for_status()
        data = response.json()

    expires_at = int(time.time()) + data.get("expires_in", 3600)

    # Try to extract email from the ID token
    email = ""
    id_token = data.get("id_token", "")
    if id_token:
        try:
            import jwt as pyjwt

            decoded = pyjwt.decode(id_token, options={"verify_signature": False})
            email = decoded.get("email", decoded.get("preferred_username", ""))
        except Exception:
            pass

    return {
        "access_token": data.get("access_token", ""),
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": expires_at,
        "expires_in": data.get("expires_in", 3600),
        "scope": data.get("scope", ""),
        "token_type": data.get("token_type", "Bearer"),
        "id_token": id_token,
        "email": email,
    }


async def refresh_microsoft_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any] | None:
    """Refresh an expired Microsoft access token using msal.

    Returns:
        Dict with new access_token, expires_at, or None on failure.
    """
    try:
        from msal import ConfidentialClientApplication

        app = ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
        )

        result = app.acquire_token_by_refresh_token(refresh_token, scopes=[])

        if "access_token" not in result:
            logger.warning("Microsoft token refresh failed: %s", result.get("error_description", "unknown"))
            return None

        return {
            "access_token": result["access_token"],
            "expires_at": int(time.time()) + result.get("expires_in", 3600),
        }
    except Exception as exc:
        logger.error("Microsoft token refresh error: %s", exc)
        return None


# =============================================================================
# Generic token refresh — used by tool factories at runtime
# =============================================================================


async def refresh_oauth_token(
    oauth_tokens: dict,
    provider: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any] | None:
    """Refresh an OAuth token for any supported provider.

    Args:
        oauth_tokens: Dict with refresh_token, access_token, expires_at.
        provider: "gmail", "google", "outlook", or "microsoft".
        client_id: OAuth client ID from settings.
        client_secret: OAuth client secret from settings.

    Returns:
        Updated tokens dict with new access_token and expires_at,
        or the original tokens if no refresh is needed.
    """
    refresh_token = oauth_tokens.get("refresh_token", "")
    if not refresh_token:
        return None

    if provider in ("gmail", "google"):
        return await refresh_google_token(refresh_token, client_id, client_secret)
    elif provider in ("outlook", "microsoft"):
        return await refresh_microsoft_token(refresh_token, client_id, client_secret)

    return None


def is_token_expired(oauth_tokens: dict) -> bool:
    """Check if an OAuth token has expired (with a 5-minute buffer)."""
    expires_at = oauth_tokens.get("expires_at", 0)
    if not expires_at:
        return True
    return int(time.time()) >= (int(expires_at) - 300)
