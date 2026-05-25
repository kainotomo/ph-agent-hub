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

# Ensure the backend src is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import Base, AsyncSessionLocal
from db.orm.tenants import Tenant
from db.orm.users import User
from db.orm.file_uploads import FileUpload


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
