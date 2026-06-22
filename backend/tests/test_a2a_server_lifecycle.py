# =============================================================================
# PH Agent Hub — A2A Server API Lifecycle Tests
# =============================================================================
# Tests for the new A2A task lifecycle endpoints (async execution, multi-turn
# resumption, cancellation, persistence).
#
# Tests call endpoint handler functions directly with mocked dependencies.
# No real database, Redis, or HTTP required.
# =============================================================================

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request, HTTPException

from src.core.config import settings as global_settings
from src.core.exceptions import NotFoundError, ValidationError
from src.api.a2a_server import (
    a2a_get_task,
    a2a_cancel_task,
    a2a_send_message,
    a2a_send_message_stream,
    get_agent_card,
    A2aSendMessageRequest,
)
from src.services import a2a_task_service as svc


pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Stub A2A settings so the router code doesn't fail."""
    monkeypatch.setattr(global_settings, "A2A_ORGANIZATION_NAME", "Test Hub")
    monkeypatch.setattr(global_settings, "A2A_PUBLIC_URL", "https://api.example.com")
    monkeypatch.setattr(global_settings, "A2A_ORGANIZATION_URL", "https://example.com")
    monkeypatch.setattr(global_settings, "A2A_DOCS_URL", "https://docs.example.com")


@pytest.fixture
def mock_db():
    """A fast Mock AsyncSession — no real DB connection."""
    return AsyncMock()


@pytest.fixture
def mock_request():
    """A mock FastAPI Request that won't trigger DB lookups."""
    req = MagicMock(spec=Request)
    req.base_url = "https://api.example.com/"
    req.state.db = None  # Agent Card handler catches this gracefully
    return req


def _make_request(
    text: str = "Hello",
    return_immediately: bool = False,
    task_id: str | None = None,
) -> A2aSendMessageRequest:
    """Build a standard A2A SendMessageRequest."""
    msg = {"parts": [{"text": text}]}
    if return_immediately:
        msg["configuration"] = {"returnImmediately": True}
    if task_id:
        msg["taskId"] = task_id
    return A2aSendMessageRequest(message=msg)


def _make_orm_task(
    task_id: str,
    context_id: str | None = None,
    session_id: str = "session-abc",
    state: str = svc.TASK_STATE_SUBMITTED,
    artifacts: str | None = None,
    history: str | None = None,
    status_message: str | None = None,
) -> MagicMock:
    """Build a mock A2aTask ORM object."""
    task = MagicMock()
    task.id = task_id
    task.context_id = context_id or str(uuid.uuid4())
    task.session_id = session_id
    task.state = state
    task.artifacts = artifacts
    task.history = history
    task.status_message = status_message
    task.created_at = datetime.now(timezone.utc)
    task.updated_at = datetime.now(timezone.utc)
    return task


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------


class TestAgentCard:
    async def test_returns_valid_card(self, mock_request):
        data = await get_agent_card(mock_request)
        assert data["name"] == "Test Hub"
        assert "supportedInterfaces" in data
        assert "capabilities" in data
        assert "skills" in data
        assert "version" in data

    async def test_capabilities(self, mock_request):
        data = await get_agent_card(mock_request)
        caps = data["capabilities"]
        assert caps["streaming"] is True
        assert caps["pushNotifications"] is False

    async def test_interfaces(self, mock_request):
        data = await get_agent_card(mock_request)
        urls = [i["url"] for i in data["supportedInterfaces"]]
        assert any("/message:send" in u for u in urls)
        assert any("/message:stream" in u for u in urls)


# ---------------------------------------------------------------------------
# a2a_send_message (sync path)
# ---------------------------------------------------------------------------


class TestSendMessageSync:
    async def test_returns_completed_task(self, mock_db, monkeypatch):
        """Sync execution returns TASK_STATE_COMPLETED with artifact."""
        task_id = str(uuid.uuid4())

        mock_session = MagicMock()
        mock_session.id = "session-abc"
        mock_session.tenant_id = "t1"
        mock_session.selected_model_id = None
        mock_session.selected_skill_id = None
        mock_session.selected_template_id = None
        mock_session.is_temporary = False
        mock_session.auto_route_enabled = True
        mock_session.auto_select_tools = True
        mock_session.thinking_enabled = None
        mock_session.temperature = None
        mock_session.cross_session_retrieval_enabled = None

        monkeypatch.setattr(
            "src.api.a2a_server.session_service.create_session",
            AsyncMock(return_value=mock_session),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.session_service.get_session_by_id",
            AsyncMock(return_value=mock_session),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.create_task", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.update_task_state", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.add_artifact", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server._run_a2a_agent",
            AsyncMock(return_value="Sync response"),
        )

        completed_orm = _make_orm_task(
            task_id, state=svc.TASK_STATE_COMPLETED,
            artifacts=json.dumps([{"artifactId": "a1", "name": "R", "parts": []}]),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(return_value=completed_orm),
        )

        result = await a2a_send_message(
            _make_request("Hello"),
            Request(scope={"type": "http"}),
            db=mock_db,
        )

        assert result["task"]["status"]["state"] == svc.TASK_STATE_COMPLETED
        assert len(result["task"]["artifacts"]) == 1


# ---------------------------------------------------------------------------
# a2a_send_message (async — returnImmediately)
# ---------------------------------------------------------------------------


class TestSendMessageAsync:
    async def test_returns_submitted_task(self, mock_db, monkeypatch):
        """returnImmediately returns TASK_STATE_SUBMITTED task."""
        mock_session = MagicMock()
        mock_session.id = "session-abc"
        monkeypatch.setattr(
            "src.api.a2a_server.session_service.create_session",
            AsyncMock(return_value=mock_session),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.create_task", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.asyncio.create_task", MagicMock(),
        )

        submitted_orm = _make_orm_task(
            str(uuid.uuid4()), state=svc.TASK_STATE_SUBMITTED,
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(return_value=submitted_orm),
        )

        result = await a2a_send_message(
            _make_request("Hello", return_immediately=True),
            Request(scope={"type": "http"}),
            db=mock_db,
        )

        assert result["task"]["status"]["state"] == svc.TASK_STATE_SUBMITTED


# ---------------------------------------------------------------------------
# a2a_send_message (multi-turn — taskId)
# ---------------------------------------------------------------------------


class TestSendMessageResume:
    async def test_resumes_input_required(self, mock_db, monkeypatch):
        """Resuming INPUT_REQUIRED returns COMPLETED."""
        task_id = str(uuid.uuid4())
        task_orm = _make_orm_task(
            task_id, state=svc.TASK_STATE_INPUT_REQUIRED,
        )

        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(return_value=task_orm),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.update_task_state", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.add_artifact", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.session_service.get_session_by_id",
            AsyncMock(return_value=MagicMock(id="session-abc")),
        )
        monkeypatch.setattr(
            "src.api.a2a_server._run_a2a_agent",
            AsyncMock(return_value="Resumed"),
        )

        completed_orm = _make_orm_task(
            task_id, state=svc.TASK_STATE_COMPLETED,
            artifacts=json.dumps([{"artifactId": "a1", "name": "R", "parts": []}]),
        )
        # Two calls to get_task: first returns suspended, second returns completed
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(side_effect=[task_orm, completed_orm]),
        )

        result = await a2a_send_message(
            _make_request("Follow-up", task_id=task_id),
            Request(scope={"type": "http"}),
            db=mock_db,
        )

        assert result["task"]["status"]["state"] == svc.TASK_STATE_COMPLETED

    async def test_rejects_resume_of_completed(self, mock_db, monkeypatch):
        """Resuming a terminal task raises HTTPException 400."""
        task_orm = _make_orm_task("done", state=svc.TASK_STATE_COMPLETED)
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(return_value=task_orm),
        )

        with pytest.raises(HTTPException) as exc:
            await a2a_send_message(
                _make_request("Nope", task_id="done"),
                Request(scope={"type": "http"}),
                db=mock_db,
            )
        assert exc.value.status_code == 400

    async def test_rejects_resume_of_nonexistent(self, mock_db, monkeypatch):
        """Resuming a nonexistent task raises HTTPException 404."""
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(side_effect=NotFoundError("not found")),
        )

        with pytest.raises(HTTPException) as exc:
            await a2a_send_message(
                _make_request("Hi", task_id="ghost"),
                Request(scope={"type": "http"}),
                db=mock_db,
            )
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}
# ---------------------------------------------------------------------------


class TestGetTask:
    async def test_returns_task(self, mock_db, monkeypatch):
        """get_task returns task dict."""
        task_id = str(uuid.uuid4())
        task_orm = _make_orm_task(task_id, state=svc.TASK_STATE_WORKING)
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(return_value=task_orm),
        )

        result = await a2a_get_task(task_id, db=mock_db)
        assert result["task"]["id"] == task_id
        assert result["task"]["status"]["state"] == svc.TASK_STATE_WORKING

    async def test_returns_404_when_not_found(self, mock_db, monkeypatch):
        """get_task raises HTTPException 404 for missing task."""
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(side_effect=NotFoundError("not found")),
        )

        with pytest.raises(HTTPException) as exc:
            await a2a_get_task("ghost", db=mock_db)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}:cancel
# ---------------------------------------------------------------------------


class TestCancelTask:
    async def test_cancels_working_task(self, mock_db, monkeypatch):
        """Cancel transitions WORKING → CANCELED."""
        task_orm = _make_orm_task("t1", session_id="s1", state=svc.TASK_STATE_WORKING)
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(return_value=task_orm),
        )
        # update_task_state returns the task with updated state
        async def _mock_update_state(_, __, state, **kw):
            task_orm.state = state
            return task_orm
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.update_task_state",
            _mock_update_state,
        )
        monkeypatch.setattr(
            "src.api.a2a_server.set_a2a_cancel", AsyncMock(),
        )

        result = await a2a_cancel_task("t1", db=mock_db)
        assert result["task"]["status"]["state"] == svc.TASK_STATE_CANCELED

    async def test_rejects_cancel_of_completed(self, mock_db, monkeypatch):
        """Cancel terminal task raises 400."""
        task_orm = _make_orm_task("t1", state=svc.TASK_STATE_COMPLETED)
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(return_value=task_orm),
        )

        with pytest.raises(HTTPException) as exc:
            await a2a_cancel_task("t1", db=mock_db)
        assert exc.value.status_code == 400

    async def test_rejects_cancel_of_nonexistent(self, mock_db, monkeypatch):
        """Cancel nonexistent task raises 404."""
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(side_effect=NotFoundError("not found")),
        )

        with pytest.raises(HTTPException) as exc:
            await a2a_cancel_task("ghost", db=mock_db)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# POST /message:stream
# ---------------------------------------------------------------------------


class TestSendMessageStream:
    async def test_sync_returns_eventsource(self, mock_db, monkeypatch):
        """Stream sync path returns an EventSourceResponse."""
        mock_session = MagicMock()
        mock_session.id = "session-abc"
        mock_session.tenant_id = "t1"
        mock_session.selected_model_id = None
        mock_session.selected_skill_id = None
        mock_session.selected_template_id = None
        mock_session.is_temporary = False
        mock_session.auto_route_enabled = True
        mock_session.auto_select_tools = True
        mock_session.thinking_enabled = None
        mock_session.temperature = None
        mock_session.cross_session_retrieval_enabled = None

        monkeypatch.setattr(
            "src.api.a2a_server.session_service.create_session",
            AsyncMock(return_value=mock_session),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.session_service.get_session_by_id",
            AsyncMock(return_value=mock_session),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.create_task", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.update_task_state", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.add_artifact", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server._run_a2a_agent",
            AsyncMock(return_value="Stream response"),
        )

        from sse_starlette.sse import EventSourceResponse

        result = await a2a_send_message_stream(
            _make_request("Hello"),
            Request(scope={"type": "http"}),
            db=mock_db,
        )
        assert isinstance(result, EventSourceResponse)

    async def test_async_returns_eventsource(self, mock_db, monkeypatch):
        """Stream with returnImmediately returns EventSourceResponse."""
        mock_session = MagicMock()
        mock_session.id = "session-abc"
        monkeypatch.setattr(
            "src.api.a2a_server.session_service.create_session",
            AsyncMock(return_value=mock_session),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.create_task", AsyncMock(),
        )
        monkeypatch.setattr(
            "src.api.a2a_server.asyncio.create_task", MagicMock(),
        )
        submitted_orm = _make_orm_task(
            str(uuid.uuid4()), state=svc.TASK_STATE_SUBMITTED,
        )
        monkeypatch.setattr(
            "src.api.a2a_server.a2a_tasks.get_task",
            AsyncMock(return_value=submitted_orm),
        )

        from sse_starlette.sse import EventSourceResponse

        result = await a2a_send_message_stream(
            _make_request("Hello", return_immediately=True),
            Request(scope={"type": "http"}),
            db=mock_db,
        )
        assert isinstance(result, EventSourceResponse)
