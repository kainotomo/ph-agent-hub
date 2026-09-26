# =============================================================================
# PH Agent Hub — Workflow Approval Events Tests
# =============================================================================
# Tests for ``approval_event_from_request_info`` and
# ``iter_workflow_sse``: responses forwarding and approval SSE emission.
# =============================================================================

import json

import pytest
from unittest.mock import MagicMock

from agent_framework import WorkflowEvent, WorkflowRunState
from src.agents.workflows.engine import approval_event_from_request_info, iter_workflow_sse


# =============================================================================
# Helper: build a real MAF approval request_info event
# =============================================================================


def _make_approval_event():
    """Build a ``WorkflowEvent.request_info`` whose data is a function-approval request.

    Returns:
        WorkflowEvent of type ``request_info`` carrying a
        ``function_approval_request`` Content.
    """
    from agent_framework import Content

    func_call = Content.from_function_call(
        call_id="call-1",
        name="web_search",
        arguments='{"q":"test query"}',
    )
    request_data = Content.from_function_approval_request(
        id="call-1",
        function_call=func_call,
    )
    return WorkflowEvent.request_info(
        request_id="req-1",
        source_executor_id="step-a",
        request_data=request_data,
        response_type=Content,
    )


def _make_non_approval_event():
    """Build a ``WorkflowEvent.request_info`` whose data is NOT a function approval.

    Returns a plain dict as data so ``approval_event_from_request_info`` returns None.
    """
    return WorkflowEvent.request_info(
        request_id="req-2",
        source_executor_id="step-b",
        request_data={"some": "plain data"},
        response_type=dict,
    )


# =============================================================================
# Mock helpers for iter_workflow_sse
# =============================================================================


class _FakeRunResult:
    """Stub mimicking a real ``WorkflowRunResult``."""

    def __init__(self, request_info_events=None):
        self._request_info_events = request_info_events or []

    def get_final_state(self):
        return WorkflowRunState.IDLE

    def get_request_info_events(self):
        return self._request_info_events


class _FakeResponseStream:
    """A lightweight mock of MAF's ``ResponseStream[WorkflowEvent, WorkflowRunResult]``.

    It is an async iterable that yields the provided events, and exposes
    a ``get_final_response()`` method returning a fake ``WorkflowRunResult``.
    """

    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        async def _gen():
            for ev in self._events:
                yield ev
        return _gen()

    async def get_final_response(self):
        request_info_events = [ev for ev in self._events if ev.type == "request_info"]
        return _FakeRunResult(request_info_events)


def _build_fake_run(events):
    """Return a callable that can be used as ``fake_workflow.run.side_effect``.

    The callable captures kwargs in a closure variable and returns a
    ``_FakeResponseStream``.
    """
    captured = {}

    def _run(message, **kwargs):
        captured.update(kwargs)
        return _FakeResponseStream(events)

    _run._captured = captured  # attach for test inspection
    return _run


# =============================================================================
# Test 1: Shape of approval_event_from_request_info
# =============================================================================


class TestApprovalEventFromRequestInfo:
    """Tests for the ``approval_event_from_request_info`` helper."""

    def test_shape(self):
        """The returned dict has exactly the expected keys and values."""
        event = _make_approval_event()
        result = approval_event_from_request_info(event)

        assert result is not None
        assert set(result.keys()) == {"type", "request_id", "step_id", "tool_name", "arguments"}
        assert result["type"] == "function_approval_request"
        assert result["request_id"] == "req-1"
        assert result["step_id"] == "step-a"
        assert result["tool_name"] == "web_search"

    def test_ignores_non_approval(self):
        """A request_info with plain-dict data returns None."""
        event = _make_non_approval_event()
        result = approval_event_from_request_info(event)
        assert result is None

    def test_ignores_request_info_without_approval_data(self):
        """A request_info whose data is a Content of a different type returns None."""
        from agent_framework import Content

        text_data = Content.from_text(text="some text response")
        event = WorkflowEvent.request_info(
            request_id="req-3",
            source_executor_id="step-c",
            request_data=text_data,
            response_type=Content,
        )
        result = approval_event_from_request_info(event)
        assert result is None


# =============================================================================
# Test 2 & 3: iter_workflow_sse — responses forwarding & approval SSE emission
# =============================================================================


class TestIterWorkflowSse:
    """Tests for ``iter_workflow_sse``: responses forwarding and approval SSE."""

    async def test_iter_workflow_sse_forwards_responses(self):
        """``workflow.run()`` receives the ``responses`` dict and ``checkpoint_id``."""
        approval_event = _make_approval_event()
        events = [
            WorkflowEvent.started(),
            WorkflowEvent.status(WorkflowRunState.STARTED),
            WorkflowEvent.status(WorkflowRunState.IN_PROGRESS),
            # An executor_invoked / executor_completed pair
            WorkflowEvent.executor_invoked("step-a"),
            WorkflowEvent.executor_completed("step-a"),
            # The approval event
            approval_event,
            WorkflowEvent.status(WorkflowRunState.IDLE),
        ]

        fake_workflow = MagicMock()
        fake_workflow.get_executors_list.return_value = [MagicMock(id="step-a")]
        fake_workflow.run.side_effect = _build_fake_run(events)

        checkpoint_storage = object()
        checkpoint_id = "cp-1"

        collected_events = []
        async for event_dict in iter_workflow_sse(
            fake_workflow,
            message=None,
            session_id="S",
            message_id="M",
            checkpoint_storage=checkpoint_storage,
            checkpoint_id=checkpoint_id,
            responses={"req-1": "yes"},
            token_counts={},
        ):
            collected_events.append(event_dict)

        # Verify workflow.run received the responses and checkpoint_id.
        run_kwargs = fake_workflow.run.side_effect._captured
        assert run_kwargs.get("responses") == {"req-1": "yes"}
        assert run_kwargs.get("checkpoint_id") == "cp-1"
        assert run_kwargs.get("stream") is True

    async def test_iter_workflow_sse_emits_approval_event(self):
        """When the stream yields a function-approval request_info, an SSE
        ``workflow_approval_required`` event is emitted with correct data."""
        approval_event = _make_approval_event()
        events = [
            WorkflowEvent.started(),
            WorkflowEvent.status(WorkflowRunState.STARTED),
            WorkflowEvent.status(WorkflowRunState.IN_PROGRESS),
            WorkflowEvent.executor_invoked("step-a"),
            WorkflowEvent.executor_completed("step-a"),
            # The approval event
            approval_event,
            WorkflowEvent.status(WorkflowRunState.IDLE),
        ]

        fake_workflow = MagicMock()
        fake_workflow.get_executors_list.return_value = [MagicMock(id="step-a")]
        fake_workflow.run.side_effect = _build_fake_run(events)

        collected_events = []
        async for event_dict in iter_workflow_sse(
            fake_workflow,
            message=None,
            session_id="S",
            message_id="M",
            checkpoint_storage=object(),
            checkpoint_id="cp-1",
            responses={"req-1": "yes"},
            token_counts={},
        ):
            collected_events.append(event_dict)

        # Find the approval-required event.
        approval_events = [
            e for e in collected_events if e.get("event") == "workflow_approval_required"
        ]
        assert len(approval_events) >= 1

        first_approval = approval_events[0]
        assert first_approval["data"] is not None
        data_obj = json.loads(first_approval["data"])
        assert data_obj["request_id"] == "req-1"
        assert data_obj["type"] == "function_approval_request"
        assert data_obj["step_id"] == "step-a"
        assert data_obj["tool_name"] == "web_search"

    async def test_iter_workflow_sse_no_approval_when_no_approval_event(self):
        """Without an approval request_info event, no ``workflow_approval_required``
        SSE events are emitted."""
        events = [
            WorkflowEvent.started(),
            WorkflowEvent.status(WorkflowRunState.STARTED),
            WorkflowEvent.status(WorkflowRunState.IN_PROGRESS),
            WorkflowEvent.executor_invoked("step-a"),
            WorkflowEvent.executor_completed("step-a"),
            WorkflowEvent.status(WorkflowRunState.IDLE),
        ]

        fake_workflow = MagicMock()
        fake_workflow.get_executors_list.return_value = [MagicMock(id="step-a")]
        fake_workflow.run.side_effect = _build_fake_run(events)

        collected_events = []
        async for event_dict in iter_workflow_sse(
            fake_workflow,
            message=None,
            session_id="S",
            message_id="M",
            checkpoint_storage=object(),
            checkpoint_id="cp-1",
            responses=None,
            token_counts={},
        ):
            collected_events.append(event_dict)

        approval_events = [
            e for e in collected_events if e.get("event") == "workflow_approval_required"
        ]
        assert len(approval_events) == 0
