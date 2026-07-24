# =============================================================================
# PH Agent Hub — A2A INPUT_REQUIRED End-to-End Tests (Issue #416)
# =============================================================================
# Tests the full A2A task lifecycle with real DB and real Redis, mocking
# only the LLM agent boundary (``_run_a2a_agent``).
#
# Prerequisites:
# - Docker stack is running (docker compose up -d)
# - Alembic migrations are up to date
#
# Usage:
#     pytest backend/tests/e2e_a2a_input_required.py -v
# =============================================================================

import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

# Use the same DB as the running stack
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
)
os.environ.setdefault("REDIS_URL", "redis://redis:6379/0")

pytestmark = [pytest.mark.e2e]


# =========================================================================
# Helpers
# =========================================================================


def _make_request(text: str = "Hello", task_id: str | None = None):
    """Build an A2A SendMessageRequest (matching test_a2a_server_lifecycle.py pattern)."""
    from src.api.a2a_server import A2aSendMessageRequest

    msg = {"parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    return A2aSendMessageRequest(message=msg)


# =========================================================================
# Shared helper — create the A2A default tenant
# =========================================================================

A2A_DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000000"


async def _ensure_a2a_tenant_and_user(db):
    """Create the A2A default tenant + system user if they don't exist."""
    from sqlalchemy import select
    from src.db.orm.tenants import Tenant
    from src.db.orm.users import User
    from src.core.security import hash_password

    # Tenant
    result = await db.execute(
        select(Tenant).where(Tenant.id == A2A_DEFAULT_TENANT_ID)
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(
            id=A2A_DEFAULT_TENANT_ID,
            name="A2A Default Tenant",
        )
        db.add(tenant)
        await db.flush()

    # System user (referenced by A2A sessions with user_id="a2a-system")
    result = await db.execute(
        select(User).where(User.id == "a2a-system")
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            id="a2a-system",
            tenant_id=A2A_DEFAULT_TENANT_ID,
            email="a2a-system@agent-hub.local",
            password_hash=hash_password("unused"),
            display_name="A2A System",
            role="user",
            is_active=True,
        )
        db.add(user)
        await db.flush()

    await db.commit()
    return tenant, user


async def _cleanup_task(db, task_id: str):
    """Delete an a2a_tasks row if it exists."""
    from src.db.orm.a2a_tasks import A2aTask

    task = await db.get(A2aTask, task_id)
    if task:
        session_id = task.session_id
        await db.delete(task)
        await db.flush()

        # Also clean up the backing session
        from src.db.orm.sessions import Session as SessionORM

        session = await db.get(SessionORM, session_id)
        if session:
            await db.delete(session)
            await db.flush()

        await db.commit()


# =========================================================================
# Tests
# =========================================================================


@pytest.mark.e2e
class TestA2AInputRequiredE2E:
    """End-to-end A2A INPUT_REQUIRED flow verification."""

    # ------------------------------------------------------------------
    # Test 1: Full INPUT_REQUIRED → Resume → COMPLETED
    # ------------------------------------------------------------------

    async def test_input_required_full_flow(self, e2e_db_session, monkeypatch):
        """Send message → agent calls ask_user → INPUT_REQUIRED → resume → COMPLETED."""
        db = e2e_db_session
        from src.api.a2a_server import a2a_send_message
        from src.core.redis import store_a2a_question, get_a2a_question
        from src.services import a2a_task_service as svc

        # ---- Setup: ensure A2A default tenant + system user exist ----
        await _ensure_a2a_tenant_and_user(db)

        captured_task_id = {}

        # ---- Step 1: First message — agent asks a question ----
        async def _agent_asks_question(
            session_id, text_content, db, task_id=None
        ):
            """Simulate agent calling ask_user tool."""
            if task_id:
                captured_task_id["id"] = task_id
                await store_a2a_question(
                    task_id, "What is your name?"
                )
            return "Let me ask you something..."

        monkeypatch.setattr(
            "src.api.a2a_server._run_a2a_agent",
            _agent_asks_question,
        )

        first_result = await a2a_send_message(
            _make_request("Hello"),
            Request(scope={"type": "http"}),
            db=db,
        )

        task_id = captured_task_id.get("id")
        assert task_id is not None, "task_id was not captured by agent mock"

        # ---- Verify response shows INPUT_REQUIRED ----
        assert (
            first_result["task"]["status"]["state"]
            == svc.TASK_STATE_INPUT_REQUIRED
        ), f"Expected INPUT_REQUIRED, got {first_result['task']['status']['state']}"
        status_msg = first_result["task"]["status"].get("message", {})
        parts = status_msg.get("parts", [])
        assert any(
            "What is your name?" in p.get("text", "") for p in parts
        ), f"Question not found in status message: {status_msg}"

        # ---- Verify DB row shows INPUT_REQUIRED ----
        db_task = await svc.get_task(db, task_id)
        assert db_task.state == svc.TASK_STATE_INPUT_REQUIRED, (
            f"DB state expected INPUT_REQUIRED, got {db_task.state}"
        )
        db_status = json.loads(db_task.status_message) if isinstance(
            db_task.status_message, str
        ) else (db_task.status_message or {})
        db_parts = db_status.get("parts", [])
        assert any(
            "What is your name?" in p.get("text", "") for p in db_parts
        ), f"Question not found in DB status_message"

        # ---- Verify Redis was cleared after detection ----
        redis_question = await get_a2a_question(task_id)
        assert redis_question is None, (
            f"Expected Redis key to be cleared, got: {redis_question}"
        )

        # ---- Step 2: Resume — agent completes ----
        monkeypatch.setattr(
            "src.api.a2a_server._run_a2a_agent",
            AsyncMock(return_value="Nice to meet you, John!"),
        )

        resume_result = await a2a_send_message(
            _make_request("My name is John", task_id=task_id),
            Request(scope={"type": "http"}),
            db=db,
        )

        # ---- Verify response shows COMPLETED ----
        assert (
            resume_result["task"]["status"]["state"]
            == svc.TASK_STATE_COMPLETED
        ), (
            f"Expected COMPLETED after resume, "
            f"got {resume_result['task']['status']['state']}"
        )
        artifacts = resume_result["task"].get("artifacts", [])
        assert len(artifacts) >= 1, "Expected at least one artifact"
        assert any(
            "Nice to meet you" in p.get("text", "")
            for artifact in artifacts
            for p in artifact.get("parts", [])
        ), "Agent response text not found in artifacts"

        # ---- Verify DB row shows COMPLETED ----
        db_task2 = await svc.get_task(db, task_id)
        assert db_task2.state == svc.TASK_STATE_COMPLETED, (
            f"DB state expected COMPLETED, got {db_task2.state}"
        )

    # ------------------------------------------------------------------
    # Test 2: Normal completion — no INPUT_REQUIRED
    # ------------------------------------------------------------------

    async def test_no_input_required_normal_completion(
        self, e2e_db_session, monkeypatch
    ):
        """Agent does NOT call ask_user → task completes directly."""
        db = e2e_db_session
        from src.api.a2a_server import a2a_send_message
        from src.services import a2a_task_service as svc

        # ---- Setup: ensure A2A default tenant + system user exist ----
        await _ensure_a2a_tenant_and_user(db)

        # Agent completes without asking any question
        monkeypatch.setattr(
            "src.api.a2a_server._run_a2a_agent",
            AsyncMock(return_value="Task completed successfully!"),
        )

        result = await a2a_send_message(
            _make_request("Do something"),
            Request(scope={"type": "http"}),
            db=db,
        )

        task_id = result["task"]["id"]

        # ---- Verify COMPLETED directly (no INPUT_REQUIRED) ----
        assert (
            result["task"]["status"]["state"]
            == svc.TASK_STATE_COMPLETED
        ), (
            f"Expected COMPLETED, "
            f"got {result['task']['status']['state']}"
        )
        artifacts = result["task"].get("artifacts", [])
        assert len(artifacts) >= 1, "Expected at least one artifact"
        assert any(
            "Task completed successfully!" in p.get("text", "")
            for artifact in artifacts
            for p in artifact.get("parts", [])
        ), "Agent response not found in artifacts"

        # ---- Verify DB row shows COMPLETED ----
        db_task = await svc.get_task(db, task_id)
        assert db_task.state == svc.TASK_STATE_COMPLETED, (
            f"DB state expected COMPLETED, got {db_task.state}"
        )
