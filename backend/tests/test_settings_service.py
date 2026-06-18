# =============================================================================
# PH Agent Hub — App Settings Service Tests
# =============================================================================

import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.app_settings import AppSetting
from src.services.settings_service import get_setting, get_all_settings, set_settings

pytestmark = [pytest.mark.integration]


class TestGetSetting:
    """Tests for get_setting."""

    async def test_existing(self, db_session: AsyncSession):
        """Should return the value when the key exists."""
        db_session.add(AppSetting(key="test.key", value="test_value"))
        await db_session.flush()

        result = await get_setting(db_session, "test.key")
        assert result == "test_value"

    async def test_nonexistent_returns_default(self, db_session: AsyncSession):
        """Should return the provided default when key is missing."""
        result = await get_setting(db_session, "missing.key", default="fallback")
        assert result == "fallback"

    async def test_nonexistent_returns_none(self, db_session: AsyncSession):
        """Should return None when key is missing and no default provided."""
        result = await get_setting(db_session, "missing.key")
        assert result is None


class TestGetAllSettings:
    """Tests for get_all_settings."""

    async def test_returns_dict(self, db_session: AsyncSession):
        """Should return a dict of all settings."""
        await get_all_settings(db_session)

    async def test_includes_new_settings(self, db_session: AsyncSession):
        """Should include newly added settings in the result."""
        db_session.add(AppSetting(key="test.new.key", value="test_value"))
        await db_session.flush()

        result = await get_all_settings(db_session)
        assert result["test.new.key"] == "test_value"

    async def test_filters_none_values(self, db_session: AsyncSession):
        """Should exclude settings with None values from the result."""
        db_session.add_all([
            AppSetting(key="test.key_a", value="value_a"),
            AppSetting(key="test.key_b", value=None),
            AppSetting(key="test.key_c", value="value_c"),
        ])
        await db_session.flush()

        result = await get_all_settings(db_session)
        assert "test.key_b" not in result
        assert result["test.key_a"] == "value_a"
        assert result["test.key_c"] == "value_c"


class TestSetSettings:
    """Tests for set_settings."""

    _counter = 0

    def _unique_key(self, prefix: str) -> str:
        TestSetSettings._counter += 1
        return f"tss_{prefix}_{TestSetSettings._counter}_{uuid.uuid4().hex[:6]}"

    async def test_create_new(self, db_session: AsyncSession):
        """Should create new settings from key-value pairs."""
        key = self._unique_key("create")
        result = await set_settings(db_session, {key: "created"})
        assert result[key] == "created"

        # Verify persisted
        row = await db_session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        assert row.scalar_one_or_none() is not None

    async def test_update_existing(self, db_session: AsyncSession):
        """Should update values of existing settings."""
        key = self._unique_key("update")
        db_session.add(AppSetting(key=key, value="original"))
        await db_session.flush()

        result = await set_settings(db_session, {key: "updated"})
        assert result[key] == "updated"

        # Verify persisted
        row = await db_session.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        assert row.scalar_one().value == "updated"

    async def test_mixed_create_and_update(self, db_session: AsyncSession):
        """Should handle a mix of new and existing keys."""
        existing_key = self._unique_key("mix_existing")
        new_key = self._unique_key("mix_new")
        db_session.add(AppSetting(key=existing_key, value="old_value"))
        await db_session.flush()

        result = await set_settings(db_session, {
            existing_key: "new_value",
            new_key: "brand_new",
        })
        assert result[existing_key] == "new_value"
        assert result[new_key] == "brand_new"

    async def test_empty_dict(self, db_session: AsyncSession):
        """Should handle an empty settings dict gracefully."""
        result = await set_settings(db_session, {})
        assert isinstance(result, dict)
