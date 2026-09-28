# =============================================================================
# PH Agent Hub — Workflow Checkpoint Restart Tests (Chunk A15)
# =============================================================================
# End-to-end proof that:
#   (a) a run interrupted mid-way can be resumed after a simulated application
#       restart — every in-process object discarded, only the database survives;
#   (b) resuming a run to completion marks the checkpoint COMPLETED and gives
#       it an expiry, closing the retention loop.
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select

from src.agents.workflows.checkpoint_storage import MariaDBCheckpointStorage
from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.engine import build_workflow, iter_workflow_sse
from src.services.workflow_checkpoint_service import (
    RUN_STATE_COMPLETED,
    latest_resumable_checkpoint,
)
from src.services.workflow_resume_service import resume_workflow_stream
from src.db.orm.workflow_checkpoints import WorkflowCheckpointRecord

# =============================================================================
# Stub agents (module-level)
# =============================================================================


class RecordingAgent:
    """Minimal agent that records every message list it receives."""

    def __init__(self, agent_id: str, response_text: str) -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = None
        self.response_text = response_text
        self.seen: list[list[tuple[str, str]]] = []

    def create_session(self):
        from agent_framework import AgentSession

        return AgentSession()

    def run(self, messages=None, *, stream=False, session=None, **kwargs):
        from agent_framework import AgentResponse, AgentResponseUpdate, Content, Message

        rec = [(m.role, m.text) for m in (messages or [])]
        self.seen.append(rec)

        if stream:

            async def _gen():
                yield AgentResponseUpdate(
                    contents=[Content(type="text", text=self.response_text)]
                )

            return _gen()
        else:

            async def _coro():
                return AgentResponse(
                    messages=[Message("assistant", [self.response_text])]
                )

            return _coro()


class FailingAgent(RecordingAgent):
    """RecordingAgent that raises synchronously when fail_flag['fail'] is True."""

    def __init__(self, agent_id: str, response_text: str, fail_flag: dict) -> None:
        super().__init__(agent_id, response_text)
        self.fail_flag = fail_flag

    def run(self, messages=None, *, stream=False, session=None, **kwargs):
        if self.fail_flag.get("fail", False):
            raise RuntimeError("simulated step failure")
        return super().run(
            messages=messages, stream=stream, session=session, **kwargs
        )


# =============================================================================
# Shared harness: factory for _build_agent_for_step patch
# =============================================================================


def _make_factory(created, fail_flag, response_by_step):
    """Return a function suitable for patching
    ``src.agents.workflows.engine._build_agent_for_step``.

    * Step ``b`` gets a ``FailingAgent`` whose ``fail_flag`` controls
      whether it raises on this invocation.
    * All other steps get a plain ``RecordingAgent``.
    * Every agent built is recorded in ``created[step.id]`` so the tests can
      inspect which agents ran on which pass.
    """

    def factory(step, model, tools=None, **kwargs):
        if step.id == "b":
            agent = FailingAgent(step.id, response_by_step[step.id], fail_flag=fail_flag)
        else:
            agent = RecordingAgent(step.id, response_by_step[step.id])
        created.setdefault(step.id, []).append(agent)
        return agent

    return factory


# =============================================================================
# Workflow definition used by both tests
# =============================================================================

defn = WorkflowDefinition(
    key="research", name="Research", description="restart",
    steps=[
        {"id": "a", "name": "A", "type": "inline", "instructions": "step a"},
        {"id": "b", "name": "B", "type": "inline", "instructions": "step b", "input": "user_message"},
        {"id": "c", "name": "C", "type": "inline", "instructions": "step c", "input": "output_of:a"},
    ],
)

RESPONSES = {"a": "OUT-a", "b": "OUT-b", "c": "OUT-c"}


# =============================================================================
# Test 1 — resume after simulated application restart
# =============================================================================

@pytest.mark.integration
async def test_resume_after_simulated_restart(db_session, test_tenant):
    """A run interrupted at step b can be resumed after the process is fully
    restarted: every in-process object is discarded and only the database
    persists.  The resumed run must NOT re-execute step a (its state was
    checkpointed to the database)."""

    created: dict[str, list] = {}

    model_mock = MagicMock(max_tokens=1000)

    # --- Phase 1: interrupted run ------------------------------------------
    # Build a storage instance, run until step b fails, then discard everything.
    storage1 = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )
    fail_flag = {"fail": True}

    with (
        patch("src.agents.workflows.engine.resolve_model", AsyncMock(return_value=model_mock)),
        patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=_make_factory(created, fail_flag, RESPONSES),
        ),
    ):
        workflow1 = await build_workflow(
            defn=defn,
            db=db_session,
            tenant_id=test_tenant.id,
            checkpoint_storage=storage1,
        )

        events1 = [
            e async for e in iter_workflow_sse(
                workflow1,
                message="HI",
                session_id="S1",
                message_id="M1",
                checkpoint_storage=storage1,
            )
        ]

    # Verify step a actually executed in the first run so the later "not re-run"
    # assertion is meaningful.
    assert len(created["a"][0].seen) > 0

    # Step b should have raised — the first run did NOT complete.
    # Verify we got at least one workflow_step event (step a started/completed).
    step_started = [e for e in events1 if e.get("event") == "workflow_step"]
    assert len(step_started) >= 1

    # --- Phase 2: simulate restart -----------------------------------------
    # Locate the resumable checkpoint.
    record = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    assert record is not None

    # Discard every in-process object: storage1, workflow1, the old created
    # registry, the old event list.  Only the database survives.
    del storage1
    del workflow1
    del created
    del events1

    # Build brand-new objects with a fresh created registry.
    created = {}
    fail_flag["fail"] = False  # step b now succeeds

    storage2 = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )

    # --- Phase 3: resume from checkpoint -----------------------------------
    with (
        patch("src.agents.workflows.engine.resolve_model", AsyncMock(return_value=model_mock)),
        patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=_make_factory(created, fail_flag, RESPONSES),
        ),
    ):
        events2 = [
            e async for e in resume_workflow_stream(
                db=db_session,
                tenant_id=test_tenant.id,
                session_id="S1",
                message_id="M1",
                defn=defn,
                checkpoint_storage=storage2,
            )
        ]

    # --- Assertions --------------------------------------------------------

    # 1. Step a agent built for the resumed run was NEVER invoked — step a was
    #    already completed in the first run (its output was in the database).
    assert created["a"][-1].seen == []

    # 2. Step b agent built for the resumed run WAS invoked.
    assert len(created["b"][-1].seen) > 0

    # 3. Step c received step a's first-run output from the database.
    assert ("user", "OUT-a") in created["c"][-1].seen[-1]


# =============================================================================
# Test 2 — completed resume marks checkpoint and sets expiry
# =============================================================================

@pytest.mark.integration
async def test_completed_resume_marks_checkpoint_and_sets_expiry(db_session, test_tenant):
    """After resuming an interrupted run to completion, the checkpoint row that
    the resume started from must be marked COMPLETED and given an ``expires_at``
    timestamp, enabling the periodic retention sweep to reclaim it."""

    created: dict[str, list] = {}

    model_mock = MagicMock(max_tokens=1000)

    # --- Phase 1: interrupted run ------------------------------------------
    storage1 = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )
    fail_flag = {"fail": True}

    with (
        patch("src.agents.workflows.engine.resolve_model", AsyncMock(return_value=model_mock)),
        patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=_make_factory(created, fail_flag, RESPONSES),
        ),
    ):
        workflow1 = await build_workflow(
            defn=defn,
            db=db_session,
            tenant_id=test_tenant.id,
            checkpoint_storage=storage1,
        )

        [e async for e in iter_workflow_sse(
            workflow1,
            message="HI",
            session_id="S1",
            message_id="M1",
            checkpoint_storage=storage1,
        )]

    # --- Phase 2: simulate restart -----------------------------------------
    record = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    assert record is not None

    # Discard every in-process object — only the database survives.
    del storage1
    del workflow1
    del created

    created = {}
    fail_flag["fail"] = False

    storage2 = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )

    # --- Phase 3: resume to completion -------------------------------------
    with (
        patch("src.agents.workflows.engine.resolve_model", AsyncMock(return_value=model_mock)),
        patch(
            "src.agents.workflows.engine._build_agent_for_step",
            side_effect=_make_factory(created, fail_flag, RESPONSES),
        ),
    ):
        [e async for e in resume_workflow_stream(
            db=db_session,
            tenant_id=test_tenant.id,
            session_id="S1",
            message_id="M1",
            defn=defn,
            checkpoint_storage=storage2,
        )]

    # --- Assertions --------------------------------------------------------
    # Reload the row the resume started from to avoid session cache staleness.
    row = (await db_session.execute(
        select(WorkflowCheckpointRecord).where(
            WorkflowCheckpointRecord.id == record.id
        )
    )).scalars().first()

    assert row is not None
    assert row.run_state == RUN_STATE_COMPLETED
    assert row.expires_at is not None
