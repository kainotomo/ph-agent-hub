# =============================================================================
# PH Agent Hub — Build identity tests (Issue #659)
# =============================================================================
# The session export names the build that produced it: ``version`` stays the
# export schema (1), and ``app_version`` / ``commit`` are additive fields.
# =============================================================================

import re

import pytest

from src.core import build_info
from src.core.build_info import APP_VERSION, get_app_version, get_commit

pytestmark = [pytest.mark.unit]


class TestBuildInfo:
    def test_app_version_is_semver(self):
        assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION)
        assert get_app_version() == APP_VERSION

    def test_app_version_can_be_overridden_at_runtime(self, monkeypatch):
        monkeypatch.setenv("APP_VERSION", "9.9.9")
        assert get_app_version() == "9.9.9"

    def test_commit_comes_from_the_build_environment(self, monkeypatch):
        monkeypatch.setenv("GIT_COMMIT", "abc1234def5678")
        get_commit.cache_clear()
        try:
            assert get_commit() == "abc1234def56"
        finally:
            get_commit.cache_clear()

    def test_commit_is_a_short_sha_or_unknown(self, monkeypatch):
        for name in build_info._COMMIT_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        get_commit.cache_clear()
        try:
            commit = get_commit()
        finally:
            get_commit.cache_clear()
        assert commit is None or re.fullmatch(r"[0-9a-f]{7,12}", commit)


class TestExportBuildFields:
    def test_export_dict_names_the_build_and_keeps_schema_version(self):
        from src.api.chat import _build_export_dict

        payload = _build_export_dict({}, [])
        assert payload["version"] == 1
        assert payload["app_version"] == get_app_version()
        assert "commit" in payload
        assert payload["commit"] is None or re.fullmatch(
            r"[0-9a-f]{7,12}", payload["commit"]
        )

    def test_old_export_without_build_fields_still_imports(self):
        """Issue #659 acceptance — the field is additive, not required."""
        from src.api.chat import _validate_import_payload

        old_export = {
            "version": 1,
            "exported_at": "2026-11-26T00:00:00+00:00",
            "application": "ph-agent-hub",
            "session": {"title": "Old"},
            "messages": [{"sender": "user", "content": None}],
        }
        title, messages = _validate_import_payload(old_export)
        assert title == "Old"
        assert len(messages) == 1
