# =============================================================================
# PH Agent Hub — Prompts API Router (User-Facing)
# =============================================================================

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_user, get_db
from ..core.exceptions import ForbiddenError, NotFoundError
from ..db.orm.users import User as UserORM
from ..services.prompt_service import (
    create_prompt as _svc_create_prompt,
    delete_prompt as _svc_delete_prompt,
    get_prompt_by_id,
    list_prompts as _svc_list_prompts,
    update_prompt as _svc_update_prompt,
)

router = APIRouter(prefix="/prompts", tags=["prompts"])


class PromptCreate(BaseModel):
    title: str
    description: str
    content: str
    template_id: str | None = None


class PromptUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    content: str | None = None
    template_id: str | None = None


class PromptResponse(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    template_id: str | None
    title: str
    description: str
    content: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[PromptResponse])
async def list_prompts(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Return all prompts owned by the current user within their tenant."""
    prompts = await _svc_list_prompts(
        db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )
    return [PromptResponse.model_validate(p) for p in prompts]


@router.post("", response_model=PromptResponse, status_code=201)
async def create_prompt(
    body: PromptCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Create a new prompt owned by the current user."""
    prompt = await _svc_create_prompt(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=body.title,
        description=body.description,
        content=body.content,
        template_id=body.template_id,
    )
    return PromptResponse.model_validate(prompt)


@router.put("/{prompt_id}", response_model=PromptResponse)
async def update_prompt(
    prompt_id: str,
    body: PromptUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Update a prompt. Only the owner may modify."""
    prompt = await get_prompt_by_id(db, prompt_id)
    if prompt is None:
        raise NotFoundError("Prompt not found")

    if prompt.user_id != current_user.id:
        raise ForbiddenError("Only the prompt owner can modify this prompt")

    if prompt.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Cannot modify a prompt from a different tenant")

    update_kwargs: dict = {}
    if body.title is not None:
        update_kwargs["title"] = body.title
    if body.description is not None:
        update_kwargs["description"] = body.description
    if body.content is not None:
        update_kwargs["content"] = body.content
    if body.template_id is not None:
        update_kwargs["template_id"] = body.template_id

    updated = await _svc_update_prompt(db, prompt_id, **update_kwargs)
    return PromptResponse.model_validate(updated)


@router.delete("/{prompt_id}", status_code=204)
async def delete_prompt(
    prompt_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Delete a prompt. Only the owner may delete."""
    prompt = await get_prompt_by_id(db, prompt_id)
    if prompt is None:
        raise NotFoundError("Prompt not found")

    if prompt.user_id != current_user.id:
        raise ForbiddenError("Only the prompt owner can delete this prompt")

    if prompt.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Cannot delete a prompt from a different tenant")

    await _svc_delete_prompt(db, prompt_id)
