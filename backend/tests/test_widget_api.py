# =============================================================================
# PH Agent Hub — Widget API Tests
# =============================================================================
# Regression coverage for Issue #622: the embed widget's first message
# crashed with ``'NoneType' object is not iterable`` because temporary
# sessions stored ``active_tool_ids: null``, and the widget/demo
# follow-up endpoints returned 500 because they called the tenant-scoped
# Redis helper with a single argument.
# =============================================================================

from unittest.mock import AsyncMock, patch

import pytest

from src.api.demo import get_demo_follow_up_questions
from src.api.widget import get_widget_follow_up_questions
from src.core.dependencies import DemoContext, GuestContext
from src.services.embed_service import create_embed_config


pytestmark = [
    pytest.mark.integration,
    pytest.mark.regression,
]


class TestWidgetMessageFlow:
    """End-to-end smoke for the widget: config -> message -> history."""

    async def test_first_message_succeeds_for_tenant_with_tools(
        self,
        async_client,
        db_session,
        test_tenant,
        test_model,
        test_tool,
    ):
        """A tenant with at least one tool must accept the widget's first message.

        This is the exact production failure: auto-select ran over a temp
        session whose ``active_tool_ids`` was ``None`` and raised
        ``TypeError: 'NoneType' object is not iterable`` before any model
        call.  ``_run_agent`` is patched so no network/model call happens.
        """
        config, raw_token = await create_embed_config(
            db_session,
            test_tenant.id,
            "Issue 622 Widget",
            default_model_id=test_model.id,
        )
        assert config.id

        config_resp = await async_client.get(f"/api/widget/config/{raw_token}")
        assert config_resp.status_code == 200, config_resp.text
        body = config_resp.json()
        guest_token = body["guest_token"]
        session_id = body["session_id"]

        headers = {
            "Authorization": f"Bearer {guest_token}",
            "Accept": "application/json",
        }

        with patch(
            "src.agents.runner._run_agent",
            new=AsyncMock(return_value=("Hello!", 10, 20, 0)),
        ) as mocked_run:
            message_resp = await async_client.post(
                "/api/widget/session/message",
                json={"content": "Hello"},
                headers=headers,
            )

        assert message_resp.status_code == 200, message_resp.text
        payload = message_resp.json()
        assert payload["content"] == "Hello!"
        assert payload["message_id"]
        mocked_run.assert_awaited_once()

        messages_resp = await async_client.get(
            "/api/widget/session/messages", headers=headers
        )
        assert messages_resp.status_code == 200, messages_resp.text
        messages = messages_resp.json()
        roles = [message["role"] for message in messages]
        assert "user" in roles
        assert "assistant" in roles
        assistant = next(
            message for message in messages if message["role"] == "assistant"
        )
        assert assistant["content"] == "Hello!"

        follow_up_resp = await async_client.get(
            "/api/widget/session/follow-up-questions", headers=headers
        )
        assert follow_up_resp.status_code == 200, follow_up_resp.text
        assert follow_up_resp.json() == {"questions": []}

        # The temp session must store an iterable, not None.
        from src.core.redis import get_temp_session

        stored = await get_temp_session(session_id)
        assert stored is not None
        assert stored["active_tool_ids"] == []


class TestFollowUpQuestionTenantScoping:
    """The widget/demo follow-up helpers must be called with (tenant, session)."""

    async def test_widget_follow_up_uses_tenant_and_session(self):
        ctx = GuestContext(
            tenant_id="tenant-widget",
            embed_config_id="embed-config",
            session_id="session-widget",
        )

        with patch(
            "src.api.widget._get_follow_up_questions",
            new=AsyncMock(return_value=["What next?"]),
        ) as mocked:
            response = await get_widget_follow_up_questions(ctx=ctx)

        mocked.assert_awaited_once_with("tenant-widget", "session-widget")
        assert response.questions == ["What next?"]

    async def test_demo_follow_up_uses_tenant_and_session(self):
        ctx = DemoContext(tenant_id="tenant-demo", session_id="session-demo")

        with (
            patch(
                "src.api.demo._assert_demo_enabled",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "src.core.redis.get_follow_up_questions",
                new=AsyncMock(return_value=[]),
            ) as mocked,
        ):
            response = await get_demo_follow_up_questions(ctx=ctx, db=None)

        mocked.assert_awaited_once_with("tenant-demo", "session-demo")
        assert response.questions == []


class TestUnhandledExceptionHandler:
    """Unhandled errors must produce JSON, never plain-text 500s."""

    async def test_unhandled_error_returns_json(self):
        import httpx

        from src.main import app

        async def _boom():
            raise RuntimeError("kaboom")

        app.add_api_route("/__issue622_boom", _boom, methods=["GET"])
        try:
            transport = httpx.ASGITransport(
                app=app, raise_app_exceptions=False
            )
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get("/__issue622_boom")
        finally:
            app.router.routes = [
                route
                for route in app.router.routes
                if getattr(route, "path", None) != "/__issue622_boom"
            ]

        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {
            "detail": "Internal server error",
            "code": "internal_error",
        }
        assert "kaboom" not in response.text

