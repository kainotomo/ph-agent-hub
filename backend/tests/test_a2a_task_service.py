# =============================================================================
# PH Agent Hub — A2A Task Service Tests
# =============================================================================
# Unit tests for the a2a_task_service CRUD and state machine.
# Uses mocked DB sessions — no database required.
# =============================================================================

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.services import a2a_task_service as svc
from src.services.a2a_task_service import task_to_dict
from src.core.exceptions import NotFoundError, ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.unit]


def _mock_db() -> AsyncMock:
    """Create a mock AsyncSession with ``flush`` and ``refresh`` stubs."""
    db = AsyncMock(spec=AsyncSession)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    return db


def _mock_task(
    task_id: str | None = None,
    context_id: str | None = None,
    session_id: str = "session-1",
    state: str = svc.TASK_STATE_SUBMITTED,
) -> MagicMock:
    """Create a mock A2aTask ORM object.

    MagicMock allows both reading and setting ``.artifacts`` etc. without
    any PropertyMock trickery.
    """
    task = MagicMock()
    task.id = task_id or str(uuid.uuid4())
    task.context_id = context_id or str(uuid.uuid4())
    task.session_id = session_id
    task.state = state
    task.artifacts = None
    task.history = None
    task.status_message = None
    task.created_at = datetime.now(timezone.utc)
    task.updated_at = datetime.now(timezone.utc)
    return task


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------


class TestCreateTask:
    async def test_creates_task_with_default_state(self):
        db = _mock_db()
        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())

        result = await svc.create_task(
            db, task_id=task_id, context_id=context_id,
            session_id="sess-1",
        )

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.id == task_id
        assert added.context_id == context_id
        assert added.session_id == "sess-1"
        assert added.state == svc.TASK_STATE_SUBMITTED
        db.flush.assert_awaited_once()

    async def test_creates_task_with_custom_state(self):
        db = _mock_db()

        await svc.create_task(
            db, task_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()),
            state=svc.TASK_STATE_WORKING,
        )

        added = db.add.call_args[0][0]
        assert added.state == svc.TASK_STATE_WORKING

    async def test_creates_task_with_history(self):
        db = _mock_db()
        history = [{"role": "user", "parts": [{"text": "Hello"}]}]

        await svc.create_task(
            db, task_id=str(uuid.uuid4()), context_id=str(uuid.uuid4()),
            history=history,
        )

        added = db.add.call_args[0][0]
        # History is JSON-dumped by the service layer
        assert json.loads(added.history) == history


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


class TestGetTask:
    async def test_returns_task_when_found(self):
        db = _mock_db()
        task = _mock_task()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        db.execute = AsyncMock(return_value=result_mock)

        found = await svc.get_task(db, task.id)
        assert found is task

    async def test_raises_not_found_when_missing(self):
        db = _mock_db()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(NotFoundError, match="not found"):
            await svc.get_task(db, "nonexistent")

    async def test_returns_none_when_raise_if_missing_false(self):
        db = _mock_db()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        found = await svc.get_task(db, "nonexistent", raise_if_missing=False)
        assert found is None


# ---------------------------------------------------------------------------
# update_task_state
# ---------------------------------------------------------------------------


class TestUpdateTaskState:
    async def test_transitions_state(self):
        db = _mock_db()
        task = _mock_task(state=svc.TASK_STATE_SUBMITTED)
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))

        result = await svc.update_task_state(db, task.id, svc.TASK_STATE_WORKING)

        assert result.state == svc.TASK_STATE_WORKING
        assert result.updated_at is not None

    async def test_rejects_transition_from_terminal(self):
        db = _mock_db()
        task = _mock_task(state=svc.TASK_STATE_COMPLETED)
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))

        with pytest.raises(ValidationError, match="terminal"):
            await svc.update_task_state(db, task.id, svc.TASK_STATE_WORKING)

    async def test_rejects_transition_from_canceled(self):
        db = _mock_db()
        task = _mock_task(state=svc.TASK_STATE_CANCELED)
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))

        with pytest.raises(ValidationError):
            await svc.update_task_state(db, task.id, svc.TASK_STATE_WORKING)

    async def test_rejects_transition_from_failed(self):
        db = _mock_db()
        task = _mock_task(state=svc.TASK_STATE_FAILED)
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))

        with pytest.raises(ValidationError):
            await svc.update_task_state(db, task.id, svc.TASK_STATE_WORKING)

    async def test_sets_status_message(self):
        db = _mock_db()
        task = _mock_task(state=svc.TASK_STATE_WORKING)
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))
        status_msg = {"role": "agent", "parts": [{"text": "I need more info"}]}

        result = await svc.update_task_state(
            db, task.id, svc.TASK_STATE_INPUT_REQUIRED,
            status_message=status_msg,
        )

        assert json.loads(result.status_message) == status_msg
        assert result.state == svc.TASK_STATE_INPUT_REQUIRED

    async def test_preserves_status_message_when_none(self):
        db = _mock_db()
        task = _mock_task(state=svc.TASK_STATE_WORKING)
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))

        result = await svc.update_task_state(db, task.id, svc.TASK_STATE_COMPLETED)

        assert result.status_message is None


# ---------------------------------------------------------------------------
# add_artifact
# ---------------------------------------------------------------------------


class TestAddArtifact:
    async def test_appends_artifact_to_empty_list(self):
        db = _mock_db()
        task = _mock_task()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))
        artifact = {"artifactId": "art-1", "name": "Response", "parts": [{"text": "Hello"}]}

        result = await svc.add_artifact(db, task.id, artifact)

        assert json.loads(result.artifacts) == [artifact]

    async def test_appends_artifact_to_existing_list(self):
        db = _mock_db()
        existing = [{"artifactId": "art-0", "name": "Previous", "parts": []}]
        task = _mock_task()
        task.artifacts = json.dumps(existing)
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))
        artifact = {"artifactId": "art-1", "name": "Response", "parts": [{"text": "Hi"}]}

        result = await svc.add_artifact(db, task.id, artifact)

        assert json.loads(result.artifacts) == existing + [artifact]


# ---------------------------------------------------------------------------
# append_history
# ---------------------------------------------------------------------------


class TestAppendHistory:
    async def test_appends_entry(self):
        db = _mock_db()
        task = _mock_task()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))
        entry = {"role": "user", "parts": [{"text": "Hello"}]}

        result = await svc.append_history(db, task.id, entry)

        assert json.loads(result.history) == [entry]

    async def test_appends_to_existing_history(self):
        db = _mock_db()
        existing = [{"role": "agent", "parts": [{"text": "Hi"}]}]
        task = _mock_task()
        task.history = json.dumps(existing)
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=task),
        ))

        entry = {"role": "user", "parts": [{"text": "Question?"}]}
        result = await svc.append_history(db, task.id, entry)

        assert json.loads(result.history) == existing + [entry]


# ---------------------------------------------------------------------------
# list_active_tasks
# ---------------------------------------------------------------------------


class TestListActiveTasks:
    async def test_returns_only_non_terminal_tasks(self):
        db = _mock_db()
        working_task = _mock_task(state=svc.TASK_STATE_WORKING)
        submitted_task = _mock_task(state=svc.TASK_STATE_SUBMITTED)
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [
            working_task, submitted_task,
        ]
        db.execute = AsyncMock(return_value=result_mock)

        tasks = await svc.list_active_tasks(db)

        assert len(tasks) == 2
        assert tasks[0].state == svc.TASK_STATE_WORKING
        assert tasks[1].state == svc.TASK_STATE_SUBMITTED

    async def test_excludes_terminal_tasks(self):
        db = _mock_db()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        tasks = await svc.list_active_tasks(db)
        assert tasks == []


# ---------------------------------------------------------------------------
# task_to_dict
# ---------------------------------------------------------------------------


class TestTaskToDict:
    def test_serializes_all_fields(self):
        task = _mock_task(
            task_id="task-1", context_id="ctx-1",
            state=svc.TASK_STATE_COMPLETED,
        )
        task.artifacts = json.dumps([{"artifactId": "a1", "name": "R", "parts": []}])
        task.history = json.dumps([{"role": "user", "parts": [{"text": "Hi"}]}])

        d = task_to_dict(task)

        assert d["id"] == "task-1"
        assert d["contextId"] == "ctx-1"
        assert d["status"]["state"] == svc.TASK_STATE_COMPLETED
        assert "timestamp" in d["status"]
        assert len(d["artifacts"]) == 1
        assert len(d["history"]) == 1

    def test_includes_status_message_when_present(self):
        task = _mock_task(state=svc.TASK_STATE_INPUT_REQUIRED)
        task.status_message = json.dumps(
            {"role": "agent", "parts": [{"text": "Please clarify"}]}
        )

        d = task_to_dict(task)
        assert "message" in d["status"]
        assert d["status"]["message"]["parts"][0]["text"] == "Please clarify"

    def test_empty_artifacts_and_history_when_null(self):
        task = _mock_task(state=svc.TASK_STATE_SUBMITTED)

        d = task_to_dict(task)
        assert d["artifacts"] == []
        assert d["history"] == []


# ---------------------------------------------------------------------------
# State constant sanity
# ---------------------------------------------------------------------------


class TestStateConstants:
    def test_all_states_are_distinct(self):
        states = {
            svc.TASK_STATE_SUBMITTED,
            svc.TASK_STATE_WORKING,
            svc.TASK_STATE_INPUT_REQUIRED,
            svc.TASK_STATE_AUTH_REQUIRED,
            svc.TASK_STATE_COMPLETED,
            svc.TASK_STATE_FAILED,
            svc.TASK_STATE_CANCELED,
            svc.TASK_STATE_REJECTED,
        }
        assert len(states) == 8

    def test_terminal_states_subset(self):
        assert svc.TASK_STATE_COMPLETED in svc.TERMINAL_STATES
        assert svc.TASK_STATE_FAILED in svc.TERMINAL_STATES
        assert svc.TASK_STATE_CANCELED in svc.TERMINAL_STATES
        assert svc.TASK_STATE_REJECTED in svc.TERMINAL_STATES
        assert svc.TASK_STATE_SUBMITTED not in svc.TERMINAL_STATES
        assert svc.TASK_STATE_WORKING not in svc.TERMINAL_STATES

    def test_suspended_states_subset(self):
        assert svc.TASK_STATE_INPUT_REQUIRED in svc.SUSPENDED_STATES
        assert svc.TASK_STATE_AUTH_REQUIRED in svc.SUSPENDED_STATES
        assert svc.TASK_STATE_COMPLETED not in svc.SUSPENDED_STATES
