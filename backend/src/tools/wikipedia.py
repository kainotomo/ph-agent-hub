# =============================================================================
# PH Agent Hub — Wikipedia Tool Factory
# =============================================================================
# Builds MAF @tool-decorated async functions for the free Wikipedia REST API.
# No API key required.  Uses httpx for async HTTP requests.
# =============================================================================

import logging
from urllib.parse import quote

import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WIKIPEDIA_REST_BASE: str = "https://{lang}.wikipedia.org/api/rest_v1"
DEFAULT_LANG: str = "en"
DEFAULT_TIMEOUT: float = 15.0
DEFAULT_MAX_RESULTS: int = 5
DEFAULT_MAX_EXTRACT_CHARS: int = 10_000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rest_base(lang: str) -> str:
    return WIKIPEDIA_REST_BASE.format(lang=lang)


async def _rest_get(client: httpx.AsyncClient, lang: str, path: str) -> dict:
    """Perform a GET to the Wikipedia REST API and return JSON."""
    url = f"{_rest_base(lang)}{path}"
    response = await client.get(
        url,
        headers={
            "User-Agent": "ph-agent-hub/1.0 (Wikipedia tool; "
            "+https://github.com/kainotomo/ph-agent-hub)",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_wikipedia_tools(tool_config: dict | None = None) -> list:
    """Return a list of MAF @tool-decorated Wikipedia functions.

    Provides:
    - ``wikipedia_search``: search Wikipedia articles by query
    - ``wikipedia_summary``: get a summary/extract for a specific article title

    Args:
        tool_config: Optional ``Tool.config`` JSON dict.  May include:
            - ``language`` (str): Wikipedia language code (default "en")
            - ``max_results`` (int): default max search results (default 5)
            - ``max_extract_chars`` (int): max characters for summary (default 10000)
            - ``timeout`` (float): request timeout in seconds (default 15)

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}

    lang: str = config.get("language", DEFAULT_LANG)
    max_results: int = int(config.get("max_results", DEFAULT_MAX_RESULTS))
    max_extract_chars: int = int(config.get("max_extract_chars", DEFAULT_MAX_EXTRACT_CHARS))
    timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT))

    @tool
    async def wikipedia_search(
        query: str,
        limit: int | None = None,
    ) -> dict:
        """Search Wikipedia for articles matching a query.

        Args:
            query: The search term (e.g. "Python programming language").
            limit: Maximum number of results to return
                (default: tool config or 5).

        Returns:
            A dict with:
            - ``query``: the search query
            - ``results``: list of dicts with ``title``, ``page_id``,
              ``description``, and ``url``
        """
        resolved_limit = limit if limit is not None else max_results
        logger.info("wikipedia_search: %s (limit=%d)", query, resolved_limit)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                data = await _rest_get(
                    client,
                    lang,
                    f"/page/summary/{quote(query)}?redirect=true",
                )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Fall back to title-based search
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        data = await _rest_get(
                            client,
                            lang,
                            f"/page/title/{quote(query)}?redirect=true",
                        )
                except httpx.HTTPStatusError:
                    return {
                        "query": query,
                        "results": [],
                        "error": f"No article found for '{query}'",
                    }
            else:
                logger.warning(
                    "wikipedia_search HTTP %d for %s",
                    exc.response.status_code,
                    query,
                )
                return {
                    "query": query,
                    "results": [],
                    "error": f"Wikipedia API error (HTTP {exc.response.status_code})",
                }
        except httpx.TimeoutException:
            logger.warning("wikipedia_search timeout for %s", query)
            return {"query": query, "results": [], "error": "Request timed out"}
        except Exception as exc:
            logger.exception("wikipedia_search failed for %s", query)
            return {"query": query, "results": [], "error": str(exc)}

        # Build result from the page summary
        result = {
            "title": data.get("title", query),
            "page_id": data.get("pageid"),
            "description": data.get("description", ""),
            "extract": (data.get("extract", "") or "")[:500],
            "url": data.get("content_urls", {})
            .get("desktop", {})
            .get("page", f"https://{lang}.wikipedia.org/wiki/{quote(query)}"),
        }

        # Also try search endpoint for more results
        all_results = [result]
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                search_url = (
                    f"{_rest_base(lang)}/search/page?"
                    f"q={quote(query)}&limit={resolved_limit}"
                )
                search_resp = await client.get(
                    search_url,
                    headers={
                        "User-Agent": (
                            "ph-agent-hub/1.0 (Wikipedia tool; "
                            "+https://github.com/kainotomo/ph-agent-hub)"
                        ),
                        "Accept": "application/json",
                    },
                )
                search_resp.raise_for_status()
                search_data = search_resp.json()

                for page in search_data.get("pages", []):
                    existing = any(
                        r.get("page_id") == page.get("id") for r in all_results
                    )
                    if not existing:
                        all_results.append({
                            "title": page.get("title", ""),
                            "page_id": page.get("id"),
                            "description": page.get("description", ""),
                            "extract": (page.get("excerpt", "") or "")[:500],
                            "url": (
                                f"https://{lang}.wikipedia.org/wiki/"
                                f"{quote(page.get('key', '').lstrip('/'))}"
                            ),
                        })
        except Exception:
            logger.debug("wikipedia_search fallback search failed, using summary only")

        # Limit results
        all_results = all_results[:resolved_limit]

        return {
            "query": query,
            "results": all_results,
            "result_count": len(all_results),
        }

    @tool
    async def wikipedia_summary(
        title: str,
    ) -> dict:
        """Get a detailed summary/extract for a specific Wikipedia article.

        Args:
            title: The exact article title (e.g. "Python_(programming_language)").

        Returns:
            A dict with:
            - ``title``: article title
            - ``page_id``: Wikipedia page ID
            - ``description``: short description
            - ``extract``: the article extract/summary text
            - ``extract_html``: extract in HTML (if available)
            - ``url``: desktop Wikipedia URL
            - ``thumbnail``: thumbnail image URL (if available)
            - ``extract_chars``: character count of extract
        """
        logger.info("wikipedia_summary: %s", title)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                data = await _rest_get(
                    client,
                    lang,
                    f"/page/summary/{quote(title)}?redirect=true",
                )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "wikipedia_summary HTTP %d for %s",
                exc.response.status_code,
                title,
            )
            return {
                "title": title,
                "error": f"Article not found (HTTP {exc.response.status_code})",
            }
        except httpx.TimeoutException:
            logger.warning("wikipedia_summary timeout for %s", title)
            return {"title": title, "error": "Request timed out"}
        except Exception as exc:
            logger.exception("wikipedia_summary failed for %s", title)
            return {"title": title, "error": str(exc)}

        extract = data.get("extract", "") or ""
        truncated = False
        if len(extract) > max_extract_chars:
            extract = extract[:max_extract_chars]
            truncated = True

        thumbnail = None
        if data.get("thumbnail"):
            thumbnail = data["thumbnail"].get("source")

        return {
            "title": data.get("title", title),
            "page_id": data.get("pageid"),
            "description": data.get("description", ""),
            "extract": extract,
            "extract_html": data.get("extract_html") if not truncated else None,
            "url": data.get("content_urls", {})
            .get("desktop", {})
            .get(
                "page",
                f"https://{lang}.wikipedia.org/wiki/{quote(title)}",
            ),
            "thumbnail": thumbnail,
            "extract_chars": len(extract),
            "truncated": truncated,
        }

    return [wikipedia_search, wikipedia_summary]
