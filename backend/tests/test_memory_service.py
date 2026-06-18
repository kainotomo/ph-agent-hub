# =============================================================================
# PH Agent Hub — Memory Service Tests
# =============================================================================
# Service-layer tests for memory CRUD, upsert, and cross-session retrieval.
# Complements ``test_memory_api.py`` which tests the HTTP layer.
# =============================================================================

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.orm.memory import Memory
from src.services.memory_service import (
    admin_delete_memory,
    create_memory,
    delete_memory,
    delete_memory_by_key,
    list_all_memories,
    list_memory,
    update_memory,
    upsert_memory,
)

pytestmark = [pytest.mark.integration]


# ===========================================================================
# UpsertMemory
# ===========================================================================


class TestUpsertMemory:
    """Tests for upsert_memory — insert-or-update by (user, tenant, key, session)."""

    async def test_creates_new_entry_when_no_match(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """No existing row → create new memory entry."""
        memory = await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="preference",
            value="dark_mode",
        )
        assert memory.key == "preference"
        assert memory.value == "dark_mode"
        assert memory.user_id == test_user.id
        assert memory.tenant_id == test_tenant.id
        assert memory.session_id is None
        assert memory.source == "automatic"

        # Verify in DB
        result = await db_session.execute(
            select(Memory).where(Memory.id == memory.id)
        )
        row = result.scalar_one()
        assert row.value == "dark_mode"

    async def test_updates_existing_entry_when_match(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Existing row with same (user, tenant, key, session) → update value."""
        # Create initial
        original = await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="theme",
            value="light",
        )

        # Upsert again with same key but new value
        updated = await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="theme",
            value="dark",
        )
        assert updated.id == original.id
        assert updated.value == "dark"

        # Verify only one row exists
        result = await db_session.execute(
            select(Memory).where(Memory.key == "theme", Memory.user_id == test_user.id)
        )
        rows = list(result.scalars().all())
        assert len(rows) == 1

    async def test_session_scoped_vs_global_are_distinct(
        self, db_session: AsyncSession, test_user, test_tenant, test_session
    ):
        """Same key with session_id=None vs session_id=X → two separate entries."""
        global_mem = await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="context",
            value="global value",
        )
        session_mem = await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="context",
            value="session value",
            session_id=test_session.id,
        )
        assert global_mem.id != session_mem.id
        assert global_mem.value == "global value"
        assert session_mem.value == "session value"

    async def test_source_defaults_to_automatic(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """source should default to 'automatic'."""
        memory = await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="auto_source",
            value="test",
        )
        assert memory.source == "automatic"

    async def test_session_scoped_upsert_updates_matching(
        self, db_session: AsyncSession, test_user, test_tenant, test_session
    ):
        """Upsert with same key+session should update, not create duplicate."""
        # Create session-scoped
        await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="session_key",
            value="v1",
            session_id=test_session.id,
        )
        # Upsert same combo
        updated = await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="session_key",
            value="v2",
            session_id=test_session.id,
        )
        assert updated.value == "v2"

        # Only one row for this combo
        result = await db_session.execute(
            select(Memory).where(
                Memory.key == "session_key",
                Memory.user_id == test_user.id,
                Memory.session_id == test_session.id,
            )
        )
        assert len(list(result.scalars().all())) == 1


# ===========================================================================
# DeleteMemoryByKey
# ===========================================================================


class TestDeleteMemoryByKey:
    """Tests for delete_memory_by_key — delete global entries by key."""

    async def test_deletes_global_entry(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Should delete a global (session_id IS NULL) entry and return True."""
        await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="delete_me",
            value="gone",
        )
        result = await delete_memory_by_key(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="delete_me",
        )
        assert result is True

        # Verify gone
        rows = await db_session.execute(
            select(Memory).where(Memory.key == "delete_me", Memory.user_id == test_user.id)
        )
        assert list(rows.scalars().all()) == []

    async def test_returns_false_when_not_found(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Non-existent key should return False."""
        result = await delete_memory_by_key(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="nonexistent",
        )
        assert result is False

    async def test_does_not_delete_session_scoped(
        self, db_session: AsyncSession, test_user, test_tenant, test_session
    ):
        """Should only delete global entries, not session-scoped ones."""
        # Create both global and session-scoped with same key
        global_mem = await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="same_key",
            value="global",
        )
        session_mem = await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="same_key",
            value="session",
            session_id=test_session.id,
        )

        result = await delete_memory_by_key(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="same_key",
        )
        assert result is True

        # Global should be gone
        global_check = await db_session.get(Memory, global_mem.id)
        assert global_check is None

        # Session-scoped should remain
        session_check = await db_session.get(Memory, session_mem.id)
        assert session_check is not None


# ===========================================================================
# UpdateMemory
# ===========================================================================


class TestUpdateMemory:
    """Tests for update_memory at the service layer."""

    async def test_update_key_only(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Updating only the key should work."""
        memory = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="old_key", value="value",
        )
        updated = await update_memory(
            db_session, memory.id, user_id=test_user.id, tenant_id=test_tenant.id,
            key="new_key",
        )
        assert updated.key == "new_key"
        assert updated.value == "value"

    async def test_update_value_only(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Updating only the value should work."""
        memory = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="key", value="old_value",
        )
        updated = await update_memory(
            db_session, memory.id, user_id=test_user.id, tenant_id=test_tenant.id,
            value="new_value",
        )
        assert updated.key == "key"
        assert updated.value == "new_value"

    async def test_update_both_key_and_value(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Updating both key and value should work."""
        memory = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="old_key", value="old_value",
        )
        updated = await update_memory(
            db_session, memory.id, user_id=test_user.id, tenant_id=test_tenant.id,
            key="new_key", value="new_value",
        )
        assert updated.key == "new_key"
        assert updated.value == "new_value"

    async def test_raises_not_found(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Non-existent memory ID should raise NotFoundError."""
        import uuid
        with pytest.raises(NotFoundError):
            await update_memory(
                db_session, str(uuid.uuid4()),
                user_id=test_user.id, tenant_id=test_tenant.id,
                value="anything",
            )

    async def test_raises_forbidden_for_other_user(
        self, db_session: AsyncSession, test_user, test_tenant, second_user
    ):
        """Cross-user update should raise ForbiddenError."""
        memory = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="mine", value="data",
        )
        with pytest.raises(ForbiddenError):
            await update_memory(
                db_session, memory.id,
                user_id=second_user.id, tenant_id=test_tenant.id,
                value="hacked",
            )


# ===========================================================================
# AdminDeleteMemory
# ===========================================================================


class TestAdminDeleteMemory:
    """Tests for admin_delete_memory — no ownership check."""

    async def test_deletes_memory(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Admin delete should work without ownership check."""
        memory = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="admin_del", value="to_delete",
        )
        await admin_delete_memory(db_session, memory.id)

        result = await db_session.get(Memory, memory.id)
        assert result is None

    async def test_raises_not_found(
        self, db_session: AsyncSession
    ):
        """Non-existent ID should raise NotFoundError."""
        import uuid
        with pytest.raises(NotFoundError):
            await admin_delete_memory(db_session, str(uuid.uuid4()))


# ===========================================================================
# DeleteMemory (ownership validation)
# ===========================================================================


class TestDeleteMemory:
    """Tests for delete_memory — validates ownership before delete."""

    async def test_deletes_with_correct_ownership(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Should delete when user/tenant match."""
        memory = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="del_own", value="data",
        )
        await delete_memory(
            db_session, memory.id, user_id=test_user.id, tenant_id=test_tenant.id,
        )
        result = await db_session.get(Memory, memory.id)
        assert result is None

    async def test_raises_not_found(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Non-existent ID should raise NotFoundError."""
        import uuid
        with pytest.raises(NotFoundError):
            await delete_memory(
                db_session, str(uuid.uuid4()),
                user_id=test_user.id, tenant_id=test_tenant.id,
            )

    async def test_raises_forbidden_for_other_user(
        self, db_session: AsyncSession, test_user, test_tenant, second_user
    ):
        """Cross-user delete should raise ForbiddenError."""
        memory = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="mine", value="data",
        )
        with pytest.raises(ForbiddenError):
            await delete_memory(
                db_session, memory.id,
                user_id=second_user.id, tenant_id=test_tenant.id,
            )


# ===========================================================================
# ListAllMemories (admin)
# ===========================================================================


class TestListAllMemories:
    """Tests for list_all_memories — admin listing with filtering/sorting."""

    async def _create_memories(self, db_session, user, tenant, count=3):
        """Helper to create N memory entries."""
        ids = []
        for i in range(count):
            mem = await create_memory(
                db_session, tenant_id=tenant.id, user_id=user.id,
                key=f"key_{i}", value=f"value_{i}",
            )
            ids.append(mem.id)
        return ids

    async def test_filters_by_tenant(
        self, db_session: AsyncSession, test_user, test_tenant, second_tenant
    ):
        """Should only return memories for the specified tenant."""
        from src.db.orm.users import User as UserORM

        await self._create_memories(db_session, test_user, test_tenant, count=2)
        # Create a memory in a different tenant
        # Use second_tenant's existing second_user fixture
        await self._create_memories(db_session, test_user, second_tenant, count=1)

        items, total = await list_all_memories(
            db_session, tenant_id=test_tenant.id,
        )
        assert total == 2
        for item in items:
            assert item.tenant_id == test_tenant.id

    async def test_filters_by_user(
        self, db_session: AsyncSession, test_user, test_tenant, second_user
    ):
        """Should only return memories for the specified user."""
        await self._create_memories(db_session, test_user, test_tenant, count=2)
        await self._create_memories(db_session, second_user, test_tenant, count=1)

        items, total = await list_all_memories(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
        )
        assert total == 2

    async def test_search_on_key_and_value(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Search should match on key or value."""
        await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="favorite_color", value="blue",
        )
        await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="favorite_food", value="pizza",
        )

        items, total = await list_all_memories(
            db_session, tenant_id=test_tenant.id, search="pizza",
        )
        assert total == 1
        assert items[0].value == "pizza"

        items, total = await list_all_memories(
            db_session, tenant_id=test_tenant.id, search="favorite",
        )
        assert total >= 2

    async def test_pagination(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Pagination should return correct slices."""
        await self._create_memories(db_session, test_user, test_tenant, count=5)

        items, total = await list_all_memories(
            db_session, tenant_id=test_tenant.id,
            page=1, page_size=2,
        )
        assert total == 5
        assert len(items) == 2


# ===========================================================================
# ListMemory
# ===========================================================================


class TestListMemory:
    """Tests for list_memory — user-facing listing."""

    async def test_session_filter_includes_global(
        self, db_session: AsyncSession, test_user, test_tenant, test_session
    ):
        """When session_id is specified, both session-scoped and global entries should be returned."""
        global_mem = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="global_key", value="global",
        )
        session_mem = await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="session_key", value="session",
            session_id=test_session.id,
        )

        items, total = await list_memory(
            db_session, user_id=test_user.id, tenant_id=test_tenant.id,
            session_id=test_session.id,
        )
        assert total == 2
        ids = {m.id for m in items}
        assert global_mem.id in ids
        assert session_mem.id in ids

    async def test_page_none_returns_all(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """When page=None, all matching entries should be returned without pagination."""
        await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="k1", value="v1",
        )
        await create_memory(
            db_session, tenant_id=test_tenant.id, user_id=test_user.id,
            key="k2", value="v2",
        )

        items, total = await list_memory(
            db_session, user_id=test_user.id, tenant_id=test_tenant.id,
            page=None,
        )
        assert total == 2
        assert len(items) == 2

    async def test_pagination(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Pagination should return correct slice."""
        for i in range(3):
            await create_memory(
                db_session, tenant_id=test_tenant.id, user_id=test_user.id,
                key=f"page_{i}", value=str(i),
            )

        items, total = await list_memory(
            db_session, user_id=test_user.id, tenant_id=test_tenant.id,
            page=1, page_size=2,
        )
        assert total == 3
        assert len(items) == 2

    async def test_empty_for_new_user(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """User with no memories should get empty results."""
        items, total = await list_memory(
            db_session, user_id=test_user.id, tenant_id=test_tenant.id,
        )
        assert items == []
        assert total == 0
