# =============================================================================
# Test: Autopilot Lifecycle — Issue #446
# =============================================================================
# Tests the autopilot SSE event emission, error handling, and the
# stream_status endpoint's autopilot state reporting.
# =============================================================================

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(event: str, data: dict) -> dict:
    """Build an SSE event dict matching StreamBridge format."""
    return {"event": event, "data": json.dumps(data)}


async def _collect_bridge_events(bridge) -> list[dict]:
    """Collect all events from a StreamBridge subscriber synchronously."""
    events: list[dict] = []
    async for event in bridge.subscribe():
        events.append(event)
    return events


def _make_mock_stream_update(
    event: str = "message_complete",
    content: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> dict:
    """Build a mock ``run_agent_stream`` event dict."""
    data = {"content": content, "tokens_in": tokens_in, "tokens_out": tokens_out} if event == "message_complete" else {}
    return {"event": event, "data": json.dumps(data)}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunAutopilotStreamEvents:
    """Verify that ``run_autopilot_stream`` emits the correct lifecycle
    events in the expected order."""

    @patch("src.agents.runner.run_agent_stream")
    @patch("src.agents.autopilot.settings")
    async def test_happy_path_events(
        self,
        mock_settings,
        mock_run_agent_stream,
        db_session,
        test_user,
        test_session,
    ):
        """A multi-turn autopilot that completes successfully should emit:
        autopilot_turn_start → message events → autopilot_turn_complete
        → autopilot_complete (on the completing turn)
        """
        # Arrange
        from src.agents.stream_bridge import StreamBridge

        mock_settings.AUTOPILOT_MAX_TURNS = 2
        mock_settings.AUTOPILOT_MAX_TOKENS = 0  # No token limit

        # Mock generator: on turn 2, function_invocation_kwargs is set
        # (because task_complete tool is added for turn > 1 or resume),
        # and we simulate task_complete by setting done=True.
        async def _mock_generator(*, function_invocation_kwargs=None, **kwargs):
            if function_invocation_kwargs:
                cs = function_invocation_kwargs.get("completion_state", {})
                cs["done"] = True
                cs["summary"] = "Goal achieved!"
            yield _make_mock_stream_update("message_complete", "Goal achieved!", 50, 100)

        mock_run_agent_stream.side_effect = _mock_generator

        bridge = StreamBridge("test-session", autopilot=True)
        session_data = {"id": test_session.id}

        from src.agents.autopilot import run_autopilot_stream

        # Act
        await run_autopilot_stream(
            session_data=session_data,
            goal="Test goal",
            db=db_session,
            current_user=test_user,
            bridge=bridge,
            max_turns=2,
        )
        await bridge.close()

        # Collect all events
        events = await _collect_bridge_events(bridge)

        # Assert event sequence
        event_names = [e["event"] for e in events]
        assert "autopilot_turn_start" in event_names
        assert "message_complete" in event_names
        assert "autopilot_turn_complete" in event_names
        assert "autopilot_complete" in event_names
        assert "autopilot_resume" not in event_names

        # Check turn_start payload
        turn_start = next(e for e in events if e["event"] == "autopilot_turn_start")
        ts_data = json.loads(turn_start["data"])
        assert ts_data["turn"] == 1
        assert ts_data["max_turns"] == 2

        # Check complete payload
        complete = next(e for e in events if e["event"] == "autopilot_complete")
        c_data = json.loads(complete["data"])
        assert c_data["turn"] == 2
        assert "summary" in c_data

    @patch("src.agents.runner.run_agent_stream")
    @patch("src.agents.autopilot.settings")
    async def test_token_budget_exceeded_emits_error(
        self,
        mock_settings,
        mock_run_agent_stream,
        db_session,
        test_user,
        test_session,
    ):
        """When the token budget is exceeded, the backend should emit
        ``"event": "error"`` with ``"code": "autopilot_error"`` (not a
        separate ``autopilot_error`` event).
        """
        # Arrange
        from src.agents.stream_bridge import StreamBridge

        mock_settings.AUTOPILOT_MAX_TURNS = 5
        mock_settings.AUTOPILOT_MAX_TOKENS = 10  # Very low budget

        # Mock turn 1 to use more tokens than the budget allows
        async def _turn_gen():
            yield _make_mock_stream_update("message_complete", "Some work", 100, 50)

        mock_run_agent_stream.return_value = _turn_gen()

        bridge = StreamBridge("test-session-budget", autopilot=True)
        session_data = {"id": test_session.id}

        from src.agents.autopilot import run_autopilot_stream

        # Act
        await run_autopilot_stream(
            session_data=session_data,
            goal="Test goal",
            db=db_session,
            current_user=test_user,
            bridge=bridge,
            max_turns=5,
        )
        await bridge.close()

        events = await _collect_bridge_events(bridge)
        event_names = [e["event"] for e in events]

        # Assert: the budget exceeded after turn 1, so we should
        # see an error event (not autopilot_error) and NO autopilot_complete.
        assert "error" in event_names
        assert "autopilot_error" not in event_names  # MUST NOT use custom event
        assert "autopilot_complete" not in event_names

        error_evt = next(e for e in events if e["event"] == "error")
        err_data = json.loads(error_evt["data"])
        assert err_data["code"] == "autopilot_error"
        assert "exceeded" in err_data["message"]

    @patch("src.agents.runner.run_agent_stream")
    @patch("src.agents.autopilot.settings")
    async def test_max_turns_reached(
        self,
        mock_settings,
        mock_run_agent_stream,
        db_session,
        test_user,
        test_session,
    ):
        """When max turns are reached without completion, the backend
        should emit ``autopilot_max_turns``."""
        # Arrange
        from src.agents.stream_bridge import StreamBridge

        mock_settings.AUTOPILOT_MAX_TURNS = 2
        mock_settings.AUTOPILOT_MAX_TOKENS = 0  # No token limit

        call_count = 0

        async def _turn_gen():
            nonlocal call_count
            call_count += 1
            # Never signal completion
            yield _make_mock_stream_update("message_complete", "Still working", 10, 20)

        mock_run_agent_stream.return_value = _turn_gen()

        bridge = StreamBridge("test-session-max", autopilot=True)
        session_data = {"id": test_session.id}

        from src.agents.autopilot import run_autopilot_stream

        # Act
        await run_autopilot_stream(
            session_data=session_data,
            goal="Test goal",
            db=db_session,
            current_user=test_user,
            bridge=bridge,
            max_turns=2,
        )
        await bridge.close()

        events = await _collect_bridge_events(bridge)
        event_names = [e["event"] for e in events]

        # Assert
        assert "autopilot_turn_start" in event_names
        assert "autopilot_turn_complete" in event_names
        assert "autopilot_max_turns" in event_names
        assert "autopilot_complete" not in event_names

    @patch("src.agents.runner.run_agent_stream")
    @patch("src.agents.autopilot.settings")
    async def test_resume_emits_autopilot_resume(
        self,
        mock_settings,
        mock_run_agent_stream,
        db_session,
        test_user,
        test_session,
    ):
        """When resuming (start_turn > 1), the backend should emit
        ``autopilot_resume`` with the correct turn number."""
        # Arrange
        from src.agents.stream_bridge import StreamBridge

        mock_settings.AUTOPILOT_MAX_TURNS = 3
        mock_settings.AUTOPILOT_MAX_TOKENS = 0

        async def _mock_generator(*, function_invocation_kwargs=None, **kwargs):
            if function_invocation_kwargs:
                cs = function_invocation_kwargs.get("completion_state", {})
                cs["done"] = True
                cs["summary"] = "Resumed work"
            yield _make_mock_stream_update("message_complete", "Resumed work", 10, 20)

        mock_run_agent_stream.side_effect = _mock_generator

        bridge = StreamBridge("test-session-resume", autopilot=True)
        session_data = {"id": test_session.id}

        from src.agents.autopilot import run_autopilot_stream

        # Act — start from turn 2 (resume)
        await run_autopilot_stream(
            session_data=session_data,
            goal="Test goal",
            db=db_session,
            current_user=test_user,
            bridge=bridge,
            max_turns=3,
            start_turn=2,
        )
        await bridge.close()

        events = await _collect_bridge_events(bridge)
        event_names = [e["event"] for e in events]

        # Assert
        assert "autopilot_resume" in event_names
        resume = next(e for e in events if e["event"] == "autopilot_resume")
        r_data = json.loads(resume["data"])
        assert r_data["turn"] == 2
        assert r_data["max_turns"] == 3


# ---------------------------------------------------------------------------
# stream_status endpoint tests
# ---------------------------------------------------------------------------


class TestStreamStatusAutopilot:
    """Verify that the ``stream_status`` endpoint returns correct autopilot
    state for various run states."""

    async def _create_autopilot_run(
        self, db_session, session_id: str, state: str, turn: int = 1, max_turns: int = 5
    ):
        """Create an AutopilotRun record directly."""
        from src.services.autopilot_service import create_autopilot_run
        run = await create_autopilot_run(db_session, session_id, "Test goal", max_turns)
        from src.services.autopilot_service import set_state
        await set_state(db_session, run.id, state)
        # Also set turn progress
        from src.services.autopilot_service import update_turn
        await update_turn(db_session, run.id, turn)
        return run

    async def test_status_completed_run(
        self, async_client, auth_headers, test_user, test_session, db_session
    ):
        """A COMPLETED autopilot run should return ``autopilot: true``
        and ``run_state: "COMPLETED"``."""
        await self._create_autopilot_run(
            db_session, test_session.id, "COMPLETED", turn=3, max_turns=5
        )
        headers = auth_headers(test_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/stream-status",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["autopilot"] is True
        assert data["active"] is False
        assert data["paused"] is False
        assert data["run_state"] == "COMPLETED"
        assert data["current_turn"] == 3
        assert data["max_turns"] == 5

    async def test_status_failed_run(
        self, async_client, auth_headers, test_user, test_session, db_session
    ):
        """A FAILED autopilot run should return ``autopilot: true``
        and ``run_state: "FAILED"``."""
        await self._create_autopilot_run(
            db_session, test_session.id, "FAILED", turn=2, max_turns=5
        )
        headers = auth_headers(test_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/stream-status",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["autopilot"] is True
        assert data["active"] is False
        assert data["run_state"] == "FAILED"

    async def test_status_paused_run(
        self, async_client, auth_headers, test_user, test_session, db_session
    ):
        """A PAUSED autopilot run should return ``autopilot: true``,
        ``paused: true``, and ``run_state: "PAUSED"``."""
        await self._create_autopilot_run(
            db_session, test_session.id, "PAUSED", turn=1, max_turns=5
        )
        headers = auth_headers(test_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/stream-status",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["autopilot"] is True
        assert data["paused"] is True
        assert data["run_state"] == "PAUSED"

    async def test_status_no_run(
        self, async_client, auth_headers, test_user, test_session
    ):
        """A session with no autopilot run should return
        ``autopilot: false`` and ``run_state: null``."""
        headers = auth_headers(test_user)
        resp = await async_client.get(
            f"/api/chat/session/{test_session.id}/stream-status",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["autopilot"] is False
        assert data["active"] is False
        assert data["paused"] is False
        # run_state may be None or absent — just check it's falsy
        assert not data.get("run_state")

    async def test_status_nonexistent_session(
        self, async_client, auth_headers, test_user
    ):
        """A non-existent session should return 200 with inactive defaults
        instead of 404, so the frontend avoids console errors for
        lazy-created sessions (Issue #475)."""
        import uuid
        fake_id = str(uuid.uuid4())
        headers = auth_headers(test_user)
        resp = await async_client.get(
            f"/api/chat/session/{fake_id}/stream-status",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False
        assert data["autopilot"] is False
        assert data["paused"] is False
        assert data["current_turn"] is None
        assert data["max_turns"] is None
        assert data["run_state"] is None
