# =============================================================================
# PH Agent Hub — A2A Input-Required Tests (Issue #411)
# =============================================================================
# Tests for the ``ask_user`` tool and the ``INPUT_REQUIRED`` task state
# transition.
#
# Mark: unit (Redis + DB mocked).
# Pattern: test_a2a_redis_cancel.py + test_a2a_server_lifecycle.py
# =============================================================================

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.redis import store_a2a_question, get_a2a_question, clear_a2a_question
from src.services import a2a_task_service as svc

pytestmark = [pytest.mark.unit]


# =========================================================================
# ask_user — Redis helpers
# =========================================================================


@pytest.fixture(autouse=True)
def _mock_redis(monkeypatch):
    """Mock ``get_redis()`` so Redis helpers work without a Redis server."""
    fake_redis = AsyncMock()
    fake_redis.setex = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.delete = AsyncMock()
    monkeypatch.setattr(
        "src.core.redis.get_redis",
        AsyncMock(return_value=fake_redis),
    )
    return fake_redis


class TestAskUserRedisHelpers:
    """``store_a2a_question``, ``get_a2a_question``, ``clear_a2a_question``."""

    async def test_store_and_get(self, _mock_redis):
        _mock_redis.get.return_value = "What is your name?"
        await store_a2a_question("task-123", "What is your name?")
        _mock_redis.setex.assert_called_once_with(
            "ask_user:task-123", 3600, "What is your name?",
        )
        result = await get_a2a_question("task-123")
        assert result == "What is your name?"

    async def test_get_returns_none_when_not_set(self, _mock_redis):
        _mock_redis.get.return_value = None
        result = await get_a2a_question("task-nonexistent")
        assert result is None

    async def test_clear(self, _mock_redis):
        await clear_a2a_question("task-123")
        _mock_redis.delete.assert_called_once_with("ask_user:task-123")


# =========================================================================
# ask_user — tool function
# =========================================================================


class TestAskUserTool:
    """The ``ask_user`` @tool function called by the agent."""

    async def test_stores_question_when_task_id_in_context(self, _mock_redis):
        """Tool stores question when context has ``task_id``."""
        from agent_framework import FunctionInvocationContext
        from src.tools.ask_user import ask_user

        ctx = MagicMock(spec=FunctionInvocationContext)
        ctx.kwargs = {"task_id": "task-123"}
        result = await ask_user(question="What is your name?", ctx=ctx)

        assert "I've asked the user" in result
        _mock_redis.setex.assert_called_once()
        args = _mock_redis.setex.call_args[0]
        assert args[0] == "ask_user:task-123"
        assert args[2] == "What is your name?"

    async def test_noop_when_no_context(self, _mock_redis):
        """Tool does nothing when called without context."""
        from src.tools.ask_user import ask_user

        result = await ask_user(question="Test?", ctx=None)

        assert "outside A2A context" in result
        _mock_redis.setex.assert_not_called()

    async def test_noop_when_no_task_id(self, _mock_redis):
        """Tool does nothing when ``task_id`` missing from kwargs."""
        from agent_framework import FunctionInvocationContext
        from src.tools.ask_user import ask_user

        ctx = MagicMock(spec=FunctionInvocationContext)
        ctx.kwargs = {}
        result = await ask_user(question="Test?", ctx=ctx)

        assert "no task_id" in result
        _mock_redis.setex.assert_not_called()


# =========================================================================
# INPUT_REQUIRED — state constants and helpers
# =========================================================================


class TestInputRequiredStateConstants:
    """Verify ``TASK_STATE_INPUT_REQUIRED`` is correctly classified."""

    def test_input_required_is_in_suspended_states(self):
        assert svc.TASK_STATE_INPUT_REQUIRED in svc.SUSPENDED_STATES

    def test_auth_required_is_in_suspended_states(self):
        assert svc.TASK_STATE_AUTH_REQUIRED in svc.SUSPENDED_STATES


# =========================================================================
# Task state transition validation
# =========================================================================


class TestInputRequiredTransitions:
    """Verify task state machine handles INPUT_REQUIRED correctly."""

    def _mock_db(self):
        db = AsyncMock(spec=AsyncMock)
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    def _make_task(self, state=svc.TASK_STATE_SUBMITTED, task_id="task-123"):
        task = MagicMock()
        task.id = task_id
        task.state = state
        task.status_message = None
        task.updated_at = MagicMock()
        return task

    async def test_can_transition_from_working_to_input_required(self):
        """WORKING → INPUT_REQUIRED is a valid transition."""
        db = self._mock_db()
        task = self._make_task(state=svc.TASK_STATE_WORKING)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        db.execute = AsyncMock(return_value=result_mock)

        result = await svc.update_task_state(
            db, "task-123", svc.TASK_STATE_INPUT_REQUIRED,
            status_message={"role": "agent", "parts": [{"text": "What is your name?"}]},
        )
        assert result.state == svc.TASK_STATE_INPUT_REQUIRED

    async def test_can_resume_from_input_required(self):
        """INPUT_REQUIRED → WORKING → COMPLETED is valid."""
        db = self._mock_db()
        result_mock = MagicMock()

        # First transition: INPUT_REQUIRED → WORKING
        task1 = self._make_task(state=svc.TASK_STATE_INPUT_REQUIRED)
        result_mock.scalar_one_or_none.return_value = task1
        db.execute = AsyncMock(return_value=result_mock)
        result = await svc.update_task_state(db, "task-123", svc.TASK_STATE_WORKING)
        assert result.state == svc.TASK_STATE_WORKING

        # Second transition: WORKING → COMPLETED
        task2 = self._make_task(state=svc.TASK_STATE_WORKING)
        result_mock.scalar_one_or_none.return_value = task2
        db.execute = AsyncMock(return_value=result_mock)
        result = await svc.update_task_state(db, "task-123", svc.TASK_STATE_COMPLETED)
        assert result.state == svc.TASK_STATE_COMPLETED

    async def test_cannot_transition_from_input_required_to_failed(self):
        """INPUT_REQUIRED → FAILED is valid (error during resume)."""
        db = self._mock_db()
        task = self._make_task(state=svc.TASK_STATE_INPUT_REQUIRED)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = task
        db.execute = AsyncMock(return_value=result_mock)

        result = await svc.update_task_state(
            db, "task-123", svc.TASK_STATE_FAILED,
            status_message={"role": "agent", "parts": [{"text": "error"}]},
        )
        assert result.state == svc.TASK_STATE_FAILED


# =========================================================================
# auth_request — Redis helpers
# =========================================================================


class TestAuthRequestRedisHelpers:
    """``store_a2a_auth_request``, ``get_a2a_auth_request``, ``clear_a2a_auth_request``."""

    async def test_store_and_get(self, _mock_redis):
        from src.core.redis import (
            store_a2a_auth_request,
            get_a2a_auth_request,
        )

        auth_info = {
            "provider": "google",
            "tool_type": "email",
            "scopes": ["https://www.googleapis.com/auth/gmail.send"],
        }
        import json

        _mock_redis.get.return_value = json.dumps(auth_info)
        await store_a2a_auth_request("task-123", auth_info)
        _mock_redis.setex.assert_called_once()
        args = _mock_redis.setex.call_args[0]
        assert args[0] == "auth_request:task-123"
        assert json.loads(args[2]) == auth_info
        result = await get_a2a_auth_request("task-123")
        assert result == auth_info

    async def test_get_returns_none_when_not_set(self, _mock_redis):
        from src.core.redis import get_a2a_auth_request

        _mock_redis.get.return_value = None
        result = await get_a2a_auth_request("task-nonexistent")
        assert result is None

    async def test_clear(self, _mock_redis):
        from src.core.redis import clear_a2a_auth_request

        await clear_a2a_auth_request("task-123")
        _mock_redis.delete.assert_called_once_with("auth_request:task-123")


# =========================================================================
# request_auth — tool function
# =========================================================================


class TestRequestAuthTool:
    """The ``request_auth`` @tool function called by the agent."""

    async def test_stores_auth_info_when_task_id_in_context(self, _mock_redis):
        """Tool stores auth info when context has ``task_id``."""
        from agent_framework import FunctionInvocationContext
        from src.tools.request_auth import request_auth

        ctx = MagicMock(spec=FunctionInvocationContext)
        ctx.kwargs = {"task_id": "task-123"}
        result = await request_auth(
            provider="google",
            tool_type="email",
            scopes=["https://www.googleapis.com/auth/gmail.send"],
            reason="I need to send email on your behalf",
            ctx=ctx,
        )

        assert "Auth requested" in result
        assert "google" in result
        _mock_redis.setex.assert_called_once()
        args = _mock_redis.setex.call_args[0]
        assert args[0] == "auth_request:task-123"

    async def test_noop_when_no_context(self, _mock_redis):
        """Tool does nothing when called without context."""
        from src.tools.request_auth import request_auth

        result = await request_auth(
            provider="google",
            tool_type="email",
            ctx=None,
        )

        assert "outside A2A context" in result
        _mock_redis.setex.assert_not_called()

    async def test_noop_when_no_task_id(self, _mock_redis):
        """Tool does nothing when ``task_id`` missing from kwargs."""
        from agent_framework import FunctionInvocationContext
        from src.tools.request_auth import request_auth

        ctx = MagicMock(spec=FunctionInvocationContext)
        ctx.kwargs = {}
        result = await request_auth(
            provider="microsoft",
            tool_type="calendar",
            ctx=ctx,
        )

        assert "no task_id" in result
        _mock_redis.setex.assert_not_called()

    async def test_minimal_invocation(self, _mock_redis):
        """Tool works with only provider and tool_type (no scopes/reason)."""
        from agent_framework import FunctionInvocationContext
        from src.tools.request_auth import request_auth

        ctx = MagicMock(spec=FunctionInvocationContext)
        ctx.kwargs = {"task_id": "task-456"}
        result = await request_auth(
            provider="google",
            tool_type="calendar",
            ctx=ctx,
        )

        assert "Auth requested" in result
        _mock_redis.setex.assert_called_once()
