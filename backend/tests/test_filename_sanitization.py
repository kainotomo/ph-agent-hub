# =============================================================================
# PH Agent Hub — File Upload Filename Sanitization Tests
# =============================================================================
# Unit tests for the ``_sanitize_storage_filename`` and
# ``_encode_content_disposition_filename`` utilities.
# =============================================================================

import pytest

from src.services.upload_service import (
    _encode_content_disposition_filename,
    _resolve_content_type,
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
        assert 'filename="file"' in result


# =============================================================================
# _resolve_content_type
# =============================================================================


class TestResolveContentType:
    """Unit tests for ``_resolve_content_type()``."""

    def test_specific_mime_type_returned_as_is(self):
        """A specific (non-generic) MIME type is returned unchanged."""
        assert _resolve_content_type("image/png", "foo.xyz") == "image/png"

    def test_generic_type_falls_back_to_override(self):
        """application/octet-stream falls back to extension-based override."""
        assert _resolve_content_type("application/octet-stream", "report.docx") == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_yml_extension_resolves_to_text_plain(self):
        """.yml files with generic content type resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "config.yml") == "text/plain"

    def test_yaml_extension_resolves_to_text_plain(self):
        """.yaml files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "config.yaml") == "text/plain"

    def test_log_extension_resolves_to_text_plain(self):
        """.log files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "server.log") == "text/plain"

    def test_py_extension_resolves_to_text_plain(self):
        """.py files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "script.py") == "text/plain"

    def test_js_extension_resolves_to_text_plain(self):
        """.js files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "app.js") == "text/plain"

    def test_ts_extension_resolves_to_text_plain(self):
        """.ts files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "component.ts") == "text/plain"

    def test_sh_extension_resolves_to_text_plain(self):
        """.sh files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "deploy.sh") == "text/plain"

    def test_sql_extension_resolves_to_text_plain(self):
        """.sql files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "query.sql") == "text/plain"

    def test_html_extension_resolves_to_text_plain(self):
        """.html files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "index.html") == "text/plain"

    def test_css_extension_resolves_to_text_plain(self):
        """.css files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "style.css") == "text/plain"

    def test_xml_extension_resolves_to_text_plain(self):
        """.xml files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "data.xml") == "text/plain"

    def test_toml_extension_resolves_to_text_plain(self):
        """.toml files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "pyproject.toml") == "text/plain"

    def test_env_extension_not_overridden_to_text_plain_directly(self):
        """`_resolve_content_type` does not map .env (os.path.splitext
        treats it as an extension-less filename; fallback is in create_upload).
        """
        result = _resolve_content_type("application/octet-stream", ".env")
        # .env is treated as extension-less by splitext → not in overrides.
        # The text/plain fallback happens in create_upload(), not here.
        assert result != "text/plain"

    def test_tf_extension_resolves_to_text_plain(self):
        """.tf files resolve to text/plain."""
        assert _resolve_content_type("application/octet-stream", "main.tf") == "text/plain"

    def test_unknown_extension_not_overridden_to_text_plain(self):
        """`_resolve_content_type` no longer applies a text/plain fallback.
        The safety net is now in ``create_upload()``, not here.
        """
        result = _resolve_content_type("application/octet-stream", "notes.xyz")
        # The result depends on the system's mimetypes DB:
        # - Docker (Debian): None → returns application/octet-stream
        # - CI (Ubuntu): maps .xyz → chemical/x-xyz
        # Either way, it should NOT be text/plain (that's in create_upload).
        assert result != "text/plain"

    def test_no_extension_returns_original_type(self):
        """A filename without extension returns the original generic type."""
        result = _resolve_content_type("application/octet-stream", "README")
        assert result == "application/octet-stream"

    def test_empty_filename_returns_original_type(self):
        """An empty filename returns the original generic type."""
        result = _resolve_content_type("application/octet-stream", "")
        assert result == "application/octet-stream"

    def test_office_extensions_still_resolve_correctly(self):
        """Office extensions still resolve to their proper MIME types."""
        assert _resolve_content_type("application/octet-stream", "data.xlsx") == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert _resolve_content_type("application/octet-stream", "doc.doc") == "application/msword"
        assert _resolve_content_type("application/octet-stream", "slides.pptx") == (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )

    def test_image_extensions_resolve_correctly(self):
        """Image extensions resolve to their proper types."""
        assert _resolve_content_type("application/octet-stream", "photo.png") == "image/png"
        assert _resolve_content_type("application/octet-stream", "photo.jpg") == "image/jpeg"
        assert _resolve_content_type("application/octet-stream", "photo.svg") == "image/svg+xml"

    def test_svg_extension_resolves_to_image_svg_xml(self):
        """.svg files resolve to image/svg+xml."""
        assert _resolve_content_type("application/octet-stream", "icon.svg") == "image/svg+xml"

    def test_exe_with_specific_mime_not_overridden(self):
        """.exe files with a specific MIME type are NOT overridden."""
        result = _resolve_content_type("application/x-msdownload", "installer.exe")
        assert result == "application/x-msdownload"
