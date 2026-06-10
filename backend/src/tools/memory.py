# =============================================================================
# PH Agent Hub — Agent Memory Tools (Built-in)
# =============================================================================
# MAF @tool functions that let the agent persist, delete, and list memory
# entries.  Always available — unconditionally appended in
# ``_resolve_tool_callables()`` alongside the file_list tools.
# =============================================================================

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from agent_framework import tool

from ..db.orm.memory import Memory

logger = logging.getLogger(__name__)


def build_memory_tools(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
) -> list:
    """Return built-in MAF @tools for persistent agent memory.

    These tools operate on **global** memory entries (``session_id IS NULL``),
    allowing the agent to persist facts, preferences, and decisions across
    all conversations for the same user.

    Args:
        db: Active async DB session.
        user_id: The current user's ID.
        tenant_id: The current tenant's ID.
    """

    @tool
    async def save_memory(key: str, value: str) -> dict[str, Any]:
        """Save a piece of information to persistent user memory.

        Use this to remember facts, preferences, decisions, or any other
        information the user wants you to keep across conversations.
        If a memory entry with the same ``key`` already exists, it will be
        updated.

        Args:
            key: A short, descriptive key for the memory (e.g. "user_name",
                "preferred_language", "project_deadline").
            value: The information to store.  Can be any text.

        Returns:
            A dict with ``key``, ``value``, and ``action`` ("created" or
            "updated") to confirm the operation.
        """
        try:
            # Check if a global memory entry with this key already exists
            result = await db.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.tenant_id == tenant_id,
                    Memory.session_id.is_(None),
                    Memory.key == key,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.value = value
                await db.commit()
                logger.debug(
                    "save_memory updated key=%r for user=%s", key, user_id
                )
                return {
                    "key": key,
                    "value": value,
                    "action": "updated",
                }

            # Create new global memory entry
            memory = Memory(
                tenant_id=tenant_id,
                user_id=user_id,
                key=key,
                value=value,
                session_id=None,
                source="automatic",
            )
            db.add(memory)
            await db.commit()
            logger.debug(
                "save_memory created key=%r for user=%s", key, user_id
            )
            return {
                "key": key,
                "value": value,
                "action": "created",
            }
        except Exception as exc:
            logger.error(
                "save_memory failed for key=%r user=%s: %s", key, user_id, exc
            )
            return {
                "key": key,
                "value": value,
                "action": "error",
                "message": f"Failed to save memory: {exc}",
            }

    @tool
    async def delete_memory(key: str) -> dict[str, Any]:
        """Delete a memory entry by its key.

        Removes a previously saved memory entry.  Only deletes entries
        you (the agent) created — will not affect user-managed entries.

        Args:
            key: The key of the memory entry to delete.

        Returns:
            A dict with ``key``, ``action`` ("deleted" or "not_found"),
            and an optional ``message`` field.
        """
        try:
            result = await db.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.tenant_id == tenant_id,
                    Memory.session_id.is_(None),
                    Memory.key == key,
                    Memory.source == "automatic",  # Only delete agent-created entries
                )
            )
            existing = result.scalar_one_or_none()

            if not existing:
                return {
                    "key": key,
                    "action": "not_found",
                    "message": f"No automatic memory entry found with key {key!r}",
                }

            await db.execute(
                delete(Memory).where(Memory.id == existing.id)
            )
            await db.commit()
            logger.debug(
                "delete_memory deleted key=%r for user=%s", key, user_id
            )
            return {
                "key": key,
                "action": "deleted",
                "message": f"Memory entry {key!r} deleted",
            }
        except Exception as exc:
            logger.error(
                "delete_memory failed for key=%r user=%s: %s", key, user_id, exc
            )
            return {
                "key": key,
                "action": "error",
                "message": f"Failed to delete memory: {exc}",
            }

    @tool
    async def list_memory() -> list[dict[str, Any]]:
        """List all memory entries for the current user.

        Returns all global memory entries (both automatic and manual),
        including their keys, values, and whether they were created by
        the agent or the user.

        Returns:
            A list of dicts, each with ``key``, ``value``, ``source``
            ("automatic" or "manual"), and ``created_at``.
        """
        result = await db.execute(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.tenant_id == tenant_id,
                Memory.session_id.is_(None),
            ).order_by(Memory.created_at.desc())
        )
        entries = result.scalars().all()

        memory_list: list[dict[str, Any]] = []
        for entry in entries:
            memory_list.append({
                "key": entry.key,
                "value": entry.value,
                "source": entry.source,
                "created_at": (
                    entry.created_at.isoformat()
                    if entry.created_at else None
                ),
            })

        logger.debug(
            "list_memory for user=%s → %d entries", user_id, len(memory_list)
        )
        return memory_list

    return [save_memory, delete_memory, list_memory]
