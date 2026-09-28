# =============================================================================
# PH Agent Hub — Memory API Router
# =============================================================================
# ``GET /memory``, ``POST /memory``, ``DELETE /memory/{id}``.
# All endpoints are scoped to the authenticated user.
# =============================================================================

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator

from ..core.dependencies import get_current_user, get_db
from ..core.pagination import PaginatedResponse
from ..db.orm.users import User as UserORM
from ..services import audit_service, memory_service
from ..services.memory_service import MEMORY_KEY_MAX_CHARS, MEMORY_VALUE_MAX_CHARS
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])


# =============================================================================
# Pydantic Schemas
# =============================================================================


class MemoryCreate(BaseModel):
    key: str = Field(min_length=1, max_length=MEMORY_KEY_MAX_CHARS)
    value: str = Field(min_length=1, max_length=MEMORY_VALUE_MAX_CHARS)
    session_id: str | None = None

    @field_validator("key")
    @classmethod
    def strip_key(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("key must not be empty after stripping whitespace")
        return stripped


class MemoryUpdate(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=MEMORY_KEY_MAX_CHARS)
    value: str | None = Field(default=None, min_length=1, max_length=MEMORY_VALUE_MAX_CHARS)


class MemoryResponse(BaseModel):
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


class MemoryExportResponse(BaseModel):
    exported_at: datetime
    count: int
    entries: list[MemoryResponse]


class MemoryClearResponse(BaseModel):
    deleted: int


class MemoryMergeRequest(BaseModel):
    target_id: str
    source_ids: list[str] = Field(min_length=1)


def _get_client_ip(request: Request) -> str | None:
    return request.headers.get("X-Real-IP") or (request.client.host if request.client else None)


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=PaginatedResponse[MemoryResponse])
async def list_memory(
    session_id: str | None = Query(None),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """List the current user's memory entries.

    Optionally filter by ``?session_id=``.
    """
    entries, total = await memory_service.list_memory(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        items=[MemoryResponse.model_validate(e) for e in entries],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, -(-total // page_size)),
    )


@router.get("/export", response_model=MemoryExportResponse)
async def export_memory(
    session_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Export all memory entries for the current user.

    Optionally filter by ``?session_id=``.
    """
    entries, _total = await memory_service.list_memory(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        session_id=session_id,
        page=None,
    )
    return MemoryExportResponse(
        exported_at=datetime.now(timezone.utc),
        count=len(entries),
        entries=[MemoryResponse.model_validate(e) for e in entries],
    )


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(
    body: MemoryCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Create a new memory entry.  Source is always ``"manual"``."""
    entry = await memory_service.create_memory(
        db=db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        key=body.key,
        value=body.value,
        session_id=body.session_id,
        source="manual",
    )

    # Audit log
    try:
        await audit_service.write_audit_log(
            db=db,
            actor=current_user,
            action="memory.created",
            target_type="memory",
            target_id=entry.id,
            payload={"key": entry.key, "source": entry.source, "session_id": entry.session_id},
            tenant_id=current_user.tenant_id,
            ip_address=_get_client_ip(request),
        )
    except Exception:
        logger.warning("Failed to write audit log for memory.created", exc_info=True)

    # Best-effort growth policy enforcement
    try:
        await memory_service.prune_memories(
            db=db,
            user_id=current_user.id,
            tenant_id=current_user.tenant_id,
        )
    except Exception:
        logger.warning("Failed to enforce memory growth policy on create", exc_info=True)

    return MemoryResponse.model_validate(entry)


@router.put("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Update a memory entry's key and/or value.  Only the owner may update it."""
    entry = await memory_service.update_memory(
        db=db,
        memory_id=memory_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        key=body.key,
        value=body.value,
    )

    # Audit log
    try:
        await audit_service.write_audit_log(
            db=db,
            actor=current_user,
            action="memory.updated",
            target_type="memory",
            target_id=memory_id,
            payload={"updated_fields": [name for name in ("key", "value") if getattr(body, name) is not None]},
            tenant_id=current_user.tenant_id,
            ip_address=_get_client_ip(request),
        )
    except Exception:
        logger.warning("Failed to write audit log for memory.updated", exc_info=True)

    return MemoryResponse.model_validate(entry)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Delete a memory entry.  Only the owner may delete it."""
    entry = await memory_service.delete_memory(
        db=db,
        memory_id=memory_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )

    # Audit log
    try:
        await audit_service.write_audit_log(
            db=db,
            actor=current_user,
            action="memory.deleted",
            target_type="memory",
            target_id=memory_id,
            payload={"key": entry.key},
            tenant_id=current_user.tenant_id,
            ip_address=_get_client_ip(request),
        )
    except Exception:
        logger.warning("Failed to write audit log for memory.deleted", exc_info=True)


@router.delete("", response_model=MemoryClearResponse)
async def clear_memory(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Delete all memory entries for the current user."""
    deleted_count = await memory_service.clear_memories(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )

    # Audit log
    try:
        await audit_service.write_audit_log(
            db=db,
            actor=current_user,
            action="memory.cleared",
            target_type="memory",
            target_id=None,
            payload={"deleted": deleted_count},
            tenant_id=current_user.tenant_id,
            ip_address=_get_client_ip(request),
        )
    except Exception:
        logger.warning("Failed to write audit log for memory.cleared", exc_info=True)

    return MemoryClearResponse(deleted=deleted_count)


@router.post("/merge", response_model=MemoryResponse)
async def merge_memory(
    body: MemoryMergeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Merge source memory entries into the target entry."""
    result = await memory_service.merge_memories(
        db=db,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        target_id=body.target_id,
        source_ids=body.source_ids,
    )

    # Audit log
    try:
        await audit_service.write_audit_log(
            db=db,
            actor=current_user,
            action="memory.merged",
            target_type="memory",
            target_id=body.target_id,
            payload={"source_ids": body.source_ids},
            tenant_id=current_user.tenant_id,
            ip_address=_get_client_ip(request),
        )
    except Exception:
        logger.warning("Failed to write audit log for memory.merged", exc_info=True)

    return MemoryResponse.model_validate(result)
