# =============================================================================
# PH Agent Hub — SEC Filings Tool Factory (sec.gov EDGAR)
# =============================================================================
# Builds MAF @tool-decorated async functions for listing and reading SEC
# EDGAR filings.  Uses the edgartools library for reliable SEC access with
# proper rate limiting, identity, and complete filing history since 1994.
# =============================================================================

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# edgartools initialisation
# ---------------------------------------------------------------------------

try:
    from edgar import Company, set_identity

    set_identity("phalouvas@gmail.com")
    _EDGAR_AVAILABLE = True
except ImportError:
    _EDGAR_AVAILABLE = False
    logger.warning("edgartools not installed — SEC tools will be unavailable")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SEC_BASE: str = "https://www.sec.gov"
DEFAULT_LIMIT: int = 10
DEFAULT_MAX_CHARS: int = 500_000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_acc_num_and_cik(url: str) -> tuple[str, str]:
    """Extract ``(accession_number, cik)`` from an SEC URL.

    Handles multiple EDGAR URL patterns.  Returns ``("", "")`` on failure.
    """
    acc = ""
    cik = ""

    # Pattern A: ?accession_number=0001564590-22-026876  (viewer query)
    m = re.search(r"accession_number=([^&]+)", url)
    if m:
        acc = m.group(1)

    # Pattern B: ix?doc=/Archives/edgar/data/CIK/ACCNODASH/FILENAME
    m = re.search(r"ix\?doc=/Archives/edgar/data/(\d+)/(\d+)/", url)
    if m:
        acc_no_dash = m.group(2)
        # Convert 000095017025100235 → 0000950170-25-100235
        if len(acc_no_dash) >= 16 and not acc:
            acc = f"{acc_no_dash[:10]}-{acc_no_dash[10:12]}-{acc_no_dash[12:]}"

    # Pattern C: …/data/CIK/ACCNODASHES/ACC-ESSION-NUMBER-index.htm[l]  (browse, two-segment)
    if not acc:
        m = re.search(
            r"/data/\d+/([^/]+)/(\d{10}-\d{2}-\d{6})-index\.html?", url
        )
        if m:
            acc = m.group(2)

    # Pattern C2: …/data/CIK/ACC-ESSION-NUMBER-index.html  (flat, no subfolder)
    if not acc:
        m = re.search(
            r"/data/\d+/(\d{10}-\d{2}-\d{6})-index\.html?", url
        )
        if m:
            acc = m.group(1)

    # Pattern D: …/data/CIK/ACCNODASH/FILENAME.htm  (direct document)
    if not acc:
        m = re.search(r"/data/(\d+)/(\d{10,})/[^/]+\.html?", url)
        if m:
            acc_no_dash = m.group(2)
            if len(acc_no_dash) >= 16:
                acc = (
                    f"{acc_no_dash[:10]}-{acc_no_dash[10:12]}-"
                    f"{acc_no_dash[12:]}"
                )

    # CIK from any path variant  …/data/CIK/…
    m = re.search(r"/data/(\d+)/", url)
    if m:
        cik = m.group(1).lstrip("0")

    return acc, cik


async def _fetch_url_direct(
    url: str, headers: dict, timeout: float
) -> tuple[str, str]:
    """Fallback: fetch and clean a filing via direct HTTP when edgartools
    cannot resolve the URL."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers, follow_redirects=True)
        resp.raise_for_status()
    html = resp.text

    # Resolve indirect pages

    # Case: inline XBRL viewer (ix?doc=…) — extract real document path
    if "ix?doc=" in url:
        m = re.search(r"ix\?doc=(/Archives/edgar/data/\d+/\d+/\S+)", url)
        if m:
            doc_path = m.group(1).rstrip("&")
            real_url = f"{SEC_BASE}{doc_path}"
            async with httpx.AsyncClient(timeout=timeout) as c2:
                r2 = await c2.get(
                    real_url, headers=headers, follow_redirects=True
                )
                r2.raise_for_status()
                html = r2.text
                url = real_url

    if "/cgi-bin/viewer" in url:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        iframe = soup.find("iframe")
        if iframe and iframe.get("src"):
            doc_url = iframe["src"]
            if doc_url.startswith("/"):
                doc_url = f"{SEC_BASE}{doc_url}"
            async with httpx.AsyncClient(timeout=timeout) as c2:
                r2 = await c2.get(doc_url, headers=headers, follow_redirects=True)
                r2.raise_for_status()
                html = r2.text
                url = doc_url
    elif "-index.htm" in url or "-index.html" in url:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        doc_link: str | None = None
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                for cell in row.find_all("td"):
                    link = cell.find("a")
                    if not link:
                        continue
                    href = (link.get("href") or "").strip()
                    if not href:
                        continue
                    lo = href.lower()
                    if lo.endswith(("_cal.xml", "_def.xml", "_lab.xml",
                                    "_pre.xml", ".xsd", ".txt", ".xml")):
                        continue
                    if lo.endswith((".htm", ".html")):
                        if href.startswith("/"):
                            doc_link = f"{SEC_BASE}{href}"
                        elif href.startswith("http"):
                            doc_link = href
                        else:
                            doc_link = f'{url.rsplit("/", 1)[0]}/{href}'
                        break
                if doc_link:
                    break
            if doc_link:
                break
        if doc_link:
            async with httpx.AsyncClient(timeout=timeout) as c2:
                r2 = await c2.get(doc_link, headers=headers, follow_redirects=True)
                r2.raise_for_status()
                html = r2.text
                url = doc_link

    # Clean HTML
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "meta", "link"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        text = "\n".join(l for l in lines if len(l) > 2 or l.isdigit())
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()

    return text, url


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_sec_filings_tools(tool_config: dict | None = None) -> list:
    """Return a list of MAF @tool-decorated SEC filing functions.

    Requires the ``edgartools`` Python package (``pip install edgartools``).

    Provides:
    - ``list_sec_filings``: list SEC filings for a company (full history)
    - ``get_filing_text``: extract readable text from a filing URL
    - ``get_filing_section``: extract a named section from a filing

    Args:
        tool_config: Optional ``Tool.config`` JSON dict. May include:
            - ``user_agent`` (str): custom User-Agent for direct HTTP fallback
            - ``timeout`` (float): request timeout (default 30)
            - ``default_limit`` (int): default max filings to list (default 10)
            - ``max_chars`` (int): max chars for ``get_filing_text``
              (default 500 000; set to 0 for no limit)

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    if not _EDGAR_AVAILABLE:
        # Return stubs that report the missing dependency
        @tool
        async def _missing(*args, **kwargs) -> dict:
            return {"error": "edgartools library is not installed"}

        return [_missing, _missing, _missing]

    config = tool_config or {}
    user_agent: str = config.get(
        "user_agent", "ph-agent-hub/1.0 (phalouvas@gmail.com)"
    )
    timeout: float = float(config.get("timeout", 30))
    default_limit: int = int(config.get("default_limit", DEFAULT_LIMIT))
    max_chars_cfg: int = int(config.get("max_chars", DEFAULT_MAX_CHARS))

    # ---------------------------------------------------------------
    # list_sec_filings
    # ---------------------------------------------------------------
    @tool
    async def list_sec_filings(
        ticker: str,
        form_types: list[str] | None = None,
        limit: int | None = None,
        filing_date_from: str | None = None,
    ) -> dict:
        """List SEC EDGAR filings for a US-listed company.

        Uses the edgartools library — full filing history since 1994,
        proper rate limiting, no API key required.

        Args:
            ticker: Stock ticker symbol (e.g. "AAPL", "MSFT").
            form_types: Optional list of form types to filter by.
                Common types: "10-K", "10-Q", "8-K", "S-1",
                "DEF 14A".  Default searches 10-K, 10-Q, 8-K, S-1.
            limit: Max filings to return (default 10, max 50).
            filing_date_from: Earliest filing date in YYYY-MM-DD format
                (e.g. "2024-01-01").  Filters to filings on or after
                this date.

        Returns:
            A dict with:
            - ``ticker``, ``cik``
            - ``filings``: list of dicts with ``filing_type``,
              ``description``, ``filing_date``, ``accession_number``,
              ``detail_url``, ``document_url``
            - ``count``: number of filings returned
            - ``source``: "SEC EDGAR (edgartools)"
        """
        lim = min(limit if limit is not None else default_limit, 50)
        sym = ticker.upper().strip()
        logger.info("list_sec_filings: %s (limit=%d)", sym, lim)

        try:
            company = await asyncio.to_thread(Company, sym)

            forms = form_types if form_types else ["10-K", "10-Q", "8-K", "S-1"]

            # Gather filings for each requested form type
            all_filings: list = []
            for form in forms:
                try:
                    batch = await asyncio.to_thread(
                        company.get_filings, form=form
                    )
                    # edgartools Filings objects are iterable
                    all_filings.extend(list(batch))
                except Exception as exc:
                    logger.debug("No %s filings for %s: %s", form, sym, exc)

            # Apply date filter client-side
            if filing_date_from:
                try:
                    cutoff = datetime.strptime(
                        filing_date_from, "%Y-%m-%d"
                    ).date()
                except ValueError:
                    return {
                        "ticker": sym,
                        "error": (
                            "filing_date_from must be YYYY-MM-DD, "
                            f"got '{filing_date_from}'"
                        ),
                    }
                all_filings = [
                    f for f in all_filings
                    if getattr(f, "filing_date", None) and f.filing_date >= cutoff
                ]

            # Sort by date descending
            all_filings.sort(
                key=lambda f: getattr(f, "filing_date", datetime.min),
                reverse=True,
            )
            all_filings = all_filings[:lim]

            # Serialise to JSON-safe dicts
            results: list[dict] = []
            for f in all_filings:
                acc = getattr(f, "accession_number", "") or ""
                acc_f = acc.replace("-", "")
                cik = str(getattr(company, "cik", "")).lstrip("0")
                primary = getattr(f, "primary_document", "") or ""
                results.append({
                    "filing_type": getattr(f, "form", ""),
                    "description": primary,
                    "filing_date": str(getattr(f, "filing_date", "")),
                    "accession_number": acc,
                    "detail_url": (
                        getattr(f, "homepage_url", "")
                        or getattr(f, "url", "")
                    ),
                    "document_url": (
                        f"{SEC_BASE}/Archives/edgar/data/"
                        f"{cik}/{acc_f}/{primary}"
                        if acc_f and cik and primary
                        else ""
                    ),
                })

            return {
                "ticker": sym,
                "cik": getattr(company, "cik", ""),
                "filings": results,
                "count": len(results),
                "source": "SEC EDGAR (edgartools)",
            }

        except Exception as exc:
            logger.exception("list_sec_filings failed for %s", sym)
            return {"ticker": sym, "error": str(exc)}

    # ---------------------------------------------------------------
    # get_filing_text
    # ---------------------------------------------------------------
    @tool
    async def get_filing_text(
        url: str, max_chars: int | None = None
    ) -> dict:
        """Extract readable text from an SEC filing.

        For the full text of large filings (10-K, 10-Q) consider using
        ``get_filing_section`` instead to retrieve only the sections
        you need.

        Args:
            url: The URL of the SEC filing page or document.
            max_chars: Override the configured character limit.
                Set to 0 for no limit.

        Returns:
            A dict with:
            - ``url``: the URL fetched
            - ``text``: cleaned plain text of the filing
            - ``text_length``: character count
            - ``truncated``: whether the text was truncated
            - ``source``: "SEC EDGAR"
        """
        logger.info("get_filing_text: %s", url)
        limit = max_chars_cfg if max_chars is None else max_chars

        if not (
            url.startswith("https://www.sec.gov/")
            or url.startswith("http://www.sec.gov/")
        ):
            return {
                "url": url,
                "error": "Only sec.gov URLs are supported.",
            }

        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html, application/xhtml+xml, */*",
        }

        # Try edgartools first (resolves by accession number)
        acc, cik = _parse_acc_num_and_cik(url)

        if acc and cik:
            EDGAR_TIMEOUT = 30  # seconds per individual edgartools operation
            try:
                company = await asyncio.wait_for(
                    asyncio.to_thread(Company, cik),
                    timeout=EDGAR_TIMEOUT,
                )
                # Search filings for matching accession number
                filings = await asyncio.wait_for(
                    asyncio.to_thread(company.get_filings, accession_number=acc),
                    timeout=EDGAR_TIMEOUT,
                )
                filing_list = list(filings) if filings else []
                if filing_list:
                    filing = filing_list[0]
                    text = await asyncio.wait_for(
                        asyncio.to_thread(filing.text),
                        timeout=EDGAR_TIMEOUT,
                    )
                    truncated = limit > 0 and len(text) > limit
                    if truncated:
                        text = text[:limit]
                    return {
                        "url": url,
                        "text": text,
                        "text_length": len(text),
                        "truncated": truncated,
                        "source": "SEC EDGAR (edgartools)",
                    }
            except asyncio.TimeoutError:
                logger.warning("get_filing_text timed out for %s", url)
                # Fall through to HTTP fallback
            except Exception as exc:
                logger.debug(
                    "edgartools lookup failed for %s: %s — falling back to HTTP",
                    url, exc,
                )

        # Fallback: direct HTTP fetch + clean
        try:
            text, resolved_url = await _fetch_url_direct(
                url, headers, timeout
            )
            truncated = limit > 0 and len(text) > limit
            if truncated:
                text = text[:limit]
            return {
                "url": resolved_url,
                "text": text,
                "text_length": len(text),
                "truncated": truncated,
                "source": "SEC EDGAR",
            }
        except httpx.HTTPStatusError as exc:
            return {
                "url": url,
                "error": f"SEC EDGAR returned HTTP {exc.response.status_code}",
            }
        except httpx.TimeoutException:
            return {"url": url, "error": "Request timed out"}
        except Exception as exc:
            logger.exception("get_filing_text failed for %s", url)
            return {"url": url, "error": str(exc)}

    # ---------------------------------------------------------------
    # get_filing_section
    # ---------------------------------------------------------------
    @tool
    async def get_filing_section(url: str, section: str) -> dict:
        """Extract a specific named section from an SEC filing.

        Uses edgartools' built-in section extraction which recognises
        standard SEC headings (Risk Factors, MD&A, etc.).

        Args:
            url: The URL of the SEC filing page.
            section: Name of the section to extract.  Case-insensitive
                partial match (e.g. ``"Risk Factors"``, ``"MD&A"``,
                ``"Item 2"``).  If not found, ``available_sections``
                is returned so you can retry.

        Returns:
            A dict with:
            - ``url``, ``section``, ``text``, ``text_length``
            - ``available_sections``: list of all detected section names
            - ``source``: "SEC EDGAR"
        """
        logger.info("get_filing_section: %s [section=%s]", url, section)

        acc, cik = _parse_acc_num_and_cik(url)

        if not (acc and cik):
            return {
                "url": url,
                "error": (
                    "Could not parse accession number / CIK from URL. "
                    "Only sec.gov EDGAR URLs are supported."
                ),
            }

        EDGAR_TIMEOUT = 30  # seconds per individual edgartools operation

        try:
            company = await asyncio.wait_for(
                asyncio.to_thread(Company, cik),
                timeout=EDGAR_TIMEOUT,
            )
            filings = await asyncio.wait_for(
                asyncio.to_thread(company.get_filings, accession_number=acc),
                timeout=EDGAR_TIMEOUT,
            )
            filing_list = list(filings) if filings else []
            if not filing_list:
                return {
                    "url": url,
                    "error": f"No filing found for accession {acc}",
                }

            filing = filing_list[0]

            # Use edgartools' built-in section extraction (handles
            # TOC vs real content deduplication, filing agent
            # detection, and multi-strategy parsing).  Try a direct
            # lookup via __getitem__ first — edgartools resolves
            # Part-qualified names correctly (e.g. "Item 2" on a
            # 10-Q returns Part I MD&A, not Part II Item 2).
            report = await asyncio.wait_for(
                asyncio.to_thread(filing.obj),
                timeout=EDGAR_TIMEOUT,
            )

            section_text = ""
            matched: str = ""
            available: list[str] = []

            if report is not None:
                available = await asyncio.wait_for(
                    asyncio.to_thread(lambda: report.items),
                    timeout=EDGAR_TIMEOUT,
                )

                # Try edgartools' built-in lookup with the raw user
                # section string — it handles Part qualification
                try:
                    direct = await asyncio.wait_for(
                        asyncio.to_thread(report.__getitem__, section.strip()),
                        timeout=EDGAR_TIMEOUT,
                    )
                    if direct:
                        matched = section.strip()
                        section_text = direct
                except (Exception, asyncio.TimeoutError):
                    direct = None

                # If direct lookup failed, try case-insensitive match
                # across the available items list, preferring Part I
                # matches on 10-Q filings
                if not section_text:
                    target = section.strip().lower()
                    # Separate Part I and Part II items
                    part_i_items = [k for k in available if k.lower().startswith("part i")]
                    part_ii_items = [k for k in available if k.lower().startswith("part ii")]
                    other_items = [k for k in available if k not in part_i_items and k not in part_ii_items]

                    # Search order: Part I items first (for 10-Q MD&A),
                    # then other items, then Part II
                    matched = None
                    for key in part_i_items + other_items + part_ii_items:
                        if target in key.lower():
                            matched = key
                            break

                    if matched:
                        extracted = await asyncio.wait_for(
                            asyncio.to_thread(report.__getitem__, matched),
                            timeout=EDGAR_TIMEOUT,
                        )
                        section_text = extracted or ""
            else:
                # Fallback: regex-based splitter for unrecognised filing types
                full_text = await asyncio.wait_for(
                    asyncio.to_thread(filing.text),
                    timeout=EDGAR_TIMEOUT,
                )
                sections = _split_filing_sections(full_text)
                available = list(sections.keys())
                target = section.strip().lower()
                matched = None
                for key in available:
                    if target in key.lower():
                        matched = key
                        break
                if matched:
                    section_text = sections.get(matched, "")

            if not section_text:
                return {
                    "url": url,
                    "section": section,
                    "error": (
                        f"Section '{section}' not found. "
                        f"Available sections: {available}"
                    ),
                    "available_sections": available,
                    "source": "SEC EDGAR",
                }

            return {
                "url": url,
                "section": matched,
                "text": section_text,
                "text_length": len(section_text),
                "available_sections": available,
                "source": "SEC EDGAR (edgartools)",
            }

        except asyncio.TimeoutError:
            logger.warning("get_filing_section timed out for %s [section=%s]", url, section)
            return {"url": url, "error": f"Operation timed out after {EDGAR_TIMEOUT}s"}
        except Exception as exc:
            logger.exception("get_filing_section failed for %s", url)
            return {"url": url, "error": str(exc)}

    return [list_sec_filings, get_filing_text, get_filing_section]


# ---------------------------------------------------------------------------
# Fallback section splitter (used when edgartools has no sections)
# ---------------------------------------------------------------------------
def _split_filing_sections(text: str) -> dict[str, str]:
    """Split cleaned filing text into named sections based on SEC headings."""
    heading = re.compile(
        r"^\s*(PART\s+(?:I|II|III|IV|V)[\s.\-]*.*|"
        r"Item\s+\d+[A-Z]?[\s.\-]*.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = list(heading.finditer(text))
    if not matches:
        return {"_full": text}

    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        name = m.group(1).strip().rstrip(".-")
        body = text[start:end].strip()
        key = name
        # SEC filings often have a Table of Contents that lists every
        # heading followed by the next heading name (tiny body), then
        # the actual content later.  When a duplicate heading appears,
        # keep the version with the larger body (the real section).
        if key in sections:
            if len(body) > len(sections[key]):
                sections[key] = body
            # Otherwise discard this shorter-body duplicate
        else:
            sections[key] = body
    return sections
