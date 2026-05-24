# =============================================================================
# PH Agent Hub — Auth API Router
# =============================================================================
# Endpoints: POST /auth/login, POST /auth/refresh, GET /auth/me, POST /auth/logout
# =============================================================================

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.dependencies import get_current_user, get_db
from ..core.exceptions import UnauthorizedError
from ..core.jwt import create_access_token, create_refresh_token, decode_token
from ..core.limiter import limiter
from ..core.redis import add_to_denylist, is_denylisted
from ..core.security import verify_password
from ..db.orm.users import User
from ..services.license_service import is_tenant_accessible
from ..services.tenant_service import count_tenants, get_tenant_ordinal
from ..services.user_service import get_user_by_email, get_user_by_id

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfile(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    tenant_id: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Authenticate user and issue access + refresh tokens."""
    user = await get_user_by_email(db, form_data.username)
    if user is None or not verify_password(form_data.password, user.password_hash):
        raise UnauthorizedError("Invalid email or password")
    if not user.is_active:
        raise UnauthorizedError("User account is inactive")

    # --- License gating (Issue #243) ---
    # Check that this tenant is within the effective limit.
    # Admin users always bypass (they need access to manage licenses).
    if user.role != "admin":
        tenant_ordinal = await get_tenant_ordinal(db, user.tenant_id)
        if tenant_ordinal is not None:
            accessible = await is_tenant_accessible(db, tenant_ordinal)
            if not accessible:
                total = await count_tenants(db)
                raise UnauthorizedError(
                    "License expired or tenant limit reached. "
                    f"This instance has {total} tenants but only "
                    "the first 3 are accessible on the free tier. "
                    "Contact your administrator to upgrade to Pro."
                )

    jti = str(uuid.uuid4())

    access_token = create_access_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
    )
    refresh_token = create_refresh_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role, "jti": jti}
    )

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        samesite="lax",
        path="/api/auth",
        secure=settings.COOKIE_SECURE,
        max_age=settings.JWT_REFRESH_EXPIRES_IN,
    )

    return TokenResponse(access_token=access_token)


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Issue a new access token using a valid refresh-token cookie."""
    token = request.cookies.get("refresh_token")
    if not token:
        raise UnauthorizedError("Refresh token missing")

    try:
        payload = decode_token(token)
    except Exception:
        raise UnauthorizedError("Invalid or expired refresh token")

    jti = payload.get("jti")
    if not jti or await is_denylisted(jti):
        raise UnauthorizedError("Refresh token has been revoked")

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Token missing subject claim")

    user = await get_user_by_id(db, user_id)

    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")

    access_token = create_access_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
    )

    return TokenResponse(access_token=access_token)


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserProfile)
async def me(current_user: User = Depends(get_current_user)):
    """Return the authenticated user's profile."""
    return current_user


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Revoke the refresh token by adding its JTI to the denylist."""
    token = request.cookies.get("refresh_token")
    if token:
        try:
            payload = decode_token(token)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                now = int(datetime.now(timezone.utc).timestamp())
                ttl = max(exp - now, 1)
                await add_to_denylist(jti, ttl)
        except Exception:
            pass  # Token invalid — nothing to revoke

    response.delete_cookie(key="refresh_token", path="/api/auth")
    return {"detail": "Logged out"}
