# =============================================================================
# PH Agent Hub — Workflow Checkpoint Pause Tests (Chunk A16)
# =============================================================================
# End-to-end proof that a workflow which pauses awaiting human input
# persists a durable checkpoint, is correctly reported as paused rather than
# finished, has its pending request re-emitted when resumed into a fresh
# instance, and can then have that request answered to completion — all
# through the real MariaDB checkpoint storage.
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock

from agent_framework import (
    Executor,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowEvent,
    WorkflowRunState,
    handler,
    response_handler,
)
from src.agents.workflows.checkpoint_storage import MariaDBCheckpointStorage
from src.agents.workflows.engine import workflow_outcome
from src.services.workflow_checkpoint_service import latest_resumable_checkpoint

# =============================================================================
# Custom executor — emits one request_info event and handles the response.
# =============================================================================


class ApprovalExecutor(Executor):
    """Emits one request_info event and handles the response to it."""

    def __init__(self, id: str = "ask") -> None:
        super().__init__(id=id)
        self.responses: list[str] = []

    @handler
    async def start(self, message: str, ctx: WorkflowContext) -> None:
        await ctx.request_info({"question": "approve?"}, str, request_id="req-1")

    @response_handler
    async def on_response(
        self, original_request: dict, response: str, ctx: WorkflowContext
    ) -> None:
        self.responses.append(response)
        await ctx.yield_output(f"answered:{response}")


# =============================================================================
# Test 1 — a paused run persists a durable checkpoint and re-emits the
#          pending request when resumed from a fresh instance.
# =============================================================================


@pytest.mark.integration
async def test_paused_run_persists_and_reemits_pending_request(
    db_session, test_tenant
):
    # --- Shared setup ----------------------------------------------------
    storage = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )
    builder_name = f"{test_tenant.id}:approval"

    # --- Run 1: first workflow instance — pauses -------------------------
    executor1 = ApprovalExecutor("ask")
    workflow1 = WorkflowBuilder(
        name=builder_name,
        description="pause",
        start_executor=executor1,
        checkpoint_storage=storage,
    ).build()

    stream1 = workflow1.run("go", stream=True, checkpoint_storage=storage)
    events1 = [ev async for ev in stream1]

    # (a) Assert at least one request_info event was emitted with the
    #     expected request_id.
    request_info_events1 = [
        ev for ev in events1 if ev.type == "request_info"
    ]
    assert len(request_info_events1) >= 1, (
        f"Expected at least one request_info event in run 1, got {len(events1)} events: "
        f"{[ev.type for ev in events1]}"
    )
    req1_events1 = [
        ev for ev in request_info_events1 if ev.request_id == "req-1"
    ]
    assert len(req1_events1) >= 1, (
        f"Expected request_info event with request_id='req-1' in run 1, "
        f"got request_ids: {[ev.request_id for ev in request_info_events1]}"
    )

    # (b) Assert the final state is IDLE_WITH_PENDING_REQUESTS and
    #     workflow_outcome reports "paused".
    final1 = await stream1.get_final_response()
    state1 = final1.get_final_state()
    assert state1 is WorkflowRunState.IDLE_WITH_PENDING_REQUESTS, (
        f"Expected IDLE_WITH_PENDING_REQUESTS, got {state1}"
    )
    assert workflow_outcome(final1) == "paused", (
        f"Expected 'paused', got {workflow_outcome(final1)}"
    )

    # (c) Assert a durable checkpoint was persisted.
    record = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    assert record is not None, (
        "Expected a resumable checkpoint record in the database"
    )

    # --- Run 2: fresh workflow instance, resumed from the checkpoint -----
    # MAF refuses a second run on an instance that still has an active
    # paused run, so we build a brand-new workflow instance for resume.
    executor2 = ApprovalExecutor("ask")
    workflow2 = WorkflowBuilder(
        name=builder_name,
        description="resume",
        start_executor=executor2,
        checkpoint_storage=storage,
    ).build()

    stream2 = workflow2.run(
        checkpoint_id=record.id, stream=True, checkpoint_storage=storage
    )
    events2 = [ev async for ev in stream2]

    # (c) Assert the pending request was re-emitted on resume.
    request_info_events2 = [
        ev for ev in events2 if ev.type == "request_info"
    ]
    req1_events2 = [
        ev for ev in request_info_events2 if ev.request_id == "req-1"
    ]
    assert len(req1_events2) >= 1, (
        f"Expected request_info event with request_id='req-1' re-emitted "
        f"on resume, but got {len(events2)} events: "
        f"{[ev.type for ev in events2]}"
    )


# =============================================================================
# Test 2 — the pending request can be answered (via responses=…) to
#          complete the workflow.
# =============================================================================


@pytest.mark.integration
async def test_pending_request_can_be_answered_to_completion(
    db_session, test_tenant
):
    # --- Shared setup ----------------------------------------------------
    storage = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S2",
        message_id="M1",
        session_factory=lambda: db_session,
    )
    builder_name = f"{test_tenant.id}:approval2"

    # --- Run 1: first workflow instance — pauses -------------------------
    executor1 = ApprovalExecutor("ask")
    workflow1 = WorkflowBuilder(
        name=builder_name,
        description="pause-then-answer",
        start_executor=executor1,
        checkpoint_storage=storage,
    ).build()

    stream1 = workflow1.run("go", stream=True, checkpoint_storage=storage)
    [ev async for ev in stream1]

    final1 = await stream1.get_final_response()
    assert final1.get_final_state() is WorkflowRunState.IDLE_WITH_PENDING_REQUESTS
    assert workflow_outcome(final1) == "paused"

    record = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S2"
    )
    assert record is not None

    # --- Run 3: resume with the response supplied ------------------------
    executor2 = ApprovalExecutor("ask")
    workflow2 = WorkflowBuilder(
        name=builder_name,
        description="resume-with-response",
        start_executor=executor2,
        checkpoint_storage=storage,
    ).build()

    stream3 = workflow2.run(
        checkpoint_id=record.id,
        responses={"req-1": "yes"},
        stream=True,
        checkpoint_storage=storage,
    )
    events3 = [ev async for ev in stream3]
    final3 = await stream3.get_final_response()

    assert workflow_outcome(final3) == "completed", (
        f"Expected 'completed' after answering the pending request, "
        f"got {workflow_outcome(final3)}"
    )

    outputs = [str(o) for o in final3.get_outputs()]
    assert "answered:yes" in outputs, (
        f"Expected 'answered:yes' in outputs, got: {outputs}"
    )
