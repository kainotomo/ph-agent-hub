# =============================================================================
# PH Agent Hub — Router Service Tests
# =============================================================================
# Integration tests for ``route_message``.  External LLM calls are mocked at
# ``src.models.base.get_chat_client`` to avoid real API calls.
# =============================================================================

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.models import Model
from src.services.router_service import route_message

pytestmark = [pytest.mark.integration]


# ===========================================================================
# Helper — create a Model row in the DB
# ===========================================================================


async def _create_model(
    db_session: AsyncSession,
    tenant_id: str,
    model_id: str = "test-model",
    name: str = "Test Model",
    provider: str = "openai",
    input_price_per_1m: float | None = 1.0,
    enabled: bool = True,
    auto_route_eligible: bool = True,
) -> Model:
    """Create and return a Model ORM row with the given attributes."""
    model = Model(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        model_id=model_id,
        name=name,
        provider=provider,
        api_key="test-key",
        enabled=enabled,
        is_public=True,
        max_tokens=4096,
        temperature=0.7,
        input_price_per_1m=input_price_per_1m,
        auto_route_eligible=auto_route_eligible,
    )
    db_session.add(model)
    await db_session.flush()
    return model


# ===========================================================================
# Helper — build a mock chat client
# ===========================================================================


def _mock_chat_client(text: str = "test-model") -> MagicMock:
    """Return a mock ``get_chat_client`` that yields ``text`` as the model ID."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_message = MagicMock()
    mock_message.text = text
    mock_response.messages = [mock_message]
    mock_client.get_response.return_value = mock_response
    return mock_client


# ===========================================================================
# Tests
# ===========================================================================


class TestRouteMessage:
    """Tests for route_message with mocked LLM classifier."""

    @patch("src.models.base.get_chat_client")
    async def test_no_eligible_models_returns_none(
        self, mock_get_client, db_session: AsyncSession, test_user, test_tenant
    ):
        """When no models have auto_route_eligible=True, should return None."""
        # Create a model that is NOT auto_route_eligible
        await _create_model(
            db_session, test_tenant.id, model_id="non-eligible",
            auto_route_eligible=False,
        )

        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
        )
        assert result is None
        mock_get_client.assert_not_called()

    @patch("src.models.base.get_chat_client")
    async def test_single_eligible_model_uses_it_as_classifier(
        self, mock_get_client, db_session: AsyncSession, test_user, test_tenant
    ):
        """Single eligible model should be both the classifier and the output."""
        model = await _create_model(
            db_session, test_tenant.id, model_id="single-model",
        )
        mock_get_client.return_value = _mock_chat_client(text="single-model")

        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
        )
        assert result == model.id

        # Verify the classifier was the only eligible model
        called_client = mock_get_client.call_args
        assert called_client is not None
        assert called_client[0][0].id == model.id

    @patch("src.models.base.get_chat_client")
    async def test_cheapest_model_used_as_classifier(
        self, mock_get_client, db_session: AsyncSession, test_user, test_tenant
    ):
        """Cheapest model (by input_price_per_1m) should be the classifier."""
        cheap = await _create_model(
            db_session, test_tenant.id, model_id="cheap-model",
            input_price_per_1m=0.5,
        )
        await _create_model(
            db_session, test_tenant.id, model_id="expensive-model",
            input_price_per_1m=10.0,
        )
        mock_get_client.return_value = _mock_chat_client(text="cheap-model")

        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
        )
        assert result == cheap.id

        # Verify classifier is the cheap model
        called_client = mock_get_client.call_args
        assert called_client is not None
        assert called_client[0][0].id == cheap.id

    @patch("src.models.base.get_chat_client")
    async def test_valid_model_id_resolved_case_insensitive(
        self, mock_get_client, db_session: AsyncSession, test_user, test_tenant
    ):
        """Classifier response should be matched case-insensitively."""
        model = await _create_model(
            db_session, test_tenant.id, model_id="DeepSeek-V4-Pro",
        )
        # Classifier returns lowercase version
        mock_get_client.return_value = _mock_chat_client(text="deepseek-v4-pro")

        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
        )
        assert result == model.id

    @patch("src.models.base.get_chat_client")
    async def test_invalid_model_id_falls_back_to_cheapest(
        self, mock_get_client, db_session: AsyncSession, test_user, test_tenant
    ):
        """When classifier returns an unknown model_id, fall back to cheapest."""
        cheap = await _create_model(
            db_session, test_tenant.id, model_id="cheap-one",
            input_price_per_1m=0.5,
        )
        await _create_model(
            db_session, test_tenant.id, model_id="expensive-one",
            input_price_per_1m=10.0,
        )
        mock_get_client.return_value = _mock_chat_client(text="nonexistent-model")

        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
        )
        # Falls back to cheapest eligible
        assert result == cheap.id

    @patch("src.models.base.get_chat_client")
    async def test_empty_response_falls_back_to_cheapest(
        self, mock_get_client, db_session: AsyncSession, test_user, test_tenant
    ):
        """Empty classifier response should fall back to cheapest."""
        cheap = await _create_model(
            db_session, test_tenant.id, model_id="cheap-one",
            input_price_per_1m=0.5,
        )
        mock_get_client.return_value = _mock_chat_client(text="")

        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
        )
        assert result == cheap.id

    @patch("src.models.base.get_chat_client")
    async def test_exception_during_classification_falls_back_to_cheapest(
        self, mock_get_client, db_session: AsyncSession, test_user, test_tenant
    ):
        """Exception in the LLM call should fall back gracefully."""
        cheap = await _create_model(
            db_session, test_tenant.id, model_id="cheap-one",
            input_price_per_1m=0.5,
        )
        mock_client = AsyncMock()
        mock_client.get_response.side_effect = RuntimeError("LLM unavailable")
        mock_get_client.return_value = mock_client

        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
        )
        assert result == cheap.id

    @patch("src.models.base.get_chat_client")
    async def test_none_price_treated_as_expensive(
        self, mock_get_client, db_session: AsyncSession, test_user, test_tenant
    ):
        """Models with None input_price_per_1m should be sorted as 999999 sentinel."""
        free_model = await _create_model(
            db_session, test_tenant.id, model_id="free-model",
            input_price_per_1m=None,
        )
        paid_model = await _create_model(
            db_session, test_tenant.id, model_id="paid-model",
            input_price_per_1m=5.0,
        )
        # Classifier returns free model's ID — classifier is the paid one (cheapest with real price)
        # Actually the classifier sorts ascending, so None → 999999 is the most expensive
        mock_get_client.return_value = _mock_chat_client(text="paid-model")

        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
        )
        # Since paid is cheapest (5.0 vs None→999999), classifier is paid
        called_client = mock_get_client.call_args
        assert called_client is not None
        assert called_client[0][0].id == paid_model.id
        assert result == paid_model.id

    @patch("src.models.base.get_chat_client")
    async def test_tenant_isolation(
        self, mock_get_client, db_session: AsyncSession,
        test_user, test_tenant, second_user, second_tenant,
    ):
        """Models from another tenant should not be visible."""
        # Create model in tenant A
        await _create_model(
            db_session, test_tenant.id, model_id="tenant-a-model",
        )
        mock_get_client.return_value = _mock_chat_client(text="tenant-a-model")

        # User from tenant B should not see tenant A's models
        result = await route_message(
            db_session,
            message="Hello",
            tenant_id=second_tenant.id,
            user_id=second_user.id,
        )
        assert result is None
        mock_get_client.assert_not_called()
