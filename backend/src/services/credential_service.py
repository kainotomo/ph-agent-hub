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

from ..core.exceptions import NotFoundError, ValidationError
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
) -> dict:
    """Test whether a credential can connect to its provider.

    For IMAP: attempts to open an IMAP connection and foldr list.
    For OAuth: attempts a token refresh or API call.

    Returns:
        A dict with ``ok`` (bool), ``message`` (str), and optionally
        ``folders`` (list[str]) for IMAP accounts.
    """
    provider = credential.provider
    creds = json.loads(credential.credentials) if credential.credentials else {}
    tokens = json.loads(credential.oauth_tokens) if credential.oauth_tokens else {}

    if provider == "imap":
        return await _test_imap_connection(creds)
    elif provider in ("gmail", "outlook", "google", "microsoft"):
        # OAuth-based — test token validity by making a lightweight API call
        return await _test_oauth_connection(provider, tokens, creds)
    else:
        return {"ok": False, "message": f"Unknown provider: {provider}"}


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


async def _test_imap_connection(creds: dict) -> dict:
    """Test IMAP connectivity."""
    import imaplib

    host = creds.get("imap_host", "")
    port = int(creds.get("imap_port", 993))
    username = creds.get("username", "")
    password = creds.get("password", "")

    if not host:
        return {"ok": False, "message": "IMAP host not configured"}
    if not username:
        return {"ok": False, "message": "IMAP username not configured"}
    if not password:
        return {"ok": False, "message": "IMAP password not configured"}

    try:
        import asyncio
        import ssl

        def _connect() -> tuple[list[str], str]:
            context = ssl.create_default_context()
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=context)
            conn.login(username, password)
            folders = []
            typ, data = conn.list()
            if typ == "OK":
                for item in data:
                    parts = item.decode().split(' "/" ')
                    if len(parts) == 2:
                        folders.append(parts[1])
                    elif len(item) > 0:
                        folders.append(item.decode().strip())
            conn.logout()
            return folders, f"Connected. Found {len(folders)} folders."

        folders, message = await asyncio.to_thread(_connect)
        return {"ok": True, "message": message, "folders": folders[:20]}

    except imaplib.IMAP4.error as exc:
        return {"ok": False, "message": f"IMAP auth failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "message": f"Connection failed: {exc}"}


async def _test_oauth_connection(provider: str, tokens: dict, creds: dict) -> dict:
    """Test OAuth connectivity with a lightweight API call."""
    try:
        access_token = tokens.get("access_token", "")
        if not access_token:
            return {"ok": False, "message": "No access token available. Reconnect the account."}

        if provider in ("gmail", "google"):
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {"ok": True, "message": f"Connected as {data.get('emailAddress', 'unknown')}"}
                elif resp.status_code == 401:
                    return {"ok": False, "message": "Token expired. Reconnect the account."}
                else:
                    return {"ok": False, "message": f"Gmail API error: HTTP {resp.status_code}"}

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
