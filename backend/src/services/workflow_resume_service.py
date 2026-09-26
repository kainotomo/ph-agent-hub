# =============================================================================
# PH Agent Hub — Workflow Resume Service
# =============================================================================
# Durable resume entry point: given a chat session, locate its latest resumable
# checkpoint, validate it against the current definition, recover the run's
# cross-step state, build a fresh MAF workflow instance seeded with that state,
# and resume the run from the checkpoint id.
#
# MAF's ``Workflow.run()`` raises ``WorkflowException`` when the instance
# already has an active run, and a paused run keeps its instance active — so a
# resumed run MUST use a **fresh** workflow instance built here via
# ``build_workflow``.
#
# The fresh instance is seeded with the cross-step state snapshot recovered
# from the checkpoint, because the step that runs next has an empty session of
# its own.
#
# A checkpoint is addressed by ``(tenant_id, workflow_name=f"{tenant_id}:{key}",
# checkpoint_id)``; DSH stores the session/message mapping itself because MAF
# has no session dimension.
#
# This is the durable-resume primitive.  Exposing it to the chat UI is a later
# milestone; nothing in this issue wires it to a route.
# =============================================================================

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.workflows.checkpoint_storage import MariaDBCheckpointStorage
from ..agents.workflows.definition import WorkflowDefinition
from ..agents.workflows.engine import build_workflow, iter_workflow_sse
from ..core.config import settings
from ..core.exceptions import NotFoundError
from .workflow_checkpoint_service import (
    RUN_STATE_COMPLETED,
    assert_resumable,
    extract_state_snapshot,
    latest_resumable_checkpoint,
    mark_checkpoint_state,
)

logger = logging.getLogger(__name__)


async def resume_workflow_stream(
    *,
    db: AsyncSession,
    tenant_id: str,
    session_id: str,
    message_id: str,
    defn: WorkflowDefinition,
    checkpoint_storage: MariaDBCheckpointStorage,
    extra_tools: list | None = None,
    base_temperature: float = 0.7,
    base_reasoning_effort: str | None = None,
    default_model_id: str | None = None,
) -> AsyncIterator[dict]:
    """Resume a workflow run from its latest resumable checkpoint.

    This is the durable-resume primitive: locate, validate, load, extract,
    build a fresh instance, and stream the resumed run.
    """
    # 1. Locate the latest resumable checkpoint for the session
    record = await latest_resumable_checkpoint(
        db, tenant_id, session_id
    )
    if record is None:
        raise NotFoundError(
            f"No resumable workflow checkpoint for session '{session_id}'"
        )

    # 2. Validate: ensure the checkpoint is safe to resume for this tenant/definition
    assert_resumable(tenant_id, record, defn)

    # 3. Load the checkpoint from persistent storage
    checkpoint = await checkpoint_storage.load(record.id)

    # 4. Extract the cross-step state snapshot
    snapshot = extract_state_snapshot(checkpoint)

    # 5. Build a fresh workflow instance seeded with the recovered state.
    # A fresh instance is required because MAF's ``Workflow.run()`` raises
    # ``WorkflowException`` if the instance already has an active run, and a
    # paused run keeps its instance active.  Not passing ``workflow_name``
    # lets it default to ``f"{tenant_id}:{defn.key}"``, matching the name
    # used when the checkpoint was originally written.
    workflow = await build_workflow(
        defn=defn,
        db=db,
        tenant_id=tenant_id,
        extra_tools=extra_tools,
        base_temperature=base_temperature,
        base_reasoning_effort=base_reasoning_effort,
        default_model_id=default_model_id,
        checkpoint_storage=checkpoint_storage,
        initial_state=snapshot,
    )

    # 6. Stream the resumed run, forwarding each SSE event dict
    outcome: str | None = None
    async for event_dict in iter_workflow_sse(
        workflow,
        message=None,
        session_id=session_id,
        message_id=message_id,
        checkpoint_storage=checkpoint_storage,
        checkpoint_id=record.id,
        # A mutable token sink is required: `iter_workflow_sse` only emits its
        # terminal `message_complete` event when `token_counts` is not None,
        # and that event is where the run outcome ("completed" vs "paused") is
        # reported.  The resume path does not surface token accounting, so a
        # throwaway dict is used here.
        token_counts={},
    ):
        if event_dict.get("event") == "message_complete":
            try:
                outcome = json.loads(event_dict.get("data") or "{}").get("outcome")
            except (ValueError, TypeError):
                outcome = None
        yield event_dict

    # message=None is required because MAF treats `message` and `checkpoint_id`
    # as mutually exclusive; checkpoint_id is what makes this a resume rather
    # than a fresh run.

    # 7. On completion, mark the checkpoint and set expiry for retention cleanup
    if outcome == "completed":
        await mark_checkpoint_state(db, tenant_id, record.id, RUN_STATE_COMPLETED)
        if settings.WORKFLOW_CHECKPOINT_TTL_SECONDS > 0:
            await checkpoint_storage.set_expiry(
                record.id,
                datetime.now(timezone.utc) + timedelta(
                    seconds=settings.WORKFLOW_CHECKPOINT_TTL_SECONDS
                ),
            )
        # A completed run is marked and given an expiry so the periodic
        # sweep can reclaim it.  A "paused" run is not marked.
