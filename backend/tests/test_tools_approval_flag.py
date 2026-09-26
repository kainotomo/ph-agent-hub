# =============================================================================
# PH Agent Hub — Tools Approval Flag Tests
# =============================================================================
# Pure unit tests for the admin tools API approval_required flag plumbing.
# No DB fixtures, no network.
# =============================================================================

from unittest.mock import MagicMock

import pytest

from src.api.admin import ToolCreate, _admin_tool_response

# ---------------------------------------------------------------------------
# Module markers — pure unit tests, no DB / no network
# ---------------------------------------------------------------------------
pytestmark = [pytest.mark.unit]


def test_admin_tool_response_includes_approval_required():
    tool = MagicMock()
    tool.approval_required = True
    # Concrete values keep resolve_capabilities() on the plain-string path.
    tool.type = "web_search"
    tool.code = None

    assert _admin_tool_response(tool)["approval_required"] is True


def test_tool_create_schema_defaults_approval_required_false():
    body = ToolCreate(name="x", type="web_search")

    assert body.approval_required is False
