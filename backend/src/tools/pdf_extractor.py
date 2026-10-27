# =============================================================================
# PH Agent Hub — PDF Text Extraction Tool Factory
# =============================================================================
# Builds a MAF @tool-decorated async function that downloads a PDF from a
# URL and extracts its readable text content using pdfplumber.
# No API key required.
# =============================================================================

import io
import logging
from urllib.parse import urlparse

import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: float = 60.0
DEFAULT_MAX_CHARS: int = 500_000
ALLOWED_SCHEMES: frozenset = frozenset({"http", "https"})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_safe_url(url: str) -> bool:
    """Reject URLs pointing to internal / private hosts."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False
    hostname = (parsed.hostname or "").lower()
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
    if hostname.startswith("169.254.") or hostname.startswith("10."):
        return False
    if hostname.startswith("172."):
        try:
            second = int(hostname.split(".")[1])
            if 16 <= second <= 31:
                return False
        except (IndexError, ValueError):
            pass
    if hostname.startswith("192.168."):
        return False
    return True


def _parse_page_selection(spec: str) -> tuple[set[int] | None, str | None]:
    """Parse a 1-based, inclusive page selection.

    Accepts ``"12"``, ``"150-160"`` or ``"12,14,150-160"``.

    Returns:
        ``(pages, error)`` — exactly one of the two is None.
    """
    pages: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_s, _, end_s = chunk.partition("-")
            try:
                start, end = int(start_s), int(end_s)
            except ValueError:
                return None, f"Invalid page range '{chunk}'."
            if start < 1 or end < start:
                return None, f"Invalid page range '{chunk}'."
            if end - start >= 500:
                return None, f"Page range '{chunk}' is too large (max 500 pages)."
            pages.update(range(start, end + 1))
        else:
            try:
                number = int(chunk)
            except ValueError:
                return None, f"Invalid page '{chunk}'."
            if number < 1:
                return None, f"Invalid page '{chunk}'."
            pages.add(number)
    if not pages:
        return None, "No pages selected."
    return pages, None


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_pdf_extractor_tools(tool_config: dict | None = None) -> list:
    """Return a list containing the MAF @tool-decorated extract_pdf function.

    Args:
        tool_config: Optional ``Tool.config`` JSON dict.  May include:
            - ``timeout`` (float): request timeout in seconds (default 60)
            - ``max_chars`` (int): max characters to return (default 500k)

    Returns:
        A list with a single callable ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}

    timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT))
    max_chars: int = int(config.get("max_chars", DEFAULT_MAX_CHARS))

    @tool
    async def extract_pdf(url: str, pages: str | None = None) -> dict:
        """Download a PDF from a URL and extract its readable text content.

        Uses pdfplumber to extract text from the pages of the PDF.
        Useful for reading financial reports, regulatory filings,
        earnings press releases, and other PDF-format documents.

        Args:
            url: The URL of the PDF file (must start with http:// or
                https://).
            pages: Optional 1-based page selection — "12", "150-160" or
                "12,14,150-160". Omit to extract every page. On a long
                filing the full text is hundreds of KB and gets clipped, so
                select the pages you need (a statement of financial position
                usually sits around page 150-200 of an annual report).

        Returns:
            A dict with:
            - ``url``: the PDF URL fetched
            - ``status_code``: HTTP status code of the download
            - ``content_type``: the Content-Type header
            - ``pages``: number of pages extracted
            - ``text``: extracted text content (truncated to the
              configured max_chars limit)
            - ``text_length``: character count of the text (before
              truncation if any)
            - ``truncated``: true if text was truncated to max_chars
            - ``source``: "pdfplumber"

            If the URL does not point to a PDF (Content-Type mismatch)
            or the file cannot be parsed, an error key is returned
            with a description.
        """
        if not _is_safe_url(url):
            return {
                "url": url,
                "error": "URL blocked — internal/private hosts are not allowed.",
            }

        selected: set[int] | None = None
        if pages:
            selected, page_error = _parse_page_selection(pages)
            if page_error:
                return {"url": url, "error": page_error}

        logger.info("extract_pdf: %s", url)

        try:
            import pdfplumber

            # Download PDF
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "ph-agent-hub/1.0 (pdf-extractor; "
                        "+https://github.com/kainotomo/ph-agent-hub)"
                    ),
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            content_type = resp.headers.get("content-type", "").lower()
            raw_bytes = resp.content

            # Quick check: does this look like a PDF?
            if not raw_bytes:
                return {
                    "url": url,
                    "status_code": resp.status_code,
                    "content_type": content_type,
                    "error": "Empty response body — no content to extract.",
                }

            # pdfplumber can still parse even if content-type is wrong
            # (some servers misreport), but we warn
            is_pdf_mime = "pdf" in content_type or "octet-stream" in content_type
            if not is_pdf_mime and not raw_bytes[:5] == b"%PDF-":
                return {
                    "url": url,
                    "status_code": resp.status_code,
                    "content_type": content_type,
                    "error": (
                        f"URL does not appear to be a PDF. "
                        f"Content-Type is '{content_type}' and file does not "
                        f"start with PDF header. Use fetch_url for HTML pages."
                    ),
                }

            # Extract text from the requested pages
            extracted: list[tuple[int, str]] = []
            page_count = 0

            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    page_count += 1
                    if selected is not None and index not in selected:
                        continue
                    page_text = page.extract_text()
                    if page_text:
                        extracted.append((index, page_text))

            if len(extracted) > 1:
                full_text = "\n\n".join(
                    f"--- Page {number} ---\n\n{text}"
                    for number, text in extracted
                )
            elif extracted:
                full_text = extracted[0][1]
            else:
                full_text = ""

            # If pdfplumber extracted nothing, try pages one-by-one
            if not full_text.strip() and page_count > 0:
                retry_parts = []
                with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                    for i, page in enumerate(pdf.pages, start=1):
                        if selected is not None and i not in selected:
                            continue
                        pt = page.extract_text() or ""
                        if pt.strip():
                            retry_parts.append(f"[Page {i}]\n{pt}")
                full_text = "\n\n".join(retry_parts) if retry_parts else ""

            text_length = len(full_text)
            truncated = text_length > max_chars
            if truncated:
                half = max_chars // 2
                full_text = (
                    full_text[:half]
                    + f"\n\n[... text truncated at {max_chars} characters "
                      f"out of {text_length} total — showing beginning "
                      f"and end ...]\n\n"
                    + full_text[-half:]
                )

            result = {
                "url": url,
                "status_code": resp.status_code,
                "content_type": content_type,
                "pages": page_count,
                "text": full_text,
                "text_length": text_length,
                "truncated": truncated,
                "source": "pdfplumber",
            }
            if selected is not None:
                result["pages_requested"] = pages
                result["pages_extracted"] = len(extracted)
            return result

        except httpx.HTTPStatusError as exc:
            logger.warning("extract_pdf HTTP error for %s: %s", url, exc)
            return {
                "url": url,
                "error": f"HTTP {exc.response.status_code} — download failed.",
            }
        except Exception as exc:
            logger.exception("extract_pdf failed for %s", url)
            return {"url": url, "error": str(exc)}

    return [extract_pdf]
