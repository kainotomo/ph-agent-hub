# =============================================================================
# PH Agent Hub — Browser Tool Factory
# =============================================================================
# Playwright headless Chromium in sandbox container. Screenshot pages,
# extract rendered text, extract tables. IP-restricted (no internal
# network access).
#
# Security model:
#   1. URL validation — only HTTP/HTTPS, block private/internal IPs
#   2. Playwright in a separate sandbox (runs in-process for simplicity;
#      for production, should run in a separate container)
#   3. 30s timeout, viewport constraints
#   4. Screenshots stored in MinIO/S3
# =============================================================================

import base64
import io
import logging
import uuid
from typing import Any
from urllib.parse import urlparse

from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: int = 30_000  # milliseconds
DEFAULT_VIEWPORT_WIDTH: int = 1280
DEFAULT_VIEWPORT_HEIGHT: int = 720
DEFAULT_PRESIGNED_TTL: int = 3600  # 1 hour


def _get_bucket_prefix() -> str:
    """Return the MinIO bucket prefix from settings."""
    from ..core.config import settings
    return settings.MINIO_BUCKET_PREFIX

ALLOWED_SCHEMES: frozenset = frozenset({"http", "https"})

# Blocked IP ranges / hostnames
BLOCKED_HOSTS: set[str] = {
    "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "host.docker.internal", "gateway.docker.internal",
}

BLOCKED_PREFIXES: list[str] = [
    "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
    "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
    "172.30.", "172.31.", "192.168.", "169.254.",
]


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


def _is_safe_url(url: str) -> bool:
    """Reject URLs pointing to internal/private hosts."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False
    hostname = (parsed.hostname or "").lower()
    if hostname in BLOCKED_HOSTS:
        return False
    for prefix in BLOCKED_PREFIXES:
        if hostname.startswith(prefix):
            return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_bucket(tenant_id: str) -> str:
    """Return the MinIO bucket name for a tenant."""
    return f"{_get_bucket_prefix()}-{tenant_id}"


async def _upload_and_get_url(
    bucket: str,
    key: str,
    data: bytes,
    content_type: str,
    expires_in: int = DEFAULT_PRESIGNED_TTL,
) -> str:
    """Upload data to MinIO/S3 and return a presigned download URL."""
    from ..storage.s3 import generate_presigned_url, upload_object
    await upload_object(bucket, key, data, content_type)
    return await generate_presigned_url(bucket, key, expires_in=expires_in)


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_browser_tools(
    tool_config: dict | None = None,
    tenant_id: str = "",
) -> list:
    """Return a list of MAF @tool-decorated async functions for browser
    automation.

    Args:
        tool_config: Optional ``Tool.config`` JSON dict.  May include:
            - ``timeout`` (int): page load timeout in milliseconds (default 30000)
            - ``viewport_width`` (int): browser viewport width (default 1280)
            - ``viewport_height`` (int): browser viewport height (default 720)
        tenant_id: The tenant ID for MinIO bucket resolution.

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}
    timeout_ms: int = int(config.get("timeout", DEFAULT_TIMEOUT))
    viewport_width: int = int(config.get("viewport_width", DEFAULT_VIEWPORT_WIDTH))
    viewport_height: int = int(config.get("viewport_height", DEFAULT_VIEWPORT_HEIGHT))
    max_content_length: int = int(config.get("max_length", 100_000))

    async def _get_browser_page(url: str):
        """Get a Playwright browser page, navigated to the given URL.

        Returns (page, browser) tuple or (None, error_dict).
        The caller is responsible for closing the browser.
        """
        if not _is_safe_url(url):
            logger.warning("Browser tool rejected unsafe URL: %s", url)
            return None, None, {
                "url": url,
                "error": "URL is not allowed (internal/private hosts are blocked)",
            }

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None, None, {
                "url": url,
                "error": (
                    "Browser automation is not available. The playwright library "
                    "is not installed. Please contact your administrator."
                ),
            }

        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ],
            )
        except Exception as exc:
            await pw.stop()
            return None, None, {
                "url": url,
                "error": f"Failed to launch browser: {str(exc)}",
            }

        try:
            context = await browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # Wait a bit for JS rendering
            await page.wait_for_timeout(2000)

            return page, browser, None
        except Exception as exc:
            await browser.close()
            await pw.stop()
            return None, None, {
                "url": url,
                "error": f"Failed to load page: {str(exc)}",
            }

    # ------------------------------------------------------------------
    @tool
    async def take_screenshot(url: str, selector: str | None = None) -> dict:
        """Take a screenshot of a web page or a specific element.

        Opens the URL in a headless browser, waits for the page to load,
        and captures a screenshot. The screenshot is stored and a download
        URL is returned.

        Args:
            url: The full URL to screenshot (must start with http:// or https://).
            selector: Optional CSS selector to screenshot only a specific element.

        Returns:
            A dict with:
            - ``url``: presigned download URL for the screenshot (PNG)
            - ``page_url``: the URL that was screenshot
            - ``size_bytes``: size of the screenshot
            - ``error``: error message if screenshot failed
        """
        if not url or not url.strip():
            return {"error": "No URL provided"}

        page, browser, error = await _get_browser_page(url)
        if error:
            return error

        try:
            if selector:
                element = await page.query_selector(selector)
                if not element:
                    await browser.close()
                    return {
                        "page_url": url,
                        "error": f"Element not found with selector: {selector}",
                    }
                screenshot_bytes = await element.screenshot(type="png")
            else:
                screenshot_bytes = await page.screenshot(type="png", full_page=True)

            await browser.close()
        except Exception as exc:
            await browser.close()
            logger.error("Screenshot failed for %s: %s", url, exc)
            return {"page_url": url, "error": f"Screenshot failed: {str(exc)}"}

        # Upload to MinIO/S3
        if not tenant_id:
            return {"page_url": url, "error": "Tenant ID not available for file storage"}

        try:
            bucket = _get_bucket(tenant_id)
            file_id = str(uuid.uuid4())
            key = f"generated/screenshots/{file_id}.png"
            download_url = await _upload_and_get_url(
                bucket, key, screenshot_bytes, "image/png"
            )

            logger.info("Screenshot saved: %s (%d bytes)", key, len(screenshot_bytes))
            return {
                "url": download_url,
                "page_url": url,
                "size_bytes": len(screenshot_bytes),
            }
        except Exception as exc:
            logger.error("Failed to upload screenshot: %s", exc)
            return {"page_url": url, "error": f"Failed to store screenshot: {str(exc)}"}

    # ------------------------------------------------------------------
    @tool
    async def extract_text(url: str) -> dict:
        """Extract rendered text content from a web page after JavaScript execution.

        Unlike fetch_url which only gets server-rendered HTML, this tool
        runs the page in a headless browser so JavaScript-rendered content
        is included.

        Args:
            url: The full URL to extract text from (must start with http:// or https://).

        Returns:
            A dict with:
            - ``url``: the URL that was fetched
            - ``title``: page title
            - ``text``: rendered text content of the page (truncated if too long)
            - ``text_length``: total character count before truncation
            - ``truncated``: True if text was truncated
            - ``error``: error message if extraction failed
        """
        if not url or not url.strip():
            return {"error": "No URL provided"}

        page, browser, error = await _get_browser_page(url)
        if error:
            return error

        try:
            title = await page.title()

            # Extract text from body
            text = await page.evaluate("""
                () => {
                    // Remove script, style, nav, footer elements
                    const elementsToRemove = document.querySelectorAll(
                        'script, style, nav, footer, header, noscript, iframe, svg'
                    );
                    elementsToRemove.forEach(el => el.remove());
                    return document.body ? document.body.innerText : '';
                }
            """)

            await browser.close()
        except Exception as exc:
            await browser.close()
            logger.error("Text extraction failed for %s: %s", url, exc)
            return {"url": url, "error": f"Text extraction failed: {str(exc)}"}

        # Clean up text
        text = text.strip()
        # Remove excessive blank lines
        import re
        text = re.sub(r'\n{3,}', '\n\n', text)

        text_length = len(text)
        truncated = text_length > max_content_length

        if truncated:
            text = text[:max_content_length] + f"\n\n... (truncated {text_length - max_content_length} more characters)"

        return {
            "url": url,
            "title": title,
            "text": text,
            "text_length": text_length,
            "truncated": truncated,
        }

    # ------------------------------------------------------------------
    @tool
    async def extract_table(url: str, table_index: int = 0) -> dict:
        """Extract HTML tables from a web page as structured data.

        Opens the URL in a headless browser, waits for JavaScript to
        render, then extracts tables. Returns the table at the given
        index as a list of dicts.

        Args:
            url: The full URL to extract tables from (must start with http:// or https://).
            table_index: Which table to extract (0 = first table, 1 = second, etc.).

        Returns:
            A dict with:
            - ``url``: the URL that was fetched
            - ``page_title``: page title
            - ``table_index``: which table was extracted
            - ``total_tables``: total number of tables found on the page
            - ``headers``: list of column headers
            - ``rows``: list of row dicts
            - ``error``: error message if extraction failed
        """
        if not url or not url.strip():
            return {"error": "No URL provided"}

        page, browser, error = await _get_browser_page(url)
        if error:
            return error

        try:
            page_title = await page.title()

            # Extract all tables as structured data
            tables_data = await page.evaluate("""
                () => {
                    const tables = document.querySelectorAll('table');
                    const result = [];
                    tables.forEach(table => {
                        const headers = [];
                        const rows = [];

                        // Get headers from thead or first row
                        const thead = table.querySelector('thead');
                        const headerRow = thead
                            ? thead.querySelector('tr')
                            : table.querySelector('tr');

                        if (headerRow) {
                            const cells = headerRow.querySelectorAll('th, td');
                            cells.forEach(cell => {
                                headers.push(cell.innerText.trim());
                            });
                        }

                        // Get data rows
                        const tbody = table.querySelector('tbody') || table;
                        const dataRows = tbody.querySelectorAll('tr');
                        dataRows.forEach((row, idx) => {
                            // Skip header row if we already got headers from thead
                            if (idx === 0 && headers.length > 0 && !table.querySelector('thead')) {
                                return;
                            }
                            const cells = row.querySelectorAll('td, th');
                            const rowData = [];
                            cells.forEach(cell => {
                                rowData.push(cell.innerText.trim());
                            });
                            if (rowData.length > 0) {
                                rows.push(rowData);
                            }
                        });

                        result.push({ headers, rows });
                    });
                    return result;
                }
            """)

            await browser.close()
        except Exception as exc:
            await browser.close()
            logger.error("Table extraction failed for %s: %s", url, exc)
            return {"url": url, "error": f"Table extraction failed: {str(exc)}"}

        total_tables = len(tables_data)

        if total_tables == 0:
            return {
                "url": url,
                "page_title": page_title,
                "table_index": table_index,
                "total_tables": 0,
                "headers": [],
                "rows": [],
                "error": "No tables found on this page",
            }

        if table_index >= total_tables:
            return {
                "url": url,
                "page_title": page_title,
                "table_index": table_index,
                "total_tables": total_tables,
                "headers": [],
                "rows": [],
                "error": f"Table index {table_index} out of range. There are {total_tables} tables on this page (indices 0-{total_tables - 1}).",
            }

        table = tables_data[table_index]
        headers = table.get("headers", [])
        raw_rows = table.get("rows", [])

        # Convert to list of dicts
        rows = []
        for raw_row in raw_rows:
            if headers:
                row_dict = {}
                for i, header in enumerate(headers):
                    val = raw_row[i] if i < len(raw_row) else ""
                    row_dict[header] = val
                rows.append(row_dict)
            else:
                # No headers — use column indices
                rows.append({str(i): val for i, val in enumerate(raw_row)})

        return {
            "url": url,
            "page_title": page_title,
            "table_index": table_index,
            "total_tables": total_tables,
            "headers": headers,
            "rows": rows,
        }

    return [take_screenshot, extract_text, extract_table]
