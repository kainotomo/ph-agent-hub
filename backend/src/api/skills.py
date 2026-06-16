# =============================================================================
# PH Agent Hub — Skills API Router (User-Facing)
# =============================================================================

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_user, get_db
from ..core.exceptions import ForbiddenError, NotFoundError
from ..db.orm.users import User as UserORM
from ..services.skill_service import (
    create_skill as _svc_create_skill,
    delete_skill as _svc_delete_skill,
    get_skill_by_id,
    list_skill_tools as _svc_list_skill_tools,
    list_skills as _svc_list_skills,
    update_skill as _svc_update_skill,
)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillCreate(BaseModel):
    title: str
    description: str | None = None
    execution_type: str
    maf_target_key: str | None = None
    template_id: str | None = None
    default_prompt_id: str | None = None
    default_model_id: str | None = None
    enabled: bool = True
    tool_ids: list[str] | None = None
    cross_session_retrieval_enabled: bool = False
    cross_session_max_snippets: int = 3
    cross_session_min_score: float = 0.30


class SkillUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    execution_type: str | None = None
    maf_target_key: str | None = None
    template_id: str | None = None
    default_prompt_id: str | None = None
    default_model_id: str | None = None
    enabled: bool | None = None
    tool_ids: list[str] | None = None
    cross_session_retrieval_enabled: bool | None = None
    cross_session_max_snippets: int | None = None
    cross_session_min_score: float | None = None


class SkillResponse(BaseModel):
    id: str
    tenant_id: str
    user_id: str | None
    title: str
    description: str | None
    execution_type: str
    maf_target_key: str | None
    visibility: str
    template_id: str | None
    default_prompt_id: str | None
    default_model_id: str | None
    enabled: bool
    cross_session_retrieval_enabled: bool
    cross_session_max_snippets: int
    cross_session_min_score: float
    created_at: datetime
    updated_at: datetime
    tool_ids: list[str] = []

    model_config = {"from_attributes": True}


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Return tenant-shared skills + user's own personal skills."""
    skills, _ = await _svc_list_skills(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )

    result: list[SkillResponse] = []
    for s in skills:
        tools = await _svc_list_skill_tools(db, s.id)
        resp = SkillResponse.model_validate(s)
        resp.tool_ids = [t.tool_id for t in tools]
        result.append(resp)

    return result


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    body: SkillCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Create a user-owned personal skill."""
    skill = await _svc_create_skill(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=body.title,
        description=body.description,
        execution_type=body.execution_type,
        maf_target_key=body.maf_target_key,
        visibility="user",
        template_id=body.template_id,
        default_prompt_id=body.default_prompt_id,
        default_model_id=body.default_model_id,
        enabled=body.enabled,
        tool_ids=body.tool_ids,
        cross_session_retrieval_enabled=body.cross_session_retrieval_enabled,
        cross_session_max_snippets=body.cross_session_max_snippets,
        cross_session_min_score=body.cross_session_min_score,
    )
    tools = await _svc_list_skill_tools(db, skill.id)
    resp = SkillResponse.model_validate(skill)
    resp.tool_ids = [t.tool_id for t in tools]
    return resp


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    body: SkillUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Update a personal skill. Cannot modify tenant-shared skills."""
    skill = await get_skill_by_id(db, skill_id)
    if skill is None:
        raise NotFoundError("Skill not found")

    if skill.user_id != current_user.id:
        raise ForbiddenError("Only the skill owner can modify this skill")

    if skill.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Cannot modify a skill from a different tenant")

    update_kwargs: dict = {}
    for field in (
        "title", "description", "execution_type", "maf_target_key",
        "template_id", "default_prompt_id", "default_model_id", "enabled",
        "cross_session_retrieval_enabled", "cross_session_max_snippets",
        "cross_session_min_score",
    ):
        val = getattr(body, field, None)
        if val is not None:
            update_kwargs[field] = val

    tool_ids = body.tool_ids
    updated = await _svc_update_skill(db, skill_id, tool_ids=tool_ids, **update_kwargs)
    tools = await _svc_list_skill_tools(db, updated.id)
    resp = SkillResponse.model_validate(updated)
    resp.tool_ids = [t.tool_id for t in tools]
    return resp


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Delete a personal skill. Cannot delete tenant-shared skills."""
    skill = await get_skill_by_id(db, skill_id)
    if skill is None:
        raise NotFoundError("Skill not found")

    if skill.user_id != current_user.id:
        raise ForbiddenError("Only the skill owner can delete this skill")

    if skill.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Cannot delete a skill from a different tenant")

    await _svc_delete_skill(db, skill_id)
