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


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session():
    """Create a fresh async DB session for each test.

    Uses a transaction that is rolled back after each test to ensure
    test isolation without creating/dropping tables.
    """
    session = AsyncSessionLocal()
    try:
        # Begin a transaction that will be rolled back
        await session.begin()
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
