# =============================================================================
# PH Agent Hub — Templates API Router (User-Facing)
# =============================================================================

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_user, get_db
from ..db.orm.users import User as UserORM
from ..services.template_service import (
    list_templates as _svc_list_templates,
)

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    description: str | None
    system_prompt: str
    scope: str
    assigned_user_id: str | None
    # Issue #595 — per-run budget configured on the template (may be None).
    agent_config: dict | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[TemplateResponse])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Return templates visible to the requesting user within their tenant."""
    templates, _ = await _svc_list_templates(
        db, tenant_id=current_user.tenant_id, current_user=current_user
    )
    return [TemplateResponse.model_validate(t) for t in templates]
