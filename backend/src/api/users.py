# =============================================================================
# PH Agent Hub — Users API Router (Current User Profile)
# =============================================================================

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_user, get_db
from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.users import User as UserORM
from ..services.model_service import get_model_by_id, list_models as _svc_list_models
from ..services.user_preferences import (
    DEFAULT_NOTIFICATION_PREFS,
    get_notification_prefs,
    normalize_notification_prefs,
    update_notification_prefs,
)
from ..services.user_service import update_user_default_model

router = APIRouter(prefix="/users", tags=["users"])


class NotificationPreferences(BaseModel):
    """Per-user browser-notification preferences.

    Defaults match ``services.user_preferences.DEFAULT_NOTIFICATION_PREFS``;
    unknown keys are ignored so an older/newer client cannot break the write.
    """

    enabled: bool = False
    on_completion: bool = True
    on_failure: bool = True
    sound: bool = False

    model_config = {"extra": "ignore"}


class UserMeResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    tenant_id: str
    is_active: bool
    default_model_id: str | None
    notification_prefs: NotificationPreferences = Field(
        default_factory=lambda: NotificationPreferences(
            **DEFAULT_NOTIFICATION_PREFS
        )
    )
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("notification_prefs", mode="before")
    @classmethod
    def _normalize_notification_prefs(cls, value):
        if isinstance(value, NotificationPreferences):
            return value
        return NotificationPreferences(**normalize_notification_prefs(value))


class SetDefaultModelRequest(BaseModel):
    model_id: str | None = None


@router.get("/me", response_model=UserMeResponse)
async def get_me(
    current_user: UserORM = Depends(get_current_user),
):
    """Return the current user's profile including default_model_id."""
    return UserMeResponse.model_validate(current_user)


@router.put("/me/default-model")
async def set_default_model(
    body: SetDefaultModelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Set the current user's default model.

    Validates that the model is accessible to the user.
    Set model_id to null to clear the default.
    """
    if body.model_id is not None:
        # Validate the model exists
        model = await get_model_by_id(db, body.model_id)
        if model is None:
            raise NotFoundError("Model not found")

        # Validate the model is accessible to this user
        accessible_models, _ = await _svc_list_models(
            db, tenant_id=current_user.tenant_id, user_id=current_user.id
        )
        accessible_ids = {m.id for m in accessible_models}
        if body.model_id not in accessible_ids:
            raise ValidationError("Model is not accessible to you")

    await update_user_default_model(db, current_user.id, body.model_id)
    return {"default_model_id": body.model_id}


@router.put("/me/notifications", response_model=NotificationPreferences)
async def set_notification_prefs(
    body: NotificationPreferences,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Persist the current user's browser-notification preferences.

    Follows the PUT convention (backend-architecture §3.22): omitted fields
    keep their stored value, so a client may send only the switch it changed.
    """
    current = await get_notification_prefs(db, current_user.id)
    merged = {**current, **body.model_dump(exclude_unset=True)}
    prefs = await update_notification_prefs(db, current_user.id, merged)
    return NotificationPreferences(**prefs)
