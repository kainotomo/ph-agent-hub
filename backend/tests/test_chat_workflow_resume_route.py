# =============================================================================
# PH Agent Hub — Chat API Workflow Resume Route Contract Tests (Chunk T3)
# =============================================================================
# Contract-level tests for POST /chat/session/{session_id}/workflow/resume:
# auth enforcement, non-workflow rejection, and unknown approval request
# rejection.  These tests patch at the import locations used inside the
# route so that the route body reaches the error-raising code paths.
# =============================================================================

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
# Mock helpers
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


def _common_session_data(test_user):
    """Return session_data dict that matches the authenticated user."""
    return {
        "id": "sess-1",
        "title": "Test",
        "tenant_id": test_user.tenant_id,
        "user_id": test_user.id,
        "is_temporary": False,
    }


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
# Tests — Route contract: validation, auth, and approval validation
# =============================================================================


class TestWorkflowResumeRouteContract:
    """Route-contract tests for the workflow resume endpoint."""

    # ------------------------------------------------------------------
    # Test 1: rejects non-workflow skill / execution type
    # ------------------------------------------------------------------

    async def test_resume_route_rejects_non_workflow_skill(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """When the session config reports a non-workflow execution type,
        the route raises ``ValidationError`` → HTTP 422.

        The route checks ``cfg.execution_type not in ("workflow", "workflow_based")``
        and raises ``ValidationError("Session execution type ... is not a workflow")``.
        ``ValidationError`` maps to HTTP 422 (see ``src/core/exceptions.py``).
        """
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
        detail = resp.json().get("detail", "")
        assert "not a workflow" in detail

    async def test_resume_route_rejects_no_maf_target_key(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """When the session config has ``skill.maf_target_key is None``,
        the route raises ``ValidationError`` → HTTP 422.

        The route checks ``cfg.skill is None or not cfg.skill.maf_target_key``
        and raises ``ValidationError("Workflow resume requires a session with a MAF workflow skill")``.
        """
        headers = auth_headers(test_user)
        payload = {"approvals": []}

        skill = _make_skill(maf_key=None)

        with patch("src.api.chat._load_session", return_value=_common_session_data(test_user)):
            with patch(
                "src.agents.runner._resolve_session_config",
                return_value=_make_session_config(skill=skill, execution_type="workflow"),
            ):
                resp = await async_client.post(
                    "/api/chat/session/sess-1/workflow/resume",
                    json=payload,
                    headers=headers,
                )

        assert resp.status_code == 422
        detail = resp.json().get("detail", "")
        assert "MAF workflow skill" in detail

    # ------------------------------------------------------------------
    # Test 2: requires authentication
    # ------------------------------------------------------------------

    async def test_resume_route_requires_auth(self, async_client):
        """Calling the route without an Authorization header should be
        rejected by FastAPI's ``OAuth2PasswordBearer`` guard before the
        route body executes.

        The route declares ``dependencies=[Depends(get_current_user)]`` on
        the ``@router.post`` decorator, so any request without a valid Bearer
        token is rejected at the dependency level.

        When ``get_current_user`` cannot decode a token (OAuth2PasswordBearer
        yields an empty string when no header is present), ``decode_token``
        raises ``JWTError`` → ``UnauthorizedError`` → HTTP 401.
        """
        payload = {"approvals": []}
        resp = await async_client.post(
            "/api/chat/session/sess-1/workflow/resume",
            json=payload,
        )
        assert resp.status_code == 401

    # ------------------------------------------------------------------
    # Test 3: rejects unknown approval request ID
    # ------------------------------------------------------------------

    async def test_resume_route_rejects_unknown_approval_request_id(
        self, async_client, auth_headers, test_user, override_get_db
    ):
        """When the client submits an approval whose ``request_id`` does not
        match any pending function-approval request in the checkpoint, the
        route's ``_build_approval_responses`` helper raises
        ``ValidationError`` → HTTP 422.

        The route flow:
        1. ``_load_session`` → returns session data
        2. ``_resolve_session_config`` → returns valid workflow config
        3. ``get_registered`` → returns workflow module
        4. ``load_workflow_definition`` → returns definition
        5. ``load_resume_target`` → returns a ResumeTarget
        6. ``_build_approval_responses`` receives the unknown request_id,
           looks up pending events, finds no match → raises
           ``ValidationError("Unknown approval request id: ...")`` → 422.
        """
        headers = auth_headers(test_user)
        # Submit an approval with a request_id that does not exist in
        # the checkpoint's pending events.
        payload = {
            "approvals": [
                {"request_id": "nonexistent-request-id", "approved": True}
            ]
        }

        skill = _make_skill()

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
                            "src.services.workflow_resume_service.load_resume_target",
                            new=AsyncMock(return_value=(AsyncMock(), AsyncMock())),
                        ):
                            # Patch _build_approval_responses to raise
                            # ValidationError when given an unknown request_id.
                            from src.core.exceptions import ValidationError

                            async def _raise_validation_error(*args, **kwargs):
                                raise ValidationError(
                                    "Unknown approval request id: nonexistent-request-id"
                                )

                            with patch(
                                "src.api.chat._build_approval_responses",
                                new=AsyncMock(side_effect=_raise_validation_error),
                            ):
                                resp = await async_client.post(
                                    "/api/chat/session/sess-1/workflow/resume",
                                    json=payload,
                                    headers=headers,
                                )

        assert resp.status_code == 422
        detail = resp.json().get("detail", "")
        assert "Unknown approval request id" in detail
