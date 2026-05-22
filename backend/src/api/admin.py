# =============================================================================
# PH Agent Hub — Admin API Router
# =============================================================================
# Tenant CRUD (admin-only) and User CRUD (admin + manager scoped).
# =============================================================================

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _get_client_ip(request: Request) -> str | None:
    """Resolve the real client IP from X-Real-IP header or fall back to request.client."""
    return request.headers.get("X-Real-IP") or (
        request.client.host if request.client else None
    )

from ..core.dependencies import (
    get_db,
    require_admin,
    require_admin_or_manager,
)
from ..core.exceptions import ForbiddenError, NotFoundError, ValidationError
from ..core.pagination import PaginatedResponse
from ..db.orm.users import User as UserORM
from ..services.audit_service import list_audit_logs, write_audit_log
from ..services.tenant_service import (
    create_tenant as _svc_create_tenant,
    delete_tenant as _svc_delete_tenant,
    force_delete_tenant as _svc_force_delete_tenant,
    get_tenant_by_id as _svc_get_tenant_by_id,
    list_tenants as _svc_list_tenants,
    update_tenant as _svc_update_tenant,
)
from ..services.usage_service import list_usage_logs, get_tenant_aggregates, get_user_aggregates
from ..services.settings_service import get_all_settings, set_settings
from ..services.user_service import (
    create_user as _svc_create_user,
    delete_user as _svc_delete_user,
    get_user_by_id,
    list_users as _svc_list_users,
    update_user as _svc_update_user,
)
from ..services.model_service import (
    create_model as _svc_create_model,
    delete_model as _svc_delete_model,
    get_model_by_id,
    list_models as _svc_list_models,
    update_model as _svc_update_model,
)
from ..services.tool_service import (
    create_tool as _svc_create_tool,
    delete_tool as _svc_delete_tool,
    get_tool_by_id,
    list_tools as _svc_list_tools,
    update_tool as _svc_update_tool,
)
from ..services.template_service import (
    create_template as _svc_create_template,
    delete_template as _svc_delete_template,
    get_template_by_id,
    list_templates as _svc_list_templates,
    update_template as _svc_update_template,
)
from ..services.skill_service import (
    create_skill as _svc_create_skill,
    delete_skill as _svc_delete_skill,
    get_skill_by_id,
    list_skill_tools as _svc_list_skill_tools,
    list_skills as _svc_list_skills,
    update_skill as _svc_update_skill,
)
from ..services.group_service import (
    add_member as _svc_add_member,
    assign_model_to_group as _svc_assign_model_to_group,
    assign_tool_to_group as _svc_assign_tool_to_group,
    create_group as _svc_create_group,
    delete_group as _svc_delete_group,
    get_group_by_id,
    list_group_members as _svc_list_group_members,
    list_group_models as _svc_list_group_models,
    list_group_tools as _svc_list_group_tools,
    list_groups as _svc_list_groups,
    list_model_groups as _svc_list_model_groups,
    list_tool_groups as _svc_list_tool_groups,
    list_user_groups as _svc_list_user_groups,
    remove_member as _svc_remove_member,
    remove_model_from_group as _svc_remove_model_from_group,
    remove_tool_from_group as _svc_remove_tool_from_group,
    update_group as _svc_update_group,
)
from ..services import memory_service

router = APIRouter(prefix="/admin", tags=["admin"])

# =============================================================================
# Pydantic Schemas — Analytics & Audit (Phase 9)
# =============================================================================


class UsageLogResponse(BaseModel):
    id: str
    tenant_id: str
    tenant_name: str | None = None
    user_id: str
    user_email: str | None = None
    user_full_name: str | None = None
    model_id: str
    model_name: str | None = None
    provider: str | None = None
    tokens_in: int
    tokens_out: int
    cache_hit_tokens: int | None = None
    cost: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    id: str
    tenant_id: str | None
    tenant_name: str | None = None
    actor_id: str
    actor_role: str
    actor_email: str | None = None
    actor_full_name: str | None = None
    action: str
    target_type: str | None
    target_id: str | None
    payload: dict | None
    ip_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# =============================================================================
# Pydantic Schemas
# =============================================================================


class TenantCreate(BaseModel):
    name: str


class TenantUpdate(BaseModel):
    name: str


class TenantResponse(BaseModel):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost: float = 0.0

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: str
    password: str
    display_name: str
    tenant_id: str | None = None
    role: Literal["admin", "manager", "user"] = "user"


class UserUpdate(BaseModel):
    email: str | None = None
    password: str | None = None
    display_name: str | None = None
    role: Literal["admin", "manager", "user"] | None = None
    is_active: bool | None = None
    tenant_id: str | None = None


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    tenant_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost: float = 0.0

    model_config = {"from_attributes": True}


class ModelCreate(BaseModel):
    tenant_id: str | None = None  # admin only — fallback to current_user.tenant_id
    name: str
    model_id: str
    provider: str
    api_key: str
    base_url: str | None = None
    enabled: bool = True
    is_public: bool = False
    max_tokens: int = 4096
    temperature: float = 0.7
    thinking_enabled: bool = False
    reasoning_effort: str | None = None
    follow_up_questions_enabled: bool = False
    context_length: int | None = None
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    cache_hit_price_per_1m: float | None = None


class ModelUpdate(BaseModel):
    tenant_id: str | None = None  # admin only
    name: str | None = None
    model_id: str | None = None
    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    is_public: bool | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    thinking_enabled: bool | None = None
    reasoning_effort: str | None = None
    follow_up_questions_enabled: bool | None = None
    context_length: int | None = None
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    cache_hit_price_per_1m: float | None = None


class ModelResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    model_id: str
    provider: str
    base_url: str | None
    enabled: bool
    is_public: bool
    max_tokens: int
    temperature: float
    thinking_enabled: bool
    reasoning_effort: str | None = None
    follow_up_questions_enabled: bool = False
    context_length: int | None = None
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    cache_hit_price_per_1m: float | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolCreate(BaseModel):
    tenant_id: str | None = None  # admin only — fallback to current_user.tenant_id
    name: str
    type: str
    config: dict | None = None
    code: str | None = None
    enabled: bool = True
    is_public: bool = False


class ToolUpdate(BaseModel):
    tenant_id: str | None = None  # admin only
    name: str | None = None
    type: str | None = None
    config: dict | None = None
    code: str | None = None
    enabled: bool | None = None
    is_public: bool | None = None


class ToolResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    type: str
    category: str
    config: dict | None
    code: str | None
    enabled: bool
    is_public: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# =============================================================================
# Pydantic Schemas — Groups
# =============================================================================


class GroupCreate(BaseModel):
    name: str


class GroupUpdate(BaseModel):
    name: str


class GroupResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: str

    model_config = {"from_attributes": True}


class GroupModelResponse(BaseModel):
    id: str
    name: str
    provider: str
    enabled: bool

    model_config = {"from_attributes": True}


class GroupToolResponse(BaseModel):
    id: str
    name: str
    type: str
    enabled: bool

    model_config = {"from_attributes": True}


class MemberAdd(BaseModel):
    user_id: str


class ModelAssign(BaseModel):
    model_id: str


class ToolAssign(BaseModel):
    tool_id: str


# =============================================================================
# Pydantic Schemas — Settings
# =============================================================================


class SettingsResponse(BaseModel):
    settings: dict[str, str]


# =============================================================================
# Tenant Endpoints (admin or manager)
# =============================================================================


@router.get("/tenants", response_model=PaginatedResponse[TenantResponse])
async def list_tenants(
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List tenants with aggregate usage stats, search, sorting, pagination.
    Admin sees all tenants; manager sees only their own tenant."""
    if current_user.role == "admin":
        tenants, total = await _svc_list_tenants(
            db, search=search, sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
    else:
        tenant = await _svc_get_tenant_by_id(db, current_user.tenant_id)
        tenants = [tenant] if tenant else []
        total = len(tenants)

    aggregates = await get_tenant_aggregates(db)

    results: list[TenantResponse] = []
    for t in tenants:
        agg = aggregates.get(t.id, {})
        resp = TenantResponse.model_validate(t)
        resp.total_tokens_in = agg.get("total_tokens_in", 0)
        resp.total_tokens_out = agg.get("total_tokens_out", 0)
        resp.total_cost = agg.get("total_cost", 0.0)
        results.append(resp)

    total_pages = max(1, -(-total // page_size)) if current_user.role == "admin" else 1
    return PaginatedResponse(
        items=results, total=total, page=page,
        page_size=page_size, total_pages=total_pages,
    )


@router.post("/tenants", response_model=TenantResponse, status_code=201)
async def create_tenant(
    body: TenantCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Create a new tenant (admin only)."""
    tenant = await _svc_create_tenant(db, body.name)
    await write_audit_log(
        db,
        actor=_admin,
        action="tenant.created",
        target_type="tenant",
        target_id=tenant.id,
        ip_address=_get_client_ip(request),
        tenant_id=None,  # platform-level action
    )
    return TenantResponse.model_validate(tenant)


@router.put("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: str,
    body: TenantUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Update a tenant's name (admin only)."""
    tenant = await _svc_update_tenant(db, tenant_id, body.name)
    await write_audit_log(
        db,
        actor=_admin,
        action="tenant.updated",
        target_type="tenant",
        target_id=tenant_id,
        ip_address=_get_client_ip(request),
        tenant_id=None,
    )
    return TenantResponse.model_validate(tenant)


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: str,
    request: Request,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Delete a tenant (admin only). Set ?force=true to cascade-delete
    all related data (users, sessions, models, tools, etc.) instead of
    blocking when related resources exist."""
    # Safety: Don't allow admins to force-delete their own tenant
    # (it would destroy their own user row and break the session).
    if force and _admin.tenant_id == tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot force-delete your own tenant. "
                   "Ask an admin from a different tenant to perform this action.",
        )
    action = "tenant.force_deleted" if force else "tenant.deleted"
    if force:
        await _svc_force_delete_tenant(db, tenant_id)
    else:
        await _svc_delete_tenant(db, tenant_id)
    await write_audit_log(
        db,
        actor=_admin,
        action=action,
        target_type="tenant",
        target_id=tenant_id,
        ip_address=_get_client_ip(request),
        tenant_id=None,
    )


# =============================================================================
# User Endpoints (admin or manager)
# =============================================================================


@router.get("/users", response_model=PaginatedResponse[UserResponse])
async def list_users(
    tenant_id: str | None = None,
    search: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List users with aggregate usage stats: admin sees all (optionally
    filtered by tenant), manager sees own tenant only.
    Supports server-side search, role/active filtering, sorting, pagination."""
    if current_user.role == "manager":
        users, total = await _svc_list_users(
            db, tenant_id=current_user.tenant_id,
            search=search, role=role, is_active=is_active,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
    else:
        users, total = await _svc_list_users(
            db, tenant_id=tenant_id,
            search=search, role=role, is_active=is_active,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )

    aggregates = await get_user_aggregates(db)

    results: list[UserResponse] = []
    for u in users:
        agg = aggregates.get(u.id, {})
        resp = UserResponse.model_validate(u)
        resp.total_tokens_in = agg.get("total_tokens_in", 0)
        resp.total_tokens_out = agg.get("total_tokens_out", 0)
        resp.total_cost = agg.get("total_cost", 0.0)
        results.append(resp)

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=results, total=total, page=page,
        page_size=page_size, total_pages=total_pages,
    )


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create a user. Admin: any tenant/role. Manager: own tenant, 'user' role only."""
    tenant_id = body.tenant_id or current_user.tenant_id
    if current_user.role == "manager":
        if tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only create users in their own tenant")
        if body.role != "user":
            raise ForbiddenError("Managers can only assign the 'user' role")

    user = await _svc_create_user(
        db,
        tenant_id=tenant_id,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        role=body.role,
    )
    await write_audit_log(
        db,
        actor=current_user,
        action="user.created",
        target_type="user",
        target_id=user.id,
        tenant_id=tenant_id,
        ip_address=_get_client_ip(request),
    )
    return UserResponse.model_validate(user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update a user. Managers scoped to own tenant with role restrictions."""
    target = await get_user_by_id(db, user_id)

    if current_user.role == "manager":
        # Manager can only modify users in their own tenant
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only modify users in their own tenant")
        # Manager cannot change tenant_id
        if body.tenant_id is not None and body.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers cannot change a user's tenant")
        # Manager cannot assign admin or manager roles
        if body.role is not None and body.role in ("admin", "manager"):
            raise ForbiddenError("Managers cannot assign admin or manager roles")
        # Manager cannot modify admins or managers
        if target.role in ("admin", "manager"):
            raise ForbiddenError("Managers cannot modify admin or manager users")

    # Build update kwargs from non-None fields
    update_kwargs: dict = {}
    if body.email is not None:
        update_kwargs["email"] = body.email
    if body.password is not None:
        update_kwargs["password"] = body.password
    if body.display_name is not None:
        update_kwargs["display_name"] = body.display_name
    if body.role is not None:
        update_kwargs["role"] = body.role
    if body.is_active is not None:
        update_kwargs["is_active"] = body.is_active
    if body.tenant_id is not None:
        update_kwargs["tenant_id"] = body.tenant_id

    user = await _svc_update_user(db, user_id, **update_kwargs)

    # Determine action key
    if body.role is not None and body.role != target.role:
        action = "user.role_changed"
    elif body.is_active is not None and body.is_active == False:  # noqa: E712
        action = "user.deactivated"
    else:
        action = "user.updated"

    await write_audit_log(
        db,
        actor=current_user,
        action=action,
        target_type="user",
        target_id=user_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete a user. Admin: any. Manager: own tenant, non-admin/manager only."""
    target = await get_user_by_id(db, user_id)

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only delete users in their own tenant")
        if target.role in ("admin", "manager"):
            raise ForbiddenError("Managers cannot delete admin or manager users")

    await _svc_delete_user(db, user_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="user.deleted",
        target_type="user",
        target_id=user_id,
        tenant_id=target.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Model Endpoints (admin or manager)
# =============================================================================


@router.get("/models", response_model=PaginatedResponse[ModelResponse])
async def list_models(
    tenant_id: str | None = None,
    search: str | None = None,
    provider: str | None = None,
    enabled: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List models. Admin sees all (optionally filtered by tenant),
    manager sees own tenant only. Supports search, provider/enabled
    filtering, sorting, pagination."""
    if current_user.role == "manager":
        models, total = await _svc_list_models(
            db, tenant_id=current_user.tenant_id,
            search=search, provider=provider, enabled=enabled,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
    else:
        models, total = await _svc_list_models(
            db, tenant_id=tenant_id,
            search=search, provider=provider, enabled=enabled,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=[ModelResponse.model_validate(m) for m in models],
        total=total, page=page, page_size=page_size, total_pages=total_pages,
    )


@router.post("/models", response_model=ModelResponse, status_code=201)
async def create_model(
    body: ModelCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create a model. Admin can specify tenant_id; manager scoped to own tenant."""
    if current_user.role == "manager" and body.tenant_id and body.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only create models in their own tenant")
    tenant_id = body.tenant_id if current_user.role == "admin" and body.tenant_id else current_user.tenant_id
    model = await _svc_create_model(
        db,
        tenant_id=tenant_id,
        name=body.name,
        model_id=body.model_id,
        provider=body.provider,
        api_key=body.api_key,
        base_url=body.base_url,
        enabled=body.enabled,
        is_public=body.is_public,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        thinking_enabled=body.thinking_enabled,
        follow_up_questions_enabled=body.follow_up_questions_enabled,
        context_length=body.context_length,
        input_price_per_1m=body.input_price_per_1m,
        output_price_per_1m=body.output_price_per_1m,
        cache_hit_price_per_1m=body.cache_hit_price_per_1m,
    )
    await write_audit_log(
        db,
        actor=current_user,
        action="model.created",
        target_type="model",
        target_id=model.id,
        payload={"name": body.name, "provider": body.provider},
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return ModelResponse.model_validate(model)


@router.put("/models/{model_id}", response_model=ModelResponse)
async def update_model(
    model_id: str,
    body: ModelUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update a model. Manager scoped to own tenant."""
    target = await get_model_by_id(db, model_id)

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only modify models in their own tenant")

    update_kwargs: dict = {}
    if body.name is not None:
        update_kwargs["name"] = body.name
    if body.model_id is not None:
        update_kwargs["model_id"] = body.model_id
    if body.provider is not None:
        update_kwargs["provider"] = body.provider
    if body.api_key is not None:
        update_kwargs["api_key"] = body.api_key
    if body.base_url is not None:
        update_kwargs["base_url"] = body.base_url
    if body.enabled is not None:
        update_kwargs["enabled"] = body.enabled
    if body.is_public is not None:
        update_kwargs["is_public"] = body.is_public
    if body.max_tokens is not None:
        update_kwargs["max_tokens"] = body.max_tokens
    if body.temperature is not None:
        update_kwargs["temperature"] = body.temperature
    if body.thinking_enabled is not None:
        update_kwargs["thinking_enabled"] = body.thinking_enabled
    if body.reasoning_effort is not None:
        update_kwargs["reasoning_effort"] = body.reasoning_effort
    if body.follow_up_questions_enabled is not None:
        update_kwargs["follow_up_questions_enabled"] = body.follow_up_questions_enabled
    if body.context_length is not None:
        update_kwargs["context_length"] = body.context_length
    if body.input_price_per_1m is not None:
        update_kwargs["input_price_per_1m"] = body.input_price_per_1m
    if body.output_price_per_1m is not None:
        update_kwargs["output_price_per_1m"] = body.output_price_per_1m
    if body.cache_hit_price_per_1m is not None:
        update_kwargs["cache_hit_price_per_1m"] = body.cache_hit_price_per_1m
    if body.tenant_id is not None:
        if current_user.role == "manager" and body.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only assign models to their own tenant")
        update_kwargs["tenant_id"] = body.tenant_id

    # Pop model_id from kwargs to avoid conflict with the route parameter
    new_api_model_id = update_kwargs.pop("model_id", None)

    model = await _svc_update_model(db, model_id, **update_kwargs)

    if new_api_model_id is not None:
        model.model_id = new_api_model_id
        await db.commit()
        await db.refresh(model)

    action = "model.api_key_updated" if body.api_key is not None else "model.updated"
    await write_audit_log(
        db,
        actor=current_user,
        action="model.api_key_updated",
        target_type="model",
        target_id=model_id,
        tenant_id=target.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return ModelResponse.model_validate(model)


# =============================================================================
# Tool Endpoints (admin or manager)
# =============================================================================


@router.get("/tools", response_model=PaginatedResponse[ToolResponse])
async def list_tools(
    tenant_id: str | None = None,
    search: str | None = None,
    type: str | None = None,
    category: str | None = None,
    enabled: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List tools. Admin sees all (optionally filtered by tenant),
    manager sees own tenant only. Supports search, type/category/enabled
    filtering, sorting, pagination."""
    if current_user.role == "manager":
        tools, total = await _svc_list_tools(
            db, tenant_id=current_user.tenant_id,
            search=search, type=type, category=category, enabled=enabled,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
    else:
        tools, total = await _svc_list_tools(
            db, tenant_id=tenant_id,
            search=search, type=type, category=category, enabled=enabled,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=[ToolResponse.model_validate(t) for t in tools],
        total=total, page=page, page_size=page_size, total_pages=total_pages,
    )


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    model_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete a model. Manager scoped to own tenant."""
    target = await get_model_by_id(db, model_id)

    if target is None:
        raise NotFoundError("Model not found")

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only delete models in their own tenant")

    await _svc_delete_model(db, model_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="model.deleted",
        target_type="model",
        target_id=model_id,
        tenant_id=target.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Tool Endpoints (admin or manager)
# =============================================================================


@router.post("/tools", response_model=ToolResponse, status_code=201)
async def create_tool(
    body: ToolCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create a tool. Admin can specify tenant_id; manager scoped to own tenant."""
    if current_user.role == "manager" and body.tenant_id and body.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only create tools in their own tenant")
    tenant_id = body.tenant_id if current_user.role == "admin" and body.tenant_id else current_user.tenant_id

    # Validate custom tool code before saving
    if body.type == "custom" and body.code:
        from ..tools.custom_tool_executor import validate_tool_code
        try:
            validate_tool_code(body.code)
        except ValueError as exc:
            raise ValidationError(str(exc))

    tool = await _svc_create_tool(
        db,
        tenant_id=tenant_id,
        name=body.name,
        type=body.type,
        config=body.config,
        code=body.code,
        enabled=body.enabled,
        is_public=body.is_public,
    )
    await write_audit_log(
        db,
        actor=current_user,
        action="tool.created",
        target_type="tool",
        target_id=tool.id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return ToolResponse.model_validate(tool)


@router.put("/tools/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: str,
    body: ToolUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update a tool. Manager scoped to own tenant."""
    target = await get_tool_by_id(db, tool_id)

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only modify tools in their own tenant")

    # Validate custom tool code before saving
    resolved_type = body.type if body.type is not None else target.type
    resolved_code = body.code if body.code is not None else target.code
    if resolved_type == "custom" and resolved_code:
        from ..tools.custom_tool_executor import validate_tool_code
        try:
            validate_tool_code(resolved_code)
        except ValueError as exc:
            raise ValidationError(str(exc))

    update_kwargs: dict = {}
    if body.name is not None:
        update_kwargs["name"] = body.name
    if body.type is not None:
        update_kwargs["type"] = body.type
    if body.config is not None:
        update_kwargs["config"] = body.config
    if body.code is not None:
        update_kwargs["code"] = body.code
    if body.enabled is not None:
        update_kwargs["enabled"] = body.enabled
    if body.is_public is not None:
        update_kwargs["is_public"] = body.is_public
    if body.tenant_id is not None:
        if current_user.role == "manager" and body.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only assign tools to their own tenant")
        update_kwargs["tenant_id"] = body.tenant_id

    tool = await _svc_update_tool(db, tool_id, **update_kwargs)

    # Determine action key based on enabled state change
    if body.enabled is not None:
        action = "tool.enabled" if body.enabled else "tool.disabled"
    else:
        action = "tool.updated"

    await write_audit_log(
        db,
        actor=current_user,
        action=action,
        target_type="tool",
        target_id=tool_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return ToolResponse.model_validate(tool)


@router.delete("/tools/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete a tool. Manager scoped to own tenant."""
    target = await get_tool_by_id(db, tool_id)

    if target is None:
        raise NotFoundError("Tool not found")

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only delete tools in their own tenant")

    await _svc_delete_tool(db, tool_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="tool.deleted",
        target_type="tool",
        target_id=tool_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Template Admin Endpoints (admin or manager)
# =============================================================================


class AdminTemplateCreate(BaseModel):
    tenant_id: str | None = None  # admin only — fallback to current_user.tenant_id
    title: str
    description: str | None = None
    system_prompt: str
    scope: str = "tenant"
    assigned_user_id: str | None = None


class AdminTemplateUpdate(BaseModel):
    tenant_id: str | None = None  # admin only
    title: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    scope: str | None = None
    assigned_user_id: str | None = None


class AdminTemplateResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    description: str | None
    system_prompt: str
    scope: str
    assigned_user_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("/templates", response_model=PaginatedResponse[AdminTemplateResponse])
async def admin_list_templates(
    tenant_id: str | None = None,
    search: str | None = None,
    scope: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List templates. Admin sees all (optionally filtered by tenant).
    Manager sees own tenant only. Supports search, scope filtering,
    sorting, pagination."""
    if current_user.role == "admin":
        templates, total = await _svc_list_templates(
            db, tenant_id=tenant_id, current_user=None,
            search=search, scope=scope,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
    else:
        templates, total = await _svc_list_templates(
            db, tenant_id=current_user.tenant_id, current_user=current_user,
            search=search, scope=scope,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=[AdminTemplateResponse.model_validate(t) for t in templates],
        total=total, page=page, page_size=page_size, total_pages=total_pages,
    )


@router.post("/templates", response_model=AdminTemplateResponse, status_code=201)
async def admin_create_template(
    body: AdminTemplateCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create a template. Admin can specify tenant_id; manager scoped to own tenant."""
    tenant_id = body.tenant_id if current_user.role == "admin" and body.tenant_id else current_user.tenant_id
    if current_user.role == "manager" and body.tenant_id and body.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only create templates in their own tenant")
    template = await _svc_create_template(
        db,
        tenant_id=tenant_id,
        title=body.title,
        description=body.description,
        system_prompt=body.system_prompt,
        scope=body.scope,
        assigned_user_id=body.assigned_user_id,
    )
    resp = AdminTemplateResponse.model_validate(template)
    await write_audit_log(
        db,
        actor=current_user,
        action="template.created",
        target_type="template",
        target_id=template.id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return resp


@router.put("/templates/{template_id}", response_model=AdminTemplateResponse)
async def admin_update_template(
    template_id: str,
    body: AdminTemplateUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update a template. Manager scoped to own tenant."""
    target = await get_template_by_id(db, template_id)

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only modify templates in their own tenant")

    update_kwargs: dict = {}
    if body.title is not None:
        update_kwargs["title"] = body.title
    if body.description is not None:
        update_kwargs["description"] = body.description
    if body.system_prompt is not None:
        update_kwargs["system_prompt"] = body.system_prompt
    if body.scope is not None:
        update_kwargs["scope"] = body.scope
    if body.assigned_user_id is not None:
        update_kwargs["assigned_user_id"] = body.assigned_user_id
    if body.tenant_id is not None:
        if current_user.role == "manager" and body.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only assign templates to their own tenant")
        update_kwargs["tenant_id"] = body.tenant_id

    template = await _svc_update_template(
        db, template_id, **update_kwargs
    )
    resp = AdminTemplateResponse.model_validate(template)
    await write_audit_log(
        db,
        actor=current_user,
        action="template.updated",
        target_type="template",
        target_id=template_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return resp


@router.delete("/templates/{template_id}", status_code=204)
async def admin_delete_template(
    template_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete a template. Manager scoped to own tenant."""
    target = await get_template_by_id(db, template_id)

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only delete templates in their own tenant")

    await _svc_delete_template(db, template_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="template.deleted",
        target_type="template",
        target_id=template_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Skill Admin Endpoints (admin or manager)
# =============================================================================


class AdminSkillCreate(BaseModel):
    tenant_id: str | None = None  # admin only — fallback to current_user.tenant_id
    user_id: str | None = None  # required when visibility=user
    title: str
    description: str | None = None
    execution_type: str
    maf_target_key: str | None = None
    visibility: str = "tenant"
    template_id: str | None = None
    default_prompt_id: str | None = None
    default_model_id: str | None = None
    enabled: bool = True
    tool_ids: list[str] | None = None
    cross_session_retrieval_enabled: bool = False
    cross_session_max_snippets: int = 3
    cross_session_min_score: float = 0.30


class AdminSkillUpdate(BaseModel):
    tenant_id: str | None = None  # admin only
    user_id: str | None = None
    title: str | None = None
    description: str | None = None
    execution_type: str | None = None
    maf_target_key: str | None = None
    visibility: str | None = None
    template_id: str | None = None
    default_prompt_id: str | None = None
    default_model_id: str | None = None
    enabled: bool | None = None
    tool_ids: list[str] | None = None
    cross_session_retrieval_enabled: bool | None = None
    cross_session_max_snippets: int | None = None
    cross_session_min_score: float | None = None


class AdminSkillResponse(BaseModel):
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


@router.get("/skills", response_model=PaginatedResponse[AdminSkillResponse])
async def admin_list_skills(
    search: str | None = None,
    execution_type: str | None = None,
    visibility: str | None = None,
    enabled: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List skills. Admin sees all. Manager sees own tenant only.
    Supports search, execution_type/visibility/enabled filtering,
    sorting, pagination."""
    if current_user.role == "admin":
        skills, total = await _svc_list_skills(
            db, tenant_id=None,
            search=search, execution_type=execution_type,
            visibility=visibility, enabled=enabled,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )
    else:
        skills, total = await _svc_list_skills(
            db, tenant_id=current_user.tenant_id,
            search=search, execution_type=execution_type,
            visibility=visibility, enabled=enabled,
            sort_by=sort_by, sort_dir=sort_dir,
            page=page, page_size=page_size,
        )

    resp_list: list[AdminSkillResponse] = []
    for s in skills:
        skill_tools = await _svc_list_skill_tools(db, s.id)
        resp = AdminSkillResponse.model_validate(s)
        resp.tool_ids = [t.tool_id for t in skill_tools]
        resp_list.append(resp)

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=resp_list, total=total, page=page,
        page_size=page_size, total_pages=total_pages,
    )


@router.post("/skills", response_model=AdminSkillResponse, status_code=201)
async def admin_create_skill(
    body: AdminSkillCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create a skill. Admin can specify tenant_id and user_id.
    Manager scoped to own tenant, creates tenant-shared skills."""
    tenant_id = body.tenant_id if current_user.role == "admin" and body.tenant_id else current_user.tenant_id
    if current_user.role == "manager" and body.tenant_id and body.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only create skills in their own tenant")
    skill = await _svc_create_skill(
        db,
        tenant_id=tenant_id,
        user_id=body.user_id,  # None = tenant-shared; set for personal skills
        title=body.title,
        description=body.description,
        execution_type=body.execution_type,
        maf_target_key=body.maf_target_key,
        visibility=body.visibility,
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
    resp = AdminSkillResponse.model_validate(skill)
    resp.tool_ids = [t.tool_id for t in tools]
    await write_audit_log(
        db,
        actor=current_user,
        action="skill.created",
        target_type="skill",
        target_id=skill.id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return resp


@router.put("/skills/{skill_id}", response_model=AdminSkillResponse)
async def admin_update_skill(
    skill_id: str,
    body: AdminSkillUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update a skill. Manager scoped to own tenant."""
    target = await get_skill_by_id(db, skill_id)

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only modify skills in their own tenant")

    update_kwargs: dict = {}
    for field in (
        "title", "description", "execution_type", "maf_target_key",
        "visibility", "template_id", "default_prompt_id", "default_model_id",
        "enabled",
        "cross_session_retrieval_enabled", "cross_session_max_snippets",
        "cross_session_min_score",
    ):
        val = getattr(body, field, None)
        if val is not None:
            update_kwargs[field] = val

    if body.tenant_id is not None:
        if current_user.role == "manager" and body.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only assign skills to their own tenant")
        update_kwargs["tenant_id"] = body.tenant_id
    if body.user_id is not None:
        update_kwargs["user_id"] = body.user_id

    skill = await _svc_update_skill(
        db, skill_id, tool_ids=body.tool_ids, **update_kwargs
    )
    tools = await _svc_list_skill_tools(db, skill.id)
    resp = AdminSkillResponse.model_validate(skill)
    resp.tool_ids = [t.tool_id for t in tools]
    await write_audit_log(
        db,
        actor=current_user,
        action="skill.updated",
        target_type="skill",
        target_id=skill_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return resp


@router.delete("/skills/{skill_id}", status_code=204)
async def admin_delete_skill(
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete a skill. Manager scoped to own tenant."""
    target = await get_skill_by_id(db, skill_id)

    if current_user.role == "manager":
        if target.tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only delete skills in their own tenant")

    await _svc_delete_skill(db, skill_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="skill.deleted",
        target_type="skill",
        target_id=skill_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Analytics & Audit Endpoints (Phase 9)
# =============================================================================


@router.get("/usage", response_model=PaginatedResponse[UsageLogResponse])
async def get_usage(
    tenant_id: str | None = None,
    user_id: str | None = None,
    search: str | None = None,
    provider: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List usage logs. Admin sees all; manager sees own tenant only.
    Supports search, provider/date filtering, sorting, pagination."""
    if current_user.role == "manager":
        tenant_id = current_user.tenant_id

    logs, total = await list_usage_logs(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        search=search,
        provider=provider,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=[UsageLogResponse.model_validate(log) for log in logs],
        total=total, page=page, page_size=page_size, total_pages=total_pages,
    )


@router.get("/audit", response_model=PaginatedResponse[AuditLogResponse])
async def get_audit(
    search: str | None = None,
    action: str | None = None,
    actor_id: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """List audit logs (admin only). Supports search, action/actor filtering,
    sorting, pagination."""
    logs, total = await list_audit_logs(
        db,
        search=search,
        action=action,
        actor_id=actor_id,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=[AuditLogResponse.model_validate(log) for log in logs],
        total=total, page=page, page_size=page_size, total_pages=total_pages,
    )


@router.get("/logs", response_model=list[dict])
async def get_logs(
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Stub: activity/error logs endpoint.

    TODO (Phase 10+): Implement a proper activity/error log storage
    strategy.  Currently returns an empty list — no dedicated logs
    table exists in the data model.
    """
    return []


# =============================================================================
# Group Endpoints (admin or manager)
# =============================================================================


@router.post("/groups", response_model=GroupResponse, status_code=201)
async def create_group(
    body: GroupCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create a user group. Manager always scoped to own tenant."""
    group = await _svc_create_group(
        db, tenant_id=current_user.tenant_id, name=body.name
    )
    await write_audit_log(
        db,
        actor=current_user,
        action="group.created",
        target_type="group",
        target_id=group.id,
        payload={"name": body.name},
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return GroupResponse.model_validate(group)


@router.get("/groups/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Get a group by ID."""
    group = await get_group_by_id(db, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and group.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only view groups in their own tenant")
    return GroupResponse.model_validate(group)


@router.put("/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: str,
    body: GroupUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update a group's name."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify groups in their own tenant")

    group = await _svc_update_group(db, group_id, body.name)
    await write_audit_log(
        db,
        actor=current_user,
        action="group.updated",
        target_type="group",
        target_id=group_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return GroupResponse.model_validate(group)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete a group (cascades to members and model assignments)."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only delete groups in their own tenant")

    await _svc_delete_group(db, group_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="group.deleted",
        target_type="group",
        target_id=group_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Group Membership Endpoints (admin or manager)
# =============================================================================


@router.get("/groups/{group_id}/members", response_model=list[GroupMemberResponse])
async def list_group_members(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List members of a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only view groups in their own tenant")

    members = await _svc_list_group_members(db, group_id)
    return [GroupMemberResponse.model_validate(m) for m in members]


@router.post("/groups/{group_id}/members", status_code=201)
async def add_member(
    group_id: str,
    body: MemberAdd,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Add a user to a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify groups in their own tenant")

    await _svc_add_member(db, group_id, body.user_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="group.member_added",
        target_type="group",
        target_id=group_id,
        payload={"user_id": body.user_id},
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    group_id: str,
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Remove a user from a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify groups in their own tenant")

    await _svc_remove_member(db, group_id, user_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="group.member_removed",
        target_type="group",
        target_id=group_id,
        payload={"user_id": user_id},
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Group-Model Assignment Endpoints (admin or manager)
# =============================================================================


@router.get("/groups/{group_id}/models", response_model=list[GroupModelResponse])
async def list_group_models(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List models assigned to a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only view groups in their own tenant")

    models = await _svc_list_group_models(db, group_id)
    return [GroupModelResponse.model_validate(m) for m in models]


@router.post("/groups/{group_id}/models", status_code=201)
async def assign_model_to_group(
    group_id: str,
    body: ModelAssign,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Assign a model to a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify groups in their own tenant")

    await _svc_assign_model_to_group(db, group_id, body.model_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="group.model_assigned",
        target_type="group",
        target_id=group_id,
        payload={"model_id": body.model_id},
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


@router.delete("/groups/{group_id}/models/{model_id}", status_code=204)
async def remove_model_from_group(
    group_id: str,
    model_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Remove a model from a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify groups in their own tenant")

    await _svc_remove_model_from_group(db, group_id, model_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="group.model_removed",
        target_type="group",
        target_id=group_id,
        payload={"model_id": model_id},
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Group-Tool Assignment Endpoints (admin or manager)
# =============================================================================


@router.get("/groups/{group_id}/tools", response_model=list[GroupToolResponse])
async def list_group_tools(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List tools assigned to a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only view groups in their own tenant")

    tools = await _svc_list_group_tools(db, group_id)
    return [GroupToolResponse.model_validate(t) for t in tools]


@router.post("/groups/{group_id}/tools", status_code=201)
async def assign_tool_to_group(
    group_id: str,
    body: ToolAssign,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Assign a tool to a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify groups in their own tenant")

    await _svc_assign_tool_to_group(db, group_id, body.tool_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="group.tool_assigned",
        target_type="group",
        target_id=group_id,
        payload={"tool_id": body.tool_id},
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


@router.delete("/groups/{group_id}/tools/{tool_id}", status_code=204)
async def remove_tool_from_group(
    group_id: str,
    tool_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Remove a tool from a group."""
    target = await get_group_by_id(db, group_id)
    if target is None:
        raise NotFoundError("Group not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify groups in their own tenant")

    await _svc_remove_tool_from_group(db, group_id, tool_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="group.tool_removed",
        target_type="group",
        target_id=group_id,
        payload={"tool_id": tool_id},
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Helper Endpoints — user groups, model groups & tool groups
# =============================================================================


@router.get("/users/{user_id}/groups", response_model=list[GroupResponse])
async def list_user_groups(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List groups a user belongs to."""
    groups = await _svc_list_user_groups(db, user_id)
    if current_user.role == "manager":
        groups = [g for g in groups if g.tenant_id == current_user.tenant_id]
    return [GroupResponse.model_validate(g) for g in groups]


@router.get("/models/{model_id}/groups", response_model=list[GroupResponse])
async def list_model_groups(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List groups a model is assigned to."""
    groups = await _svc_list_model_groups(db, model_id)
    if current_user.role == "manager":
        groups = [g for g in groups if g.tenant_id == current_user.tenant_id]
    return [GroupResponse.model_validate(g) for g in groups]


@router.get("/tools/{tool_id}/groups", response_model=list[GroupResponse])
async def list_tool_groups(
    tool_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List groups a tool is assigned to."""
    groups = await _svc_list_tool_groups(db, tool_id)
    if current_user.role == "manager":
        groups = [g for g in groups if g.tenant_id == current_user.tenant_id]
    return [GroupResponse.model_validate(g) for g in groups]


# =============================================================================
# Memory Admin Endpoints (admin or manager)
# =============================================================================


class AdminMemoryResponse(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    session_id: str | None
    key: str
    value: str
    source: str
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("/memories", response_model=PaginatedResponse[AdminMemoryResponse])
async def admin_list_memories(
    tenant_id: str | None = None,
    user_id: str | None = None,
    search: str | None = None,
    source: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List all memory entries. Admin sees all (optionally filtered).
    Manager sees own tenant only. Supports search, source filtering,
    sorting, pagination."""
    if current_user.role == "manager":
        tenant_id = current_user.tenant_id

    entries, total = await memory_service.list_all_memories(
        db, tenant_id=tenant_id, user_id=user_id,
        search=search, source=source,
        sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size,
    )

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=[AdminMemoryResponse.model_validate(e) for e in entries],
        total=total, page=page, page_size=page_size, total_pages=total_pages,
    )


@router.delete("/memories/{memory_id}", status_code=204)
async def admin_delete_memory(
    memory_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete a memory entry.  Admin: any.  Manager: own tenant only."""
    from ..db.orm.memory import Memory as MemoryORM
    result = await db.execute(
        select(MemoryORM).where(MemoryORM.id == memory_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise NotFoundError("Memory entry not found")
    if current_user.role == "manager" and entry.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only delete memories in their own tenant")

    await memory_service.admin_delete_memory(db, memory_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="memory.deleted",
        target_type="memory",
        target_id=memory_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Admin Session Management
# =============================================================================


class AdminSessionResponse(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    title: str
    is_pinned: bool
    is_temporary: bool
    tags: list[dict] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("/sessions", response_model=PaginatedResponse[AdminSessionResponse])
async def admin_list_sessions(
    tenant_id: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    is_pinned: bool | None = None,
    is_temporary: bool | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List sessions with filtering, sorting, pagination.

    Admin: can see all tenants. Manager: only own tenant.
    Supports search, tag/pinned/temporary filtering, sorting, pagination.
    """
    # Manager scope check
    if current_user.role == "manager":
        if tenant_id and tenant_id != current_user.tenant_id:
            raise ForbiddenError("Managers can only view sessions in their own tenant")
        tenant_id = current_user.tenant_id

    from ..services.session_service import list_admin_sessions as _svc_list_admin_sessions

    sessions, total = await _svc_list_admin_sessions(
        db,
        tenant_id=tenant_id,
        tag=tag,
        search=search,
        is_pinned=is_pinned,
        is_temporary=is_temporary,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )

    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=[
            AdminSessionResponse(
                id=s.id,
                tenant_id=s.tenant_id,
                user_id=s.user_id,
                title=s.title,
                is_pinned=s.is_pinned,
                is_temporary=s.is_temporary,
                tags=[
                    {"id": t.id, "name": t.name, "color": t.color}
                    for t in (s.tags or [])
                ],
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in sessions
        ],
        total=total, page=page, page_size=page_size, total_pages=total_pages,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def admin_delete_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete a session. Admin: any. Manager: own tenant only."""
    from ..db.orm.sessions import Session as SessionORM

    result = await db.execute(
        select(SessionORM).where(SessionORM.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise NotFoundError("Session not found")

    if current_user.role == "manager" and session.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only delete sessions in their own tenant")

    await session_service.delete_session(db, session_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="session.deleted",
        target_type="session",
        target_id=session_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


# =============================================================================
# Settings Endpoints (admin only)
# =============================================================================


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Get all application settings (admin only)."""
    settings = await get_all_settings(db)
    return SettingsResponse(settings=settings)


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: dict[str, str],
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Bulk update application settings (admin only)."""
    settings = await set_settings(db, body)
    return SettingsResponse(settings=settings)
