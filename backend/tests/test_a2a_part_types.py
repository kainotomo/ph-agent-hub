# =============================================================================
# PH Agent Hub — A2A Part Type Tests (Issue #408)
# =============================================================================
# Tests the full Part type support (text, data, url, raw) in the A2A tool
# wrapper and the A2A server-side Part parsing.
# =============================================================================

import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from src.tools.a2a import _format_response_part


# ---------------------------------------------------------------------------
#  _format_response_part  unit tests
# ---------------------------------------------------------------------------


class TestFormatResponsePart:
    """Unit tests for _format_response_part — all four A2A Part types."""

    @staticmethod
    def _make_part(which_oneof: str | None = None, **kwargs):
        """Create a mock Part with only the specified oneof field set.

        All other content fields are explicitly set to None so the
        fallback attribute inspection doesn't pick up spurious Mock
        values.
        """
        part = MagicMock()
        # Default all content fields to None
        part.text = None
        part.data = None
        part.url = None
        part.raw = None

        # Set the requested content field
        for key, val in kwargs.items():
            setattr(part, key, val)

        # Configure WhichOneof if the mock needs it
        if which_oneof:
            part.WhichOneof = MagicMock(return_value=which_oneof)
        else:
            # No WhichOneof — forces the attribute fallback path
            del part.WhichOneof

        return part

    def test_text_part(self):
        """Should return the text content directly."""
        part = self._make_part("text", text="Hello, world!")

        result = _format_response_part(part)
        assert result == "Hello, world!"

    def test_text_part_no_whichoneof(self):
        """Should detect text via attribute fallback."""
        part = self._make_part(None, text="Plain text")

        result = _format_response_part(part)
        assert result == "Plain text"

    def test_data_part_json(self):
        """Should format structured data as JSON with [data] label."""
        part = self._make_part(
            "data",
            data={"key": "value", "nested": {"a": 1}},
            media_type="application/json",
        )

        result = _format_response_part(part)
        assert result is not None
        assert "[data: application/json]" in result
        assert '"key"' in result
        assert '"value"' in result

    def test_url_part_with_filename(self):
        """Should format URL part with filename label."""
        part = self._make_part(
            "url",
            url="https://storage.example.com/file.pdf",
            filename="report.pdf",
        )

        result = _format_response_part(part)
        assert result == "[file: report.pdf] https://storage.example.com/file.pdf"

    def test_url_part_without_filename(self):
        """Should format URL part with generic [url] label."""
        part = self._make_part(
            "url",
            url="https://example.com/data",
            filename="",
        )

        result = _format_response_part(part)
        assert result == "[url] https://example.com/data"

    def test_raw_part_with_metadata(self):
        """Should format raw/binary part with size and media type."""
        part = self._make_part(
            "raw",
            raw=b"binary content here",  # 19 bytes
            filename="image.png",
            media_type="image/png",
        )

        result = _format_response_part(part)
        assert result == "[binary: image.png] (image/png, 19 bytes)"

    def test_raw_part_minimal(self):
        """Should format raw part without filename."""
        part = self._make_part(
            "raw",
            raw=b"x" * 100,
            filename="",
            media_type="",
        )

        result = _format_response_part(part)
        assert result == "[binary] (100 bytes)"

    def test_empty_part_returns_none(self):
        """Should return None for a Part with no content set."""
        part = self._make_part(None)

        result = _format_response_part(part)
        assert result is None


# ---------------------------------------------------------------------------
#  A2A Server _extract_text_from_parts  tests
# ---------------------------------------------------------------------------


class TestExtractTextFromParts:
    """Tests for the server-side Part parser."""

    def test_text_part(self):
        """Should extract text from text parts."""
        from src.api.a2a_server import _extract_text_from_parts

        parts = [{"text": "Hello"}, {"text": " world"}]
        result = _extract_text_from_parts(parts)
        assert result == "Hello\n world"

    def test_data_part_dict(self):
        """Should serialize data dict to JSON string."""
        from src.api.a2a_server import _extract_text_from_parts

        parts = [{"data": {"key": "value"}}]
        result = _extract_text_from_parts(parts)
        assert '"key"' in result
        assert '"value"' in result

    def test_data_part_string(self):
        """Should pass through data string directly."""
        from src.api.a2a_server import _extract_text_from_parts

        parts = [{"data": '{"a":1}'}]
        result = _extract_text_from_parts(parts)
        assert result == '{"a":1}'

    def test_url_part_with_filename(self):
        """Should label URL parts with filename."""
        from src.api.a2a_server import _extract_text_from_parts

        parts = [{"url": "https://example.com/file.pdf", "filename": "report.pdf"}]
        result = _extract_text_from_parts(parts)
        assert result == "[file: report.pdf] https://example.com/file.pdf"

    def test_url_part_without_filename(self):
        """Should label URL parts without filename."""
        from src.api.a2a_server import _extract_text_from_parts

        parts = [{"url": "https://example.com/data"}]
        result = _extract_text_from_parts(parts)
        assert result == "[url] https://example.com/data"

    def test_raw_part(self):
        """Should format raw parts with base64 size info."""
        from src.api.a2a_server import _extract_text_from_parts

        raw_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAUA"
        parts = [{"raw": raw_b64, "filename": "img.png"}]
        result = _extract_text_from_parts(parts)
        assert "[binary: img.png]" in result
        assert "base64" in result

    def test_mixed_parts(self):
        """Should combine text, data, and url parts."""
        from src.api.a2a_server import _extract_text_from_parts

        parts = [
            {"text": "Here is the report:"},
            {"data": {"total": 42}},
            {"url": "https://example.com/chart.png", "filename": "chart.png"},
        ]
        result = _extract_text_from_parts(parts)
        assert "Here is the report:" in result
        assert '"total"' in result
        assert "[file: chart.png]" in result

    def test_empty_parts(self):
        """Should return empty string for no parts."""
        from src.api.a2a_server import _extract_text_from_parts

        result = _extract_text_from_parts([])
        assert result == ""


# ---------------------------------------------------------------------------
#  Agent Card  a2a_metadata  integration test
# ---------------------------------------------------------------------------


class TestAgentCardA2aMetadata:
    """Tests that the Agent Card reads a2a_metadata from skills."""

    async def test_skill_with_a2a_metadata(self, monkeypatch):
        """Agent Card should use a2a_metadata when present."""
        from unittest.mock import MagicMock, AsyncMock
        from src.api.a2a_server import get_agent_card

        # Mock settings
        monkeypatch.setattr(
            "src.api.a2a_server.settings.A2A_PUBLIC_URL",
            "https://api.example.com",
        )
        monkeypatch.setattr(
            "src.api.a2a_server.settings.A2A_ORGANIZATION_NAME", "Test Hub"
        )
        monkeypatch.setattr(
            "src.api.a2a_server.settings.A2A_ORGANIZATION_URL",
            "https://example.com",
        )
        monkeypatch.setattr(
            "src.api.a2a_server.settings.A2A_DOCS_URL", ""
        )

        # Build a mock request with a mock DB that returns a skill with a2a_metadata
        request = MagicMock()
        request.base_url = "https://api.example.com/"
        request.state = MagicMock()

        mock_skill = MagicMock()
        mock_skill.id = "skill-uuid-1"
        mock_skill.name = "JSON Analyzer"
        mock_skill.description = "Analyzes JSON payloads"
        mock_skill.a2a_metadata = {
            "inputModes": ["application/json"],
            "outputModes": ["application/json", "text/plain"],
            "examples": ["Analyze this: {\"foo\":\"bar\"}"],
            "tags": ["json", "analysis"],
        }

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_skill]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        request.state.db = mock_db

        data = await get_agent_card(request)

        assert len(data["skills"]) == 1
        skill = data["skills"][0]
        assert skill["id"] == "skill-uuid-1"
        assert skill["name"] == "JSON Analyzer"
        assert skill["inputModes"] == ["application/json"]
        assert skill["outputModes"] == ["application/json", "text/plain"]
        assert skill["examples"] == ["Analyze this: {\"foo\":\"bar\"}"]
        assert skill["tags"] == ["json", "analysis"]

    async def test_skill_without_a2a_metadata_falls_back(self, monkeypatch):
        """Agent Card should fall back to text/plain when a2a_metadata is None."""
        from unittest.mock import MagicMock, AsyncMock
        from src.api.a2a_server import get_agent_card

        monkeypatch.setattr(
            "src.api.a2a_server.settings.A2A_PUBLIC_URL",
            "https://api.example.com",
        )
        monkeypatch.setattr(
            "src.api.a2a_server.settings.A2A_ORGANIZATION_NAME", "Test Hub"
        )
        monkeypatch.setattr(
            "src.api.a2a_server.settings.A2A_ORGANIZATION_URL",
            "https://example.com",
        )
        monkeypatch.setattr(
            "src.api.a2a_server.settings.A2A_DOCS_URL", ""
        )

        request = MagicMock()
        request.base_url = "https://api.example.com/"
        request.state = MagicMock()

        mock_skill = MagicMock()
        mock_skill.id = "skill-uuid-2"
        mock_skill.name = "Basic Agent"
        mock_skill.description = "A basic agent"
        mock_skill.a2a_metadata = None  # No metadata

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_skill]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        request.state.db = mock_db

        data = await get_agent_card(request)

        skill = data["skills"][0]
        assert skill["inputModes"] == ["text/plain"]
        assert skill["outputModes"] == ["text/plain"]
        assert skill["examples"] == []
        assert skill["tags"] == []


# ---------------------------------------------------------------------------
#  Part type round-trip tests — format → extract cycle
# ---------------------------------------------------------------------------


class TestPartTypeRoundTrip:
    """Verify that Parts formatted by _format_response_part can be
    parsed by _extract_text_from_parts (text content preservation).

    These test the end-to-end format→extract cycle to ensure Part
    fidelity across the A2A pipeline.
    """

    def test_text_round_trip(self):
        """Text Part: formatted text should be extractable."""
        from src.api.a2a_server import _extract_text_from_parts
        from src.tools.a2a import _format_response_part

        # Simulate a response Part
        from unittest.mock import MagicMock

        part = MagicMock()
        part.text = "Hello, world!"
        part.data = None
        part.url = None
        part.raw = None
        part.WhichOneof = MagicMock(return_value="text")
        part.filename = ""
        part.media_type = ""

        formatted = _format_response_part(part)
        assert formatted == "Hello, world!"

        # Simulate the server receiving this as a dict-based Part
        extracted = _extract_text_from_parts([{"text": formatted}])
        assert "Hello, world!" in extracted

    def test_data_round_trip(self):
        """Data Part: JSON content should survive format→extract."""
        from src.api.a2a_server import _extract_text_from_parts
        from src.tools.a2a import _format_response_part
        from unittest.mock import MagicMock

        # Simulate a data Part with structured content
        part = MagicMock()
        part.text = None
        part.url = None
        part.raw = None
        part.WhichOneof = MagicMock(return_value="data")
        part.filename = ""
        part.media_type = "application/json"

        # Mock the data attribute to have a dict-like structure
        mock_data = MagicMock()
        mock_data.items.return_value = [("key", "value"), ("count", 42)]
        # MessageToDict-like behavior
        from google.protobuf.json_format import MessageToDict
        part.data = mock_data

        # For the JSON serialization path, we need MessageToDict to work
        # Since we're using MagicMock, simulate it differently
        # The actual code does json.dumps(MessageToDict(r_part.data), indent=2)
        # So we patch MessageToDict to return a known dict
        from unittest.mock import patch
        with patch(
            "google.protobuf.json_format.MessageToDict",
            return_value={"key": "value", "count": 42},
        ):
            formatted = _format_response_part(part)
            assert formatted is not None
            assert '"key"' in formatted
            assert '"value"' in formatted

            extracted = _extract_text_from_parts([{"text": formatted}])
            assert "key" in extracted
            assert "value" in extracted

    def test_mixed_parts_round_trip(self):
        """Mixed text+data Parts should survive format→extract."""
        from src.api.a2a_server import _extract_text_from_parts
        from src.tools.a2a import _format_response_part
        from unittest.mock import MagicMock, patch

        text_part = MagicMock()
        text_part.text = "Analysis result:"
        text_part.data = None
        text_part.url = None
        text_part.raw = None
        text_part.WhichOneof = MagicMock(return_value="text")
        text_part.filename = ""
        text_part.media_type = ""

        url_part = MagicMock()
        url_part.text = None
        url_part.data = None
        url_part.url = "https://example.com/chart.png"
        url_part.raw = None
        url_part.WhichOneof = MagicMock(return_value="url")
        url_part.filename = "chart.png"
        url_part.media_type = ""

        text_fmt = _format_response_part(text_part)
        url_fmt = _format_response_part(url_part)

        assert text_fmt == "Analysis result:"
        assert url_fmt == "[file: chart.png] https://example.com/chart.png"

        # The server would receive these as dict-based parts
        parts = [
            {"text": text_fmt},
            {"text": url_fmt},
        ]
        extracted = _extract_text_from_parts(parts)
        assert "Analysis result:" in extracted
        assert "chart.png" in extracted
