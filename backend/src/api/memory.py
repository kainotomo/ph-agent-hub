# =============================================================================
# PH Agent Hub — Memory API Router
# =============================================================================
# ``GET /memory``, ``POST /memory``, ``DELETE /memory/{id}``.
# All endpoints are scoped to the authenticated user.
# =============================================================================

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..core.dependencies import get_current_user, get_db
from ..db.orm.users import User as UserORM
from ..services import memory_service
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/memory", tags=["memory"])

# =============================================================================
# Pydantic Schemas
# =============================================================================


class MemoryCreate(BaseModel):
    key: str
    value: str
    session_id: str | None = None


class MemoryUpdate(BaseModel):
    key: str | None = None
    value: str | None = None


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


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=list[MemoryResponse])
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
    return [MemoryResponse.model_validate(e) for e in entries]


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(
    body: MemoryCreate,
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
    return MemoryResponse.model_validate(entry)


@router.put("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
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
    return MemoryResponse.model_validate(entry)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: UserORM = Depends(get_current_user),
):
    """Delete a memory entry.  Only the owner may delete it."""
    await memory_service.delete_memory(
        db=db,
        memory_id=memory_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )
