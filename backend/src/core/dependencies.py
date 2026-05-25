# =============================================================================
# PH Agent Hub — FastAPI Dependencies (Auth Middleware)
# =============================================================================
# Reusable dependency callables for injecting the current user and DB session.
# =============================================================================

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_db as _get_db
from ..db.orm.users import User
from ..services.user_service import get_user_by_id
from .exceptions import ForbiddenError, UnauthorizedError
from .jwt import decode_token, decode_guest_token

# ---------------------------------------------------------------------------
# OAuth2 scheme — tells OpenAPI that /auth/login issues bearer tokens
# ---------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ---------------------------------------------------------------------------
# DB session dependency (re-exported for consistency)
# ---------------------------------------------------------------------------
get_db = _get_db


# ---------------------------------------------------------------------------
# get_current_user — JWT auth guard for protected endpoints
# ---------------------------------------------------------------------------
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    """Decode JWT, load user from DB, raise 401 if anything is wrong."""
    try:
        payload = decode_token(token)
    except JWTError:
        raise UnauthorizedError("Invalid or expired token")

    user_id: str | None = payload.get("sub")
    if user_id is None:
        raise UnauthorizedError("Token missing subject claim")

    user = await get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise UnauthorizedError("User account is inactive")

    return user


# ---------------------------------------------------------------------------
# Guest (widget) auth — no user account required
# ---------------------------------------------------------------------------


class GuestContext:
    """Minimal context representing an anonymous widget visitor.

    This is used instead of a full User object for embedded chat sessions.
    """

    def __init__(self, tenant_id: str, embed_config_id: str, session_id: str = ""):
        self.tenant_id = tenant_id
        self.embed_config_id = embed_config_id
        self.session_id = session_id
        self.id = f"guest:{embed_config_id}"
        self.is_guest = True


async def get_guest_context(
    token: str = Depends(oauth2_scheme),
) -> GuestContext:
    """Validate a guest token and return the embed context.

    Used by widget endpoints where no PH Agent Hub user exists.
    """
    try:
        payload = decode_guest_token(token)
    except JWTError:
        raise UnauthorizedError("Invalid or expired guest token")

    if payload.get("type") != "guest":
        raise UnauthorizedError("Token is not a guest token")

    tenant_id = payload.get("tenant_id")
    embed_config_id = payload.get("sub")
    session_id = payload.get("session_id", "")

    if not tenant_id or not embed_config_id:
        raise UnauthorizedError("Guest token missing required claims")

    return GuestContext(tenant_id=tenant_id, embed_config_id=embed_config_id, session_id=session_id)


# ---------------------------------------------------------------------------
# Unified auth — accepts either user JWT or guest token
# ---------------------------------------------------------------------------


async def get_current_user_or_guest(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | GuestContext:
    """Try user JWT first, fall back to guest token.

    Returns either a full User object (authenticated) or a GuestContext
    (widget visitor).  Used by endpoints shared between the main app
    and the embed widget.
    """
    # First, try decoding as a standard user JWT
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        if user_id:
            user = await get_user_by_id(db, user_id)
            if user and user.is_active:
                return user
    except JWTError:
        pass

    # Fall back to guest token
    return await get_guest_context(token)


# ---------------------------------------------------------------------------
# Role-based access control dependencies
# ---------------------------------------------------------------------------


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Allow only users with the 'admin' role."""
    if current_user.role != "admin":
        raise ForbiddenError("Admin access required")
    return current_user


async def require_admin_or_manager(
    current_user: User = Depends(get_current_user),
) -> User:
    """Allow users with 'admin' or 'manager' role."""
    if current_user.role not in ("admin", "manager"):
        raise ForbiddenError("Admin or manager access required")
    return current_user
