# =============================================================================
# PH Agent Hub — Workflow Checkpoint E2E Tests (Chunk A13)
# =============================================================================
# End-to-end proof that a workflow interrupted after N steps resumes and
# completes without redoing the first N steps, and that the interrupted run's
# cross-step state (user_message and output_of:<step>) is recovered from the
# checkpoint.
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.workflows.checkpoint_storage import MariaDBCheckpointStorage
from src.agents.workflows.definition import WorkflowDefinition
from src.agents.workflows.engine import build_workflow, iter_workflow_sse
from src.services.workflow_checkpoint_service import latest_resumable_checkpoint
from src.services.workflow_resume_service import resume_workflow_stream

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
    key="research",
    name="Research",
    description="e2e",
    steps=[
        {"id": "a", "name": "A", "type": "inline", "instructions": "step a"},
        {"id": "b", "name": "B", "type": "inline", "instructions": "step b", "input": "user_message"},
        {"id": "c", "name": "C", "type": "inline", "instructions": "step c", "input": "output_of:a"},
    ],
)

RESPONSES = {"a": "OUT-a", "b": "OUT-b", "c": "OUT-c"}


# =============================================================================
# Test 1 — resume does not redo completed steps
# =============================================================================

@pytest.mark.integration
async def test_resume_after_interrupted_run_does_not_redo_completed_steps(
    db_session, test_tenant
):
    """A run that fails partway saves a resumable checkpoint.  On resume the
    already-completed steps are skipped — their agents are built but never
    invoked (``seen`` stays empty)."""

    # --- Setup -----------------------------------------------------------
    storage = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )

    created: dict[str, list] = {}
    fail_flag = {"fail": True}  # step b fails on first pass

    model_mock = MagicMock(max_tokens=1000)

    # --- First run: interrupted at step b -------------------------------
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
            checkpoint_storage=storage,
        )

        events1 = [
            e async for e in iter_workflow_sse(
                workflow1,
                message="HI",
                session_id="S1",
                message_id="M1",
                checkpoint_storage=storage,
            )
        ]

    # Step b should have raised — the first run did NOT complete.
    # Verify we got at least one workflow_step event (step a started/completed).
    step_started = [e for e in events1 if e.get("event") == "workflow_step"]
    assert len(step_started) >= 1

    # --- Verify resumable checkpoint exists ------------------------------
    record = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    assert record is not None

    # --- Second run: resumed (fail_flag cleared so b succeeds) ---------
    fail_flag["fail"] = False

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
                checkpoint_storage=storage,
            )
        ]

        # --- Assertions ------------------------------------------------------

        # 1. Step a agent built for the resumed run was NEVER invoked — step a
        #    was already completed in the first run.
        assert created["a"][-1].seen == []

        # 2. Step b agent built for the resumed run WAS invoked (it's the first
        #    step on the resumed run).
        assert len(created["b"][-1].seen) > 0

        # 3. No error event in the resumed run.
        error_events = [e for e in events2 if e.get("event") == "error"]
        assert len(error_events) == 0


# =============================================================================
# Test 2 — checkpoint recovers cross-step state
# =============================================================================

@pytest.mark.integration
async def test_resume_recovers_user_message_and_output_of_from_checkpoint(
    db_session, test_tenant
):
    """On resume, step b receives the original user_message from the
    checkpoint (not a downstream step's output), and step c receives
    output_of:a resolved from the first run's checkpointed state."""

    # --- Setup -----------------------------------------------------------
    storage = MariaDBCheckpointStorage(
        tenant_id=test_tenant.id,
        session_id="S1",
        message_id="M1",
        session_factory=lambda: db_session,
    )

    created: dict[str, list] = {}
    fail_flag = {"fail": True}  # step b fails on first pass

    model_mock = MagicMock(max_tokens=1000)

    # --- First run: interrupted at step b -------------------------------
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
            checkpoint_storage=storage,
        )

        events1 = [
            e async for e in iter_workflow_sse(
                workflow1,
                message="HI",
                session_id="S1",
                message_id="M1",
                checkpoint_storage=storage,
            )
        ]

    # --- Verify resumable checkpoint exists ------------------------------
    record = await latest_resumable_checkpoint(
        db_session, test_tenant.id, "S1"
    )
    assert record is not None

    # --- Second run: resumed (fail_flag cleared so b succeeds) ---------
    fail_flag["fail"] = False

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
                checkpoint_storage=storage,
            )
        ]

        # --- Assertions ------------------------------------------------------

        # 1. Step b receives the original user message ("HI") from the
        #    checkpoint's user_message field — NOT a downstream step's output.
        b_seen_last = created["b"][-1].seen[-1]  # last messages received by b
        b_messages = dict(b_seen_last)
        assert ("user", "HI") in b_seen_last, (
            f"Expected ('user', 'HI') in b's received messages, got: {b_seen_last}"
        )

        # 2. Step c receives output_of:a resolved to step a's output from the
        #    first run (which the resumed run never re-executed).
        c_seen_last = created["c"][-1].seen[-1]
        assert ("user", "OUT-a") in c_seen_last, (
            f"Expected ('user', 'OUT-a') in c's received messages, got: {c_seen_last}"
        )
