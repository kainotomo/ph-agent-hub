# =============================================================================
# PH Agent Hub — Tenant Service (CRUD)
# =============================================================================

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import ConflictError, NotFoundError
from ..db.orm.tenants import Tenant


async def list_tenants(
    db: AsyncSession,
    *,
    search: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    page: int | None = None,
    page_size: int = 25,
) -> tuple[list[Tenant], int]:
    """Return all tenants with optional search, sorting, pagination."""
    stmt = select(Tenant)

    from ..core.pagination import apply_search, apply_sorting, paginate
    stmt = apply_search(stmt, search, [Tenant.name])
    stmt = apply_sorting(
        stmt, sort_by, sort_dir,
        column_map={
            "name": Tenant.name,
            "created_at": Tenant.created_at,
        },
        default_sort=Tenant.created_at,
    )

    return await paginate(db, stmt, page=page, page_size=page_size)


async def get_tenant_by_id(db: AsyncSession, tenant_id: str) -> Tenant | None:
    """Look up a tenant by primary key."""
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()


async def count_tenants(db: AsyncSession) -> int:
    """Return the total number of tenants in the system."""
    result = await db.execute(select(func.count()).select_from(Tenant))
    return result.scalar() or 0


async def get_tenant_ordinal(db: AsyncSession, tenant_id: str) -> int | None:
    """Return the 1-based ordinal position of a tenant ordered by creation date.
    Returns None if the tenant is not found."""
    result = await db.execute(
        select(Tenant).order_by(Tenant.created_at.asc())
    )
    tenants = result.scalars().all()
    for idx, t in enumerate(tenants, start=1):
        if t.id == tenant_id:
            return idx
    return None


async def create_tenant(db: AsyncSession, name: str) -> Tenant:
    """Create a new tenant. Raises ConflictError if the name already exists."""
    existing = await db.execute(select(Tenant).where(Tenant.name == name))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("A tenant with this name already exists")

    tenant = Tenant(name=name)
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant


async def update_tenant(db: AsyncSession, tenant_id: str, name: str) -> Tenant:
    """Update a tenant's name. Raises NotFoundError or ConflictError."""
    tenant = await get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")

    duplicate = await db.execute(
        select(Tenant).where(Tenant.name == name, Tenant.id != tenant_id)
    )
    if duplicate.scalar_one_or_none() is not None:
        raise ConflictError("A tenant with this name already exists")

    tenant.name = name
    await db.commit()
    await db.refresh(tenant)
    return tenant


async def delete_tenant(db: AsyncSession, tenant_id: str) -> None:
    """Delete a tenant by ID. Raises NotFoundError if missing, ConflictError
    if the tenant still has active resources (users, models, sessions, etc.).
    Audit/usage logs are auto-cleaned up (they are historical records)."""
    tenant = await get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")

    # ------------------------------------------------------------------
    # Usage/audit logs are denormalized (no FKs) — they survive deletion
    # and do NOT need to be cleaned up.
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Blocker check: active resources that must be handled manually
    # ------------------------------------------------------------------
    blockers: list[str] = []

    from ..db.orm.users import User
    from ..db.orm.sessions import Session as SessionORM
    from ..db.orm.tools import Tool
    from ..db.orm.templates import Template
    from ..db.orm.skills import Skill
    from ..db.orm.tags import Tag as TagORM
    from ..db.orm.memory import Memory
    from ..db.orm.file_uploads import FileUpload
    from ..db.orm.models import Model as ModelORM
    from ..db.orm.groups import UserGroup
    from ..db.orm.prompts import Prompt
    from ..db.orm.rag import RAGDocument

    checks: list[tuple[str, type]] = [
        ("users", User),
        ("models", ModelORM),
        ("sessions", SessionORM),
        ("tools", Tool),
        ("templates", Template),
        ("skills", Skill),
        ("tags", TagORM),
        ("memories", Memory),
        ("file uploads", FileUpload),
        ("user groups", UserGroup),
        ("prompts", Prompt),
        ("RAG documents", RAGDocument),
    ]

    for label, model in checks:
        result = await db.execute(
            select(func.count()).select_from(model).where(
                getattr(model, "tenant_id") == tenant_id
            )
        )
        count = result.scalar() or 0
        if count > 0:
            blockers.append(f"{count} {label}")

    if blockers:
        raise ConflictError(
            "Cannot delete this tenant — it still has related data: "
            + ", ".join(blockers)
            + ". Remove or reassign all related resources first."
        )

    await db.delete(tenant)
    await db.commit()


async def force_delete_tenant(db: AsyncSession, tenant_id: str) -> None:
    """Cascade-delete a tenant and ALL related data.

    Deletion order respects FK dependency chains (leaf tables first):
    1. message_feedback, session_tags, session_active_tools,
    #    skill_allowed_tools,
       tool_groups, model_groups, user_group_members
    2. messages, memory, file_uploads (S3 first, then DB)
    3. prompts, skills, templates, sessions
    4. tags, rag_documents, tools, models, user_groups
    5. cross-tenant rows referencing users being deleted:
       user_tool_preferences, user_group_members, message_feedback,
       prompts, memory, file_uploads → DELETE;
       skills.user_id, templates.assigned_user_id → SET NULL;
       then cross-tenant sessions & children → DELETE
    6. users
    7. tenant itself

    Usage/audit logs are denormalized (no FKs) and survive deletion.
    Also deletes S3 objects for all file_uploads belonging to the tenant
    before removing the DB rows.  Raises NotFoundError if the tenant does
    not exist.
    """
    from sqlalchemy import select as _select, delete as _delete

    from ..db.orm.tenants import Tenant
    from ..db.orm.users import User as UserORM
    from ..db.orm.models import Model as ModelORM
    from ..db.orm.sessions import Session as SessionORM, SessionActiveTool
    from ..db.orm.tools import Tool
    from ..db.orm.templates import Template
    from ..db.orm.skills import Skill, SkillAllowedTool
    from ..db.orm.tags import Tag as TagORM, SessionTag
    from ..db.orm.memory import Memory
    from ..db.orm.file_uploads import FileUpload
    from ..db.orm.groups import UserGroup, UserGroupMember, ModelGroup, ToolGroup
    from ..db.orm.prompts import Prompt
    from ..db.orm.rag import RAGDocument
    from ..db.orm.messages import Message, MessageFeedback
    from ..db.orm.usage_logs import UsageLog
    from ..db.orm.audit_logs import AuditLog

    # Verify the tenant exists
    result = await db.execute(_select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise NotFoundError("Tenant not found")

    # ==================================================================
    # Step 0 — Delete S3 objects for all file uploads in this tenant
    # ==================================================================
    from ..storage.s3 import delete_object

    uploads_result = await db.execute(
        _select(FileUpload.storage_key, FileUpload.bucket).where(
            FileUpload.tenant_id == tenant_id
        )
    )
    uploads = uploads_result.all()
    for storage_key, bucket in uploads:
        try:
            await delete_object(bucket, storage_key)
        except Exception:
            # Best-effort S3 cleanup — don't block tenant deletion
            pass

    # ==================================================================
    # Step 1 — Leaf junction / association tables (no dependents of their own)
    # ==================================================================
    await db.execute(
        _delete(MessageFeedback).where(
            MessageFeedback.message_id.in_(
                _select(Message.id).where(
                    Message.session_id.in_(
                        _select(SessionORM.id).where(
                            SessionORM.tenant_id == tenant_id
                        )
                    )
                )
            )
        )
    )
    await db.execute(
        _delete(SessionTag).where(
            SessionTag.session_id.in_(
                _select(SessionORM.id).where(SessionORM.tenant_id == tenant_id)
            )
        )
    )
    await db.execute(
        _delete(SessionActiveTool).where(
            SessionActiveTool.session_id.in_(
                _select(SessionORM.id).where(SessionORM.tenant_id == tenant_id)
            )
        )
    )
    await db.execute(
        _delete(SkillAllowedTool).where(
            SkillAllowedTool.skill_id.in_(
                _select(Skill.id).where(Skill.tenant_id == tenant_id)
            )
        )
    )
    await db.execute(
        _delete(ToolGroup).where(
            ToolGroup.group_id.in_(
                _select(UserGroup.id).where(UserGroup.tenant_id == tenant_id)
            )
        )
    )
    await db.execute(
        _delete(ModelGroup).where(
            ModelGroup.group_id.in_(
                _select(UserGroup.id).where(UserGroup.tenant_id == tenant_id)
            )
        )
    )
    await db.execute(
        _delete(UserGroupMember).where(
            UserGroupMember.group_id.in_(
                _select(UserGroup.id).where(UserGroup.tenant_id == tenant_id)
            )
        )
    )
    await db.flush()

    # ==================================================================
    # Step 2 — Tables with FKs to tables deleted in step 4/5
    # ==================================================================
    await db.execute(
        _delete(Memory).where(Memory.tenant_id == tenant_id)
    )
    await db.execute(
        _delete(FileUpload).where(FileUpload.tenant_id == tenant_id)
    )
    await db.execute(
        _delete(Message).where(
            Message.session_id.in_(
                _select(SessionORM.id).where(SessionORM.tenant_id == tenant_id)
            )
        )
    )
    await db.flush()

    # ==================================================================
    # Step 3 — Tables referencing users + parent tables
    # ==================================================================
    await db.execute(
        _delete(Prompt).where(Prompt.tenant_id == tenant_id)
    )
    # Skills FK to default_prompt_id (SET NULL), so prompts can be deleted first
    await db.execute(
        _delete(Skill).where(Skill.tenant_id == tenant_id)
    )
    # Sessions FK to skills (nullable), templates (nullable), models (nullable)
    await db.execute(
        _delete(SessionORM).where(SessionORM.tenant_id == tenant_id)
    )
    # Templates FK to models (nullable), users (nullable)
    await db.execute(
        _delete(Template).where(Template.tenant_id == tenant_id)
    )
    await db.flush()

    # ==================================================================
    # Step 4 — Tables referencing only tenants (no cross-table FKs)
    # ==================================================================
    await db.execute(_delete(TagORM).where(TagORM.tenant_id == tenant_id))
    await db.execute(_delete(RAGDocument).where(RAGDocument.tenant_id == tenant_id))
    await db.execute(_delete(Tool).where(Tool.tenant_id == tenant_id))
    await db.execute(_delete(ModelORM).where(ModelORM.tenant_id == tenant_id))
    await db.execute(_delete(UserGroup).where(UserGroup.tenant_id == tenant_id))
    await db.flush()

    # ==================================================================
    # Step 5 — Clean up rows in OTHER tenants that reference users
    # being deleted.  This is needed because a user may have been
    # moved between tenants: their old rows retain the original
    # tenant_id but still reference the user via FK.  Without this
    # step, the user delete in step 6 would fail with a foreign-key
    # violation from those cross-tenant rows.
    # ==================================================================
    from ..db.orm.user_tool_preferences import UserToolPreference

    user_ids_subq = _select(UserORM.id).where(UserORM.tenant_id == tenant_id)

    # 5a — Tables with a direct user_id FK and no tenant_id (or the
    #      tenant-scoped delete already handled same-tenant rows).
    await db.execute(
        _delete(UserToolPreference).where(
            UserToolPreference.user_id.in_(user_ids_subq)
        )
    )
    await db.execute(
        _delete(UserGroupMember).where(
            UserGroupMember.user_id.in_(user_ids_subq)
        )
    )
    # MessageFeedback has its own user_id FK (who left the feedback)
    await db.execute(
        _delete(MessageFeedback).where(
            MessageFeedback.user_id.in_(user_ids_subq)
        )
    )
    # Prompts: user_id is non-null — delete cross-tenant prompts
    await db.execute(
        _delete(Prompt).where(Prompt.user_id.in_(user_ids_subq))
    )
    await db.execute(
        _delete(Memory).where(Memory.user_id.in_(user_ids_subq))
    )
    # FileUpload: skip S3 cleanup for cross-tenant files (they belong to
    # another tenant; we only remove the DB row so the FK doesn't block
    # the user delete).
    await db.execute(
        _delete(FileUpload).where(FileUpload.user_id.in_(user_ids_subq))
    )
    await db.flush()

    # 5b — Nullable user_id FKs: set to NULL for cross-tenant rows
    #      (these reference a creator/assignee; destroying another
    #      tenant's skill/template would be too aggressive).
    from sqlalchemy import update as _update
    await db.execute(
        _update(Skill)
        .where(Skill.user_id.in_(user_ids_subq))
        .values(user_id=None)
    )
    await db.execute(
        _update(Template)
        .where(Template.assigned_user_id.in_(user_ids_subq))
        .values(assigned_user_id=None)
    )
    await db.flush()

    # 5c — Leaf children of sessions (cross-tenant)
    await db.execute(
        _delete(MessageFeedback).where(
            MessageFeedback.message_id.in_(
                _select(Message.id).where(
                    Message.session_id.in_(
                        _select(SessionORM.id).where(
                            SessionORM.user_id.in_(user_ids_subq)
                        )
                    )
                )
            )
        )
    )
    await db.execute(
        _delete(SessionTag).where(
            SessionTag.session_id.in_(
                _select(SessionORM.id).where(
                    SessionORM.user_id.in_(user_ids_subq)
                )
            )
        )
    )
    await db.execute(
        _delete(SessionActiveTool).where(
            SessionActiveTool.session_id.in_(
                _select(SessionORM.id).where(
                    SessionORM.user_id.in_(user_ids_subq)
                )
            )
        )
    )

    # 5d — Messages (cross-tenant)
    await db.execute(
        _delete(Message).where(
            Message.session_id.in_(
                _select(SessionORM.id).where(
                    SessionORM.user_id.in_(user_ids_subq)
                )
            )
        )
    )
    await db.flush()

    # 5e — Sessions themselves (cross-tenant)
    await db.execute(
        _delete(SessionORM).where(
            SessionORM.user_id.in_(user_ids_subq)
        )
    )
    await db.flush()

    # ==================================================================
    # Step 6 — Users (FK to tenants)
    # (Usage/audit logs are denormalized — no FKs, survive deletion)
    # ==================================================================
    await db.execute(
        _delete(UserORM).where(UserORM.tenant_id == tenant_id)
    )
    await db.flush()

    # ==================================================================
    # Step 7 — The tenant itself
    # ==================================================================
    await db.delete(tenant)
    await db.commit()
