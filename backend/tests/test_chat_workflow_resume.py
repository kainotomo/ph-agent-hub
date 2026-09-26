# =============================================================================
# PH Agent Hub — Chat API Workflow Resume Tests (Chunk C1)
# =============================================================================
# Tests for POST /chat/session/{session_id}/workflow/resume.
# =============================================================================

import json
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio

from src.main import app

pytestmark = [
    pytest.mark.integration,
]


# =============================================================================
# Mock helpers (imported early so they're available)
# =============================================================================


def _make_session_config(skill=None, execution_type="workflow"):
    """Return a minimal ``SessionConfig``-like namespace."""
    from types import SimpleNamespace

    return SimpleNamespace(
        model=None,
        model_client=None,
        system_prompt="",
        skill=skill,
        active_tool_callables=[],
        execution_type=execution_type,
        agent_name="test-agent",
        thinking_enabled=False,
        reasoning_effort=None,
        temperature=0.7,
        tenant_name="Test Tenant",
        cleanup_clients=[],
    )


def _make_skill(
    maf_key="test_workflow",
    tenant_id="test-tenant",
    default_model_id="test-model",
):
    """Return a minimal ``Skill``-like namespace."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id="test-skill-id",
        title="Test Skill",
        execution_type="workflow",
        maf_target_key=maf_key,
        tenant_id=tenant_id,
        default_model_id=default_model_id,
        description="A test skill",
    )


def _make_defn(key="test_workflow"):
    """Return a minimal ``WorkflowDefinition``-like namespace."""
    from types import SimpleNamespace

    return SimpleNamespace(key=key)


async def _aiter(items):
    """Convert an iterable to an async iterable generator."""
    for item in items:
        yield item


def _make_checkpoint_storage_mock(pending_events=None):
    """Create an async mock for MariaDBCheckpointStorage.

    pending_events: dict of request_id -> event objects (defaults to empty).
    """
    from agent_framework import Content

    fc = Content(type="function_call", id="fc-1")

    class FuncApprovalData:
        type = "function_approval_request"
        function_call = fc

    class Event:
        request_id = "req-pending"
        data = FuncApprovalData()

    if pending_events is None:
        pending_events = {}
    pending_events["req-pending"] = Event()

    fake_checkpoint = AsyncMock()
    fake_checkpoint.pending_request_info_events = pending_events
    fake_checkpoint.checkpoint_id = "ck-1"

    fake_storage = AsyncMock()
    fake_storage.load = AsyncMock(return_value=fake_checkpoint)
    return fake_storage


def _load_resume_target_patch(checkpoint_id="ck-1", workflow_key="test_workflow"):
    """Return a context manager that patches ``load_resume_target``.

    Avoids the real DB call that looks up resumable checkpoints.
    """
    from src.services.workflow_resume_service import ResumeTarget

    fake_target = ResumeTarget(
        checkpoint_id=checkpoint_id,
        workflow_key=workflow_key,
    )
    return patch(
        "src.services.workflow_resume_service.load_resume_target",
        new=AsyncMock(return_value=(fake_target, AsyncMock())),
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest_asyncio.fixture
async def async_client(override_get_db) -> httpx.AsyncClient:
    """Create an async HTTP client wired to the FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# =============================================================================
# Helper: common setup for endpoint tests
# =============================================================================


def _common_session_data(test_user):
    """Return session_data dict that matches the authenticated user."""
    return {
        "id": "sess-1",
        "title": "Test",
        "tenant_id": test_user.tenant_id,
        "user_id": test_user.id,
        "is_temporary": False,
    }


def _build_patch_contexts(
    test_user,
    skill=None,
    execution_type="workflow",
    maf_key="test_workflow",
):
    """Yield a context manager stack for a typical endpoint call.

    Patch locations match the **import targets** inside the endpoint:
    _load_session        → src.api.chat
    _resolve_session_config → src.agents.runner
    get_registered       → src.agents.registry
    load_workflow_defn   → src.agents.workflows.engine
    resume_workflow_with_responses → src.services.workflow_resume_service
    """
    if skill is None:
        skill = _make_skill(maf_key=maf_key)
    cfg = _make_session_config(skill=skill, execution_type=execution_type)
    defn = _make_defn(maf_key)
    return (
        patch("src.api.chat._load_session", return_value=_common_session_data(test_user)),
        patch(
            "src.agents.runner._resolve_session_config",
            return_value=cfg,
        ),
        patch(
            "src.agents.registry.get_registered",
            return_value=skill,
        ),
        patch(
            "src.agents.workflows.engine.load_workflow_definition",
            return_value=defn,
        ),
    )


# =============================================================================
# Tests — Error cases
# =============================================================================


class TestWorkflowResumeErrors:
    """Tests for error paths in the workflow resume endpoint."""

    async def test_unauthenticated(self, async_client):
        """Unauthenticated request is rejected."""
        payload = {"approvals": []}
        resp = await async_client.post(
            "/api/chat/session/sess-1/workflow/resume", json=payload
        )
        assert resp.status_code == 401

    async def test_no_maf_skill(self, async_client, auth_headers, test_user, override_get_db):
        """Session without a MAF skill returns a validation error."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill(maf_key=None)

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=None, execution_type="agent"),
            ):
                resp = await async_client.post(
                    "/api/chat/session/sess-1/workflow/resume",
                    json=payload,
                    headers=headers,
                )
        assert resp.status_code == 422
        assert "MAF workflow skill" in resp.json().get("detail", "")

    async def test_non_workflow_execution_type(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """Session with agent execution type returns error."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill()

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="agent"),
            ):
                resp = await async_client.post(
                    "/api/chat/session/sess-1/workflow/resume",
                    json=payload,
                    headers=headers,
                )
        assert resp.status_code == 422
        assert "not a workflow" in resp.json().get("detail", "")


# =============================================================================
# Tests — Successful resume (mocked)
# =============================================================================


class TestWorkflowResumeSuccess:
    """Tests for the happy-path workflow resume flow."""

    @pytest.fixture(autouse=True)
    def _patch_load_resume_target(self):
        """Stub the checkpoint lookup the endpoint performs before resuming.

        Without this the route reaches the real database and returns 404,
        so ``resume_workflow_with_responses`` is never invoked.
        """
        from src.services.workflow_resume_service import ResumeTarget

        target = ResumeTarget(checkpoint_id="ck-1", workflow_key="test_workflow")
        with patch(
            "src.services.workflow_resume_service.load_resume_target",
            new=AsyncMock(return_value=(target, AsyncMock())),
        ):
            yield

    async def test_resume_streams_sse_events(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """Successful resume streams SSE events via EventSourceResponse."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill()

        fake_events = [
            {
                "event": "workflow_resumed",
                "data": json.dumps({
                    "session_id": "sess-1",
                    "message_id": "msg-1",
                    "workflow_key": "test_workflow",
                    "checkpoint_id": "ck-1",
                    "pending_request_ids": [],
                }),
            },
            {
                "event": "workflow_step",
                "data": json.dumps({
                    "workflow_key": "test_workflow",
                    "step_id": "step1",
                    "status": "completed",
                }),
            },
            {
                "event": "message_complete",
                "data": json.dumps({"outcome": "completed"}),
            },
        ]

        async def fake_resume_wf(*args, **kwargs):
            for ev in fake_events:
                yield ev

        fake_storage = _make_checkpoint_storage_mock()

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with _load_resume_target_patch():
                            with patch(
                                "src.services.workflow_resume_service.resume_workflow_with_responses",
                                fake_resume_wf,
                            ):
                                with patch(
                                    "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                                    return_value=fake_storage,
                                ):
                                    resp = await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        text = resp.text
        events = [line for line in text.split("\n") if line.startswith("event:")]
        assert len(events) == 3
        assert "workflow_resumed" in events[0]
        assert "workflow_step" in events[1]
        assert "message_complete" in events[2]

    async def test_resume_with_approvals(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """Approval decisions are forwarded into the responses dict as Content objects."""
        headers = auth_headers(test_user)
        payload = {
            "approvals": [
                {"request_id": "req-1", "approved": True},
                {"request_id": "req-2", "approved": False},
            ]
        }
        skill = _make_skill()
        captured = {}

        from agent_framework import Content

        # Build a fake checkpoint with pending function_approval_request events
        fc1 = Content(type="function_call", id="fc-1")
        fc2 = Content(type="function_call", id="fc-2")

        class FuncApprovalData1:
            type = "function_approval_request"
            function_call = fc1

        class Event1:
            request_id = "req-1"
            data = FuncApprovalData1()

        class FuncApprovalData2:
            type = "function_approval_request"
            function_call = fc2

        class Event2:
            request_id = "req-2"
            data = FuncApprovalData2()

        fake_checkpoint = AsyncMock()
        fake_checkpoint.pending_request_info_events = {
            "req-1": Event1(),
            "req-2": Event2(),
        }
        fake_checkpoint.checkpoint_id = "ck-1"

        fake_storage = AsyncMock()
        fake_storage.load = AsyncMock(return_value=fake_checkpoint)

        async def fake_resume_wf(*args, **kwargs):
            captured["responses"] = kwargs.get("responses")
            async for _ in _aiter([]):
                yield _

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with patch(
                            "src.services.workflow_resume_service.resume_workflow_with_responses",
                            fake_resume_wf,
                        ):
                            with _load_resume_target_patch():
                                with patch(
                                    "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                                    return_value=fake_storage,
                                ):
                                    await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

        assert len(captured["responses"]) == 2
        for resp in captured["responses"].values():
            assert isinstance(resp, Content)
            assert resp.type == "function_approval_response"
        assert captured["responses"]["req-1"].approved is True
        assert captured["responses"]["req-2"].approved is False

    async def test_resume_with_empty_approvals(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """Empty approvals list produces an empty responses dict (but still needs pending checkpoint)."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill()
        captured = {}

        from agent_framework import Content

        fc = Content(type="function_call", id="fc-1")

        class FuncApprovalData:
            type = "function_approval_request"
            function_call = fc

        class Event:
            request_id = "req-1"
            data = FuncApprovalData()

        fake_checkpoint = AsyncMock()
        fake_checkpoint.pending_request_info_events = {
            "req-1": Event(),
        }
        fake_checkpoint.checkpoint_id = "ck-1"

        fake_storage = AsyncMock()
        fake_storage.load = AsyncMock(return_value=fake_checkpoint)

        async def fake_resume_wf(*args, **kwargs):
            captured["responses"] = kwargs.get("responses")
            async for _ in _aiter([]):
                yield _

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with patch(
                            "src.services.workflow_resume_service.resume_workflow_with_responses",
                            fake_resume_wf,
                        ):
                            with patch(
                                "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                                return_value=fake_storage,
                            ):
                                await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

        assert captured["responses"] == {}

    async def test_resume_no_decisions_field(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """Omitting approvals defaults to empty list."""
        headers = auth_headers(test_user)
        payload = {}
        skill = _make_skill()
        captured = {}

        from agent_framework import Content

        fc = Content(type="function_call", id="fc-1")

        class FuncApprovalData:
            type = "function_approval_request"
            function_call = fc

        class Event:
            request_id = "req-1"
            data = FuncApprovalData()

        fake_checkpoint = AsyncMock()
        fake_checkpoint.pending_request_info_events = {
            "req-1": Event(),
        }
        fake_checkpoint.checkpoint_id = "ck-1"

        fake_storage = AsyncMock()
        fake_storage.load = AsyncMock(return_value=fake_checkpoint)

        async def fake_resume_wf(*args, **kwargs):
            captured["responses"] = kwargs.get("responses")
            async for _ in _aiter([]):
                yield _

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with patch(
                            "src.services.workflow_resume_service.resume_workflow_with_responses",
                            fake_resume_wf,
                        ):
                            with patch(
                                "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                                return_value=fake_storage,
                            ):
                                await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

        assert captured["responses"] == {}

    async def test_resume_streams_approval_required_event(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """Resume forwards workflow_approval_required events from the engine."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill()

        approval_event = {
            "event": "workflow_approval_required",
            "data": json.dumps({
                "type": "function_approval_request",
                "request_id": "req-req-1",
                "step_id": "step2",
                "tool_name": "my_tool",
                "arguments": {"key": "value"},
            }),
        }

        async def fake_resume_wf(*args, **kwargs):
            yield {"event": "workflow_resumed", "data": json.dumps({"session_id": "sess-1"})}
            yield approval_event
            return

        fake_storage = _make_checkpoint_storage_mock()

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with patch(
                            "src.services.workflow_resume_service.resume_workflow_with_responses",
                            fake_resume_wf,
                        ):
                            with patch(
                                "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                                return_value=fake_storage,
                            ):
                                resp = await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

        assert resp.status_code == 200
        text = resp.text
        assert "workflow_approval_required" in text
        assert "req-req-1" in text
        assert "my_tool" in text

    async def test_resume_streams_token_events(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """Token events from the workflow engine are forwarded."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill()

        async def fake_resume_wf(*args, **kwargs):
            yield {"event": "workflow_resumed", "data": json.dumps({"session_id": "sess-1"})}
            yield {
                "event": "token",
                "data": json.dumps({"delta": "Hello", "is_final": False}),
            }
            yield {
                "event": "message_complete",
                "data": json.dumps({"outcome": "completed"}),
            }

        fake_storage = _make_checkpoint_storage_mock()

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with patch(
                            "src.services.workflow_resume_service.resume_workflow_with_responses",
                            fake_resume_wf,
                        ):
                            with patch(
                                "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                                return_value=fake_storage,
                            ):
                                resp = await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

        assert resp.status_code == 200
        text = resp.text
        assert "token" in text
        assert "Hello" in text

    async def test_resume_handles_workflow_paused(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """When the resumed workflow pauses again, message_complete carries
        ``outcome: "paused"`` and ``pending_request_ids``."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill()

        async def fake_resume_wf(*args, **kwargs):
            yield {"event": "workflow_resumed", "data": json.dumps({"session_id": "sess-1"})}
            yield {
                "event": "message_complete",
                "data": json.dumps({
                    "outcome": "paused",
                    "pending_request_ids": ["new-req-id"],
                }),
            }

        fake_storage = _make_checkpoint_storage_mock()

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with patch(
                            "src.services.workflow_resume_service.resume_workflow_with_responses",
                            fake_resume_wf,
                        ):
                            with patch(
                                "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                                return_value=fake_storage,
                            ):
                                resp = await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

        assert resp.status_code == 200
        text = resp.text
        assert "paused" in text
        assert "new-req-id" in text

    async def test_resume_uses_skill_tenant_id_for_checkpoint(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """The checkpoint storage is created with the user's tenant_id."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill(tenant_id="skill-tenant-id")

        async def fake_resume_wf(*args, **kwargs):
            async for _ in _aiter([]):
                yield _

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with patch(
                            "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                            autospec=True,
                        ) as MockStorage:
                            fake_storage = _make_checkpoint_storage_mock()
                            MockStorage.return_value = fake_storage

                            with patch(
                                "src.services.workflow_resume_service.resume_workflow_with_responses",
                                fake_resume_wf,
                            ):
                                await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

            call_args = MockStorage.call_args
            assert call_args is not None
            # The tenant_id comes from the session/user, not the skill
            assert call_args.kwargs["tenant_id"] == test_user.tenant_id
            assert call_args.kwargs["session_id"] == "sess-1"

    async def test_resume_uses_correct_message_id(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """The checkpoint storage uses the generated message_id (UUID)."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}
        skill = _make_skill()

        async def fake_resume_wf(*args, **kwargs):
            async for _ in _aiter([]):
                yield _

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                with patch(
                    "src.agents.registry.get_registered",
                    return_value=skill,
                ):
                    with patch(
                        "src.agents.workflows.engine.load_workflow_definition",
                        return_value=_make_defn(),
                    ):
                        with patch(
                            "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
                            autospec=True,
                        ) as MockStorage:
                            fake_storage = _make_checkpoint_storage_mock()
                            MockStorage.return_value = fake_storage

                            with patch(
                                "src.services.workflow_resume_service.resume_workflow_with_responses",
                                fake_resume_wf,
                            ):
                                await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

            call_args = MockStorage.call_args
            assert call_args is not None
            assert call_args.kwargs["session_id"] == "sess-1"
            # message_id should be a valid UUID
            try:
                uuid.UUID(call_args.kwargs["message_id"])
            except ValueError:
                pytest.fail(
                    f"message_id is not a valid UUID: {call_args.kwargs['message_id']}"
                )


# =============================================================================
# _build_approval_responses unit tests
# =============================================================================


class TestBuildApprovalResponses:
    """Tests for the _build_approval_responses async helper function."""

    @pytest.fixture
    def fake_checkpoint(self):
        """Create a fake checkpoint with pending function_approval_request events."""
        from agent_framework import Content

        # Create function_call Content objects that events reference
        fc1 = Content(type="function_call", id="fc-1")
        fc2 = Content(type="function_call", id="fc-2")

        # Build pending_request_info_events with function_approval_request type
        pending = {}

        # req-1: function_approval_request with function_call
        class FuncApprovalData1:
            type = "function_approval_request"
            function_call = fc1

        class Event1:
            request_id = "req-1"
            data = FuncApprovalData1()
            content = fc1

        pending["req-1"] = Event1()

        # req-2: another function_approval_request
        class FuncApprovalData2:
            type = "function_approval_request"
            function_call = fc2

        class Event2:
            request_id = "req-2"
            data = FuncApprovalData2()
            content = fc2

        pending["req-2"] = Event2()

        # Some non-approval event (should be ignored)
        class OtherData:
            type = "some_other_type"

        class OtherEvent:
            request_id = "other"
            data = OtherData()

        pending["other"] = OtherEvent()

        checkpoint = AsyncMock()
        checkpoint.pending_request_info_events = pending
        checkpoint.checkpoint_id = "ck-1"
        return checkpoint

    @pytest.fixture
    def fake_target(self):
        target = AsyncMock()
        target.checkpoint_id = "ck-1"
        return target

    @pytest.fixture
    def fake_checkpoint_storage(self):
        storage = AsyncMock()
        return storage

    def test_empty_approvals_list(self, fake_checkpoint, fake_target, fake_checkpoint_storage):
        """Empty approvals list returns empty responses dict."""
        import asyncio
        from src.api.chat import _build_approval_responses

        fake_checkpoint_storage.load = AsyncMock(return_value=fake_checkpoint)

        async def _run():
            return await _build_approval_responses(
                None, "tenant-1", fake_target, fake_checkpoint_storage, []
            )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result == {}

    def test_single_approval_approved(self, fake_checkpoint, fake_target, fake_checkpoint_storage):
        """Single approved decision produces a Content response."""
        import asyncio
        from agent_framework import Content
        from src.api.chat import WorkflowApprovalDecision, _build_approval_responses

        fake_checkpoint_storage.load = AsyncMock(return_value=fake_checkpoint)

        approvals = [WorkflowApprovalDecision(request_id="req-1", approved=True)]

        async def _run():
            return await _build_approval_responses(
                None, "tenant-1", fake_target, fake_checkpoint_storage, approvals
            )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert len(result) == 1
        resp = result["req-1"]
        assert isinstance(resp, Content)
        assert resp.type == "function_approval_response"
        assert resp.approved is True

    def test_single_approval_rejected(self, fake_checkpoint, fake_target, fake_checkpoint_storage):
        """Single rejected decision produces a Content response with approved=False."""
        import asyncio
        from agent_framework import Content
        from src.api.chat import WorkflowApprovalDecision, _build_approval_responses

        fake_checkpoint_storage.load = AsyncMock(return_value=fake_checkpoint)

        approvals = [WorkflowApprovalDecision(request_id="req-2", approved=False)]

        async def _run():
            return await _build_approval_responses(
                None, "tenant-1", fake_target, fake_checkpoint_storage, approvals
            )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert len(result) == 1
        resp = result["req-2"]
        assert isinstance(resp, Content)
        assert resp.type == "function_approval_response"
        assert resp.approved is False

    def test_unknown_request_id_raises(self, fake_checkpoint, fake_target, fake_checkpoint_storage):
        """Unknown request_id raises ValidationError."""
        import asyncio
        from src.api.chat import WorkflowApprovalDecision, ValidationError, _build_approval_responses

        fake_checkpoint_storage.load = AsyncMock(return_value=fake_checkpoint)

        approvals = [WorkflowApprovalDecision(request_id="unknown-req", approved=True)]

        async def _run():
            return await _build_approval_responses(
                None, "tenant-1", fake_target, fake_checkpoint_storage, approvals
            )

        with pytest.raises(ValidationError, match="Unknown approval request id"):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_checkpoint_no_pending_raises(self, fake_target, fake_checkpoint_storage):
        """Checkpoint with no pending requests raises ValidationError."""
        import asyncio
        from src.api.chat import WorkflowApprovalDecision, ValidationError, _build_approval_responses

        empty_checkpoint = AsyncMock()
        empty_checkpoint.pending_request_info_events = {}
        empty_checkpoint.checkpoint_id = "ck-empty"
        fake_checkpoint_storage.load = AsyncMock(return_value=empty_checkpoint)

        approvals = [WorkflowApprovalDecision(request_id="req-1", approved=True)]

        async def _run():
            return await _build_approval_responses(
                None, "tenant-1", fake_target, fake_checkpoint_storage, approvals
            )

        with pytest.raises(ValidationError, match="Checkpoint has no pending approval requests"):
            asyncio.get_event_loop().run_until_complete(_run())

    def test_multiple_approvals(self, fake_checkpoint, fake_target, fake_checkpoint_storage):
        """Multiple approvals produce multiple Content responses."""
        import asyncio
        from agent_framework import Content
        from src.api.chat import WorkflowApprovalDecision, _build_approval_responses

        fake_checkpoint_storage.load = AsyncMock(return_value=fake_checkpoint)

        approvals = [
            WorkflowApprovalDecision(request_id="req-1", approved=True),
            WorkflowApprovalDecision(request_id="req-2", approved=False),
        ]

        async def _run():
            return await _build_approval_responses(
                None, "tenant-1", fake_target, fake_checkpoint_storage, approvals
            )

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert len(result) == 2
        for resp in result.values():
            assert isinstance(resp, Content)
            assert resp.type == "function_approval_response"


# =============================================================================
# Pydantic model tests
# =============================================================================


class TestWorkflowApprovalDecisionModel:
    """Tests for the WorkflowApprovalDecision Pydantic model."""

    def test_required_fields(self):
        from src.api.chat import WorkflowApprovalDecision

        d = WorkflowApprovalDecision(request_id="r1", approved=True)
        assert d.request_id == "r1"
        assert d.approved is True

    def test_model_dump(self):
        from src.api.chat import WorkflowApprovalDecision

        d = WorkflowApprovalDecision(request_id="r1", approved=True)
        dumped = d.model_dump()
        assert dumped == {"request_id": "r1", "approved": True}

    def test_model_dump_json(self):
        from src.api.chat import WorkflowApprovalDecision

        d = WorkflowApprovalDecision(request_id="r1", approved=False)
        dumped = d.model_dump_json()
        assert '"request_id":"r1"' in dumped or '"request_id": "r1"' in dumped
        assert '"approved":false' in dumped or '"approved": false' in dumped


class TestWorkflowResumeRequestModel:
    """Tests for the WorkflowResumeRequest Pydantic model."""

    def test_empty_list_default(self):
        from src.api.chat import WorkflowResumeRequest

        req = WorkflowResumeRequest()
        assert req.approvals == []

    def test_with_decisions(self):
        from src.api.chat import WorkflowApprovalDecision, WorkflowResumeRequest

        decisions = [WorkflowApprovalDecision(request_id="r1", approved=True)]
        req = WorkflowResumeRequest(approvals=decisions)
        assert len(req.approvals) == 1
        assert req.approvals[0].request_id == "r1"

    def test_model_dump(self):
        from src.api.chat import WorkflowApprovalDecision, WorkflowResumeRequest

        decisions = [WorkflowApprovalDecision(request_id="r1", approved=True)]
        req = WorkflowResumeRequest(approvals=decisions)
        dumped = req.model_dump()
        assert len(dumped["approvals"]) == 1
        assert dumped["approvals"][0]["request_id"] == "r1"


# =============================================================================
# Tenant isolation tests
# =============================================================================


class TestWorkflowResumeTenantIsolation:
    """Tests that tenant isolation is enforced in the resume endpoint."""

    async def test_session_owner_mismatch(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """User cannot resume a session owned by a different user."""
        headers = auth_headers(test_user)
        payload = {"approvals": []}

        # Session belonging to a different user
        session_data = {
            "id": "sess-1",
            "title": "Test",
            "tenant_id": test_user.tenant_id,
            "user_id": "different-user-id",
            "is_temporary": False,
        }
        skill = _make_skill()

        with patch("src.api.chat._load_session", return_value=session_data):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                resp = await async_client.post(
                    "/api/chat/session/sess-1/workflow/resume",
                    json=payload,
                    headers=headers,
                )
        assert resp.status_code == 403
