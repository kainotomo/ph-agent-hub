# =============================================================================
# PH Agent Hub — fetch_url SEC live verification (Issue #594)
# =============================================================================
# Opt-in test that calls the real SEC EDGAR endpoint. A mocked test cannot
# prove that SEC accepts the User-Agent; this one can.
#
# Run manually (requires the usual backend test env):
#   cd backend
#   RUN_LIVE_API_TESTS=1 .venv/bin/python -m pytest tests/test_fetch_url_sec_live.py -q
# =============================================================================

import os

import pytest

from src.tools.fetch_url import build_fetch_url_tools

pytestmark = [
    pytest.mark.live_api,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_API_TESTS") != "1",
        reason="live SEC test; set RUN_LIVE_API_TESTS=1 to enable",
    ),
]

SEC_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/data/726728/000072672826000056/"
    "0000726728-26-000056-index.html"
)


async def test_sec_index_returns_200_with_default_user_agent():
    (fetch_url,) = build_fetch_url_tools()
    result = await fetch_url(url=SEC_INDEX_URL)

    assert result.get("status_code") == 200, result
    assert "EDGAR Filing Documents" in result.get("title", "")
    assert "Undeclared Automated Tool" not in result.get("text", "")


async def test_sec_index_returns_200_with_configured_user_agent():
    (fetch_url,) = build_fetch_url_tools(
        {"sec_user_agent": "ph-agent-hub-test/1.0 (phalouvas@gmail.com)"}
    )
    result = await fetch_url(url=SEC_INDEX_URL)

    assert result.get("status_code") == 200, result
    assert "Undeclared Automated Tool" not in result.get("text", "")
