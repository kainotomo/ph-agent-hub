# =============================================================================
# PH Agent Hub — Shared OAuth Token Refresh Helper
# =============================================================================
# Called by tool factories (email, calendar, tasks) when an API call returns
# 401 due to an expired access token.  Uses the stored refresh_token to
# obtain a fresh access_token in-place.
# =============================================================================

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def ensure_fresh_token(
    tokens: dict,
    provider: str,
    tool_name: str = "Tool",
    credential_orm: object | None = None,
    tokens_dict: dict | None = None,
    db: object | None = None,
) -> bool:
    """Check token expiry and refresh proactively before an API call.

    Uses ``is_token_expired`` (5-minute buffer) to decide whether to
    refresh.  This avoids making a doomed API call just to get a 401.

    Args:
        tokens: Dict with refresh_token, access_token, expires_at.
        provider: "gmail", "google", "outlook", or "microsoft".
        tool_name: Human-readable label for log messages.
        credential_orm: Optional ``UserToolCredential`` ORM object to
            persist the refreshed token to the database.
        tokens_dict: The original tokens dict to persist.
        db: Optional async DB session for persisting refreshed tokens.

    Returns:
        True if the token is fresh (already valid or successfully
        refreshed), False otherwise.
    """
    from ..core.oauth import is_token_expired

    if not is_token_expired(tokens):
        return True

    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        return False

    result = await refresh_token_if_expired(
        tokens, provider, tool_name,
        credential_orm=credential_orm,
        tokens_dict=tokens_dict,
        db=db,
    )
    return result is not None


async def refresh_token_if_expired(
    tokens: dict,
    provider: str,
    tool_name: str = "Tool",
    credential_orm: object | None = None,
    tokens_dict: dict | None = None,
    db: object | None = None,
) -> dict | None:
    """Try to refresh an OAuth token.  Modifies ``tokens`` in-place.

    When ``credential_orm``, ``tokens_dict``, and ``db`` are provided,
    persists the refreshed tokens back to the database so subsequent
    sessions don't need to re-refresh.

    Args:
        tokens: Dict with refresh_token, access_token, expires_at.
        provider: "gmail", "google", "outlook", or "microsoft".
        tool_name: Human-readable label for log messages.
        credential_orm: Optional ``UserToolCredential`` ORM object to
            persist the refreshed token to the database.
        tokens_dict: The original tokens dict (from ``_parse_credential``)
            to persist. Usually the same as ``tokens``.
        db: Optional async DB session for persisting refreshed tokens.

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

            # Handle refresh token rotation — Microsoft may issue a new one
            new_rt = result.get("refresh_token")
            if new_rt:
                tokens["refresh_token"] = new_rt

            logger.info("%s token refreshed successfully", tool_name)

            # Sync refreshed values to tokens_dict when it's a different dict
            # (e.g., calendar.py uses user_creds_map for `tokens` but
            #  _tokens_dict from _parse_credential for `tokens_dict`)
            if tokens_dict is not None and tokens_dict is not tokens:
                tokens_dict["access_token"] = tokens["access_token"]
                tokens_dict["expires_at"] = tokens["expires_at"]
                if new_rt:
                    tokens_dict["refresh_token"] = new_rt

            # Persist refreshed tokens to the ORM object + commit
            if credential_orm is not None and tokens_dict is not None:
                credential_orm.oauth_tokens = json.dumps(tokens_dict)
                if db is not None:
                    try:
                        db.add(credential_orm)
                        await db.commit()
                        logger.info("%s token persisted to database (committed)", tool_name)
                    except Exception:
                        logger.warning(
                            "%s token DB commit failed (tokens still fresh in-memory)",
                            tool_name,
                            exc_info=True,
                        )
                else:
                    logger.info(
                        "%s token set on ORM object but no db session to commit",
                        tool_name,
                    )

            return tokens
    except Exception:
        logger.warning("%s token refresh failed", tool_name, exc_info=True)

    return None
