# =============================================================================
# PH Agent Hub — Workflow Checkpoint Storage (MariaDB)
# =============================================================================
# MariaDB-backed checkpoint storage that satisfies the MAF ``CheckpointStorage``
# protocol.  Only ``save`` and ``load`` are implemented (chunks A4 and A10
# add listing, deletion, and retention).
#
# Key assumptions and guarantees:
#
# 1. Single-resume assumption: concurrent resume of the same checkpoint is
#    explicitly not handled, by design.  MAF coordinates single-writer access
#    to each checkpoint id.
#
# 2. Every query is filtered by ``self.tenant_id``.  There is no code path
#    that reads or writes a checkpoint without the tenant filter.
#
# 3. Each public method opens its own session because MAF calls these methods
#    with no session argument.  A caller-injected ``session_factory`` (used by
#    tests) is NOT closed: the caller owns its lifecycle.
#
# 4. ``workflow_name`` is expected to be tenant-namespaced by the caller
#    (MAF itself has no tenant dimension).
# =============================================================================

from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework import WorkflowCheckpointException

from ...db.orm.workflow_checkpoints import WorkflowCheckpointRecord
from .checkpoint_codec import decode_checkpoint, encode_checkpoint

logger = logging.getLogger(__name__)


class MariaDBCheckpointStorage:
    """MariaDB-backed checkpoint storage for MAF workflow checkpoints."""

    def __init__(
        self,
        tenant_id: str,
        *,
        session_id: str | None = None,
        message_id: str | None = None,
        session_factory: Any | None = None,
    ) -> None:
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty or whitespace-only")

        self.tenant_id: str = tenant_id
        self.session_id: str | None = session_id
        self.message_id: str | None = message_id
        self._session_factory = session_factory

    @contextlib.asynccontextmanager
    async def _session(self) -> AsyncIterator[Any]:
        """Yield a database session.

        A caller-injected ``session_factory`` is NOT closed: the caller owns
        its lifecycle (tests inject the fixture session). Only a session this
        storage created itself is closed.
        """
        if self._session_factory is not None:
            yield self._session_factory()
        else:
            from ...db.base import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                yield session

    async def save(self, checkpoint: Any) -> str:
        """Save (or upsert) a checkpoint.

        Returns the checkpoint id so the caller can verify storage.

        ``run_state`` and ``expires_at`` are deliberately left ``None`` here
        and are set by later chunks (A4 list/delete, A10 retention).
        """
        payload = encode_checkpoint(checkpoint)

        stmt = (
            insert(WorkflowCheckpointRecord)
            .values(
                id=checkpoint.checkpoint_id,
                tenant_id=self.tenant_id,
                workflow_name=checkpoint.workflow_name,
                graph_signature_hash=checkpoint.graph_signature_hash,
                session_id=self.session_id,
                message_id=self.message_id,
                previous_checkpoint_id=checkpoint.previous_checkpoint_id,
                iteration_count=checkpoint.iteration_count,
                run_state=None,
                payload=payload,
                expires_at=None,
            )
            .on_duplicate_key_update(
                payload=payload,
                run_state=None,
                expires_at=None,
            )
        )

        try:
            async with self._session() as session:
                await session.execute(stmt)
                await session.commit()
        except SQLAlchemyError as exc:
            raise WorkflowCheckpointException(
                f"Failed to save checkpoint {checkpoint.checkpoint_id}: {exc}"
            ) from exc

        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> Any:
        """Load a checkpoint by id.

        Raises ``WorkflowCheckpointException`` when no row matches or when the
        row belongs to a different tenant — the caller never learns that the
        checkpoint exists for another tenant.
        """
        stmt = (
            select(WorkflowCheckpointRecord)
            .where(
                WorkflowCheckpointRecord.id == checkpoint_id,
                WorkflowCheckpointRecord.tenant_id == self.tenant_id,
            )
            .limit(1)
        )

        async with self._session() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()

        if row is None:
            raise WorkflowCheckpointException(
                f"No checkpoint found with ID {checkpoint_id}"
            )

        return decode_checkpoint(row.payload)

    async def list_checkpoints(self, *, workflow_name: str) -> list[Any]:
        """List all checkpoint objects for a given workflow name.

        Tenant-scoped: only rows for ``self.tenant_id`` are returned.
        Ordered by ``created_at`` ascending, then ``id`` ascending.
        """
        stmt = (
            select(WorkflowCheckpointRecord)
            .where(
                WorkflowCheckpointRecord.tenant_id == self.tenant_id,
                WorkflowCheckpointRecord.workflow_name == workflow_name,
            )
            .order_by(WorkflowCheckpointRecord.created_at.asc(), WorkflowCheckpointRecord.id.asc())
        )

        try:
            async with self._session() as session:
                result = await session.execute(stmt)
                rows = result.scalars().all()
        except SQLAlchemyError as exc:
            raise WorkflowCheckpointException(
                f"Failed to list checkpoints for workflow {workflow_name}: {exc}"
            ) from exc

        return [decode_checkpoint(row.payload) for row in rows]

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        """List checkpoint ids for a given workflow name.

        Tenant-scoped: only rows for ``self.tenant_id`` are returned.
        Ordered by ``created_at`` ascending, then ``id`` ascending.
        Does NOT decode payloads.
        """
        stmt = (
            select(WorkflowCheckpointRecord.id)
            .where(
                WorkflowCheckpointRecord.tenant_id == self.tenant_id,
                WorkflowCheckpointRecord.workflow_name == workflow_name,
            )
            .order_by(WorkflowCheckpointRecord.created_at.asc(), WorkflowCheckpointRecord.id.asc())
        )

        try:
            async with self._session() as session:
                result = await session.execute(stmt)
                return [row[0] for row in result.all()]
        except SQLAlchemyError as exc:
            raise WorkflowCheckpointException(
                f"Failed to list checkpoint ids for workflow {workflow_name}: {exc}"
            ) from exc

    async def get_latest(self, *, workflow_name: str) -> Any | None:
        """Get the latest checkpoint for a given workflow name.

        Tenant-scoped: only rows for ``self.tenant_id`` are returned.
        Scoped to a tenant and a workflow name, **not** to a chat session,
        so it must never be used on its own to choose which run to resume
        when a tenant has several sessions of the same workflow.

        Checkpoint ordering is defined by the ``previous_checkpoint_id``
        lineage chain, not by ``iteration_count`` or ``created_at`` alone
        (the column has whole-second precision only).

        Implements lineage-tail logic:
        1. Select all rows for this tenant and workflow name.
        2. If no rows, return None.
        3. Build ``referenced = {row.previous_checkpoint_id for row in rows
           if row.previous_checkpoint_id}``.
        4. ``tails = [row for row in rows if row.id not in referenced]``.
           These are the ends of the lineage chains. (There can be more than
           one: each independent run starts a new chain with
           ``previous_checkpoint_id = None``.)
        5. If ``tails`` is empty (defensive, should not happen), fall back
           to all rows.
        6. Pick ``latest = max(tails, key=lambda r: (r.created_at,
           r.iteration_count, r.id))``.
        7. Return ``decode_checkpoint(latest.payload)``.
        """
        stmt = (
            select(WorkflowCheckpointRecord)
            .where(
                WorkflowCheckpointRecord.tenant_id == self.tenant_id,
                WorkflowCheckpointRecord.workflow_name == workflow_name,
            )
        )

        try:
            async with self._session() as session:
                result = await session.execute(stmt)
                rows = result.scalars().all()
        except SQLAlchemyError as exc:
            raise WorkflowCheckpointException(
                f"Failed to get latest checkpoint for workflow {workflow_name}: {exc}"
            ) from exc

        if not rows:
            return None

        # Build the set of checkpoint ids that are referenced as a parent.
        referenced = {row.previous_checkpoint_id for row in rows if row.previous_checkpoint_id}

        # Tails are checkpoints that no other checkpoint points to — i.e.
        # the ends of the lineage chains.  There can be more than one when
        # independent runs share the same workflow_name.
        tails = [row for row in rows if row.id not in referenced]

        if not tails:
            # Defensive: should not happen since a checkpoint with
            # previous_checkpoint_id=None would never be in ``referenced``.
            tails = rows

        latest = max(tails, key=lambda r: (r.created_at, r.iteration_count, r.id))
        return decode_checkpoint(latest.payload)

    async def delete(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint by id.

        Tenant-scoped: only rows for ``self.tenant_id`` are affected.
        Returns ``True`` if exactly one row was deleted, ``False`` otherwise.
        A row belonging to another tenant must return ``False``.
        """
        stmt = (
            delete(WorkflowCheckpointRecord)
            .where(
                WorkflowCheckpointRecord.id == checkpoint_id,
                WorkflowCheckpointRecord.tenant_id == self.tenant_id,
            )
        )

        try:
            async with self._session() as session:
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount == 1
        except SQLAlchemyError as exc:
            raise WorkflowCheckpointException(
                f"Failed to delete checkpoint {checkpoint_id}: {exc}"
            ) from exc

    async def set_expiry(self, checkpoint_id: str, expires_at: datetime) -> bool:
        """Set ``expires_at`` on one of this tenant's checkpoints.

        Completed runs are expired by the caller once the run outcome is
        known.  Tenant-scoped: a row belonging to another tenant is never
        modified.
        """
        stmt = (
            update(WorkflowCheckpointRecord)
            .where(
                WorkflowCheckpointRecord.id == checkpoint_id,
                WorkflowCheckpointRecord.tenant_id == self.tenant_id,
            )
            .values(expires_at=expires_at)
        )

        try:
            async with self._session() as session:
                result = await session.execute(stmt)
                await session.commit()
                return result.rowcount == 1
        except SQLAlchemyError as exc:
            raise WorkflowCheckpointException(
                f"Failed to set expiry for checkpoint {checkpoint_id}: {exc}"
            ) from exc


async def delete_expired_checkpoints(
    session_factory: Any | None = None,
    session: AsyncSession | None = None,
) -> int:
    """Delete checkpoint rows whose explicit ``expires_at`` is in the past.

    This function is intentionally NOT tenant-scoped: it is a system-wide
    retention sweep, not a tenant-scoped resolution path.  It deletes only
    rows whose explicit ``expires_at`` has already passed, so it can never
    remove a checkpoint that is still inside its retention window.  It must
    never be called from a tenant-scoped request path.
    """
    if session_factory is not None:
        async with session_factory() as session:
            stmt = (
                delete(WorkflowCheckpointRecord)
                .where(
                    WorkflowCheckpointRecord.expires_at.is_not(None),
                    WorkflowCheckpointRecord.expires_at < func.now(),
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0
    elif session is not None:
        # Caller-provided session — use it but do NOT close (caller owns it).
        stmt = (
            delete(WorkflowCheckpointRecord)
            .where(
                WorkflowCheckpointRecord.expires_at.is_not(None),
                WorkflowCheckpointRecord.expires_at < func.now(),
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0
    else:
        from ...db.base import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            stmt = (
                delete(WorkflowCheckpointRecord)
                .where(
                    WorkflowCheckpointRecord.expires_at.is_not(None),
                    WorkflowCheckpointRecord.expires_at < func.now(),
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount or 0


async def enforce_checkpoint_retention(
    ttl_seconds: int,
    session_factory: Any | None = None,
    session: AsyncSession | None = None,
) -> int:
    """Expire and delete checkpoints older than ``ttl_seconds``.

    Two steps: any row older than the cutoff whose ``expires_at`` is still
    NULL is stamped with the Python-computed cutoff timestamp (so the
    subsequent delete definitely sees it as expired), then
    :func:`delete_expired_checkpoints` removes every row past its expiry.
    Returns the number of rows deleted.  A non-positive ``ttl_seconds``
    disables the sweep and returns 0.
    """
    if ttl_seconds <= 0:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)

    if session_factory is not None:
        async with session_factory() as session:
            stmt = (
                update(WorkflowCheckpointRecord)
                .where(
                    WorkflowCheckpointRecord.expires_at.is_(None),
                    WorkflowCheckpointRecord.created_at < cutoff,
                )
                .values(expires_at=cutoff)
            )
            await session.execute(stmt)
            await session.commit()
    elif session is not None:
        # Caller-provided session — use it but do NOT close.
        stmt = (
            update(WorkflowCheckpointRecord)
            .where(
                WorkflowCheckpointRecord.expires_at.is_(None),
                WorkflowCheckpointRecord.created_at < cutoff,
            )
            .values(expires_at=cutoff)
        )
        await session.execute(stmt)
        await session.commit()
    else:
        from ...db.base import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            stmt = (
                update(WorkflowCheckpointRecord)
                .where(
                    WorkflowCheckpointRecord.expires_at.is_(None),
                    WorkflowCheckpointRecord.created_at < cutoff,
                )
                .values(expires_at=cutoff)
            )
            await session.execute(stmt)
            await session.commit()

    return await delete_expired_checkpoints(session_factory=session_factory, session=session)
