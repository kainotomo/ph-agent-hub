# =============================================================================
# PH Agent Hub — File Upload Filename Sanitization Tests
# =============================================================================
# Unit tests for the ``_sanitize_storage_filename`` and
# ``_encode_content_disposition_filename`` utilities.
# =============================================================================

import pytest

from src.services.upload_service import (
    _encode_content_disposition_filename,
    _sanitize_storage_filename,
)

pytestmark = [pytest.mark.unit]


# =============================================================================
# _sanitize_storage_filename
# =============================================================================


class TestSanitizeStorageFilename:
    """Unit tests for ``_sanitize_storage_filename()``."""

    def test_normal_ascii_filename(self):
        """A plain ASCII filename passes through unchanged."""
        assert _sanitize_storage_filename("hello.txt") == "hello.txt"

    def test_path_traversal(self):
        """Path separators and parent-dir sequences are neutralized."""
        result = _sanitize_storage_filename("../../../etc/passwd")
        assert "/" not in result
        assert ".." not in result
        assert result == "etc_passwd"

    def test_backslash_path_separator(self):
        """Backslash path separators are neutralized."""
        result = _sanitize_storage_filename("..\\..\\windows\\system32\\config")
        assert "\\" not in result
        assert ".." not in result
        assert result == "windows_system32_config"

    def test_header_injection_special_chars(self):
        """Characters unsafe for HTTP headers (quotes, semicolons, etc.) are removed."""
        result = _sanitize_storage_filename('file"; DROP TABLE;.txt')
        assert '"' not in result
        assert "'" not in result
        assert ";" not in result
        assert ":" not in result
        assert "*" not in result
        assert result.endswith(".txt")

    def test_unicode_normalization(self):
        """Non-ASCII Unicode characters are normalized to ASCII where possible."""
        # NFKD: résumé → re´sume´ → encodes to re?sume? → ? replaced with _
        result = _sanitize_storage_filename("résumé.pdf")
        assert all(ord(c) < 128 for c in result), "Should be pure ASCII"
        assert result.endswith(".pdf")

    def test_unicode_cjk_replaced(self):
        """CJK characters (no ASCII equivalent) are replaced with underscores."""
        result = _sanitize_storage_filename("中文文档.docx")
        assert all(ord(c) < 128 for c in result), "Should be pure ASCII"
        assert result.endswith(".docx")

    def test_empty_filename_fallback(self):
        """An empty or whitespace-only filename falls back to 'file'."""
        assert _sanitize_storage_filename("") == "file"
        assert _sanitize_storage_filename("   ") == "file"
        assert _sanitize_storage_filename("...") == "file"

    def test_null_byte_stripped(self):
        """Null bytes are replaced."""
        result = _sanitize_storage_filename("null\x00byte.txt")
        assert "\x00" not in result
        assert result.endswith(".txt")

    def test_long_filename_truncated(self):
        """Filenames longer than 200 chars are truncated, preserving extension."""
        long_name = "a" * 250 + ".pdf"
        result = _sanitize_storage_filename(long_name)
        assert len(result) <= 200
        assert result.endswith(".pdf")

    def test_leading_trailing_dots_and_spaces(self):
        """Leading/trailing dots, spaces, and underscores are stripped."""
        result = _sanitize_storage_filename("  ...hello world...  ")
        # Consecutive dots are collapsed first (traversal prevention),
        # then spaces replaced with underscores, then leading/trailing
        # underscores stripped.
        assert " " not in result
        assert ".." not in result
        assert not result.endswith(".")
        assert result == "hello_world"

    def test_collapses_consecutive_underscores(self):
        """Multiple consecutive underscores are collapsed into one."""
        result = _sanitize_storage_filename("a///b\\\\\\c")
        # After replacing / and \ with _, then collapsing: a_b_c
        assert "_" in result
        assert "___" not in result

    def test_no_extension_preserved_as_is(self):
        """A filename without an extension is preserved (minus special chars)."""
        result = _sanitize_storage_filename("README")
        assert result == "README"

    def test_dotfile_preserved(self):
        """A dotfile like '.htaccess' preserves its leading dot."""
        result = _sanitize_storage_filename(".htaccess")
        # Dots are only stripped from the end, so leading dot is preserved
        assert result == ".htaccess"

    def test_all_special_chars_fallback(self):
        """A filename consisting entirely of special chars falls back."""
        result = _sanitize_storage_filename('*?"<>|')
        assert result == "file"


# =============================================================================
# _encode_content_disposition_filename
# =============================================================================


class TestEncodeContentDispositionFilename:
    """Unit tests for ``_encode_content_disposition_filename()``."""

    def test_ascii_only_simple_filename(self):
        """ASCII-only filenames produce a simple filename= parameter."""
        result = _encode_content_disposition_filename("hello.txt")
        assert result.startswith('attachment; filename="')
        assert result.endswith('"')
        assert "filename*=" not in result

    def test_non_ascii_includes_rfc5987_param(self):
        """Non-ASCII filenames include both filename= and filename*= parameters."""
        result = _encode_content_disposition_filename("résumé.pdf")
        assert result.startswith("attachment; ")
        assert 'filename="' in result
        assert "filename*=UTF-8''" in result

    def test_non_ascii_encoding_is_percent_encoded(self):
        """The filename* value is properly percent-encoded."""
        result = _encode_content_disposition_filename("café.txt")
        # UTF-8 percent-encoded form of 'é' is %C3%A9
        assert "%C3%A9" in result

    def test_special_chars_not_breaking_header(self):
        """Characters like quotes and semicolons don't break the header structure."""
        result = _encode_content_disposition_filename('file"test.csv')
        # Should not contain unescaped quotes that break the header
        assert result.startswith("attachment; ")

    def test_empty_fallback(self):
        """An empty/weird filename still produces a valid header."""
        result = _encode_content_disposition_filename("")
        assert result.startswith("attachment; ")
