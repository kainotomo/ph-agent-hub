# =============================================================================
# PH Agent Hub — User Notification Preferences Tests
# =============================================================================
# Covers the pure normaliser and the two endpoints that expose it:
# ``GET /api/users/me`` and ``PUT /api/users/me/notifications``.
# =============================================================================

import pytest

from src.services.user_preferences import (
    DEFAULT_NOTIFICATION_PREFS,
    normalize_notification_prefs,
)

pytestmark = [pytest.mark.integration]


class TestNormalizeNotificationPrefs:
    def test_null_returns_defaults(self):
        assert normalize_notification_prefs(None) == DEFAULT_NOTIFICATION_PREFS

    def test_partial_dict_keeps_defaults_for_missing_keys(self):
        prefs = normalize_notification_prefs({"enabled": True})
        assert prefs["enabled"] is True
        assert prefs["on_completion"] is True
        assert prefs["on_failure"] is True
        assert prefs["sound"] is False

    def test_non_boolean_values_fall_back_to_default(self):
        prefs = normalize_notification_prefs({"enabled": "yes", "sound": 1})
        assert prefs["enabled"] is False
        assert prefs["sound"] is False

    def test_unknown_keys_are_dropped(self):
        prefs = normalize_notification_prefs({"enabled": True, "bogus": True})
        assert set(prefs) == set(DEFAULT_NOTIFICATION_PREFS)


class TestGetMeNotificationPrefs:
    async def test_defaults_when_user_has_no_preferences(
        self, async_client, auth_headers, test_user
    ):
        response = await async_client.get(
            "/api/users/me", headers=auth_headers(test_user)
        )
        assert response.status_code == 200
        assert response.json()["notification_prefs"] == DEFAULT_NOTIFICATION_PREFS

    async def test_persisted_values_are_returned(
        self, async_client, auth_headers, test_user
    ):
        headers = auth_headers(test_user)
        await async_client.put(
            "/api/users/me/notifications",
            headers=headers,
            json={"enabled": True, "sound": True},
        )
        response = await async_client.get("/api/users/me", headers=headers)
        prefs = response.json()["notification_prefs"]
        assert prefs["enabled"] is True
        assert prefs["sound"] is True
        assert prefs["on_completion"] is True


class TestSetNotificationPrefs:
    async def test_put_returns_normalised_preferences(
        self, async_client, auth_headers, test_user
    ):
        response = await async_client.put(
            "/api/users/me/notifications",
            headers=auth_headers(test_user),
            json={"enabled": True},
        )
        assert response.status_code == 200
        assert response.json() == {
            "enabled": True,
            "on_completion": True,
            "on_failure": True,
            "sound": False,
        }

    async def test_put_with_empty_body_keeps_defaults(
        self, async_client, auth_headers, test_user
    ):
        response = await async_client.put(
            "/api/users/me/notifications",
            headers=auth_headers(test_user),
            json={},
        )
        assert response.status_code == 200
        assert response.json() == DEFAULT_NOTIFICATION_PREFS

    async def test_put_merges_omitted_fields(
        self, async_client, auth_headers, test_user
    ):
        headers = auth_headers(test_user)
        await async_client.put(
            "/api/users/me/notifications",
            headers=headers,
            json={"enabled": True, "sound": True},
        )
        response = await async_client.put(
            "/api/users/me/notifications",
            headers=headers,
            json={"on_completion": False},
        )
        assert response.status_code == 200
        prefs = response.json()
        assert prefs["enabled"] is True
        assert prefs["sound"] is True
        assert prefs["on_completion"] is False

    async def test_put_rejects_non_boolean_enabled(
        self, async_client, auth_headers, test_user
    ):
        response = await async_client.put(
            "/api/users/me/notifications",
            headers=auth_headers(test_user),
            json={"enabled": "maybe"},
        )
        assert response.status_code == 422

    async def test_put_ignores_unknown_keys(
        self, async_client, auth_headers, test_user
    ):
        response = await async_client.put(
            "/api/users/me/notifications",
            headers=auth_headers(test_user),
            json={"enabled": True, "bogus": 1},
        )
        assert response.status_code == 200
        assert "bogus" not in response.json()

    async def test_requires_authentication(self, async_client):
        response = await async_client.put(
            "/api/users/me/notifications", json={"enabled": True}
        )
        assert response.status_code == 401
