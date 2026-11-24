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


# =============================================================================
# Issue #634 — a produced answer is never downgraded to "empty output"
# =============================================================================
# Root cause seen in production: a tool left the shared AsyncSession in
# ``PendingRollback``, and the terminal path then raised while reading an
# expired ``cfg.model`` attribute *before* persistence, so a fully streamed
# answer was reported as ``empty_output`` and lost.  These tests pin the
# hardened path: terminal reads come from plain-value snapshots, persistence
# retries once against a fresh session, an unsaved answer gets its own
# ``persist_failed`` code, and a sub-agent failure cannot block persistence.


class _ExpiringModel:
    """Model stub that fails on attribute access once ``locked`` is set.

    Reproduces the ``PendingRollbackError`` raised by an ORM instance that a
    failed tool flush expired.
    """

    def __init__(self) -> None:
        self.locked = False
        self._attrs = {
            "id": "m1",
            "name": "Test Model",
            "model_id": "model-1",
            "provider": "openai",
            "base_url": None,
            "api_key": None,
            "follow_up_questions_enabled": False,
            "input_price_per_1m": None,
            "output_price_per_1m": None,
            "cache_hit_price_per_1m": None,
        }

    def __getattr__(self, name):
        attrs = self.__dict__.get("_attrs", {})
        if name in attrs:
            if self.__dict__.get("locked"):
                raise RuntimeError(
                    "This Session's transaction has been rolled back due to a "
                    "previous exception during flush"
                )
            return attrs[name]
        raise AttributeError(name)


class _PoisonedSession:
    """Minimal session stand-in left in a failed transaction."""

    def __init__(self) -> None:
        self.is_active = False
        self.rolled_back = 0

    async def rollback(self) -> None:
        self.rolled_back += 1
        self.is_active = True


class TestPersistenceHardening:
    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._maybe_summarize_and_build_context")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner._run_agent_stream")
    async def test_persistence_is_retried_once_on_a_transient_failure(
        self, mock_stream, mock_config, mock_summarize, mock_persist_user,
        mock_persist_assistant, mock_post,
        db_session, test_session, test_tenant, test_user,
    ):
        """One failed write is retried against a fresh session (Issue #634)."""
        from src.agents.runner import run_agent_stream

        message_id = str(uuid.uuid4())
        mock_config.return_value = _cfg()
        mock_summarize.return_value = ("context", None)
        mock_persist_user.return_value = "user-msg-id"
        mock_persist_assistant.side_effect = [RuntimeError("db blip"), message_id]

        async def _stream(**kwargs):
            yield _token("the answer")

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

        payload = _payload(_terminal_events(events)[0])
        assert payload["outcome"] == "completed"
        assert payload["persisted"] is True
        assert payload["message_id"] == message_id
        assert mock_persist_assistant.await_count == 2
        assert "message_complete" in [e["event"] for e in events]

    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._maybe_summarize_and_build_context")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner._run_agent_stream")
    async def test_unsaved_answer_is_reported_as_persist_failed(
        self, mock_stream, mock_config, mock_summarize, mock_persist_user,
        mock_persist_assistant, mock_post,
        db_session, test_session, test_tenant, test_user,
    ):
        """Produced-but-unsaved is not the same failure as empty output."""
        from src.agents.runner import run_agent_stream

        message_id = str(uuid.uuid4())
        mock_config.return_value = _cfg()
        mock_summarize.return_value = ("context", None)
        mock_persist_user.return_value = "user-msg-id"
        mock_persist_assistant.side_effect = RuntimeError("disk full")

        async def _stream(**kwargs):
            yield _token("the answer")

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

        payload = _payload(_terminal_events(events)[0])
        assert payload["outcome"] == "failed"
        assert payload["error_code"] == "persist_failed"
        assert payload["persisted"] is False
        assert payload["message_id"] is None
        assert "could not be saved" in payload["message"]
        assert mock_persist_assistant.await_count == 2
        names = [e["event"] for e in events]
        assert "message_complete" not in names

    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._maybe_summarize_and_build_context")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner._run_agent_stream")
    async def test_expired_model_attributes_do_not_lose_the_answer(
        self, mock_stream, mock_config, mock_summarize, mock_persist_user,
        mock_persist_assistant, mock_post,
        db_session, test_session, test_tenant, test_user,
    ):
        """The terminal path must not touch the ORM model after tools ran."""
        from src.agents.runner import run_agent_stream

        message_id = str(uuid.uuid4())
        model = _ExpiringModel()
        cfg = _cfg()
        cfg.model = model
        mock_config.return_value = cfg
        mock_summarize.return_value = ("context", None)
        mock_persist_user.return_value = "user-msg-id"
        mock_persist_assistant.return_value = message_id

        async def _stream(**kwargs):
            # A tool's failed flush expires the model instance mid-run.
            model.locked = True
            yield _token("the answer")

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

        payload = _payload(_terminal_events(events)[0])
        assert payload["outcome"] == "completed"
        assert payload["persisted"] is True
        assert payload["message_id"] == message_id

    @patch("src.agents.runner._fold_subagent_usage")
    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._maybe_summarize_and_build_context")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner._run_agent_stream")
    async def test_subagent_drain_failure_does_not_block_persistence(
        self, mock_stream, mock_config, mock_summarize, mock_persist_user,
        mock_persist_assistant, mock_post, mock_fold,
        db_session, test_session, test_tenant, test_user,
    ):
        """Sub-agent bookkeeping is best effort; the answer still persists."""
        from src.agents.runner import run_agent_stream

        message_id = str(uuid.uuid4())
        mock_config.return_value = _cfg()
        mock_summarize.return_value = ("context", None)
        mock_persist_user.return_value = "user-msg-id"
        mock_persist_assistant.return_value = message_id
        mock_fold.side_effect = RuntimeError("bus exploded")

        async def _stream(**kwargs):
            yield _token("the answer")

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

        payload = _payload(_terminal_events(events)[0])
        assert payload["outcome"] == "completed"
        assert payload["persisted"] is True
        assert payload["message_id"] == message_id

    @patch("src.agents.runner._schedule_post_response_tasks")
    @patch("src.agents.runner._persist_assistant_message")
    @patch("src.agents.runner._persist_user_message")
    @patch("src.agents.runner._maybe_summarize_and_build_context")
    @patch("src.agents.runner._resolve_session_config")
    @patch("src.agents.runner._run_agent_stream")
    async def test_a_failed_shared_session_is_healed_before_persistence(
        self, mock_stream, mock_config, mock_summarize, mock_persist_user,
        mock_persist_assistant, mock_post,
        test_session, test_tenant, test_user,
    ):
        """A PendingRollback session is rolled back, not carried forward."""
        from src.agents.runner import run_agent_stream

        message_id = str(uuid.uuid4())
        poisoned = _PoisonedSession()
        mock_config.return_value = _cfg()
        mock_summarize.return_value = ("context", None)
        mock_persist_user.return_value = "user-msg-id"
        mock_persist_assistant.return_value = message_id

        async def _stream(**kwargs):
            yield _token("the answer")

        mock_stream.side_effect = _stream

        events = await _collect(
            run_agent_stream(
                _session_data(test_session, test_tenant, test_user),
                "Hello",
                poisoned,
                None,
                message_id,
            )
        )

        payload = _payload(_terminal_events(events)[0])
        assert payload["outcome"] == "completed"
        assert poisoned.rolled_back >= 1

