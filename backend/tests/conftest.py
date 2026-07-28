# =============================================================================
# PH Agent Hub — Test Fixtures
# =============================================================================
# Async SQLAlchemy session, test client, and test data fixtures for RAG tests.
# =============================================================================

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure the backend src is importable (use parent dir + src. prefix so that
# relative imports in src/db/base.py etc. resolve correctly)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Disable background scheduler and other lifespan tasks during tests
os.environ["TESTING"] = "true"

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Base, AsyncSessionLocal
from src.db.orm.tenants import Tenant
from src.db.orm.users import User
from src.db.orm.file_uploads import FileUpload
from src.core.jwt import create_access_token
from src.core.dependencies import get_db
from src.core.limiter import reset_limiter
from src.main import app


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the in-memory rate limiter before each test to prevent
    rate-limit state from leaking across test cases."""
    reset_limiter()
    yield


@pytest.fixture(autouse=True)
def _disable_license_gate(monkeypatch):
    """Lift the tenant limit for all tests so that pre-existing tenants
    from earlier test runs never cause a 401 on login.

    The license gate (``get_effective_tenant_limit``) checks how many
    tenants exist and blocks non-admin users in tenant N+1 when the
    free-tier limit is hit.  No test in the suite exercises this gate
    intentionally, so we patch it to return a high ceiling.
    """
    import src.services.license_service as ls

    async def _unlimited_limit(db):
        return 1_000_000

    monkeypatch.setattr(ls, "get_effective_tenant_limit", _unlimited_limit)


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session():
    """Create a fresh async DB session for each test.

    Uses a savepoint-backed transaction that is **always** rolled back in the
    ``finally`` block, even when service functions call ``await db.commit()``
    internally.

    How it works
    ------------
    1. An outer transaction is started (``session.begin``).
    2. A savepoint is created within it (``session.begin_nested``).
    3. The test uses the session normally.
    4. When a service calls ``await db.commit()``, the savepoint is released
       but the outer transaction remains — **nothing is persisted to DB yet**.
    5. In ``finally``, ``session.rollback()`` rolls back the *outer* transaction,
       undoing **everything**, including data the service "committed".

    This guarantees complete test isolation even when services call ``commit()``.
    """
    session = AsyncSessionLocal()
    try:
        await session.begin()

        # Monkey-patch commit() to be flush() so service functions never
        # actually commit data to the database.  The outer begin() keeps
        # the transaction open, and the finally block's rollback() undoes
        # everything.
        original_commit = session.commit
        original_flush = session.flush

        async def _noop_commit():
            # Call flush to ensure data is visible within the session
            # (services expect this), but never actually commit.
            await original_flush()

        session.commit = _noop_commit  # type: ignore[method-assign]

        yield session
    finally:
        await session.rollback()
        await session.close()


@pytest_asyncio.fixture
async def test_tenant(db_session: AsyncSession) -> Tenant:
    """Create a test tenant."""
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=f"Test Tenant {uuid.uuid4().hex[:8]}",
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession, test_tenant: Tenant) -> User:
    """Create a test user within the test tenant."""
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="pbkdf2:sha256:600000$test-salt$test-hash",
        display_name="Test User",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def sample_file_upload(
    db_session: AsyncSession,
    test_tenant: Tenant,
    test_user: User,
) -> FileUpload:
    """Create a FileUpload row with extracted sample text."""
    file_id = str(uuid.uuid4())
    upload = FileUpload(
        id=file_id,
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        session_id=None,
        original_filename="sample.txt",
        content_type="text/plain",
        size_bytes=512,
        storage_key=f"uploads/{test_user.id}/test/{file_id}-sample.txt",
        bucket=f"phhub-test-{test_tenant.id}",
        is_temporary=False,
        extracted_text=(
            "Python is a versatile, high-level programming language "
            "known for its readability and simplicity. It was created by "
            "Guido van Rossum and first released in 1991. Python supports "
            "multiple programming paradigms, including procedural, "
            "object-oriented, and functional programming.\n\n"
            "Python is widely used in web development, data science, "
            "artificial intelligence, scientific computing, and automation."
        ),
    )
    db_session.add(upload)
    await db_session.flush()
    return upload


# =============================================================================
# Security / Tenant-Isolation Fixtures
# =============================================================================


@pytest_asyncio.fixture
async def second_tenant(db_session: AsyncSession) -> Tenant:
    """Create a second test tenant for cross-tenant isolation tests."""
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=f"Second Tenant {uuid.uuid4().hex[:8]}",
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest_asyncio.fixture
async def second_user(
    db_session: AsyncSession,
    second_tenant: Tenant,
) -> User:
    """Create a test user in the second tenant."""
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=second_tenant.id,
        email=f"second-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="pbkdf2:sha256:600000$test-salt$test-hash",
        display_name="Second User",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def admin_user(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> User:
    """Create an admin user in the test tenant."""
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="pbkdf2:sha256:600000$test-salt$test-hash",
        display_name="Admin User",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def manager_user(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> User:
    """Create a manager user in the test tenant."""
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        email=f"manager-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="pbkdf2:sha256:600000$test-salt$test-hash",
        display_name="Manager User",
        role="manager",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def inactive_user(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> User:
    """Create an inactive user for auth tests."""
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        email=f"inactive-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="pbkdf2:sha256:600000$test-salt$test-hash",
        display_name="Inactive User",
        role="user",
        is_active=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
def auth_headers() -> callable:
    """Return a helper that generates Authorization headers for a given user.

    Usage::

        def test_something(auth_headers, test_user):
            headers = auth_headers(test_user)
            response = client.get("/api/me", headers=headers)
    """

    def _make_headers(
        user: User,
        extra_claims: dict | None = None,
    ) -> dict[str, str]:
        payload = {
            "sub": user.id,
            "tenant_id": user.tenant_id,
            "role": user.role,
        }
        if extra_claims:
            payload.update(extra_claims)
        token = create_access_token(payload)
        return {"Authorization": f"Bearer {token}"}

    return _make_headers


@pytest_asyncio.fixture
async def override_get_db(db_session: AsyncSession):
    """Override the FastAPI ``get_db`` dependency with the test DB session.

    Use this fixture in any test file that makes HTTP requests to endpoints
    requiring database access (auth, API CRUD, etc.).

    Usage::

        async def test_something(override_get_db, async_client, test_user):
            response = await async_client.get("/api/auth/me")
    """
    app.dependency_overrides[get_db] = lambda: db_session
    yield
    app.dependency_overrides.pop(get_db, None)


# =============================================================================
# HTTP Client
# =============================================================================


@pytest_asyncio.fixture
async def async_client(override_get_db: None):
    """Provide an httpx AsyncClient wired to the FastAPI app via ASGI transport.

    Use with ``auth_headers(test_user)`` to authenticate requests::

        headers = auth_headers(test_user)
        response = await async_client.get("/memory", headers=headers)
    """
    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


# =============================================================================
# Domain Fixtures — Models, Tools, Templates, Prompts, Sessions, Credentials
# =============================================================================


@pytest_asyncio.fixture
async def test_model(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> "Model":
    """Create a minimal enabled model in the test tenant."""
    from src.db.orm.models import Model

    model = Model(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        name="Test Model",
        model_id="test-model",
        provider="openai",
        api_key="test-key",
        enabled=True,
        is_public=True,
        max_tokens=4096,
        temperature=0.7,
    )
    db_session.add(model)
    await db_session.flush()
    return model


@pytest_asyncio.fixture
async def test_tool(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> "Tool":
    """Create a minimal enabled tool in the test tenant."""
    from src.db.orm.tools import Tool

    tool = Tool(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        name="Test Calculator",
        type="calculator",
        category="general",
        config=None,
        enabled=True,
    )
    db_session.add(tool)
    await db_session.flush()
    return tool


@pytest_asyncio.fixture
async def test_template(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> "Template":
    """Create a minimal template in the test tenant."""
    from src.db.orm.templates import Template

    template = Template(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        title="Test Template",
        system_prompt="You are a helpful assistant.",
        scope="tenant",
    )
    db_session.add(template)
    await db_session.flush()
    return template


@pytest_asyncio.fixture
async def test_prompt(
    db_session: AsyncSession,
    test_tenant: Tenant,
    test_user: User,
) -> "Prompt":
    """Create a prompt owned by the test user."""
    from src.db.orm.prompts import Prompt

    prompt = Prompt(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Test Prompt",
        description="A test prompt",
        content="You are a test assistant.",
    )
    db_session.add(prompt)
    await db_session.flush()
    return prompt


@pytest_asyncio.fixture
async def test_session(
    db_session: AsyncSession,
    test_tenant: Tenant,
    test_user: User,
    test_model: "Model",
) -> "Session":
    """Create a permanent session owned by the test user."""
    from src.db.orm.sessions import Session

    session = Session(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Test Session",
        is_temporary=False,
        selected_model_id=test_model.id,
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest_asyncio.fixture
async def test_credential(
    db_session: AsyncSession,
    test_tenant: Tenant,
    test_user: User,
    test_tool: "Tool",
) -> "UserToolCredential":
    """Create a credential entry in the test tenant."""
    from src.db.orm.user_tool_credentials import UserToolCredential

    credential = UserToolCredential(
        id=str(uuid.uuid4()),
        user_id=test_user.id,
        tenant_id=test_tenant.id,
        tool_id=test_tool.id,
        label="Test Gmail",
        provider="gmail",
        email_address="test@example.com",
        is_default=True,
        status="active",
    )
    db_session.add(credential)
    await db_session.flush()
    return credential


# =============================================================================
# Agent Runner Fixtures — Skills, Sessions, Models
# =============================================================================


@pytest_asyncio.fixture
async def test_skill(
    db_session: AsyncSession,
    test_tenant: Tenant,
    test_template: "Template",
) -> "Skill":
    """Create a minimal skill in the test tenant, linked to the test template."""
    from src.db.orm.skills import Skill

    skill = Skill(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        title="Test Skill",
        execution_type="agent",
        template_id=test_template.id,
        cross_session_retrieval_enabled=False,
        visibility="tenant",
    )
    db_session.add(skill)
    await db_session.flush()
    return skill


@pytest_asyncio.fixture
async def test_session_with_skill(
    db_session: AsyncSession,
    test_tenant: Tenant,
    test_user: User,
    test_model: "Model",
    test_skill: "Skill",
    test_tool: "Tool",
) -> "Session":
    """Create a permanent session with a skill and an active tool."""
    from src.db.orm.sessions import Session, SessionActiveTool

    session = Session(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Test Session with Skill",
        is_temporary=False,
        selected_model_id=test_model.id,
        selected_skill_id=test_skill.id,
        selected_template_id=test_skill.template_id,
    )
    db_session.add(session)
    await db_session.flush()

    # Add an active tool to the session
    active_tool = SessionActiveTool(
        session_id=session.id,
        tool_id=test_tool.id,
    )
    db_session.add(active_tool)
    await db_session.flush()
    return session


@pytest_asyncio.fixture
async def test_session_temp(
    db_session: AsyncSession,
    test_tenant: Tenant,
    test_user: User,
) -> "Session":
    """Create a temporary (Redis-backed) session."""
    from src.db.orm.sessions import Session

    session = Session(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Test Temp Session",
        is_temporary=True,
    )
    db_session.add(session)
    await db_session.flush()
    return session


@pytest_asyncio.fixture
async def test_deepseek_model(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> "Model":
    """Create a DeepSeek model with thinking enabled."""
    from src.db.orm.models import Model

    model = Model(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        name="Test DeepSeek",
        model_id="deepseek-chat",
        provider="deepseek",
        api_key="test-ds-key",
        enabled=True,
        is_public=True,
        max_tokens=8192,
        temperature=0.7,
        thinking_enabled=True,
        reasoning_effort="medium",
        context_length=65536,
    )
    db_session.add(model)
    await db_session.flush()
    return model


@pytest_asyncio.fixture
async def test_anthropic_model(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> "Model":
    """Create an Anthropic model."""
    from src.db.orm.models import Model

    model = Model(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        name="Test Anthropic",
        model_id="claude-3-haiku",
        provider="anthropic",
        api_key="test-anthropic-key",
        enabled=True,
        is_public=True,
        max_tokens=4096,
        temperature=0.5,
        context_length=100000,
    )
    db_session.add(model)
    await db_session.flush()
    return model


@pytest_asyncio.fixture
async def test_ollama_model(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> "Model":
    """Create an Ollama model."""
    from src.db.orm.models import Model

    model = Model(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        name="Test Ollama",
        model_id="llama3",
        provider="ollama",
        api_key="ollama",
        enabled=True,
        is_public=True,
        max_tokens=4096,
        temperature=0.7,
        context_length=8192,
    )
    db_session.add(model)
    await db_session.flush()
    return model


# =============================================================================
# Admin & Management Service Fixtures — Tenants with balance/groups
# =============================================================================


@pytest_asyncio.fixture
async def admin_tenant(db_session: AsyncSession) -> Tenant:
    """Create a tenant with a balance and warning threshold set (for balance/usage tests)."""
    from decimal import Decimal
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=f"Admin Tenant {uuid.uuid4().hex[:8]}",
        balance_euros=Decimal("100.00"),
        warning_threshold_eur=Decimal("10.00"),
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest_asyncio.fixture
async def empty_tenant(db_session: AsyncSession) -> Tenant:
    """Create a bare tenant with no users, models, sessions, or other resources."""
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=f"Empty Tenant {uuid.uuid4().hex[:8]}",
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest_asyncio.fixture
async def unlimited_tenant(db_session: AsyncSession) -> Tenant:
    """Create a tenant with no balance limit (balance_euros=None)."""
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=f"Unlimited Tenant {uuid.uuid4().hex[:8]}",
        balance_euros=None,
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest_asyncio.fixture
async def test_group(
    db_session: AsyncSession,
    test_tenant: Tenant,
) -> "UserGroup":
    """Create a user group in the test tenant."""
    from src.db.orm.groups import UserGroup

    group = UserGroup(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        name=f"Test Group {uuid.uuid4().hex[:8]}",
    )
    db_session.add(group)
    await db_session.flush()
    return group


@pytest_asyncio.fixture
async def test_model_group(
    db_session: AsyncSession,
    test_group: "UserGroup",
    test_model: "Model",
) -> "ModelGroup":
    """Create a model-to-group assignment."""
    from src.db.orm.groups import ModelGroup

    mg = ModelGroup(
        group_id=test_group.id,
        model_id=test_model.id,
    )
    db_session.add(mg)
    await db_session.flush()
    return mg


@pytest_asyncio.fixture
async def test_tool_group(
    db_session: AsyncSession,
    test_group: "UserGroup",
    test_tool: "Tool",
) -> "ToolGroup":
    """Create a tool-to-group assignment."""
    from src.db.orm.groups import ToolGroup

    tg = ToolGroup(
        group_id=test_group.id,
        tool_id=test_tool.id,
    )
    db_session.add(tg)
    await db_session.flush()
    return tg


# =============================================================================
# E2E Test Fixture — auto-rollback for uncommitted changes
# =============================================================================


@pytest_asyncio.fixture
async def e2e_db_session():
    """Provide an async DB session for E2E tests backed by a savepoint that is
    **always** rolled back in the ``finally`` block, even when service functions
    call ``await db.commit()`` internally.

    Same savepoint mechanism as ``db_session`` — see that fixture's docstring
    for details.

    For tests that genuinely need cross-session persistence (write in one
    session, read from another), use explicit ``AsyncSessionLocal()`` instances
    and add explicit cleanup — those are handled by the ``pytest_sessionfinish``
    safety net.

    Expects ``DATABASE_URL`` env var (falls back to the Docker Compose
    default connection string).
    """
    import os

    os.environ.setdefault(
        "DATABASE_URL",
        "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
    )
    # Re-import with updated env var
    import importlib

    import src.db.base as db_base
    importlib.reload(db_base)
    from src.db.base import AsyncSessionLocal

    session = AsyncSessionLocal()
    try:
        await session.begin()

        # Same monkey-patch as db_session: commit → flush so services never
        # persist data to the database.  The outer begin() keeps the
        # transaction open, and the finally block's rollback() undoes
        # everything.
        original_flush = session.flush

        async def _noop_commit():
            await original_flush()

        session.commit = _noop_commit  # type: ignore[method-assign]

        yield session
    finally:
        await session.rollback()
        await session.close()


# =============================================================================
# Session-level cleanup — removes test data after the full test run
# =============================================================================


def pytest_sessionfinish(session, exitstatus):
    """After the entire test session finishes, delete test-created rows that
    were left behind (service functions called by tests often ``commit()``
    internally, bypassing fixture-level rollback).

    Uses a **separate engine and session** so it is not affected by any
    fixture-level transaction.

    The cleanup is selective — it deletes only rows whose names / emails match
    known test-fixture patterns.  The A2A default tenant and system user
    (fixed IDs) are **not** deleted.

    This hook runs unconditionally for every pytest session, including runs
    that exclude ``e2e`` markers.
    """
    import asyncio

    asyncio.run(_cleanup_test_data())


async def _cleanup_test_data():
    """Delete rows matching test-data patterns and log counts."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    # Use a sync engine to avoid event-loop nesting issues
    db_url = os.environ.get(
        "DATABASE_URL",
        "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
    )
    # Convert aiomysql → mysqlconnector or pymysql for sync access
    sync_url = db_url.replace("+aiomysql", "+pymysql")
    engine = create_engine(sync_url)
    total = 0

    with Session(engine) as db:
        # ---- SAFETY GUARD --------------------------------------------------
        # Only run cleanup if the database contains ONLY test-pattern users.
        # This prevents accidental data loss when pytest is run against a live
        # database (e.g. via `docker compose exec backend pytest ...`).
        _real_user_count = db.execute(
            text(
                "SELECT COUNT(*) FROM users"
                " WHERE email NOT LIKE '%@example.com'"
                " AND email NOT LIKE '%@test.com'"
                " AND email NOT LIKE '%@e2e.test'"
            )
        ).scalar()
        if _real_user_count > 0:
            print(
                f"  Cleanup: SKIPPED — database has {_real_user_count} "
                "non-test user(s). This cleanup only runs safely in CI."
            )
            return

        # Common subquery to scope child-table deletes to test sessions
        _test_session_ids = (
            "SELECT id FROM sessions WHERE"
            " title LIKE 'Test%'"
            " OR title LIKE 'E2E%'"
            " OR title LIKE 'Journey%'"
            " OR title LIKE 'CRUD%'"
            " OR title LIKE 'A2A task%'"
            " OR title LIKE 'Manual%'"
            " OR title LIKE 'API%'"
            " OR title LIKE 'Auto Tools%'"
            " OR title LIKE 'Auto Model%'"
            " OR title LIKE 'Auto Route%'"
            " OR title LIKE 'Tenant%'"
            " OR title LIKE 'Model Test%'"
            " OR title LIKE 'Updated Title%'"
            " OR title LIKE '%Chat%'"
            " OR title LIKE 'Temp%'"
            " OR title LIKE 'Skill%'"
            " OR title LIKE 'Tool%'"
            " OR title LIKE 'New%'"
            " OR title LIKE 'Multi%'"
            " OR title LIKE 'Session %'"
            " OR title LIKE 'Other%'"
            " OR title LIKE 'Finalized%'"
            " OR title LIKE 'Secret%'"
            " OR title LIKE 'Empty ID%'"
            " OR title LIKE 'DeepSeek%'"
            " OR title LIKE 'Paginated%'"
            " OR title LIKE 'Old%'"
            " OR title LIKE 'Special%'"
            " OR title LIKE 'Cross-Tenant%'"
        )
        # Named tables unconditionally deleted because they never hold seed data
        _UNCONDITIONAL_TABLES = frozenset({
            "a2a_call_logs", "a2a_tasks", "audit_logs", "autopilot_runs",
            "balance_transactions", "embed_configs", "model_groups",
            "notifications", "rag_documents", "scheduled_tasks",
            "skill_allowed_tools", "tags", "tool_groups",
            "usage_logs", "user_group_members", "user_tool_preferences",
        })

        # ---- Child / leaf tables (no FK references to other test data) ----
        # These are unconditionally deleted because they never hold seed data.
        # Tables that reference real user data (messages, file_uploads, etc.)
        # are scoped to test sessions via a subquery.
        tables = [
            (t, None) for t in sorted(_UNCONDITIONAL_TABLES)
        ] + [
            ("file_uploads", f"session_id IN ({_test_session_ids})"),
            ("message_embeddings",
             f"message_id IN (SELECT id FROM messages WHERE session_id IN ({_test_session_ids}))"),
            ("messages", f"session_id IN ({_test_session_ids})"),
            ("message_feedback",
             f"message_id IN (SELECT id FROM messages WHERE session_id IN ({_test_session_ids}))"),
            ("session_active_tools", f"session_id IN ({_test_session_ids})"),
            ("session_tags", f"session_id IN ({_test_session_ids})"),
            ("memory",
             "session_id IS NOT NULL"
             f" AND session_id IN ({_test_session_ids})"
             " OR (session_id IS NULL"
             "  AND `key` LIKE '%test%')"),
        ] + [

            # ---- Tables with name/title patterns ----
            (
                "sessions",
                "title LIKE 'Test%'"
                " OR title LIKE 'E2E%'"
                " OR title LIKE 'Journey%'"
                " OR title LIKE 'CRUD%'"
                " OR title LIKE 'A2A task%'"
                " OR title LIKE 'Manual%'"
                " OR title LIKE 'API%'"
                " OR title LIKE 'Auto Tools%'"
                " OR title LIKE 'Auto Model%'"
                " OR title LIKE 'Auto Route%'"
                " OR title LIKE 'Tenant%'"
                " OR title LIKE 'Model Test%'"
                " OR title LIKE 'Updated Title%'"
                " OR title LIKE '%Chat%'"
                " OR title LIKE 'Temp%'"
                " OR title LIKE 'Skill%'"
                " OR title LIKE 'Tool%'"
                " OR title LIKE 'New%'"
                " OR title LIKE 'Multi%'"
                " OR title LIKE 'Session %'"
                " OR title LIKE 'Other%'"
                " OR title LIKE 'Finalized%'"
                " OR title LIKE 'Secret%'"
                " OR title LIKE 'Empty ID%'"
                " OR title LIKE 'DeepSeek%'"
                " OR title LIKE 'Paginated%'"
                " OR title LIKE 'Old%'"
                " OR title LIKE 'Special%'"
                " OR title LIKE 'Cross-Tenant%'"
            ),
            (
                "skills",
                "title LIKE 'Test%'"
                " OR title LIKE 'E2E%'"
                " OR title LIKE 'Skill with%'"
                " OR title LIKE 'Skill A%'"
                " OR title LIKE 'Skill B%'"
                " OR title LIKE 'Old%'"
                " OR title LIKE 'New Skill%'"
                " OR title LIKE 'Paginated%'"
                " OR title LIKE 'Special%'"
                " OR title LIKE 'Tenant A%'"
                " OR title LIKE 'Cross-Tenant%'"
                " OR title LIKE 'Charlie%'"
                " OR title LIKE 'Skill With%'"
            ),
            (
                "prompts",
                "title LIKE 'Test%'"
                " OR title LIKE 'Tenant A%'"
                " OR description LIKE '%test%'"
                " OR description LIKE '%Test%'"
                " OR description LIKE '%tenant A%'"
            ),
            (
                "templates",
                "title LIKE 'Test%'"
                " OR title LIKE 'E2E%'"
                " OR title LIKE 'First%'"
                " OR title LIKE 'Second%'"
                " OR title LIKE 'Customer%'"
                " OR title LIKE 'Dev%'"
                " OR title LIKE 'Tenant%'"
                " OR title LIKE 'User%'"
                " OR title LIKE 'Shared%'"
                " OR title LIKE 'Mine%'"
                " OR title LIKE 'Theirs%'"
                " OR title LIKE 'Found%'"
            ),
            (
                "user_groups",
                "name LIKE 'Test%'"
                " OR name LIKE 'E2E%'"
                " OR name LIKE 'Idempotent%'"
            ),
            (
                "user_tool_credentials",
                "label LIKE 'Test%'"
                " OR label LIKE 'Tool%'"
                " OR label LIKE 'Other%'"
            ),
            (
                "models",
                "name LIKE 'Test%'"
                " OR name LIKE 'E2E%'"
                " OR name LIKE 'Blocking%'"
                " OR name LIKE 'Idempotent%'"
                " OR name LIKE 'auto-model%'"
                " OR name LIKE 'Cross-Tenant%'"
            ),
            (
                "tools",
                "name LIKE 'Test%'"
                " OR name LIKE 'E2E%'"
                " OR name LIKE 'Tool%'"
                " OR name LIKE 'Other%'"
                " OR name LIKE 'Weather%'"
                " OR name LIKE 'Stock%'"
                " OR name LIKE 'Calc%'"
                " OR name LIKE 'Public%'"
                " OR name LIKE 'Group Only%'"
                " OR name LIKE 'Hidden%'"
                " OR name LIKE 'Beta%'"
                " OR name LIKE 'Alpha%'"
                " OR name LIKE 'Always-On%'"
                " OR name LIKE 'Cross-Tenant%'"
                " OR name LIKE 'My Tool%'"
                " OR name LIKE 'Renamed%'"
                " OR name LIKE 'Disabled%'"
                " OR name LIKE 'datetime'"
                " OR name LIKE 'disabled%'"
            ),
            (
                "mcp_servers",
                "name LIKE 'Test%'"
                " OR name LIKE 'E2E%'"
                " OR name LIKE 'Mcp%'"
                " OR name LIKE 'Server%'"
            ),
            (
                "a2a_servers",
                "name LIKE 'Test%'"
                " OR name LIKE 'E2E%'"
                " OR name LIKE 'Update%'"
                " OR name LIKE 'Dep%'"
                " OR name LIKE 'Sync%'"
                " OR name LIKE 'Skillful%'"
                " OR name LIKE 'test:%'"
            ),
            # ---- Parent tables (FK targets) ----
            (
                "users",
                "email LIKE '%@example.com'"
                " OR email LIKE '%@test.com'"
                " OR email LIKE '%@e2e.test'"
            ),
            (
                "tenants",
                "name LIKE 'Test%'"
                " OR name LIKE 'E2E%'"
                " OR name LIKE 'Admin%'"
                " OR name LIKE 'Empty%'"
                " OR name LIKE 'Unlimited%'"
                " OR name LIKE 'UserTest%'"
                " OR name LIKE 'GroupTest%'"
                " OR name LIKE 'AuditTest%'"
                " OR name LIKE 'AutoRoute%'"
                " OR name LIKE 'AutoTools%'"
                " OR name LIKE 'UserJourney%'"
                " OR name LIKE 'Demo%'"
                " OR name LIKE 'Second%'"
                " OR name LIKE 'Journey%'"
                " OR name LIKE 'Test Tenant%'"
                " OR name LIKE 'AAA%'"
                " OR name LIKE 'BBB%'"
                " OR name LIKE 'Unique%'"
                " OR name LIKE 'Updated%'"
                " OR name LIKE 'Other Tenant%'"
                " OR name LIKE 'Blocker Tenant%'"
                " OR name LIKE 'Models Blocker%'"
                " OR name LIKE 'Multi Blocker%'"
                " OR name LIKE 'Demo Candidate%'"
                " OR name LIKE 'Zero%'"
                " OR name LIKE 'Negative%'"
                " OR name LIKE 'No Threshold%'"
            ),
        ]

        # Disable FK checks so we can delete in any order
        db.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

        for table_name, where_clause in tables:
            try:
                if where_clause:
                    sql = f"DELETE FROM {table_name} WHERE {where_clause}"
                else:
                    sql = f"DELETE FROM {table_name}"
                result = db.execute(text(sql))
                deleted = result.rowcount
                if deleted:
                    total += deleted
                    print(f"  Cleanup: deleted {deleted} rows from {table_name}")
            except Exception as exc:
                print(f"  Cleanup: SKIP {table_name} ({exc})")

        db.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        db.commit()

    engine.dispose()
    if total:
        print(f"  Total: deleted {total} test-data rows")
    else:
        print("  No test data found to clean up")
