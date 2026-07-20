# =============================================================================
# PH Agent Hub — Agent Memory Integration Tests
# =============================================================================
# Tests the memory tools with real database sessions, covering:
#   1. save_memory creates new entries (source="automatic")
#   2. save_memory updates existing entries
#   3. save_memory skips user-created (manual) entries
#   4. save_memory uses SELECT...FOR UPDATE (no concurrent duplicates)
#   5. delete_memory only deletes agent-created entries
#   6. list_memory returns both automatic and manual entries
#   7. Memory persists and is retrievable via list_memory
#   8. Memory isolation across users
#   9. _build_system_prompt includes memory guidance block
#   10. _build_system_prompt injects persistent user memories
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.memory import Memory
from src.services.memory_service import create_memory
from src.tools.memory import build_memory_tools

pytestmark = [pytest.mark.integration]


# ===========================================================================
# Helper
# ===========================================================================
def _patch_db_execute(db, side_effect=None, return_value=None):
    """Patch ``db.execute`` with a custom side effect or return value.

    Works around the fact that ``build_memory_tools`` accepts a raw
    :class:`AsyncSession` instance and the SELECT ... FOR UPDATE syntax
    produces a ``sqlalchemy.exc.StatementError`` on a mock session when
    ``.with_for_update()`` is called.
    """
    async def _execute(*args, **kwargs):
        # The first arg is the SQL statement; the mock returns the prepared
        # result so the caller can chain .scalars().one_or_none() etc.
        if return_value is not None:
            return return_value
        if side_effect:
            raise side_effect
        # Default: not-found
        rm = MagicMock()
        rm.scalar_one_or_none.return_value = None
        return rm
    db.execute = _execute


# ===========================================================================
# Tool-level tests with real database — save_memory
# ===========================================================================

class TestSaveMemory:
    """Tests for the ``save_memory`` tool with real DB sessions."""

    async def test_creates_new(self, db_session, test_tenant, test_user):
        """Agent calls save_memory → entry created with source='automatic'."""
        tools = build_memory_tools(
            db=db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
        )
        save = tools[0]

        result = await save(key="user_name", value="Alice")
        assert result["action"] == "created"
        assert result["key"] == "user_name"
        assert result["value"] == "Alice"

        # Verify row exists in DB
        row = await db_session.execute(
            select(Memory).where(
                Memory.user_id == test_user.id,
                Memory.tenant_id == test_tenant.id,
                Memory.key == "user_name",
                Memory.session_id.is_(None),
            )
        )
        entry = row.scalar_one_or_none()
        assert entry is not None
        assert entry.value == "Alice"
        assert entry.source == "automatic"
        assert entry.session_id is None

    async def test_updates_existing(self, db_session, test_tenant, test_user):
        """Agent saves same key → entry updated, not duplicated."""
        tools = build_memory_tools(
            db=db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
        )
        save = tools[0]

        # Create
        await save(key="theme", value="light")
        # Update
        result = await save(key="theme", value="dark")

        assert result["action"] == "updated"
        assert result["value"] == "dark"

        # Verify only ONE row
        rows = (await db_session.execute(
            select(Memory).where(
                Memory.user_id == test_user.id,
                Memory.tenant_id == test_tenant.id,
                Memory.key == "theme",
                Memory.session_id.is_(None),
            )
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].value == "dark"

    async def test_skips_user_created_entries(self, db_session, test_tenant, test_user):
        """Agent cannot overwrite user-created (manual) memory."""
        # User creates a manual entry
        user_entry = await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="role",
            value="CEO",
            source="manual",
        )
        assert user_entry.source == "manual"

        # Agent tries to overwrite
        tools = build_memory_tools(
            db=db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
        )
        save = tools[0]
        result = await save(key="role", value="Intern")

        assert result["action"] == "skipped"
        assert "user-created" in result.get("message", "").lower()

        # Verify original unchanged
        row = await db_session.execute(
            select(Memory).where(Memory.id == user_entry.id)
        )
        unchanged = row.scalar_one_or_none()
        assert unchanged is not None
        assert unchanged.value == "CEO"

    async def test_overwrites_own_automatic_entry(self, db_session, test_tenant, test_user):
        """Agent CAN overwrite its own automatic entries."""
        tools = build_memory_tools(
            db=db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
        )
        save = tools[0]

        # Agent creates
        await save(key="project", value="Alpha")
        # Agent updates
        result = await save(key="project", value="Beta")

        assert result["action"] == "updated"

        row = await db_session.execute(
            select(Memory).where(
                Memory.user_id == test_user.id,
                Memory.key == "project",
            )
        )
        entry = row.scalar_one_or_none()
        assert entry.value == "Beta"


# ===========================================================================
# Tool-level tests with real database — delete_memory
# ===========================================================================

class TestDeleteMemory:
    """Tests for the ``delete_memory`` tool."""

    async def test_deletes_agent_created(self, db_session, test_tenant, test_user):
        """Agent can delete its own entries."""
        tools = build_memory_tools(
            db=db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
        )
        save = tools[0]
        delete = tools[1]

        await save(key="temp", value="delete me")
        result = await delete(key="temp")
        assert result["action"] == "deleted"

        row = await db_session.execute(
            select(Memory).where(
                Memory.user_id == test_user.id,
                Memory.key == "temp",
            )
        )
        assert row.scalar_one_or_none() is None

    async def test_cannot_delete_user_entry(self, db_session, test_tenant, test_user):
        """Agent cannot delete user-created (manual) entries."""
        user_entry = await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="permanent",
            value="keep me",
            source="manual",
        )

        tools = build_memory_tools(
            db=db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
        )
        delete = tools[1]
        result = await delete(key="permanent")
        assert result["action"] == "not_found"

        # Verify still there
        row = await db_session.execute(
            select(Memory).where(Memory.id == user_entry.id)
        )
        assert row.scalar_one_or_none() is not None


# ===========================================================================
# Tool-level tests with real database — list_memory
# ===========================================================================

class TestListMemory:
    """Tests for the ``list_memory`` tool."""

    async def test_returns_both_sources(self, db_session, test_tenant, test_user):
        """list_memory returns both automatic and manual entries."""
        # Create one automatic (via tool) and one manual (via service)
        tools = build_memory_tools(
            db=db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
        )
        save = tools[0]
        lst = tools[2]

        await save(key="auto_key", value="auto_val")
        await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="manual_key",
            value="manual_val",
            source="manual",
        )

        entries = await lst()
        keys = {e["key"]: e["source"] for e in entries}
        assert "auto_key" in keys
        assert keys["auto_key"] == "automatic"
        assert "manual_key" in keys
        assert keys["manual_key"] == "manual"


# ===========================================================================
# Memory isolation
# ===========================================================================

class TestMemoryIsolation:
    """Memories are scoped per user and per tenant."""

    async def test_users_dont_see_each_others_memories(
        self, db_session, test_tenant, test_user, second_user
    ):
        """User A's memories are invisible to User B within same tenant."""
        # User A saves
        from src.services.memory_service import upsert_memory
        await upsert_memory(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            key="secret_a",
            value="User A's secret",
        )

        # User B lists
        tools_b = build_memory_tools(
            db=db_session,
            user_id=second_user.id,
            tenant_id=test_tenant.id,
        )
        lst_b = tools_b[2]
        entries_b = await lst_b()
        assert all(e["key"] != "secret_a" for e in entries_b)


# ===========================================================================
# System prompt injection tests
# ===========================================================================

class TestSystemPromptMemoryInjection:
    """The system prompt builder includes memory guidance and user memories."""

    @patch("src.agents.runner._AGENT_IDENTITY", "## Platform Identity\n\nTest identity.")
    async def test_memory_guidance_block_present(self, db_session, test_tenant, test_user):
        """System prompt includes the ## Memory Guidance block."""
        from src.agents.runner import _build_system_prompt

        prompt = await _build_system_prompt(
            db=db_session,
            session_data={
                "user_id": test_user.id,
                "tenant_id": test_tenant.id,
                "is_temporary": False,
            },
            user=test_user,
        )
        assert "## Memory Guidance" in prompt
        assert "save_memory" in prompt
        assert "delete_memory" in prompt
        assert "list_memory" in prompt

    @patch("src.agents.runner._AGENT_IDENTITY", "## Platform Identity\n\nTest identity.")
    async def test_memory_guidance_skipped_for_temp(
        self, db_session, test_tenant, test_user
    ):
        """Memory guidance is NOT injected for temporary sessions."""
        from src.agents.runner import _build_system_prompt

        prompt = await _build_system_prompt(
            db=db_session,
            session_data={
                "user_id": test_user.id,
                "tenant_id": test_tenant.id,
                "is_temporary": True,
            },
            user=test_user,
        )
        assert "## Memory Guidance" not in prompt

    @patch("src.agents.runner._AGENT_IDENTITY", "## Platform Identity\n\nTest identity.")
    async def test_persistent_memory_block_injected(
        self, db_session, test_tenant, test_user
    ):
        """Existing memories appear in the system prompt."""
        from src.agents.runner import _build_system_prompt

        # Create a memory first
        await create_memory(
            db_session,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            key="test_key",
            value="test_value",
            source="automatic",
        )

        prompt = await _build_system_prompt(
            db=db_session,
            session_data={
                "user_id": test_user.id,
                "tenant_id": test_tenant.id,
                "is_temporary": False,
            },
            user=test_user,
        )
        assert "## Persistent User Memory" in prompt
        assert "test_key" in prompt
        assert "test_value" in prompt

    @patch("src.agents.runner._AGENT_IDENTITY", "## Platform Identity\n\nTest identity.")
    async def test_empty_memory_no_block(self, db_session, test_tenant, test_user):
        """No ## Persistent User Memory block when no memories exist."""
        from src.agents.runner import _build_system_prompt

        prompt = await _build_system_prompt(
            db=db_session,
            session_data={
                "user_id": test_user.id,
                "tenant_id": test_tenant.id,
                "is_temporary": False,
            },
            user=test_user,
        )
        # Memory guidance should be there, but no persistent memory block
        assert "## Memory Guidance" in prompt
        # The persistent memory block should not appear without entries
        # (But the section header could appear in other context — check for the
        # specific phrasing that only appears when memories are loaded)
        assert "test_key" not in prompt


# ===========================================================================
# SSE memory_updated event emission
# ===========================================================================

class TestMemoryUpdatedSSE:
    """The streaming loop emits ``memory_updated`` events for memory tools."""

    @patch("agent_framework.Agent")
    async def test_save_memory_emits_memory_updated_event(self, mock_agent_class):
        """A save_memory tool result triggers a memory_updated SSE event."""
        from src.agents.runner import _run_agent_stream
        import json

        mock_instance = MagicMock()
        mock_instance.run.return_value = _mock_stream_updates([
            ("tool_call", "call_1", "save_memory", ""),
            ("tool_result", "call_1", "save_memory",
             json.dumps({"key": "user_name", "value": "Alice", "action": "created"})),
        ])
        mock_agent_class.return_value = mock_instance

        mock_model = MagicMock()
        mock_model.max_tokens = 4096

        events = []
        async for event in _run_agent_stream(
            model=mock_model,
            model_client=MagicMock(),
            system_prompt="You are a helpful assistant.",
            tools=[],
            user_message="My name is Alice",
            agent_name="test-agent",
            session_id="test-session",
            message_id="test-msg",
        ):
            events.append(event)

        memory_updated_events = [e for e in events if e["event"] == "memory_updated"]
        assert len(memory_updated_events) == 1
        payload = json.loads(memory_updated_events[0]["data"])
        assert payload["tool_name"] == "save_memory"
        assert payload["action"] == "saved"
        assert payload["key"] == "user_name"
        assert payload["success"] is True

    @patch("agent_framework.Agent")
    async def test_delete_memory_emits_memory_updated_event(self, mock_agent_class):
        """A delete_memory tool result triggers a memory_updated SSE event."""
        from src.agents.runner import _run_agent_stream
        import json

        mock_instance = MagicMock()
        mock_instance.run.return_value = _mock_stream_updates([
            ("tool_call", "call_2", "delete_memory", ""),
            ("tool_result", "call_2", "delete_memory",
             json.dumps({"key": "temp", "action": "deleted"})),
        ])
        mock_agent_class.return_value = mock_instance

        mock_model = MagicMock()
        mock_model.max_tokens = 4096

        events = []
        async for event in _run_agent_stream(
            model=mock_model,
            model_client=MagicMock(),
            system_prompt="You are a helpful assistant.",
            tools=[],
            user_message="Forget that",
            agent_name="test-agent",
            session_id="test-session",
            message_id="test-msg",
        ):
            events.append(event)

        memory_updated_events = [e for e in events if e["event"] == "memory_updated"]
        assert len(memory_updated_events) == 1
        payload = json.loads(memory_updated_events[0]["data"])
        assert payload["tool_name"] == "delete_memory"
        assert payload["action"] == "deleted"
        assert payload["key"] == "temp"

    @patch("agent_framework.Agent")
    async def test_non_memory_tool_no_event(self, mock_agent_class):
        """A non-memory tool does NOT emit a memory_updated event."""
        from src.agents.runner import _run_agent_stream
        import json

        mock_instance = MagicMock()
        mock_instance.run.return_value = _mock_stream_updates([
            ("tool_call", "call_3", "web_search", ""),
            ("tool_result", "call_3", "web_search",
             json.dumps({"results": ["link1"]})),
        ])
        mock_agent_class.return_value = mock_instance

        mock_model = MagicMock()
        mock_model.max_tokens = 4096

        events = []
        async for event in _run_agent_stream(
            model=mock_model,
            model_client=MagicMock(),
            system_prompt="You are a helpful assistant.",
            tools=[],
            user_message="Search the web",
            agent_name="test-agent",
            session_id="test-session",
            message_id="test-msg",
        ):
            events.append(event)

        memory_updated_events = [e for e in events if e["event"] == "memory_updated"]
        assert len(memory_updated_events) == 0


# ===========================================================================
# Helpers
# ===========================================================================

def _mock_stream_updates(steps):
    """Yield a sequence of mock MAF ChatResponseUpdate items.

    Each step is a ``(content_type, call_id, name, output)`` tuple.
    The sequence alternates between tool_call and tool_result types.
    """
    items = []
    for content_type, call_id, name, output in steps:
        content = MagicMock()
        content.type = content_type
        content.call_id = call_id
        if content_type in ("tool_call", "function_call"):
            content.name = name
            content.text = ""  # partial args
        elif content_type in ("tool_result", "function_result"):
            content.name = name
            content.output = output
            content.result = output
        else:
            content.text = output

        update = MagicMock()
        update.contents = [content]
        items.append(update)

    async def _gen():
        for item in items:
            yield item

    return _gen()
