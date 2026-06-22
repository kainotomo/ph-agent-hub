# =============================================================================
# PH Agent Hub — A2A OAuth2 Callback Router (Issue #418)
# =============================================================================
# Public endpoint (no admin auth) — receives the OAuth2 provider's redirect
# after the admin authorizes a remote A2A agent connection.
# =============================================================================

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.dependencies import get_db
from ..core.redis import get_a2a_oauth_state
from ..services.a2a_service import (
    get_a2a_server as _svc_get_a2a_server,
    exchange_oauth2_code as _svc_exchange_oauth2_code,
    _encrypt_oauth2_tokens,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["a2a-oauth"])

FRONTEND_SUCCESS_URL = f"{settings.FRONTEND_URL}/admin/a2a-servers"
FRONTEND_ERROR_URL = f"{settings.FRONTEND_URL}/admin/a2a-servers"


@router.get("/a2a/oauth2/callback")
async def a2a_oauth2_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle the OAuth2 provider's callback after admin authorization.

    This endpoint is public (no admin auth required) because the redirect
    comes from the OAuth2 provider, not from the admin UI.

    Flow:
        1. If ``error`` parameter is present → user denied access → redirect
           to frontend with error message.
        2. Validate ``state`` nonce via Redis atomic get+delete.
        3. Load the A2A server by server_id from state data.
        4. Exchange the authorization ``code`` for tokens.
        5. Encrypt and persist tokens on the server record.
        6. Redirect to frontend admin page with success indicator.
    """
    # --- Error path: user denied or provider error ---
    error = request.query_params.get("error")
    if error:
        error_description = request.query_params.get("error_description", error)
        logger.info(
            "A2A OAuth2 callback received error: %s", error_description,
        )
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?oauth_error={error_description}",
            status_code=302,
        )

    # --- Success path ---
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code or not state:
        logger.warning("A2A OAuth2 callback missing code or state parameter")
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?oauth_error=Missing+authorization+code",
            status_code=302,
        )

    # Validate state nonce (atomic get+delete — one-time use)
    state_data = await get_a2a_oauth_state(state)
    if state_data is None:
        logger.warning(
            "A2A OAuth2 callback with invalid/expired/replayed state nonce: %s",
            state,
        )
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?oauth_error=Invalid+or+expired+state",
            status_code=302,
        )

    server_id = state_data.get("server_id")

    # Load the A2A server
    server = await _svc_get_a2a_server(db, server_id)
    if server is None:
        logger.warning(
            "A2A OAuth2 callback for unknown server: %s", server_id,
        )
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?oauth_error=Server+not+found",
            status_code=302,
        )

    # Exchange code for tokens
    redirect_uri = f"{settings.API_BASE_URL}/api/a2a/oauth2/callback"
    tokens = await _svc_exchange_oauth2_code(code, redirect_uri, server)
    if tokens is None:
        logger.warning(
            "A2A OAuth2 code exchange failed for server %s", server_id,
        )
        return RedirectResponse(
            url=f"{FRONTEND_ERROR_URL}?oauth_error=Token+exchange+failed",
            status_code=302,
        )

    # Encrypt and persist tokens
    server.oauth2_tokens = _encrypt_oauth2_tokens(tokens)
    await db.commit()

    logger.info(
        "A2A OAuth2 authorization successful for server '%s' (id=%s)",
        server.name, server_id,
    )
    return RedirectResponse(
        url=f"{FRONTEND_SUCCESS_URL}?authorized=true&server={server_id}",
        status_code=302,
    )
