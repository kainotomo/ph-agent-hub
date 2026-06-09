# =============================================================================
# PH Agent Hub — User Tool Credential Service
# =============================================================================
# CRUD for per-user tool credentials (email, calendar, tasks accounts).
# Credential values are encrypted at rest via the ORM's EncryptedString.
# =============================================================================

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.tools import Tool
from ..db.orm.user_tool_credentials import UserToolCredential


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

async def create_credential(
    db: AsyncSession,
    user_id: str,
    tool_id: str,
    label: str,
    provider: str,
    email_address: str | None = None,
    credentials: dict | None = None,
    oauth_tokens: dict | None = None,
    is_default: bool = False,
) -> UserToolCredential:
    """Create a new credential entry for a user + tool combination.

    Args:
        db: Active DB session.
        user_id: Who owns this credential.
        tool_id: Which tool this credential is for.
        label: User-defined display name (e.g. "Work Gmail").
        provider: "gmail", "outlook", "imap", "google", or "microsoft".
        email_address: Associated email address.
        credentials: Dict of non-OAuth secrets (IMAP/SMTP passwords, etc.).
        oauth_tokens: Dict of OAuth tokens (access_token, refresh_token, expires_at).
        is_default: Whether this is the default account for this tool.

    Returns:
        The created UserToolCredential ORM instance.
    """
    _validate_provider(provider)

    # If setting as default, unset any existing default first
    if is_default:
        await _unset_default_for_tool(db, user_id, tool_id)

    credential = UserToolCredential(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tool_id=tool_id,
        label=label,
        provider=provider,
        email_address=email_address,
        credentials=json.dumps(credentials) if credentials else None,
        oauth_tokens=json.dumps(oauth_tokens) if oauth_tokens else None,
        is_default=is_default,
        status="active",
    )
    db.add(credential)
    await db.commit()
    await db.refresh(credential)
    return credential


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def list_credentials(
    db: AsyncSession,
    user_id: str,
    tool_id: str | None = None,
) -> list[UserToolCredential]:
    """List all credential entries for a user, optionally filtered by tool."""
    stmt = select(UserToolCredential).where(
        UserToolCredential.user_id == user_id,
    )
    if tool_id:
        stmt = stmt.where(UserToolCredential.tool_id == tool_id)

    stmt = stmt.order_by(UserToolCredential.is_default.desc(), UserToolCredential.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_credential_by_id(
    db: AsyncSession,
    credential_id: str,
    user_id: str | None = None,
) -> UserToolCredential:
    """Get a single credential by ID. Optionally verify ownership."""
    stmt = select(UserToolCredential).where(
        UserToolCredential.id == credential_id,
    )
    if user_id:
        stmt = stmt.where(UserToolCredential.user_id == user_id)

    result = await db.execute(stmt)
    cred = result.scalar_one_or_none()
    if not cred:
        raise NotFoundError("Credential not found")
    return cred


async def get_default_credential(
    db: AsyncSession,
    user_id: str,
    tool_id: str,
) -> UserToolCredential | None:
    """Get the default credential for a user + tool, if any."""
    result = await db.execute(
        select(UserToolCredential).where(
            UserToolCredential.user_id == user_id,
            UserToolCredential.tool_id == tool_id,
            UserToolCredential.is_default == True,  # noqa: E712
            UserToolCredential.status == "active",
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

async def update_credential(
    db: AsyncSession,
    credential_id: str,
    user_id: str,
    *,
    label: str | None = None,
    credentials: dict | None = None,
    oauth_tokens: dict | None = None,
    is_default: bool | None = None,
    status: str | None = None,
) -> UserToolCredential:
    """Update fields on an existing credential entry."""
    cred = await get_credential_by_id(db, credential_id, user_id=user_id)

    if label is not None:
        cred.label = label
    if credentials is not None:
        cred.credentials = json.dumps(credentials)
    if oauth_tokens is not None:
        cred.oauth_tokens = json.dumps(oauth_tokens)
    if is_default is not None:
        if is_default:
            await _unset_default_for_tool(db, cred.user_id, cred.tool_id)
        cred.is_default = is_default
    if status is not None:
        cred.status = status

    cred.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(cred)
    return cred


async def update_oauth_tokens(
    db: AsyncSession,
    credential_id: str,
    oauth_tokens: dict,
) -> UserToolCredential:
    """Refresh just the OAuth tokens (called by token refresh logic)."""
    cred = await get_credential_by_id(db, credential_id)
    cred.oauth_tokens = json.dumps(oauth_tokens)
    cred.status = "active"
    cred.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(cred)
    return cred


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def delete_credential(
    db: AsyncSession,
    credential_id: str,
    user_id: str,
) -> None:
    """Delete a credential entry by ID. Verifies ownership."""
    cred = await get_credential_by_id(db, credential_id, user_id=user_id)
    await db.delete(cred)
    await db.commit()


async def delete_user_credentials(
    db: AsyncSession,
    user_id: str,
    tool_id: str | None = None,
) -> int:
    """Delete all credentials for a user, optionally filtered by tool.

    Returns the number of deleted rows.
    """
    stmt = delete(UserToolCredential).where(
        UserToolCredential.user_id == user_id,
    )
    if tool_id:
        stmt = stmt.where(UserToolCredential.tool_id == tool_id)

    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------

async def test_connection(
    credential: UserToolCredential,
    db: AsyncSession | None = None,
) -> dict:
    """Test whether a credential can connect to its provider.

    Pass ``db`` to enable automatic token refresh on expiry.
    """
    return await _do_test_connection(credential, db=db)


async def test_raw_imap_connection(
    host: str, port: int, username: str, password: str,
) -> dict:
    """Test IMAP connectivity without a stored credential.

    Used by the pre-save test in the manual IMAP setup UI.
    """
    return await _test_imap_connection_raw(host, port, username, password)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

VALID_PROVIDERS = frozenset({"gmail", "outlook", "imap", "google", "microsoft"})


def _validate_provider(provider: str) -> None:
    if provider not in VALID_PROVIDERS:
        raise ValidationError(
            f"Invalid provider '{provider}'. Must be one of: "
            f"{', '.join(sorted(VALID_PROVIDERS))}"
        )


async def _unset_default_for_tool(db: AsyncSession, user_id: str, tool_id: str) -> None:
    """Clear the is_default flag on all of a user's credentials for a tool."""
    await db.execute(
        update(UserToolCredential)
        .where(
            UserToolCredential.user_id == user_id,
            UserToolCredential.tool_id == tool_id,
            UserToolCredential.is_default == True,  # noqa: E712
        )
        .values(is_default=False, updated_at=datetime.now(timezone.utc))
    )


async def _do_test_connection(
    credential: UserToolCredential,
    db: AsyncSession | None = None,
) -> dict:
    """Test a stored credential against its provider.

    If the test returns a 401 (token expired) and a refresh token is
    available, attempts to auto-refresh the token before retrying.
    """
    import json
    from ..core.config import settings
    from ..core.oauth import refresh_oauth_token

    provider = credential.provider
    creds = json.loads(credential.credentials) if credential.credentials else {}
    tokens = json.loads(credential.oauth_tokens) if credential.oauth_tokens else {}

    # Resolve the tool type to pick the right test endpoint
    tool_type = "email"  # default
    if db and credential.tool_id:
        from ..db.orm.tools import Tool
        tool_result = await db.execute(
            select(Tool).where(Tool.id == credential.tool_id)
        )
        tool = tool_result.scalar_one_or_none()
        if tool:
            tool_type = tool.type

    if provider == "imap":
        return await _test_imap_connection(creds)
    elif provider in ("gmail", "outlook", "google", "microsoft"):
        result = await _test_oauth_connection(provider, tool_type, tokens)

        # If expired and we have a refresh_token, try refreshing
        if result.get("ok") is False and "expired" in result.get("message", "").lower():
            refresh_token = tokens.get("refresh_token", "")
            if refresh_token:
                # Get client credentials from settings
                if provider in ("gmail", "google"):
                    client_id = settings.GOOGLE_CLIENT_ID
                    client_secret = settings.GOOGLE_CLIENT_SECRET
                else:
                    client_id = settings.MS_CLIENT_ID
                    client_secret = settings.MS_CLIENT_SECRET

                new_tokens = await refresh_oauth_token(tokens, provider, client_id, client_secret)
                if new_tokens:
                    # Update stored tokens with new access_token and expires_at
                    tokens["access_token"] = new_tokens.get("access_token", tokens.get("access_token", ""))
                    if "expires_at" in new_tokens:
                        tokens["expires_at"] = new_tokens["expires_at"]
                    credential.oauth_tokens = json.dumps(tokens)
                    if db:
                        await db.commit()

                    # Retry test with refreshed token
                    result = await _test_oauth_connection(provider, tool_type, tokens)

        return result
    else:
        return {"ok": False, "message": f"Unknown provider: {provider}"}


async def _test_imap_connection_raw(host: str, port: int, username: str, password: str) -> dict:
    """Test raw IMAP credentials (without a stored credential row)."""
    if not host:
        return {"ok": False, "message": "IMAP host not provided"}
    if not username:
        return {"ok": False, "message": "IMAP username not provided"}
    if not password:
        return {"ok": False, "message": "IMAP password not provided"}

    import asyncio
    import imaplib
    import ssl

    try:
        def _connect():
            ctx = ssl.create_default_context()
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
            conn.login(username, password)
            folders = []
            typ, data = conn.list()
            if typ == "OK":
                for item in data:
                    parts = item.decode().split(' "/" ')
                    folders.append(parts[1] if len(parts) == 2 else item.decode().strip())
            conn.logout()
            return folders, f"Connected. Found {len(folders)} folders."

        folders, message = await asyncio.to_thread(_connect)
        return {"ok": True, "message": message, "folders": folders[:20]}
    except imaplib.IMAP4.error as exc:
        return {"ok": False, "message": f"IMAP auth failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "message": f"Connection failed: {exc}"}


async def _test_imap_connection(creds: dict) -> dict:
    """Test IMAP connectivity from a stored credential dict."""
    host = creds.get("imap_host", "")
    port = int(creds.get("imap_port", 993))
    username = creds.get("username", "")
    password = creds.get("password", "")
    return await _test_imap_connection_raw(host, port, username, password)


async def _test_oauth_connection(provider: str, tool_type: str, tokens: dict) -> dict:
    """Test OAuth connectivity with a lightweight API call for the given tool type."""
    try:
        access_token = tokens.get("access_token", "")
        if not access_token:
            return {"ok": False, "message": "No access token available. Reconnect the account."}

        if provider in ("gmail", "google"):
            import httpx

            # Pick the right test endpoint based on tool type
            if tool_type == "calendar":
                test_url = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
                api_label = "Calendar"
            elif tool_type == "tasks":
                test_url = "https://tasks.googleapis.com/tasks/v1/users/@me/lists"
                api_label = "Tasks"
            else:
                test_url = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
                api_label = "Gmail"

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    test_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    if tool_type == "calendar":
                        data = resp.json()
                        items = data.get("items", [])
                        return {"ok": True, "message": f"Connected — {len(items)} calendar(s) found"}
                    elif tool_type == "tasks":
                        data = resp.json()
                        items = data.get("items", [])
                        return {"ok": True, "message": f"Connected — {len(items)} task list(s) found"}
                    else:
                        data = resp.json()
                        return {"ok": True, "message": f"Connected as {data.get('emailAddress', 'unknown')}"}
                elif resp.status_code == 401:
                    return {"ok": False, "message": "Token expired. Reconnect the account."}
                else:
                    return {"ok": False, "message": f"{api_label} API error: HTTP {resp.status_code}"}

        elif provider in ("outlook", "microsoft"):
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {"ok": True, "message": f"Connected as {data.get('mail', data.get('userPrincipalName', 'unknown'))}"}
                elif resp.status_code == 401:
                    return {"ok": False, "message": "Token expired. Reconnect the account."}
                else:
                    return {"ok": False, "message": f"Microsoft Graph error: HTTP {resp.status_code}"}

    except Exception as exc:
        return {"ok": False, "message": f"Connection test failed: {exc}"}

    return {"ok": False, "message": "Could not test connection (unknown error)"}
