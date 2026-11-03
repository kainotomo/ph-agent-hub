# =============================================================================
# PH Agent Hub — Fetch URL Tool Factory
# =============================================================================
# Builds a MAF @tool-decorated async function that fetches a web page and
# returns its readable text content.  Uses httpx for the HTTP request and
# html2text to convert HTML into plain text.
# =============================================================================

import logging
import re
from urllib.parse import urlparse

import html2text
import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: float = 30.0
DEFAULT_MAX_CONTENT_LENGTH: int = 500_000
DEFAULT_USER_AGENT: str = (
    "ph-agent-hub/1.0 (fetch-url tool; +https://github.com/kainotomo/ph-agent-hub)"
)
DEFAULT_CONTACT_EMAIL: str = "phalouvas@gmail.com"
# SEC fair-access policy: the User-Agent must declare a contact address and
# must not contain a URL (SEC answers "403 Undeclared Automated Tool").
DEFAULT_SEC_USER_AGENT: str = (
    f"ph-agent-hub/1.0 (fetch-url tool; {DEFAULT_CONTACT_EMAIL})"
)
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
    # Block loopback, link-local, and private ranges
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


def _is_sec_host(url: str) -> bool:
    """Return True for sec.gov and its subdomains."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "sec.gov" or host.endswith(".sec.gov")


def _resolve_user_agent(url: str, user_agent: str, sec_user_agent: str) -> str:
    """Return the User-Agent to send for *url*.

    Precedence for sec.gov hosts: ``sec_user_agent`` then ``user_agent``
    then ``DEFAULT_SEC_USER_AGENT``.  Other hosts: ``user_agent`` then
    ``DEFAULT_USER_AGENT``.
    """
    if _is_sec_host(url):
        return sec_user_agent or user_agent or DEFAULT_SEC_USER_AGENT
    return user_agent or DEFAULT_USER_AGENT


_SEC_USER_AGENT_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_SEC_USER_AGENT_URL = re.compile(r"https?://|www\.", re.IGNORECASE)


def _warn_if_sec_user_agent_noncompliant(user_agent: str) -> None:
    """Log a warning when a sec.gov request risks SEC's fair-access 403."""
    if _SEC_USER_AGENT_EMAIL.search(user_agent) and not _SEC_USER_AGENT_URL.search(
        user_agent
    ):
        return
    logger.warning(
        "fetch_url: sec.gov requires a contact email and rejects User-Agents "
        "containing a URL; %r may be rejected with 403",
        user_agent,
    )


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_fetch_url_tools(tool_config: dict | None = None) -> list:
    """Return a list containing the MAF @tool-decorated fetch_url function.

    Args:
        tool_config: Optional ``Tool.config`` JSON dict.  May include:
            - ``user_agent`` (str): User-Agent header for every host
              (default: the project UA)
            - ``sec_user_agent`` (str): User-Agent for ``*.sec.gov`` hosts.
              Defaults to a SEC-compliant UA with a contact email.  SEC's
              fair-access policy rejects User-Agents that contain a URL,
              so keep this in ``Product/Version (contact@example.com)`` form.
            - ``timeout`` (float): request timeout in seconds (default 30)
            - ``max_content_length`` (int): max chars to return (default 500k)

    Returns:
        A list with a single callable ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}

    user_agent: str = str(config.get("user_agent") or "").strip()
    sec_user_agent: str = str(config.get("sec_user_agent") or "").strip()
    timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT))
    max_len: int = int(config.get("max_content_length", DEFAULT_MAX_CONTENT_LENGTH))

    @tool
    async def fetch_url(url: str) -> dict:
        """Fetch a web page and return its readable text content.

        SEC EDGAR (sec.gov) requests declare a contact User-Agent to satisfy
        SEC's fair-access policy.  For EDGAR filings, the ``sec_filings``
        tools parse the documents and are usually a better choice.

        Args:
            url: The full URL to fetch (must start with http:// or https://).

        Returns:
            A dict with:
            - ``url``: the URL fetched
            - ``status_code``: HTTP status code
            - ``title``: page title extracted from HTML
            - ``text``: plain-text content of the page (truncated if too long)
            - ``content_type``: Content-Type header value
            - ``truncated``: True if text was truncated
        """
        if not _is_safe_url(url):
            logger.warning("fetch_url rejected unsafe URL: %s", url)
            return {
                "url": url,
                "error": "URL is not allowed (internal/private hosts are blocked)",
            }

        logger.info("fetch_url: %s", url)

        request_user_agent = _resolve_user_agent(url, user_agent, sec_user_agent)
        if _is_sec_host(url):
            _warn_if_sec_user_agent_noncompliant(request_user_agent)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": request_user_agent},
                    follow_redirects=True,
                )
        except httpx.TimeoutException:
            logger.warning("fetch_url timeout for %s", url)
            return {"url": url, "error": "Request timed out"}
        except httpx.RequestError as exc:
            logger.warning("fetch_url request error for %s: %s", url, exc)
            return {"url": url, "error": f"Request failed: {exc}"}

        content_type = response.headers.get("content-type", "")

        # Only convert HTML content
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            raw_text = response.text
            truncated = len(raw_text) > max_len
            if truncated:
                half = max_len // 2
                text = (
                    raw_text[:half]
                    + f"\n\n[... text truncated at {max_len} characters "
                      f"out of {len(raw_text)} total — showing beginning "
                      f"and end ...]\n\n"
                    + raw_text[-half:]
                )
            else:
                text = raw_text
            return {
                "url": str(response.url),
                "status_code": response.status_code,
                "content_type": content_type,
                "title": "",
                "text": text,
                "truncated": truncated,
            }

        # Convert HTML to plain text
        converter = html2text.HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True
        converter.body_width = 0  # don't wrap
        converter.skip_internal_links = True
        converter.single_line_break = True

        try:
            markdown_text = converter.handle(response.text)
        except Exception:
            logger.exception("html2text conversion failed for %s", url)
            return {"url": url, "error": "Failed to parse HTML content"}

        # Extract title
        title = ""
        try:
            from html.parser import HTMLParser

            class TitleParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.in_title = False
                    self.title = ""

                def handle_starttag(self, tag, attrs):
                    if tag == "title":
                        self.in_title = True

                def handle_data(self, data):
                    if self.in_title:
                        self.title += data

                def handle_endtag(self, tag):
                    if tag == "title":
                        self.in_title = False

            tp = TitleParser()
            tp.feed(response.text)
            title = tp.title.strip()[:500]
        except Exception:
            pass

        text = markdown_text.strip()
        truncated = len(text) > max_len
        if truncated:
            half = max_len // 2
            text = (
                text[:half]
                + f"\n\n[... text truncated at {max_len} characters "
                  f"out of {len(text)} total — showing beginning "
                  f"and end ...]\n\n"
                + text[-half:]
            )

        logger.debug(
            "fetch_url %s → %d chars (truncated=%s)", url, len(text), truncated
        )
        return {
            "url": str(response.url),
            "status_code": response.status_code,
            "content_type": content_type,
            "title": title,
            "text": text,
            "truncated": truncated,
        }

    return [fetch_url]
