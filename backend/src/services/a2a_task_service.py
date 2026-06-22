# =============================================================================
# PH Agent Hub — A2A Task Service
# =============================================================================
# CRUD and lifecycle management for A2A tasks backed by the a2a_tasks table.
#
# JSON columns (artifacts, history, status_message) are stored as LONGTEXT
# in MySQL; the service layer handles serialisation/deserialisation so the
# API layer never touches raw text.
# =============================================================================

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.exceptions import NotFoundError, ValidationError
from ..db.orm.a2a_tasks import A2aTask

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# A2A task state constants
# ---------------------------------------------------------------------------

TASK_STATE_SUBMITTED = "TASK_STATE_SUBMITTED"
TASK_STATE_WORKING = "TASK_STATE_WORKING"
TASK_STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
TASK_STATE_AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"
TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
TASK_STATE_FAILED = "TASK_STATE_FAILED"
TASK_STATE_CANCELED = "TASK_STATE_CANCELED"
TASK_STATE_REJECTED = "TASK_STATE_REJECTED"

# Terminal states — task cannot transition away from these
TERMINAL_STATES = frozenset({
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_CANCELED,
    TASK_STATE_REJECTED,
})

# Suspended states — task is waiting for external input
SUSPENDED_STATES = frozenset({
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_AUTH_REQUIRED,
})


# ---------------------------------------------------------------------------
# JSON column helpers
# ---------------------------------------------------------------------------


def _json_dumps(value: object) -> str | None:
    """Serialize *value* to a JSON string, or return None."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None) -> object:
    """Deserialize a JSON string, or return None."""
    if value is None:
        return None
    return json.loads(value)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_task(
    db: AsyncSession,
    *,
    task_id: str,
    context_id: str,
    session_id: str | None = None,
    state: str = TASK_STATE_SUBMITTED,
    history: list | None = None,
) -> A2aTask:
    """Create a new A2A task record and flush to DB."""
    task = A2aTask(
        id=task_id,
        context_id=context_id,
        session_id=session_id,
        state=state,
        history=_json_dumps(history),
    )
    db.add(task)
    await db.flush()
    logger.info("Created A2A task %s (state=%s)", task_id, state)
    return task


async def get_task(
    db: AsyncSession,
    task_id: str,
    *,
    raise_if_missing: bool = True,
) -> A2aTask | None:
    """Fetch an A2A task by ID.

    Returns None when *raise_if_missing* is False and the task does not
    exist.  Otherwise raises ``NotFoundError``.
    """
    result = await db.execute(select(A2aTask).where(A2aTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None and raise_if_missing:
        raise NotFoundError(f"A2A task '{task_id}' not found")
    return task


async def update_task_state(
    db: AsyncSession,
    task_id: str,
    state: str,
    *,
    status_message: dict | None = None,
) -> A2aTask:
    """Transition an A2A task to *state* and update *updated_at*.

    Validates that the transition is legal (terminal states cannot be
    changed).  Returns the refreshed task row.
    """
    task = await get_task(db, task_id)

    if task.state in TERMINAL_STATES:
        raise ValidationError(
            f"Cannot transition task '{task_id}' from terminal state "
            f"'{task.state}' to '{state}'"
        )

    task.state = state
    if status_message is not None:
        task.status_message = _json_dumps(status_message)
    task.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(task)
    logger.info("A2A task %s state → %s", task_id, state)
    return task


async def add_artifact(
    db: AsyncSession,
    task_id: str,
    artifact: dict,
) -> A2aTask:
    """Append an artifact dict to the task's artifacts list."""
    task = await get_task(db, task_id)
    artifacts: list = _json_loads(task.artifacts) or []
    artifacts.append(artifact)
    task.artifacts = _json_dumps(artifacts)
    task.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(task)
    return task


async def append_history(
    db: AsyncSession,
    task_id: str,
    entry: dict,
) -> A2aTask:
    """Append a message-history entry to the task."""
    task = await get_task(db, task_id)
    history: list = _json_loads(task.history) or []
    history.append(entry)
    task.history = _json_dumps(history)
    task.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(task)
    return task


async def list_active_tasks(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> list[A2aTask]:
    """Return non-terminal tasks, newest first."""
    from sqlalchemy import select

    result = await db.execute(
        select(A2aTask)
        .where(A2aTask.state.notin_(TERMINAL_STATES))  # noqa: E711
        .order_by(A2aTask.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Serialisation helper (API responses)
# ---------------------------------------------------------------------------


def task_to_dict(task: A2aTask) -> dict:
    """Convert an ORM ``A2aTask`` to the A2A wire-format task dict.

    The returned dict matches the shape expected by ``/tasks/{id}``,
    ``/message:send``, and polling clients.
    """
    return {
        "id": task.id,
        "contextId": task.context_id,
        "status": {
            "state": task.state,
            "timestamp": (
                task.updated_at.isoformat()
                if task.updated_at
                else task.created_at.isoformat()
            ),
            **(
                {"message": _json_loads(task.status_message)}
                if task.status_message
                else {}
            ),
        },
        "artifacts": _json_loads(task.artifacts) or [],
        "history": _json_loads(task.history) or [],
    }
