# =============================================================================
# PH Agent Hub — RSS Feed Tool Factory
# =============================================================================
# Builds a MAF @tool-decorated async function that reads an RSS/Atom feed
# using the feedparser library.  The feed URL is configured by the admin
# in the tool's config.
# =============================================================================

import asyncio
import logging
from datetime import datetime, timezone

import feedparser
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_ENTRIES: int = 20
DEFAULT_MAX_SUMMARY_CHARS: int = 1000
DEFAULT_TIMEOUT: float = 15.0


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_rss_feed_tools(tool_config: dict | None = None) -> list:
    """Return a list containing the MAF @tool-decorated read_rss_feed function.

    The admin must configure ``feed_url`` in the tool's config JSON.

    Args:
        tool_config: ``Tool.config`` JSON dict.  Required keys:
            - ``feed_url`` (str): the RSS/Atom feed URL to read.
            Optional keys:
            - ``max_entries`` (int): max entries to return (default 20).
            - ``timeout`` (float): request timeout in seconds (default 15).

    Returns:
        A list with a single callable ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}

    feed_url: str = config.get("feed_url", "")
    if not feed_url:
        logger.error("build_rss_feed_tools called without feed_url in config")

        @tool
        async def read_rss_feed() -> dict:
            """Read the configured RSS/Atom feed and return its entries.

            The feed URL must be configured by an admin in the tool settings.
            """
            return {
                "error": "RSS feed not configured. "
                "An admin must set feed_url in the tool config."
            }

        return [read_rss_feed]

    max_entries: int = int(config.get("max_entries", DEFAULT_MAX_ENTRIES))
    timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT))

    @tool
    async def read_rss_feed(max_entries_override: int | None = None) -> dict:
        """Read the configured RSS/Atom feed and return recent entries.

        Args:
            max_entries_override: Override the default max entries to return.

        Returns:
            A dict with:
            - ``feed_title``: the feed's title
            - ``feed_link``: the feed's website link
            - ``feed_description``: the feed's description/subtitle
            - ``entries``: list of entry dicts, each with ``title``,
              ``link``, ``published``, ``summary``, ``author``
            - ``entry_count``: number of entries returned
        """
        limit = max_entries_override if max_entries_override is not None else max_entries
        logger.info("read_rss_feed: %s (limit=%d)", feed_url, limit)

        try:
            # feedparser is synchronous, run in thread
            parsed = await asyncio.to_thread(
                feedparser.parse, feed_url
            )
        except Exception as exc:
            logger.exception("read_rss_feed parse failed for %s", feed_url)
            return {"error": f"Failed to parse feed: {exc}"}

        if parsed.get("bozo", 0) and not parsed.get("entries"):
            bozo_msg = parsed.get("bozo_exception", None)
            exc_msg = str(bozo_msg) if bozo_msg else "Unknown parse error"
            logger.warning("read_rss_feed bozo error for %s: %s", feed_url, exc_msg)
            return {
                "error": f"Feed parse error: {exc_msg}",
                "feed_url": feed_url,
            }

        feed_info = parsed.get("feed", {})
        entries_raw = parsed.get("entries", [])[:limit]

        entries = []
        for entry in entries_raw:
            published = entry.get("published", "") or entry.get("updated", "")
            # Clean up HTML from summary
            summary = ""
            if entry.get("summary"):
                from html.parser import HTMLParser

                class StripHTML(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.text = ""

                    def handle_data(self, data):
                        self.text += data

                stripper = StripHTML()
                try:
                    stripper.feed(entry.summary)
                    summary = stripper.text.strip()[:DEFAULT_MAX_SUMMARY_CHARS]
                except Exception:
                    summary = entry.summary[:DEFAULT_MAX_SUMMARY_CHARS]

            entries.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": published,
                "summary": summary,
                "author": entry.get("author", ""),
            })

        return {
            "feed_title": feed_info.get("title", ""),
            "feed_link": feed_info.get("link", ""),
            "feed_description": feed_info.get("subtitle", "") or feed_info.get("description", ""),
            "entries": entries,
            "entry_count": len(entries),
        }

    return [read_rss_feed]
