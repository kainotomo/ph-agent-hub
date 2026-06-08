# =============================================================================
# PH Agent Hub — User Tool Credential API
# =============================================================================
# Endpoints for users to manage their connected tool accounts
# (email, calendar, tasks, etc.). Includes OAuth URL generation.
# =============================================================================

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_user, get_db
from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.users import User as UserORM
from ..db.orm.tools import Tool as ToolORM
from ..services.credential_service import (
    create_credential as _svc_create_credential,
    list_credentials as _svc_list_credentials,
    get_credential_by_id as _svc_get_credential_by_id,
    update_credential as _svc_update_credential,
    delete_credential as _svc_delete_credential,
    test_connection as _svc_test_connection,
    test_raw_imap_connection as _svc_test_raw_imap,
)
from ..db.orm.user_tool_credentials import UserToolCredential

router = APIRouter(prefix="/credentials", tags=["credentials"])


# =============================================================================
# Pydantic schemas
# =============================================================================


class CredentialResponse(BaseModel):
    id: str
    user_id: str
    tool_id: str
    tool_type: str = ""
    label: str
    provider: str
    email_address: str | None
    is_default: bool
    status: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class CredentialListResponse(BaseModel):
    items: list[CredentialResponse]
    total: int


class CreateCredentialRequest(BaseModel):
    tool_id: str
    label: str
    provider: str
    email_address: str | None = None
    credentials: dict | None = None
    oauth_tokens: dict | None = None
    is_default: bool = False


class UpdateCredentialRequest(BaseModel):
    label: str | None = None
    credentials: dict | None = None
    is_default: bool | None = None
    status: str | None = None


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str
    folders: list[str] | None = None


class TestRawImapRequest(BaseModel):
    host: str
    port: int = 993
    username: str
    password: str


class OAuthUrlResponse(BaseModel):
    url: str
    state: str


# =============================================================================
# Helpers
# =============================================================================


def _cred_to_response(
    cred: UserToolCredential,
    tool_type_map: dict[str, str] | None = None,
) -> CredentialResponse:
    """Convert ORM to response model, masking sensitive fields."""
    tt = (tool_type_map or {}).get(cred.tool_id, "")
    return CredentialResponse(
        id=cred.id,
        user_id=cred.user_id,
        tool_id=cred.tool_id,
        tool_type=tt,
        label=cred.label,
        provider=cred.provider,
        email_address=cred.email_address,
        is_default=cred.is_default,
        status=cred.status,
        created_at=cred.created_at.isoformat() if cred.created_at else "",
        updated_at=cred.updated_at.isoformat() if cred.updated_at else "",
    )


# =============================================================================
# CRUD Endpoints
# =============================================================================


@router.get("/tool-id", response_model=dict)
async def get_tool_id_by_type(
    tool_type: str = Query(..., description="Tool type (email, calendar, tasks)"),
    current_user: UserORM = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Look up a tool's ID by its type.

    Used by the frontend when creating credentials — the user selects
    a tool type (email, calendar, tasks) and needs the actual tool ID
    to pass to the create credential endpoint.
    """
    result = await db.execute(
        _select(ToolORM)
        .where(
            ToolORM.type == tool_type,
            ToolORM.tenant_id == current_user.tenant_id,
            ToolORM.enabled == True,  # noqa: E712
        )
        .limit(1)
    )
    tool = result.scalar_one_or_none()
    if not tool:
        raise NotFoundError(
            f"No enabled '{tool_type}' tool found. "
            f"An administrator must create one in Admin Area → Tools."
        )
    return {"tool_id": tool.id}


@router.get("", response_model=CredentialListResponse)
async def list_credentials(
    tool_id: str | None = Query(None, description="Filter by tool ID"),
    current_user: UserORM = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all connected accounts for the current user, optionally filtered by tool."""

    items = await _svc_list_credentials(
        db, user_id=current_user.id, tool_id=tool_id,
    )

    # Build tool_id → tool_type map for the response
    tool_ids = list({c.tool_id for c in items})
    tool_type_map: dict[str, str] = {}
    if tool_ids:
        tools_result = await db.execute(
            _select(ToolORM).where(ToolORM.id.in_(tool_ids))
        )
        for t in tools_result.scalars().all():
            tool_type_map[t.id] = t.type

    return CredentialListResponse(
        items=[_cred_to_response(c, tool_type_map) for c in items],
        total=len(items),
    )


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CreateCredentialRequest,
    current_user: UserORM = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually add a credential entry (e.g., IMAP email account).

    For OAuth-based providers (gmail, outlook, google, microsoft), use the
    OAuth flow instead — this endpoint is mainly for manual IMAP setup.
    """
    # Verify tool exists
    from sqlalchemy import select as _select

    result = await db.execute(
        _select(ToolORM).where(ToolORM.id == body.tool_id)
    )
    if not result.scalar_one_or_none():
        raise NotFoundError(f"Tool '{body.tool_id}' not found")

    cred = await _svc_create_credential(
        db,
        user_id=current_user.id,
        tool_id=body.tool_id,
        label=body.label,
        provider=body.provider,
        email_address=body.email_address,
        credentials=body.credentials,
        oauth_tokens=body.oauth_tokens,
        is_default=body.is_default,
    )
    return _cred_to_response(cred)


@router.put("/{credential_id}", response_model=CredentialResponse)
async def update_credential(
    credential_id: str,
    body: UpdateCredentialRequest,
    current_user: UserORM = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a credential's label, credentials, or default status."""
    cred = await _svc_update_credential(
        db,
        credential_id=credential_id,
        user_id=current_user.id,
        label=body.label,
        credentials=body.credentials,
        is_default=body.is_default,
        status=body.status,
    )
    return _cred_to_response(cred)


@router.delete("/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: str,
    current_user: UserORM = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a connected account and revoke its access."""
    await _svc_delete_credential(db, credential_id, user_id=current_user.id)


@router.post("/test-imap", response_model=TestConnectionResponse)
async def test_raw_imap(
    body: TestRawImapRequest,
    current_user: UserORM = Depends(get_current_user),
):
    """Test IMAP connectivity with raw credentials (pre-save validation).

    Used by the manual IMAP setup form to verify credentials before saving.
    """
    result = await _svc_test_raw_imap(
        host=body.host, port=body.port,
        username=body.username, password=body.password,
    )
    return TestConnectionResponse(**result)


@router.post("/{credential_id}/test", response_model=TestConnectionResponse)
async def test_credential_connection(
    credential_id: str,
    current_user: UserORM = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test whether a credential can connect to its email provider."""
    cred = await _svc_get_credential_by_id(
        db, credential_id, user_id=current_user.id
    )
    result = await _svc_test_connection(cred)
    return TestConnectionResponse(**result)


# =============================================================================
# OAuth URL Endpoints
# =============================================================================


@router.get("/oauth/google/url", response_model=OAuthUrlResponse)
async def google_oauth_url(
    tool_id: str = Query(..., description="Which tool to connect (email/calendar/tasks)"),
    current_user: UserORM = Depends(get_current_user),
):
    """Generate a Google OAuth consent URL for the specified tool type.

    Scopes are selected based on the tool:
    - email_tool: Gmail read + send
    - calendar_tool: Calendar read/write
    - tasks_tool: Tasks read/write
    """
    from ..core.config import settings

    client_id = settings.GOOGLE_CLIENT_ID
    if not client_id:
        raise ValidationError(
            "Google OAuth is not configured. "
            "Ask your administrator to set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        )

    redirect_uri = f"{settings.API_BASE_URL}/api/credentials/oauth/google/callback"
    scopes = _google_scopes_for_tool(tool_id)

    import uuid
    state = f"{current_user.id}:{tool_id}:{uuid.uuid4().hex[:8]}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    from urllib.parse import urlencode
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    return OAuthUrlResponse(url=url, state=state)


@router.get("/oauth/microsoft/url", response_model=OAuthUrlResponse)
async def microsoft_oauth_url(
    tool_id: str = Query(..., description="Which tool to connect (email/calendar/tasks)"),
    current_user: UserORM = Depends(get_current_user),
):
    """Generate a Microsoft OAuth consent URL for the specified tool type."""
    from ..core.config import settings

    client_id = settings.MS_CLIENT_ID
    if not client_id:
        raise ValidationError(
            "Microsoft OAuth is not configured. "
            "Ask your administrator to set MS_CLIENT_ID and MS_CLIENT_SECRET."
        )

    redirect_uri = f"{settings.API_BASE_URL}/api/credentials/oauth/microsoft/callback"
    scopes = _microsoft_scopes_for_tool(tool_id)

    import uuid
    state = f"{current_user.id}:{tool_id}:{uuid.uuid4().hex[:8]}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "response_mode": "query",
        "state": state,
    }

    from urllib.parse import urlencode
    url = f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?{urlencode(params)}"

    return OAuthUrlResponse(url=url, state=state)


# =============================================================================
# OAuth Callbacks
# =============================================================================


@router.get("/oauth/google/callback")
async def google_oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth redirect. Exchange code for tokens, store credential."""
    from ..core.config import settings
    from ..core.oauth import exchange_google_code

    try:
        user_id, tool_id = state.split(":")[:2]
    except (ValueError, IndexError):
        raise ValidationError("Invalid OAuth state parameter")

    redirect_uri = f"{settings.API_BASE_URL}/api/credentials/oauth/google/callback"

    tokens = await exchange_google_code(
        code=code,
        redirect_uri=redirect_uri,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )

    # Extract email from ID token
    import jwt as pyjwt
    email = ""
    id_token = tokens.get("id_token", "")
    if id_token:
        try:
            decoded = pyjwt.decode(id_token, options={"verify_signature": False})
            email = decoded.get("email", "")
        except Exception:
            pass

    # Resolve tool_id (e.g. "email_tool") to the actual tool UUID from the tools table
    tool_type = tool_id.replace("_tool", "")
    result = await db.execute(
        _select(ToolORM).where(ToolORM.type == tool_type).limit(1)
    )
    tool = result.scalars().first()
    if tool is None:
        # Create the tool on-the-fly if it doesn't exist (first-time setup)
        from ..db.orm.tenants import Tenant as TenantORM
        result = await db.execute(_select(TenantORM).limit(1))
        tenant = result.scalars().first()
        if not tenant:
            raise ValidationError("No tenant found. Run the seed script first.")
        tool = ToolORM(
            tenant_id=tenant.id,
            name=tool_type.capitalize(),
            type=tool_type,
            config={},
            enabled=True,
            is_public=True,
        )
        db.add(tool)
        await db.flush()

    # Create credential entry
    cred = await _svc_create_credential(
        db,
        user_id=user_id,
        tool_id=tool.id,
        label=f"Google ({email or 'Gmail'})",
        provider="gmail" if "gmail" in (tokens.get("scope", "") or "") else "google",
        email_address=email or None,
        credentials={},
        oauth_tokens={
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_at": tokens.get("expires_at", 0),
        },
        is_default=True,
    )

    # Redirect to frontend settings page
    from fastapi.responses import RedirectResponse
    frontend_url = settings.FRONTEND_URL or "http://localhost:3000"
    return RedirectResponse(
        url=f"{frontend_url}/settings?connected=true&provider=google&label={email or 'Gmail'}",
        status_code=302,
    )


@router.get("/oauth/microsoft/callback")
async def microsoft_oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle Microsoft OAuth redirect. Exchange code for tokens, store credential."""
    from ..core.config import settings
    from ..core.oauth import exchange_microsoft_code

    try:
        user_id, tool_id = state.split(":")[:2]
    except (ValueError, IndexError):
        raise ValidationError("Invalid OAuth state parameter")

    redirect_uri = f"{settings.API_BASE_URL}/api/credentials/oauth/microsoft/callback"

    tokens = await exchange_microsoft_code(
        code=code,
        redirect_uri=redirect_uri,
        client_id=settings.MS_CLIENT_ID,
        client_secret=settings.MS_CLIENT_SECRET,
    )

    email = tokens.get("email", "")

    # Resolve tool_id (e.g. "email_tool") to the actual tool UUID from the tools table
    tool_type = tool_id.replace("_tool", "")
    result = await db.execute(
        _select(ToolORM).where(ToolORM.type == tool_type).limit(1)
    )
    tool = result.scalars().first()
    if tool is None:
        # Create the tool on-the-fly if it doesn't exist (first-time setup)
        from ..db.orm.tenants import Tenant as TenantORM
        result = await db.execute(_select(TenantORM).limit(1))
        tenant = result.scalars().first()
        if not tenant:
            raise ValidationError("No tenant found. Run the seed script first.")
        tool = ToolORM(
            tenant_id=tenant.id,
            name=tool_type.capitalize(),
            type=tool_type,
            config={},
            enabled=True,
            is_public=True,
        )
        db.add(tool)
        await db.flush()

    # Create credential entry
    cred = await _svc_create_credential(
        db,
        user_id=user_id,
        tool_id=tool.id,
        label=f"Outlook ({email or 'Outlook'})",
        provider="outlook",
        email_address=email or None,
        credentials={},
        oauth_tokens={
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_at": tokens.get("expires_at", 0),
        },
        is_default=True,
    )

    from fastapi.responses import RedirectResponse
    frontend_url = settings.FRONTEND_URL or "http://localhost:3000"
    return RedirectResponse(
        url=f"{frontend_url}/settings?connected=true&provider=microsoft&label={email or 'Outlook'}",
        status_code=302,
    )


# =============================================================================
# Scope helpers
# =============================================================================

GOOGLE_SCOPES = {
    "email_tool": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ],
    "calendar_tool": [
        "https://www.googleapis.com/auth/calendar",
    ],
    "tasks_tool": [
        "https://www.googleapis.com/auth/tasks",
    ],
}

MICROSOFT_SCOPES = {
    "email_tool": [
        "https://graph.microsoft.com/Mail.Read",
        "https://graph.microsoft.com/Mail.Send",
        "offline_access",
    ],
    "calendar_tool": [
        "https://graph.microsoft.com/Calendars.ReadWrite",
        "offline_access",
    ],
    "tasks_tool": [
        "https://graph.microsoft.com/Tasks.ReadWrite",
        "offline_access",
    ],
}


def _google_scopes_for_tool(tool_id: str) -> list[str]:
    """Return Google OAuth scopes for the given tool type."""
    # Try direct match first
    if tool_id in GOOGLE_SCOPES:
        return GOOGLE_SCOPES[tool_id]

    # Fall back to checking the tool's type in the database (handled at call time)
    # Return a reasonable default: Gmail scopes
    return GOOGLE_SCOPES["email_tool"]


def _microsoft_scopes_for_tool(tool_id: str) -> list[str]:
    """Return Microsoft OAuth scopes for the given tool type."""
    if tool_id in MICROSOFT_SCOPES:
        return MICROSOFT_SCOPES[tool_id]
    return MICROSOFT_SCOPES["email_tool"]
