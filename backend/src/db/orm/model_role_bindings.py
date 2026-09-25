# =============================================================================
# PH Agent Hub — ORM: Tenant Model Role Bindings
# =============================================================================
# Maps a logical workflow model role (for example ``@reasoning``) to one or
# more of a tenant's ``Model`` rows.  Developers reference roles in workflow
# definitions; admins bind each role to concrete tenant models.  A role may
# bind several models (a pool); resolution is deterministic and cost-agnostic
# — see ``services/model_role_service.resolve_role_model``.
#
# ``tenant_id`` is carried explicitly so every resolution path can be
# tenant-scoped with a single filter, and it must always match the referenced
# model's tenant.
# =============================================================================

from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant


class ModelRoleBinding(Base):
    __tablename__ = "model_role_bindings"

    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("models.id"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
