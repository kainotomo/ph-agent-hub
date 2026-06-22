# =============================================================================
# PH Agent Hub — Admin API Router
# =============================================================================
# Tenant CRUD (admin-only) and User CRUD (admin + manager scoped).
# =============================================================================

from datetime import datetime
from typing import Literal
import uuid

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
from ..core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ..core.pagination import PaginatedResponse
from ..core.redis import store_a2a_oauth_state
from ..db.orm.users import User as UserORM
from ..services.audit_service import list_audit_logs, write_audit_log
from ..services.tenant_service import (
    count_tenants,
    create_tenant as _svc_create_tenant,
    delete_tenant as _svc_delete_tenant,
    force_delete_tenant as _svc_force_delete_tenant,
    get_demo_tenant as _svc_get_demo_tenant,
    get_tenant_by_id as _svc_get_tenant_by_id,
    list_tenants as _svc_list_tenants,
    set_demo_tenant as _svc_set_demo_tenant,
    update_tenant as _svc_update_tenant,
)
from ..services.usage_service import list_usage_logs, get_tenant_aggregates, get_user_aggregates
from ..services.balance_service import (
    add_funds as _svc_add_funds,
    deduct_usage,
    disable_limit as _svc_disable_limit,
    get_balance,
    get_transaction_history,
    get_warning_threshold,
    set_warning_threshold as _svc_set_warning_threshold,
)
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
from ..services.mcp_service import (
    create_mcp_server as _svc_create_mcp_server,
    delete_mcp_server as _svc_delete_mcp_server,
    get_mcp_server as _svc_get_mcp_server,
    list_mcp_servers as _svc_list_mcp_servers,
    update_mcp_server as _svc_update_mcp_server,
    test_mcp_connection as _svc_test_mcp_connection,
    sync_mcp_tools as _svc_sync_mcp_tools,
    decrypt_env_vars,
    decrypt_headers,
    mask_dict,
)
from ..services.a2a_service import (
    create_a2a_server as _svc_create_a2a_server,
    delete_a2a_server as _svc_delete_a2a_server,
    get_a2a_server as _svc_get_a2a_server,
    list_a2a_servers as _svc_list_a2a_servers,
    update_a2a_server as _svc_update_a2a_server,
    test_a2a_connection as _svc_test_a2a_connection,
    sync_a2a_tools as _svc_sync_a2a_tools,
    decrypt_auth_token,
    decrypt_headers as _a2a_decrypt_headers,
    mask_dict as _a2a_mask_dict,
    get_oauth2_tokens_status as _a2a_oauth2_tokens_status,
    decrypt_oauth2_client_secret as _a2a_decrypt_oauth2_client_secret,
)
from ..services.a2a_circuit_breaker import A2ACircuitBreaker
from ..db.orm.a2a_call_logs import A2aCallLog
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
from ..services.embed_service import (
    create_embed_config as _svc_create_embed_config,
    delete_embed_config as _svc_delete_embed_config,
    get_embed_config_by_id as _svc_get_embed_config_by_id,
    list_embed_configs as _svc_list_embed_configs,
    update_embed_config as _svc_update_embed_config,
    regenerate_token as _svc_regenerate_embed_token,
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
    is_demo: bool | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    is_demo: bool
    balance_euros: float | None = None
    warning_threshold_eur: float | None = None
    balance_warning: bool = False
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
    auto_route_eligible: bool = True
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
    auto_route_eligible: bool | None = None
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
    auto_route_eligible: bool = True
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    cache_hit_price_per_1m: float | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolCreate(BaseModel):
    tenant_id: str | None = None  # admin only — fallback to current_user.tenant_id
    name: str
    description: str | None = None
    type: str
    config: dict | None = None
    code: str | None = None
    enabled: bool = True
    is_public: bool = False


class ToolUpdate(BaseModel):
    tenant_id: str | None = None  # admin only
    name: str | None = None
    description: str | None = None
    type: str | None = None
    config: dict | None = None
    code: str | None = None
    enabled: bool | None = None
    is_public: bool | None = None


class ToolResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None = None
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
        # Compute balance_warning: balance is numeric, above 0, but below threshold
        if (
            t.balance_euros is not None
            and t.warning_threshold_eur is not None
            and t.balance_euros > 0
            and t.balance_euros <= t.warning_threshold_eur
        ):
            resp.balance_warning = True
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
    """Create a new tenant (admin only).

    Gated by licensing (Issue #243): free tier allows up to MAX_FREE_TENANTS
    (default 3). A valid Pro license key removes this limit.
    """
    from ..services.license_service import can_create_tenant

    current_count = await count_tenants(db)
    allowed, reason = await can_create_tenant(db, current_count)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=reason,
        )

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
    """Update a tenant's name or mark it as the demo tenant (admin only)."""
    if body.is_demo is True:
        tenant = await _svc_set_demo_tenant(db, tenant_id)
    else:
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
# Balance Endpoints (admin only)
# =============================================================================


class BalanceUpdateRequest(BaseModel):
    amount_eur: float
    reason: str = "admin_adjustment"


class BalanceConfigRequest(BaseModel):
    warning_threshold_eur: float | None = None


class BalanceTransactionResponse(BaseModel):
    id: str
    tenant_id: str
    admin_user_id: str | None = None
    amount_eur: float
    balance_after: float
    reason: str
    reference_type: str | None = None
    reference_id: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.put("/tenants/{tenant_id}/balance", response_model=TenantResponse)
async def update_tenant_balance(
    tenant_id: str,
    body: BalanceUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Add or subtract funds from a tenant's balance (admin only).

    Positive amount_eur = top-up (may enable limit for previously unlimited tenant).
    Negative amount_eur = deduction.
    """
    from decimal import Decimal

    txn = await _svc_add_funds(
        db,
        tenant_id=tenant_id,
        amount_eur=Decimal(str(body.amount_eur)),
        admin_user_id=_admin.id,
        reason=body.reason,
    )

    # Reload tenant for response
    from ..services.tenant_service import get_tenant_by_id as _svc_get_tenant_by_id

    tenant = await _svc_get_tenant_by_id(db, tenant_id)

    await write_audit_log(
        db,
        actor=_admin,
        action="tenant.balance_updated",
        target_type="tenant",
        target_id=tenant_id,
        ip_address=_get_client_ip(request),
        tenant_id=None,
    )
    return TenantResponse.model_validate(tenant)


@router.delete("/tenants/{tenant_id}/balance", status_code=204)
async def disable_tenant_balance_limit(
    tenant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Remove the balance limit for a tenant, making it unlimited again (admin only)."""
    await _svc_disable_limit(
        db,
        tenant_id=tenant_id,
        admin_user_id=_admin.id,
        reason="admin_disabled",
    )
    await write_audit_log(
        db,
        actor=_admin,
        action="tenant.balance_disabled",
        target_type="tenant",
        target_id=tenant_id,
        ip_address=_get_client_ip(request),
        tenant_id=None,
    )


@router.get("/tenants/{tenant_id}/balance/transactions")
async def list_balance_transactions(
    tenant_id: str,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Get paginated balance transaction history for a tenant (admin only)."""
    transactions, total = await get_transaction_history(
        db, tenant_id=tenant_id, page=page, page_size=page_size,
    )
    items = [BalanceTransactionResponse.model_validate(t) for t in transactions]
    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=items, total=total, page=page,
        page_size=page_size, total_pages=total_pages,
    )


@router.put("/tenants/{tenant_id}/balance/config", response_model=TenantResponse)
async def update_tenant_balance_config(
    tenant_id: str,
    body: BalanceConfigRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Set or clear the warning threshold for a tenant (admin only)."""
    tenant = await _svc_set_warning_threshold(
        db,
        tenant_id=tenant_id,
        threshold_eur=body.warning_threshold_eur,
    )
    await write_audit_log(
        db,
        actor=_admin,
        action="tenant.balance_config_updated",
        target_type="tenant",
        target_id=tenant_id,
        ip_address=_get_client_ip(request),
        tenant_id=None,
    )
    return TenantResponse.model_validate(tenant)


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
        auto_route_eligible=body.auto_route_eligible,
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
    if body.auto_route_eligible is not None:
        update_kwargs["auto_route_eligible"] = body.auto_route_eligible
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
        description=body.description,
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
    if body.description is not None:
        update_kwargs["description"] = body.description
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
# MCP Server Endpoints (admin or manager)
# =============================================================================


class McpServerCreate(BaseModel):
    tenant_id: str | None = None  # admin only
    name: str
    transport: str  # "stdio", "streamable_http", "websocket"
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env_vars: dict | None = None
    headers: dict | None = None
    allowed_tools: list[str] | None = None
    enabled: bool = True


class McpServerUpdate(BaseModel):
    name: str | None = None
    transport: str | None = None
    url: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env_vars: dict | None = None
    headers: dict | None = None
    allowed_tools: list[str] | None = None
    enabled: bool | None = None


class McpServerResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    transport: str
    url: str | None = None
    command: str | None = None
    args: list | None = None
    env_vars: dict | None = None  # masked values
    headers: dict | None = None  # masked values
    allowed_tools: list | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class McpServerTestResponse(BaseModel):
    connected: bool
    tools: list[dict] = []
    error: str | None = None


class McpServerSyncResponse(BaseModel):
    created: int
    updated: int
    deprecated: int


@router.get("/mcp-servers", response_model=PaginatedResponse[McpServerResponse])
async def list_mcp_servers(
    tenant_id: str | None = None,
    search: str | None = None,
    transport: str | None = None,
    enabled: bool | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List MCP servers. Admin sees all; manager scoped to own tenant."""
    if current_user.role == "manager":
        servers, total = await _svc_list_mcp_servers(
            db, tenant_id=current_user.tenant_id,
            search=search, transport=transport, enabled=enabled,
            page=page, page_size=page_size,
        )
    else:
        servers, total = await _svc_list_mcp_servers(
            db, tenant_id=tenant_id,
            search=search, transport=transport, enabled=enabled,
            page=page, page_size=page_size,
        )

    total_pages = max(1, -(-total // page_size))
    items = []
    for s in servers:
        decrypted_env = decrypt_env_vars(s)
        decrypted_headers = decrypt_headers(s)
        server_dict = {c.name: getattr(s, c.name) for c in s.__table__.columns}
        server_dict["env_vars"] = decrypted_env
        server_dict["headers"] = decrypted_headers
        data = McpServerResponse.model_validate(server_dict)
        items.append(data)

    return PaginatedResponse(
        items=items, total=total, page=page, page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/mcp-servers", response_model=McpServerResponse, status_code=201)
async def create_mcp_server(
    body: McpServerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create an MCP server config. Admin can specify tenant_id."""
    if current_user.role == "manager" and body.tenant_id and body.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only create MCP servers in their own tenant")
    tenant_id = body.tenant_id if current_user.role == "admin" and body.tenant_id else current_user.tenant_id

    server = await _svc_create_mcp_server(
        db,
        tenant_id=tenant_id,
        name=body.name,
        transport=body.transport,
        url=body.url,
        command=body.command,
        args=body.args,
        env_vars=body.env_vars,
        headers=body.headers,
        allowed_tools=body.allowed_tools,
        enabled=body.enabled,
    )
    await write_audit_log(
        db,
        actor=current_user,
        action="mcp_server.created",
        target_type="mcp_server",
        target_id=server.id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    decrypted_env = decrypt_env_vars(server)
    decrypted_headers = decrypt_headers(server)
    server_dict = {c.name: getattr(server, c.name) for c in server.__table__.columns}
    server_dict["env_vars"] = decrypted_env
    server_dict["headers"] = decrypted_headers
    data = McpServerResponse.model_validate(server_dict)
    return data


@router.get("/mcp-servers/{server_id}", response_model=McpServerResponse)
async def get_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Get a single MCP server config. Manager scoped to own tenant."""
    server = await _svc_get_mcp_server(db, server_id)
    if server is None:
        raise NotFoundError("MCP server not found")
    if current_user.role == "manager" and server.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only view MCP servers in their own tenant")

    decrypted_env = decrypt_env_vars(server)
    decrypted_headers = decrypt_headers(server)
    server_dict = {c.name: getattr(server, c.name) for c in server.__table__.columns}
    server_dict["env_vars"] = decrypted_env
    server_dict["headers"] = decrypted_headers
    data = McpServerResponse.model_validate(server_dict)
    return data


@router.put("/mcp-servers/{server_id}", response_model=McpServerResponse)
async def update_mcp_server(
    server_id: str,
    body: McpServerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update an MCP server config. Manager scoped to own tenant."""
    target = await _svc_get_mcp_server(db, server_id)
    if target is None:
        raise NotFoundError("MCP server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify MCP servers in their own tenant")

    update_kwargs = {}
    for field in ("name", "transport", "url", "command", "args", "env_vars",
                  "headers", "allowed_tools", "enabled"):
        val = getattr(body, field, None)
        if val is not None:
            update_kwargs[field] = val

    server = await _svc_update_mcp_server(db, server_id, **update_kwargs)

    action = "mcp_server.disabled" if body.enabled is False else "mcp_server.updated"
    await write_audit_log(
        db,
        actor=current_user,
        action=action,
        target_type="mcp_server",
        target_id=server_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    decrypted_env = decrypt_env_vars(server)
    decrypted_headers = decrypt_headers(server)
    server_dict = {c.name: getattr(server, c.name) for c in server.__table__.columns}
    server_dict["env_vars"] = decrypted_env
    server_dict["headers"] = decrypted_headers
    data = McpServerResponse.model_validate(server_dict)
    return data


@router.delete("/mcp-servers/{server_id}", status_code=204)
async def delete_mcp_server(
    server_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete an MCP server and its synced tools. Manager scoped to own tenant."""
    target = await _svc_get_mcp_server(db, server_id)
    if target is None:
        raise NotFoundError("MCP server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only delete MCP servers in their own tenant")

    await _svc_delete_mcp_server(db, server_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="mcp_server.deleted",
        target_type="mcp_server",
        target_id=server_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


@router.post("/mcp-servers/{server_id}/test", response_model=McpServerTestResponse)
async def test_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Test connection to an MCP server and return discovered tools."""
    target = await _svc_get_mcp_server(db, server_id)
    if target is None:
        raise NotFoundError("MCP server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only test MCP servers in their own tenant")

    return await _svc_test_mcp_connection(db, server_id)


@router.post("/mcp-servers/{server_id}/sync-tools", response_model=McpServerSyncResponse)
async def sync_mcp_server_tools(
    server_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Sync tools from an MCP server into the tools table."""
    target = await _svc_get_mcp_server(db, server_id)
    if target is None:
        raise NotFoundError("MCP server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only sync MCP servers in their own tenant")

    result = await _svc_sync_mcp_tools(db, server_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="mcp_server.tools_synced",
        target_type="mcp_server",
        target_id=server_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return result


# =============================================================================
# A2A Server Endpoints (admin or manager)
# =============================================================================


class A2aServerCreate(BaseModel):
    tenant_id: str | None = None  # admin only
    name: str
    url: str
    agent_card_path: str = "/.well-known/agent-card.json"
    protocol_binding: str = "rest"  # "jsonrpc", "rest", "grpc"
    auth_scheme: str = "none"
    auth_token: str | None = None
    headers: dict | None = None
    allowed_skills: list[str] | None = None
    enabled: bool = True
    # --- Resilience config (Issue #409) ---
    retry_max_attempts: int | None = None
    retry_backoff_base_seconds: float | None = None
    retry_backoff_max_seconds: float | None = None
    timeout_connect_seconds: float | None = None
    timeout_read_seconds: float | None = None
    timeout_stream_seconds: float | None = None
    circuit_breaker_threshold: int | None = None
    circuit_breaker_window_seconds: int | None = None
    circuit_breaker_cooldown_seconds: int | None = None
    # --- OAuth2 config (Issue #418) ---
    oauth2_client_id: str | None = None
    oauth2_client_secret: str | None = None
    oauth2_authorize_url: str | None = None
    oauth2_token_url: str | None = None
    oauth2_scopes: str | None = None


class A2aServerUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    agent_card_path: str | None = None
    protocol_binding: str | None = None
    auth_scheme: str | None = None
    auth_token: str | None = None
    headers: dict | None = None
    allowed_skills: list[str] | None = None
    enabled: bool | None = None
    # --- Resilience config (Issue #409) ---
    retry_max_attempts: int | None = None
    retry_backoff_base_seconds: float | None = None
    retry_backoff_max_seconds: float | None = None
    timeout_connect_seconds: float | None = None
    timeout_read_seconds: float | None = None
    timeout_stream_seconds: float | None = None
    circuit_breaker_threshold: int | None = None
    circuit_breaker_window_seconds: int | None = None
    circuit_breaker_cooldown_seconds: int | None = None
    # --- OAuth2 config (Issue #418) ---
    oauth2_client_id: str | None = None
    oauth2_client_secret: str | None = None
    oauth2_authorize_url: str | None = None
    oauth2_token_url: str | None = None
    oauth2_scopes: str | None = None
    oauth2_tokens: str | None = None  # set to null to revoke


class A2aServerResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    url: str | None = None
    agent_card_path: str = "/.well-known/agent-card.json"
    protocol_binding: str
    auth_scheme: str | None = None
    auth_token: str | None = None  # masked
    headers: dict | None = None  # masked
    allowed_skills: list | None = None
    enabled: bool
    # --- Resilience config (Issue #409) ---
    retry_max_attempts: int | None = None
    retry_backoff_base_seconds: float | None = None
    retry_backoff_max_seconds: float | None = None
    timeout_connect_seconds: float | None = None
    timeout_read_seconds: float | None = None
    timeout_stream_seconds: float | None = None
    circuit_breaker_threshold: int | None = None
    circuit_breaker_window_seconds: int | None = None
    circuit_breaker_cooldown_seconds: int | None = None
    # --- OAuth2 config (Issue #418) ---
    oauth2_client_id: str | None = None
    oauth2_client_secret: str | None = None  # masked
    oauth2_authorize_url: str | None = None
    oauth2_token_url: str | None = None
    oauth2_scopes: str | None = None
    oauth2_tokens_status: str | None = None  # "authorized" | "expired" | "none" | None
    agent_card_cache: dict | None = None
    agent_card_cached_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class A2aServerTestResponse(BaseModel):
    connected: bool
    agent_name: str | None = None
    agent_description: str | None = None
    capabilities: dict | None = None
    skills: list[dict] = []
    error: str | None = None


class A2aServerSyncResponse(BaseModel):
    created: int
    updated: int
    deprecated: int


@router.get("/a2a-servers", response_model=PaginatedResponse[A2aServerResponse])
async def list_a2a_servers(
    tenant_id: str | None = None,
    search: str | None = None,
    protocol_binding: str | None = None,
    enabled: bool | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List A2A servers. Admin sees all; manager scoped to own tenant."""
    if current_user.role == "manager":
        servers, total = await _svc_list_a2a_servers(
            db, tenant_id=current_user.tenant_id,
            search=search, protocol_binding=protocol_binding,
            enabled=enabled, page=page, page_size=page_size,
        )
    else:
        servers, total = await _svc_list_a2a_servers(
            db, tenant_id=tenant_id,
            search=search, protocol_binding=protocol_binding,
            enabled=enabled, page=page, page_size=page_size,
        )

    total_pages = max(1, -(-total // page_size))
    items = []
    for s in servers:
        decrypted_token = decrypt_auth_token(s)
        decrypted_headers = _a2a_decrypt_headers(s)
        server_dict = {c.name: getattr(s, c.name) for c in s.__table__.columns}
        server_dict["auth_token"] = _a2a_mask_dict({"token": decrypted_token})["token"] if decrypted_token else None
        server_dict["headers"] = _a2a_mask_dict(decrypted_headers) if decrypted_headers else None
        server_dict["oauth2_tokens_status"] = _a2a_oauth2_tokens_status(s)
        decrypted_secret = _a2a_decrypt_oauth2_client_secret(s)
        server_dict["oauth2_client_secret"] = _a2a_mask_dict({"secret": decrypted_secret or ""})["secret"] if decrypted_secret else None
        data = A2aServerResponse.model_validate(server_dict)
        items.append(data)

    return PaginatedResponse(
        items=items, total=total, page=page, page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/a2a-servers", response_model=A2aServerResponse, status_code=201)
async def create_a2a_server(
    body: A2aServerCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create an A2A server config. Admin can specify tenant_id."""
    if current_user.role == "manager" and body.tenant_id and body.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only create A2A servers in their own tenant")
    tenant_id = body.tenant_id if current_user.role == "admin" and body.tenant_id else current_user.tenant_id

    server = await _svc_create_a2a_server(
        db,
        tenant_id=tenant_id,
        name=body.name,
        url=body.url,
        agent_card_path=body.agent_card_path,
        protocol_binding=body.protocol_binding,
        auth_scheme=body.auth_scheme,
        auth_token=body.auth_token,
        headers=body.headers,
        allowed_skills=body.allowed_skills,
        enabled=body.enabled,
        retry_max_attempts=body.retry_max_attempts,
        retry_backoff_base_seconds=body.retry_backoff_base_seconds,
        retry_backoff_max_seconds=body.retry_backoff_max_seconds,
        timeout_connect_seconds=body.timeout_connect_seconds,
        timeout_read_seconds=body.timeout_read_seconds,
        timeout_stream_seconds=body.timeout_stream_seconds,
        circuit_breaker_threshold=body.circuit_breaker_threshold,
        circuit_breaker_window_seconds=body.circuit_breaker_window_seconds,
        circuit_breaker_cooldown_seconds=body.circuit_breaker_cooldown_seconds,
        # OAuth2 config
        oauth2_client_id=body.oauth2_client_id,
        oauth2_client_secret=body.oauth2_client_secret,
        oauth2_authorize_url=body.oauth2_authorize_url,
        oauth2_token_url=body.oauth2_token_url,
        oauth2_scopes=body.oauth2_scopes,
    )
    await write_audit_log(
        db,
        actor=current_user,
        action="a2a_server.created",
        target_type="a2a_server",
        target_id=server.id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    decrypted_token = decrypt_auth_token(server)
    decrypted_headers = _a2a_decrypt_headers(server)
    server_dict = {c.name: getattr(server, c.name) for c in server.__table__.columns}
    server_dict["auth_token"] = _a2a_mask_dict({"token": decrypted_token})["token"] if decrypted_token else None
    server_dict["headers"] = _a2a_mask_dict(decrypted_headers) if decrypted_headers else None
    server_dict["oauth2_tokens_status"] = _a2a_oauth2_tokens_status(server)
    decrypted_secret = _a2a_decrypt_oauth2_client_secret(server)
    server_dict["oauth2_client_secret"] = _a2a_mask_dict({"secret": decrypted_secret or ""})["secret"] if decrypted_secret else None
    data = A2aServerResponse.model_validate(server_dict)
    return data


@router.get("/a2a-servers/{server_id}", response_model=A2aServerResponse)
async def get_a2a_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Get a single A2A server config. Manager scoped to own tenant."""
    server = await _svc_get_a2a_server(db, server_id)
    if server is None:
        raise NotFoundError("A2A server not found")
    if current_user.role == "manager" and server.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only view A2A servers in their own tenant")

    decrypted_token = decrypt_auth_token(server)
    decrypted_headers = _a2a_decrypt_headers(server)
    server_dict = {c.name: getattr(server, c.name) for c in server.__table__.columns}
    server_dict["auth_token"] = _a2a_mask_dict({"token": decrypted_token})["token"] if decrypted_token else None
    server_dict["headers"] = _a2a_mask_dict(decrypted_headers) if decrypted_headers else None
    server_dict["oauth2_tokens_status"] = _a2a_oauth2_tokens_status(server)
    decrypted_secret = _a2a_decrypt_oauth2_client_secret(server)
    server_dict["oauth2_client_secret"] = _a2a_mask_dict({"secret": decrypted_secret or ""})["secret"] if decrypted_secret else None
    data = A2aServerResponse.model_validate(server_dict)
    return data


@router.put("/a2a-servers/{server_id}", response_model=A2aServerResponse)
async def update_a2a_server(
    server_id: str,
    body: A2aServerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update an A2A server config. Manager scoped to own tenant."""
    target = await _svc_get_a2a_server(db, server_id)
    if target is None:
        raise NotFoundError("A2A server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only modify A2A servers in their own tenant")

    update_kwargs = {}
    for field in ("name", "url", "agent_card_path", "protocol_binding",
                  "auth_scheme", "auth_token", "headers", "allowed_skills",
                  "enabled",
                  "retry_max_attempts", "retry_backoff_base_seconds",
                  "retry_backoff_max_seconds", "timeout_connect_seconds",
                  "timeout_read_seconds", "timeout_stream_seconds",
                  "circuit_breaker_threshold", "circuit_breaker_window_seconds",
                  "circuit_breaker_cooldown_seconds",
                  "oauth2_client_id", "oauth2_client_secret",
                  "oauth2_authorize_url", "oauth2_token_url",
                  "oauth2_scopes"):
        val = getattr(body, field, None)
        if val is not None:
            update_kwargs[field] = val
    # oauth2_tokens is special: null means "clear tokens" (e.g. revoke)
    if "oauth2_tokens" in body.model_fields_set:
        update_kwargs["oauth2_tokens"] = body.oauth2_tokens

    server = await _svc_update_a2a_server(db, server_id, **update_kwargs)

    action = "a2a_server.disabled" if body.enabled is False else "a2a_server.updated"
    await write_audit_log(
        db,
        actor=current_user,
        action=action,
        target_type="a2a_server",
        target_id=server_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    decrypted_token = decrypt_auth_token(server)
    decrypted_headers = _a2a_decrypt_headers(server)
    server_dict = {c.name: getattr(server, c.name) for c in server.__table__.columns}
    server_dict["auth_token"] = _a2a_mask_dict({"token": decrypted_token})["token"] if decrypted_token else None
    server_dict["headers"] = _a2a_mask_dict(decrypted_headers) if decrypted_headers else None
    server_dict["oauth2_tokens_status"] = _a2a_oauth2_tokens_status(server)
    decrypted_secret = _a2a_decrypt_oauth2_client_secret(server)
    server_dict["oauth2_client_secret"] = _a2a_mask_dict({"secret": decrypted_secret or ""})["secret"] if decrypted_secret else None
    data = A2aServerResponse.model_validate(server_dict)
    return data


@router.delete("/a2a-servers/{server_id}", status_code=204)
async def delete_a2a_server(
    server_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete an A2A server and its synced tools. Manager scoped to own tenant."""
    target = await _svc_get_a2a_server(db, server_id)
    if target is None:
        raise NotFoundError("A2A server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only delete A2A servers in their own tenant")

    await _svc_delete_a2a_server(db, server_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="a2a_server.deleted",
        target_type="a2a_server",
        target_id=server_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


@router.post("/a2a-servers/{server_id}/test", response_model=A2aServerTestResponse)
async def test_a2a_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Test connection to an A2A server and return discovered skills."""
    target = await _svc_get_a2a_server(db, server_id)
    if target is None:
        raise NotFoundError("A2A server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only test A2A servers in their own tenant")

    return await _svc_test_a2a_connection(db, server_id)


@router.post("/a2a-servers/{server_id}/sync-tools", response_model=A2aServerSyncResponse)
async def sync_a2a_server_tools(
    server_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Sync skills from an A2A server into the tools table."""
    target = await _svc_get_a2a_server(db, server_id)
    if target is None:
        raise NotFoundError("A2A server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only sync A2A servers in their own tenant")

    result = await _svc_sync_a2a_tools(db, server_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="a2a_server.tools_synced",
        target_type="a2a_server",
        target_id=server_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return result


class A2aOAuth2AuthorizeResponse(BaseModel):
    authorization_url: str


@router.post("/a2a-servers/{server_id}/oauth2/authorize", response_model=A2aOAuth2AuthorizeResponse)
async def authorize_a2a_server_oauth2(
    server_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Generate an OAuth2 authorization URL for an A2A server.

    Stores a state nonce in Redis for callback validation.
    The admin must open the returned URL in a browser to complete authorization.
    """
    from ..core.config import settings

    target = await _svc_get_a2a_server(db, server_id)
    if target is None:
        raise NotFoundError("A2A server not found")
    if current_user.role == "manager" and target.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only authorize A2A servers in their own tenant")

    # Validate OAuth2 config is present
    if target.auth_scheme != "oauth2":
        raise ValidationError("A2A server is not configured for OAuth2 authentication")
    if not target.oauth2_client_id or not target.oauth2_authorize_url or not target.oauth2_token_url:
        raise ValidationError(
            "OAuth2 client_id, authorize_url, and token_url must be configured"
        )

    # Generate state nonce and store in Redis
    nonce = str(uuid.uuid4())
    await store_a2a_oauth_state(nonce, server_id, current_user.id)

    # Build redirect URI
    redirect_uri = f"{settings.API_BASE_URL}/api/a2a/oauth2/callback"

    # Build authorization URL
    from urllib.parse import urlencode
    params = {
        "response_type": "code",
        "client_id": target.oauth2_client_id,
        "redirect_uri": redirect_uri,
        "state": nonce,
    }
    if target.oauth2_scopes:
        params["scope"] = target.oauth2_scopes

    authorization_url = f"{target.oauth2_authorize_url}?{urlencode(params)}"

    await write_audit_log(
        db,
        actor=current_user,
        action="a2a_server.oauth2_authorize_initiated",
        target_type="a2a_server",
        target_id=server_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return A2aOAuth2AuthorizeResponse(authorization_url=authorization_url)


# =============================================================================
# A2A Circuit Breaker & Call Logs (Issue #409)
# =============================================================================


class A2aCircuitBreakerState(BaseModel):
    failures: int = 0
    degraded_at: str | None = None
    last_failure_at: str | None = None
    last_probe_at: str | None = None
    threshold: int
    window_seconds: int
    cooldown_seconds: int
    degraded: bool = False


class A2aCallLogResponse(BaseModel):
    id: str
    tenant_id: str
    a2a_server_id: str
    a2a_server_name: str | None = None
    skill_id: str | None = None
    session_id: str | None = None
    trace_id: str
    status: str
    latency_ms: int | None = None
    retry_count: int = 0
    error_message: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get(
    "/a2a-servers/{server_id}/circuit-breaker",
    response_model=A2aCircuitBreakerState,
)
async def get_a2a_circuit_breaker(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Get the current circuit breaker state for an A2A server."""
    server = await _svc_get_a2a_server(db, server_id)
    if server is None:
        raise NotFoundError("A2A server not found")
    if current_user.role == "manager" and server.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Managers can only view A2A servers in their own tenant")

    from ..core.config import settings

    cb = A2ACircuitBreaker(
        server_id=server.id,
        threshold=server.circuit_breaker_threshold or settings.A2A_DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
        window_seconds=server.circuit_breaker_window_seconds or settings.A2A_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS,
        cooldown_seconds=server.circuit_breaker_cooldown_seconds or settings.A2A_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    )
    return await cb.get_state()


@router.post(
    "/a2a-servers/{server_id}/circuit-breaker/reset",
    status_code=200,
)
async def reset_a2a_circuit_breaker(
    server_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Manually reset the circuit breaker for an A2A server (admin only)."""
    if current_user.role != "admin":
        raise ForbiddenError("Only admins can reset the circuit breaker")

    server = await _svc_get_a2a_server(db, server_id)
    if server is None:
        raise NotFoundError("A2A server not found")

    from ..core.config import settings

    cb = A2ACircuitBreaker(
        server_id=server.id,
        threshold=server.circuit_breaker_threshold or settings.A2A_DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
        window_seconds=server.circuit_breaker_window_seconds or settings.A2A_DEFAULT_CIRCUIT_BREAKER_WINDOW_SECONDS,
        cooldown_seconds=server.circuit_breaker_cooldown_seconds or settings.A2A_DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    )
    await cb.reset()

    await write_audit_log(
        db,
        actor=current_user,
        action="a2a_server.circuit_breaker_reset",
        target_type="a2a_server",
        target_id=server_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return {"status": "ok", "message": "Circuit breaker reset"}


@router.get(
    "/a2a-call-logs",
    response_model=PaginatedResponse[A2aCallLogResponse],
)
async def list_a2a_call_logs(
    a2a_server_id: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """List A2A call logs. Admin sees all; manager scoped to own tenant."""
    from ..core.pagination import paginate

    stmt = select(A2aCallLog).order_by(A2aCallLog.created_at.desc())

    if current_user.role == "manager":
        stmt = stmt.where(A2aCallLog.tenant_id == current_user.tenant_id)
    if a2a_server_id:
        stmt = stmt.where(A2aCallLog.a2a_server_id == a2a_server_id)
    if status:
        stmt = stmt.where(A2aCallLog.status == status)
    if date_from:
        stmt = stmt.where(A2aCallLog.created_at >= date_from)
    if date_to:
        stmt = stmt.where(A2aCallLog.created_at <= date_to)

    return await paginate(db, stmt, page=page, page_size=page_size)


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
    a2a_metadata: dict | None = None


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
    a2a_metadata: dict | None = None


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
    a2a_metadata: dict | None = None
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
        a2a_metadata=body.a2a_metadata,
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
        "cross_session_min_score", "a2a_metadata",
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
    """Get all application settings (admin only).

    The response includes a 'license_status' key (VALID, INVALID, EXPIRED,
    or NOT_SET) that reflects the current state of the stored license key.
    """
    from ..services.license_service import get_license_status

    settings = await get_all_settings(db)
    license_status, _ = await get_license_status(db)
    settings["license_status"] = license_status.value
    return SettingsResponse(settings=settings)


@router.put("/settings", response_model=SettingsResponse)
async def update_settings(
    body: dict[str, str],
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Bulk update application settings (admin only).

    If a 'license_key' is provided, it is validated before saving.
    Invalid or expired license keys are rejected with a clear error.
    """
    from ..services.license_service import verify_license_key, _is_expired

    license_key = body.get("license_key")
    if license_key is not None and license_key.strip():
        info = verify_license_key(license_key)
        if info is None:
            if _is_expired(license_key):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="License key has expired. Please obtain a new license.",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Invalid license key. The key could not be verified. "
                    "Please check that you entered it correctly."
                ),
            )

    settings = await set_settings(db, body)
    return SettingsResponse(settings=settings)


# =============================================================================
# License & Tenant Status Endpoints (Issue #243)
# =============================================================================


class LicenseStatusResponse(BaseModel):
    status: str  # valid | invalid | expired | not_set
    licensee: str | None = None
    max_tenants: int | None = None
    expires_at: str | None = None
    tenant_count: int = 0
    tenant_limit: int = 3


class TenantStatusResponse(BaseModel):
    total_tenants: int
    effective_limit: int
    license_status: str
    can_create: bool
    message: str | None = None


@router.get("/license/status", response_model=LicenseStatusResponse)
async def get_license_status_endpoint(
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Get detailed license status (admin only).

    Returns the current license state, tenant count, and effective limit.
    Used by the frontend for real-time license validation feedback.
    """
    from ..services.license_service import (
        get_license_status,
        get_effective_tenant_limit,
    )

    status, info = await get_license_status(db)
    limit = await get_effective_tenant_limit(db)
    from ..services.tenant_service import count_tenants
    total = await count_tenants(db)

    # Format limit for display: -1 / large sentinel → "Unlimited"
    display_limit = limit
    if limit >= 1_000_000:
        display_limit = -1  # signal unlimited to frontend

    return LicenseStatusResponse(
        status=status.value,
        licensee=info.licensee if info else None,
        max_tenants=info.max_tenants if info else None,
        expires_at=info.expires_at.isoformat() if info else None,
        tenant_count=total,
        tenant_limit=display_limit,
    )


@router.get("/tenant-status", response_model=TenantStatusResponse)
async def get_tenant_status(
    db: AsyncSession = Depends(get_db),
    _admin: UserORM = Depends(require_admin),
):
    """Get tenant capacity status for UI banners (admin only).

    Returns whether new tenants can be created and an appropriate message.
    """
    from ..services.license_service import (
        can_create_tenant,
        get_license_status,
        get_effective_tenant_limit,
    )
    from ..services.tenant_service import count_tenants

    total = await count_tenants(db)
    limit = await get_effective_tenant_limit(db)
    status, _ = await get_license_status(db)
    allowed, message = await can_create_tenant(db, total)

    return TenantStatusResponse(
        total_tenants=total,
        effective_limit=limit if limit < 1_000_000 else -1,
        license_status=status.value,
        can_create=allowed,
        message=message if not allowed else None,
    )


# =============================================================================
# RAG Document Management (admin only)
# =============================================================================

RAG_DOCUMENTS_TAG = "RAG Documents"


class RAGDocumentResponse(BaseModel):
    file_id: str
    title: str
    original_filename: str | None = None
    content_type: str | None = None
    chunk_count: int = 0
    created_at: str | None = None

    model_config = {"from_attributes": True}


@router.get(
    "/rag/documents",
    response_model=PaginatedResponse[RAGDocumentResponse],
    tags=[RAG_DOCUMENTS_TAG],
)
async def admin_list_rag_documents(
    page: int = 1,
    page_size: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin),
):
    """List indexed RAG documents, grouped by source file.

    Admin sees all tenants. Shows filename, chunk count, and creation date.
    """
    from ..services.rag_service import list_documents as _rag_list

    tenant_id = current_user.tenant_id
    items, total = await _rag_list(
        db, tenant_id=tenant_id, page=page, page_size=page_size,
    )

    resp_list = [RAGDocumentResponse(**item) for item in items]
    total_pages = max(1, -(-total // page_size))
    return PaginatedResponse(
        items=resp_list, total=total, page=page,
        page_size=page_size, total_pages=total_pages,
    )


@router.delete(
    "/rag/documents/{file_id}",
    status_code=204,
    tags=[RAG_DOCUMENTS_TAG],
)
async def admin_delete_rag_document(
    file_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin),
):
    """Delete all RAG document chunks for a given file upload."""
    from ..services.rag_service import delete_document as _rag_delete

    deleted = await _rag_delete(db, file_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="rag.document.deleted",
        target_type="rag_document",
        target_id=file_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
        payload={"chunks_deleted": deleted},
    )


@router.post(
    "/rag/documents/{file_id}/reindex",
    status_code=200,
    tags=[RAG_DOCUMENTS_TAG],
)
async def admin_reindex_rag_document(
    file_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin),
):
    """Re-index a file upload's text in the RAG system.

    Deletes existing chunks and re-creates them from the stored
    extracted text.
    """
    from ..db.orm.file_uploads import FileUpload
    from ..services.rag_service import index_document as _rag_index

    result = await db.execute(
        select(FileUpload).where(FileUpload.id == file_id)
    )
    upload = result.scalar_one_or_none()
    if upload is None:
        raise NotFoundError("File upload not found")

    chunk_count = await _rag_index(db, upload)
    await write_audit_log(
        db,
        actor=current_user,
        action="rag.document.reindexed",
        target_type="rag_document",
        target_id=file_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
        payload={"chunks_indexed": chunk_count},
    )

    return {"file_id": file_id, "chunks_indexed": chunk_count}


# =============================================================================
# Embed Configurations
# =============================================================================

EMBED_TAG = "Embed Configs"


class EmbedConfigAdminResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    is_active: bool
    theme: dict | None = None
    feature_flags: dict | None = None
    default_model_id: str | None = None
    default_skill_id: str | None = None
    default_template_id: str | None = None
    guest_token: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmbedConfigAdminCreate(BaseModel):
    name: str
    theme: dict | None = None
    feature_flags: dict | None = None
    default_model_id: str | None = None
    default_skill_id: str | None = None
    default_template_id: str | None = None


class EmbedConfigAdminUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    theme: dict | None = None
    feature_flags: dict | None = None
    default_model_id: str | None = None
    default_skill_id: str | None = None
    default_template_id: str | None = None


@router.get("/embed-configs", response_model=PaginatedResponse[EmbedConfigAdminResponse], tags=[EMBED_TAG])
async def admin_list_embed_configs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
    page: int | None = None,
    page_size: int = 25,
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
):
    """List embed configs for the current tenant (admin sees all)."""
    tenant_id = None if current_user.role == "admin" else current_user.tenant_id
    items, total = await _svc_list_embed_configs(
        db, tenant_id=tenant_id,
        search=search, sort_by=sort_by, sort_dir=sort_dir,
        page=page, page_size=page_size,
    )
    return PaginatedResponse(
        items=[EmbedConfigAdminResponse.model_validate(i) for i in items],
        total=total, page=page or 1, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size if page else 1,
    )


@router.post("/embed-configs", response_model=EmbedConfigAdminResponse, status_code=201, tags=[EMBED_TAG])
async def admin_create_embed_config(
    body: EmbedConfigAdminCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Create a new embed config. Returns the config with the raw guest token."""
    config, raw_token = await _svc_create_embed_config(
        db,
        tenant_id=current_user.tenant_id,
        name=body.name,
        theme=body.theme,
        feature_flags=body.feature_flags,
        default_model_id=body.default_model_id,
        default_skill_id=body.default_skill_id,
        default_template_id=body.default_template_id,
    )
    await write_audit_log(
        db,
        actor=current_user,
        action="embed_config.created",
        target_type="embed_config",
        target_id=config.id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    response = EmbedConfigAdminResponse.model_validate(config)
    response.guest_token = raw_token
    return response


@router.get("/embed-configs/{config_id}", response_model=EmbedConfigAdminResponse, tags=[EMBED_TAG])
async def admin_get_embed_config(
    config_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Get a single embed config."""
    config = await _svc_get_embed_config_by_id(db, config_id)
    if config is None:
        raise NotFoundError("Embed config not found")
    if current_user.role == "manager" and config.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Access denied")
    return EmbedConfigAdminResponse.model_validate(config)


@router.put("/embed-configs/{config_id}", response_model=EmbedConfigAdminResponse, tags=[EMBED_TAG])
async def admin_update_embed_config(
    config_id: str,
    body: EmbedConfigAdminUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Update an embed config."""
    config = await _svc_get_embed_config_by_id(db, config_id)
    if config is None:
        raise NotFoundError("Embed config not found")
    if current_user.role == "manager" and config.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Access denied")

    updated = await _svc_update_embed_config(
        db, config_id,
        name=body.name,
        is_active=body.is_active,
        theme=body.theme,
        feature_flags=body.feature_flags,
        default_model_id=body.default_model_id,
        default_skill_id=body.default_skill_id,
        default_template_id=body.default_template_id,
    )
    await write_audit_log(
        db,
        actor=current_user,
        action="embed_config.updated",
        target_type="embed_config",
        target_id=config_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    return EmbedConfigAdminResponse.model_validate(updated)


@router.delete("/embed-configs/{config_id}", status_code=204, tags=[EMBED_TAG])
async def admin_delete_embed_config(
    config_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Delete an embed config."""
    config = await _svc_get_embed_config_by_id(db, config_id)
    if config is None:
        raise NotFoundError("Embed config not found")
    if current_user.role == "manager" and config.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Access denied")

    await _svc_delete_embed_config(db, config_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="embed_config.deleted",
        target_type="embed_config",
        target_id=config_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )


@router.post("/embed-configs/{config_id}/regenerate-token", response_model=EmbedConfigAdminResponse, tags=[EMBED_TAG])
async def admin_regenerate_embed_token(
    config_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(require_admin_or_manager),
):
    """Regenerate the guest token. Old token is immediately invalidated."""
    config = await _svc_get_embed_config_by_id(db, config_id)
    if config is None:
        raise NotFoundError("Embed config not found")
    if current_user.role == "manager" and config.tenant_id != current_user.tenant_id:
        raise ForbiddenError("Access denied")

    config, raw_token = await _svc_regenerate_embed_token(db, config_id)
    await write_audit_log(
        db,
        actor=current_user,
        action="embed_config.token_regenerated",
        target_type="embed_config",
        target_id=config_id,
        tenant_id=current_user.tenant_id,
        ip_address=_get_client_ip(request),
    )
    response = EmbedConfigAdminResponse.model_validate(config)
    response.guest_token = raw_token
    return response
