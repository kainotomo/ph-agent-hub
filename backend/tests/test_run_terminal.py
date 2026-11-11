# =============================================================================
# PH Agent Hub — Producer-reported run terminal (Issue #608)
# =============================================================================
# `run_agent_stream` used to swallow its exception into an `error` SSE frame
# and return normally, so `_run_agent_background` kept its default
# `OUTCOME_COMPLETED`.  The producer now ends every normal path with exactly
# one `run_terminal` frame: {outcome, error_code, message_id, persisted}.
#
# These tests pin the frames for the paths the producer can observe:
# exception before output, empty output, cancellation with partial text,
# and normal completion.
# =============================================================================

import json
import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.agents.runner import SessionConfig

pytestmark = pytest.mark.integration


def _cfg() -> SessionConfig:
    cfg = SessionConfig(
        model=MagicMock(),
        model_client=MagicMock(),
        system_prompt="prompt",
        skill=None,
        active_tool_callables=[],
        execution_type="agent",
        agent_name="assistant",
        thinking_enabled=False,
        temperature=0.7,
        tenant_name="Test",
    )
    cfg.model.id = "m1"
    cfg.model.name = "Test Model"
    cfg.model.provider = "openai"
    cfg.model.follow_up_questions_enabled = False
    cfg.cleanup_clients = []
    cfg.run_budget = None
    return cfg


def _token(text: str = "hi") -> dict:
    return {
        "event": "token",
        "data": json.dumps({"delta": text, "message_id": "msg-id"}),
    }


async def _collect(gen):
    events = []
    async for event in gen:
        events.append(event)
    return events


def _terminal_events(events):
    return [e for e in events if e.get("event") == "run_terminal"]


def _payload(event) -> dict:
    return json.loads(event["data"])


def _session_data(session, tenant, user):
    return {
        "id": session.id,
        "tenant_id": tenant.id,
        "user_id": user.id,
    }


class TestRunTerminalFrames:
    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner.logger")
    async def test_provider_exception_before_output_is_failed_not_completed(
        self, mock_logger, mock_config, mock_persist_user,
        mock_persist_assistant, mock_post,
        db_session, test_session, test_tenant, test_user,
    ):
        from src.agents.runner import run_agent_stream

        mock_config.side_effect = RuntimeError("provider exploded")
        mock_persist_user.return_value = "user-msg-id"

        events = await _collect(
            run_agent_stream(
                _session_data(test_session, test_tenant, test_user),
                "Hello",
                db_session,
                None,
                str(uuid.uuid4()),
            )
        )

        terminal = _terminal_events(events)
        assert len(terminal) == 1
        assert events[-1]["event"] == "run_terminal"
        payload = _payload(terminal[0])
        assert payload["outcome"] == "failed"
        assert payload["error_code"] == "internal_error"
        assert payload["persisted"] is False
        assert payload["message_id"] is None

        # The cause is no longer lost: the producer logs it with traceback.
        mock_logger.exception.assert_called_once()
        # No answer exists, so no assistant row may be written.
        mock_persist_assistant.assert_not_called()
        # And nothing pretends the turn completed.
        assert not [e for e in events if e["event"] == "message_complete"]

    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._maybe_summarize_and_build_context")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner._run_agent_stream")
    async def test_empty_output_is_failed_with_empty_output_code(
        self, mock_stream, mock_config, mock_summarize, mock_persist_user,
        mock_persist_assistant, mock_post,
        db_session, test_session, test_tenant, test_user,
    ):
        from src.agents.runner import run_agent_stream

        mock_config.return_value = _cfg()
        mock_summarize.return_value = ("context", None)
        mock_persist_user.return_value = "user-msg-id"

        async def _empty_stream(**kwargs):
            if False:  # pragma: no cover - makes this an async generator
                yield None

        mock_stream.side_effect = _empty_stream

        events = await _collect(
            run_agent_stream(
                _session_data(test_session, test_tenant, test_user),
                "Hello",
                db_session,
                None,
                str(uuid.uuid4()),
            )
        )

        terminal = _terminal_events(events)
        assert len(terminal) == 1
        assert events[-1]["event"] == "run_terminal"
        payload = _payload(terminal[0])
        assert payload["outcome"] == "failed"
        assert payload["error_code"] == "empty_output"
        assert payload["persisted"] is False
        mock_persist_assistant.assert_not_called()

    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._maybe_summarize_and_build_context")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner._run_agent_stream")
    @patch("src.agents.runner.clear_stream_cancel")
    @patch("src.agents.runner.check_stream_cancel")
    async def test_cancelled_with_partial_text_reports_cancelled_and_persisted(
        self, mock_check_cancel, mock_clear_cancel, mock_stream, mock_config,
        mock_summarize, mock_persist_user, mock_persist_assistant, mock_post,
        db_session, test_session, test_tenant, test_user,
    ):
        from src.agents.runner import run_agent_stream

        message_id = str(uuid.uuid4())
        mock_config.return_value = _cfg()
        mock_summarize.return_value = ("context", None)
        mock_persist_user.return_value = "user-msg-id"
        mock_check_cancel.return_value = True
        mock_clear_cancel.return_value = None
        mock_persist_assistant.return_value = message_id

        async def _partial_stream(**kwargs):
            yield _token("partial answer")

        mock_stream.side_effect = _partial_stream

        events = await _collect(
            run_agent_stream(
                _session_data(test_session, test_tenant, test_user),
                "Hello",
                db_session,
                None,
                message_id,
            )
        )

        terminal = _terminal_events(events)
        assert len(terminal) == 1
        assert events[-1]["event"] == "run_terminal"
        payload = _payload(terminal[0])
        assert payload["outcome"] == "cancelled"
        assert payload["persisted"] is True
        assert payload["message_id"] == message_id
        mock_persist_assistant.assert_called_once()

    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._maybe_summarize_and_build_context")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner._run_agent_stream")
    async def test_normal_completion_reports_completed_only_when_persisted(
        self, mock_stream, mock_config, mock_summarize, mock_persist_user,
        mock_persist_assistant, mock_post,
        db_session, test_session, test_tenant, test_user,
    ):
        from src.agents.runner import run_agent_stream

        message_id = str(uuid.uuid4())
        mock_config.return_value = _cfg()
        mock_summarize.return_value = ("context", None)
        mock_persist_user.return_value = "user-msg-id"
        mock_persist_assistant.return_value = message_id

        async def _stream(**kwargs):
            yield _token("hello")

        mock_stream.side_effect = _stream

        events = await _collect(
            run_agent_stream(
                _session_data(test_session, test_tenant, test_user),
                "Hello",
                db_session,
                None,
                message_id,
            )
        )

        terminal = _terminal_events(events)
        assert len(terminal) == 1
        assert events[-1]["event"] == "run_terminal"
        payload = _payload(terminal[0])
        assert payload["outcome"] == "completed"
        assert payload["persisted"] is True
        assert payload["message_id"] == message_id
        # Existing clients still get message_complete, before run_terminal.
        names = [e["event"] for e in events]
        assert "message_complete" in names
        assert names.index("message_complete") < names.index("run_terminal")
