# =============================================================================
# PH Agent Hub — Agent Memory Tools (Built-in)
# =============================================================================
# MAF @tool functions that let the agent persist, delete, and list memory
# entries.  Always available — unconditionally appended in
# ``_resolve_tool_callables()`` alongside the file_list tools.
#
# These tools use the runner's shared ``db`` session but **commit their own
# writes**.  They cannot rely on the caller committing: the streaming chat
# path deliberately persists the assistant message through a separate session
# and rolls the agent session back when it finishes, so an uncommitted memory
# write was silently discarded and the model would then deny knowing something
# it had just claimed to remember.
#
# ``list_memory`` stays read-only and never commits.
# =============================================================================

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from agent_framework import tool

from ..core.config import settings
from ..db.orm.memory import Memory
from ..services.memory_service import (
    find_global_memory,
    prune_memories,
    remove_global_memory,
    MEMORY_KEY_MAX_CHARS,
    MEMORY_VALUE_MAX_CHARS,
)

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
        db: Active async DB session (shared with the agent runner — changes
            are persisted when the runner commits its transaction).
        user_id: The current user's ID.
        tenant_id: The current tenant's ID.
    """

    async def _enforce_limits():
        """Run prune_memories so the agent save path honours the growth policy."""
        try:
            await prune_memories(db, user_id=user_id, tenant_id=tenant_id)
        except Exception as exc:
            logger.warning(
                "prune_memories failed after save for user=%s: %s",
                user_id, exc,
            )

    @tool
    async def save_memory(key: str, value: str, overwrite: bool = False) -> dict[str, Any]:
        """Save a piece of information to persistent user memory.

        Use this to remember facts, preferences, decisions, or any other
        information the user wants you to keep across conversations.
        If a memory entry with the same ``key`` already exists, it will be
        updated.

        To overwrite a user-created (``source="manual"``) memory entry, call
        this function again with ``overwrite=True`` after the user has
        confirmed the change.

        Args:
            key: A short, descriptive key for the memory (e.g. "user_name",
                "preferred_language", "project_deadline").
            value: The information to store.  Can be any text.
            overwrite: If ``True``, overwrites any existing entry even if its
                source is ``"manual"``.  Defaults to ``False``.

        Returns:
            A dict with ``key``, ``value``, and ``action`` (one of
            ``"created"``, ``"updated"``, ``"needs_confirmation"``, or
            ``"error"``) to confirm the operation.  When
            ``"overwrote_manual": True`` is present the entry was updated
            despite having ``source="manual"``.
        """
        try:
            key = key.strip()
            value = value.strip()

            if not key:
                return {
                    "key": key,
                    "value": value,
                    "action": "error",
                    "message": "Key must not be empty.",
                }
            if len(key) > MEMORY_KEY_MAX_CHARS:
                return {
                    "key": key,
                    "value": value,
                    "action": "error",
                    "message": (
                        f"Key length {len(key)} exceeds the maximum "
                        f"of {MEMORY_KEY_MAX_CHARS} characters."
                    ),
                }
            if len(value) > MEMORY_VALUE_MAX_CHARS:
                return {
                    "key": key,
                    "value": value,
                    "action": "error",
                    "message": (
                        f"Value length {len(value)} exceeds the maximum "
                        f"of {MEMORY_VALUE_MAX_CHARS} characters."
                    ),
                }

            existing = await find_global_memory(
                db, user_id=user_id, tenant_id=tenant_id, key=key,
            )

            if existing is not None:
                if existing.source == "manual" and not overwrite:
                    return {
                        "key": key,
                        "value": value,
                        "action": "needs_confirmation",
                        "current_value": existing.value,
                        "message": (
                            f"A user-created memory with key {key!r} "
                            "already exists. Ask the user to confirm the "
                            "change, then call save_memory again with "
                            "overwrite=True."
                        ),
                    }
                if overwrite and existing.source == "manual":
                    existing.value = value
                    existing.source = "manual"
                    await db.commit()
                    await _enforce_limits()
                    logger.debug(
                        "save_memory overwrote manual key=%r for user=%s",
                        key, user_id,
                    )
                    return {
                        "key": key,
                        "value": value,
                        "action": "updated",
                        "overwrote_manual": True,
                    }
                existing.value = value
                await db.commit()
                await _enforce_limits()
                logger.debug(
                    "save_memory updated key=%r for user=%s", key, user_id
                )
                return {
                    "key": key,
                    "value": value,
                    "action": "updated",
                }

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
            await _enforce_limits()
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
    async def delete_memory(key: str, force: bool = False) -> dict[str, Any]:
        """Delete a memory entry by its key.

        Removes a previously saved memory entry.  Agent-created entries
        are deleted immediately.  User-created (``source="manual"``) entries
        require ``force=True`` — the agent must only set this after the
        explicit user has agreed to the deletion.

        Args:
            key: The key of the memory entry to delete.
            force: If ``True``, also deletes user-created (``"manual"``)
                entries.  Defaults to ``False``.

        Returns:
            A dict with ``key``, ``action`` (``"deleted"``, ``"not_found"``,
            ``"needs_confirmation"``, or ``"error"``), and an optional
            ``message`` field.
        """
        try:
            existing = await find_global_memory(
                db, user_id=user_id, tenant_id=tenant_id, key=key,
            )

            if existing is None:
                return {
                    "key": key,
                    "action": "not_found",
                    "message": f"No memory entry found with key {key!r}",
                }

            if existing.source == "manual" and not force:
                return {
                    "key": key,
                    "action": "needs_confirmation",
                    "message": (
                        f"A user-created memory with key {key!r} exists. "
                        "Ask the user to confirm deletion, then call "
                        "delete_memory with force=True."
                    ),
                }

            await remove_global_memory(
                db, user_id=user_id, tenant_id=tenant_id, key=key,
                commit=True,
            )
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
    async def list_memory() -> dict[str, Any]:
        """List all memory entries for the current user.

        Returns global memory entries (both automatic and manual),
        ordered newest first.  Output is self-bounded so the runner
        tool-output cap cannot silently clip the response.

        * At most ``settings.MEMORY_TOOL_MAX_ENTRIES`` newest entries
          are fetched from the DB (default 100).
        * Entries are then accumulated into the response while the
          running character sum stays within
          ``settings.MEMORY_TOOL_MAX_CHARS`` (default 20 000).  Each
          entry costs ``len(key) + len(value) + 32`` characters toward
          the budget.  When adding the next entry would overflow the
          budget **and** at least one entry is already present the
          loop stops early, so the model always receives something.

        Returns:
            A dict with ``entries`` (list of dicts each containing
            ``key``, ``value``, ``source``, and ``created_at``),
            ``total`` (the true count of matching entries),
            ``truncated`` (``True`` when ``total`` exceeds the number
            of returned entries),
            ``returned`` (number of entries actually returned), and,
            when ``truncated`` is ``True``, also ``omitted`` (total -
            returned) and ``message`` explaining that older entries
            were omitted to stay within the output budget.
        """
        try:
            count_result = await db.execute(
                select(func.count()).select_from(Memory).where(
                    Memory.user_id == user_id,
                    Memory.tenant_id == tenant_id,
                    Memory.session_id.is_(None),
                )
            )
            total = count_result.scalar_one()

            max_entries = settings.MEMORY_TOOL_MAX_ENTRIES
            max_chars = settings.MEMORY_TOOL_MAX_CHARS

            result = await db.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.tenant_id == tenant_id,
                    Memory.session_id.is_(None),
                )
                .order_by(Memory.created_at.desc())
                .limit(max_entries)
            )
            entries_raw = result.scalars().all()

            memory_list: list[dict[str, Any]] = []
            used = 0
            for entry in entries_raw:
                item_len = len(entry.key) + len(entry.value) + 32
                if memory_list and used + item_len > max_chars:
                    break
                memory_list.append({
                    "key": entry.key,
                    "value": entry.value,
                    "source": entry.source,
                    "created_at": (
                        entry.created_at.isoformat()
                        if entry.created_at else None
                    ),
                })
                used += item_len

            truncated = total > len(memory_list)

            resp: dict[str, Any] = {
                "entries": memory_list,
                "total": total,
                "truncated": truncated,
                "returned": len(memory_list),
            }
            if truncated:
                resp["omitted"] = total - len(memory_list)
                resp["message"] = (
                    "Older entries were omitted to stay within the output "
                    "budget. Use delete_memory to remove stale entries."
                )

            logger.debug(
                "list_memory for user=%s → %d entries (total=%d, truncated=%s)",
                user_id, len(memory_list), total, truncated,
            )
            return resp
        except Exception as exc:
            logger.error(
                "list_memory failed for user=%s: %s", user_id, exc
            )
            return {"error": str(exc)}

    return [save_memory, delete_memory, list_memory]
