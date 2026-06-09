# =============================================================================
# PH Agent Hub — Shared OAuth Token Refresh Helper
# =============================================================================
# Called by tool factories (email, calendar, tasks) when an API call returns
# 401 due to an expired access token.  Uses the stored refresh_token to
# obtain a fresh access_token in-place.
# =============================================================================

import json
import logging

logger = logging.getLogger(__name__)


async def refresh_token_if_expired(
    tokens: dict,
    provider: str,
    tool_name: str = "Tool",
    credential_orm: object | None = None,
    tokens_dict: dict | None = None,
) -> dict | None:
    """Try to refresh an OAuth token.  Modifies ``tokens`` in-place.

    When ``credential_orm`` and ``tokens_dict`` are provided, persists the
    refreshed tokens back to the database so subsequent sessions don't
    need to re-refresh.

    Args:
        tokens: Dict with refresh_token, access_token, expires_at.
        provider: "gmail", "google", "outlook", or "microsoft".
        tool_name: Human-readable label for log messages.
        credential_orm: Optional ``UserToolCredential`` ORM object to
            persist the refreshed token to the database.
        tokens_dict: The original tokens dict (from ``_parse_credential``)
            to persist. Usually the same as ``tokens``.

    Returns:
        The updated tokens dict on success, or None on failure.
    """
    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        return None

    from ..core.oauth import refresh_oauth_token
    from ..core.config import settings

    if provider in ("gmail", "google"):
        client_id = settings.GOOGLE_CLIENT_ID
        client_secret = settings.GOOGLE_CLIENT_SECRET
    else:
        client_id = settings.MS_CLIENT_ID
        client_secret = settings.MS_CLIENT_SECRET

    try:
        result = await refresh_oauth_token(tokens, provider, client_id, client_secret)
        if result:
            tokens["access_token"] = result.get("access_token", tokens.get("access_token", ""))
            tokens["expires_at"] = result.get("expires_at", tokens.get("expires_at", 0))
            logger.info("%s token refreshed successfully", tool_name)

            # Persist refreshed tokens to the ORM object if available
            if credential_orm is not None and tokens_dict is not None:
                credential_orm.oauth_tokens = json.dumps(tokens_dict)
                logger.info("%s token persisted to database", tool_name)

            return tokens
    except Exception:
        logger.warning("%s token refresh failed", tool_name, exc_info=True)

    return None
