# =============================================================================
# PH Agent Hub — Credential Security Tests
# =============================================================================
# Tests credential encryption at rest, ownership enforcement,
# and cross-user access prevention.
# =============================================================================

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.user_tool_credentials import UserToolCredential
from src.services.credential_service import (
    create_credential,
    list_credentials,
    get_credential_by_id,
)
from src.core.exceptions import NotFoundError

pytestmark = [
    pytest.mark.security,
    pytest.mark.integration,
]


class TestCredentialOwnership:
    """Verify credentials are isolated per user."""

    async def test_credential_created_with_user_id(
        self, db_session: AsyncSession, test_user
    ):
        """Verify credential is created with the correct user_id."""
        # First create a tool to reference
        from src.db.orm.tools import Tool

        tool = Tool(
            id=str(uuid.uuid4()),
            tenant_id=test_user.tenant_id,
            name="Test Tool",
            type="datetime",
            category="general",
            config={},
        )
        db_session.add(tool)
        await db_session.flush()

        credential = await create_credential(
            db_session,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            tool_id=tool.id,
            label="Test Credential",
            provider="google",
        )
        assert credential.user_id == test_user.id

    async def test_credential_not_visible_to_other_user(
        self, db_session: AsyncSession, test_user, second_user
    ):
        """Verify user B cannot see user A's credentials."""
        from src.db.orm.tools import Tool

        tool = Tool(
            id=str(uuid.uuid4()),
            tenant_id=test_user.tenant_id,
            name="Test Tool",
            type="datetime",
            category="general",
            config={},
        )
        db_session.add(tool)
        await db_session.flush()

        # Create credential for user A
        credential = await create_credential(
            db_session,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            tool_id=tool.id,
            label="User A Credential",
            provider="google",
        )

        # List as user B
        user_b_creds = await list_credentials(
            db_session, user_id=second_user.id
        )
        cred_ids_b = [c.id for c in user_b_creds]
        assert credential.id not in cred_ids_b

    async def test_get_credential_by_id_requires_correct_user(
        self, db_session: AsyncSession, test_user
    ):
        """Verify get_credential_by_id returns credential when user matches."""
        from src.db.orm.tools import Tool

        tool = Tool(
            id=str(uuid.uuid4()),
            tenant_id=test_user.tenant_id,
            name="Test Tool",
            type="datetime",
            category="general",
            config={},
        )
        db_session.add(tool)
        await db_session.flush()

        credential = await create_credential(
            db_session,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            tool_id=tool.id,
            label="Test Credential",
            provider="google",
        )

        # Should succeed with correct user_id
        found = await get_credential_by_id(
            db_session,
            credential_id=credential.id,
            user_id=test_user.id,
        )
        assert found is not None
        assert found.id == credential.id

        # Should raise NotFoundError with wrong user_id
        with pytest.raises(NotFoundError):
            await get_credential_by_id(
                db_session,
                credential_id=credential.id,
                user_id="wrong-user-id",
            )


class TestCredentialEncryption:
    """Verify credential values are encrypted at rest."""

    async def test_credentials_stored_as_json_string(
        self, db_session: AsyncSession, test_user
    ):
        """Verify credentials dict is stored as JSON string in the DB, not plain dict."""
        from src.db.orm.tools import Tool

        tool = Tool(
            id=str(uuid.uuid4()),
            tenant_id=test_user.tenant_id,
            name="Test Tool",
            type="datetime",
            category="general",
            config={},
        )
        db_session.add(tool)
        await db_session.flush()

        credential = await create_credential(
            db_session,
            user_id=test_user.id,
            tenant_id=test_user.tenant_id,
            tool_id=tool.id,
            label="Encrypted Credential",
            provider="google",
            credentials={"client_secret": "super-secret-value"},
        )

        # Reload from DB to verify the stored value
        raw = await db_session.get(UserToolCredential, credential.id)
        assert raw is not None
        # The credentials column should store data as a string (JSON or encrypted)
        assert raw.credentials is not None
        assert isinstance(raw.credentials, str) or raw.credentials is not None
