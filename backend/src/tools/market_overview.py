# =============================================================================
# PH Agent Hub — Market Overview Tool Factory (yfinance)
# =============================================================================
# Builds MAF @tool-decorated async functions for index quotes and market
# movers using the free yfinance library. No API key required.
# =============================================================================

import asyncio
import logging

from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Major global indices — ticker symbols for yfinance
# ---------------------------------------------------------------------------
_INDICES: dict[str, str] = {
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Dow Jones": "^DJI",
    "VIX": "^VIX",
    "FTSE 100": "^FTSE",
    "DAX": "^GDAXI",
    "Nikkei 225": "^N225",
    "Hang Seng": "^HSI",
    "Nifty 50": "^NSEI",
    "ASX 200": "^AXJO",
}

_INDEX_NAMES_BY_SYMBOL: dict[str, str] = {
    v: k for k, v in _INDICES.items()
}


def _safe_float(val) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _ticker_to_dict(t, prefix: str = "") -> dict:
    """Extract key fields from a yfinance Ticker info dict into a clean dict."""
    info = {}
    try:
        info = t.info or {}
    except Exception:
        pass

    price = (
        _safe_float(info.get("regularMarketPrice"))
        or _safe_float(info.get("previousClose"))
        or _safe_float(info.get("currentPrice"))
    )
    prev_close = _safe_float(info.get("previousClose")) or _safe_float(
        info.get("regularMarketPreviousClose")
    )
    change = None
    change_pct = None
    if price is not None and prev_close is not None and prev_close != 0:
        change = round(price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2)

    return {
        "symbol": info.get("symbol", ""),
        "name": info.get("shortName") or info.get("longName") or "",
        "price": price,
        "previous_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "day_high": _safe_float(info.get("regularMarketDayHigh") or info.get("dayHigh")),
        "day_low": _safe_float(info.get("regularMarketDayLow") or info.get("dayLow")),
        "volume": _safe_float(info.get("regularMarketVolume") or info.get("volume")),
        "open": _safe_float(info.get("regularMarketOpen") or info.get("open")),
        "market_cap": _safe_float(info.get("marketCap")),
        "fifty_day_avg": _safe_float(info.get("fiftyDayAverage")),
        "two_hundred_day_avg": _safe_float(info.get("twoHundredDayAverage")),
        "currency": info.get("currency", ""),
        "exchange": info.get("exchange") or info.get("fullExchangeName") or "",
    }


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_market_overview_tools(tool_config: dict | None = None) -> list:
    """Return a list of MAF @tool-decorated market overview functions.

    Provides:
    - ``get_index_quote``: get current quote for a major index
    - ``get_market_movers``: get day's top gainers and losers

    Args:
        tool_config: Optional ``Tool.config`` JSON dict. Currently unused.

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    _ = tool_config

    @tool
    async def get_index_quote(symbol: str | None = None) -> dict:
        """Get current quote for one or all major global stock indices.

        Uses yfinance — free, no API key required.

        Args:
            symbol: Optional index name or ticker. If omitted or "all",
                returns all tracked indices. Accepted names: "S&P 500",
                "Nasdaq", "Dow Jones", "VIX", "FTSE 100", "DAX",
                "Nikkei 225", "Hang Seng", "Nifty 50", "ASX 200".
                You can also pass the raw ticker like "^GSPC".

        Returns:
            A dict with a ``quotes`` list of index quote dicts, each with:
            ``symbol``, ``name``, ``price``, ``previous_close``,
            ``change``, ``change_pct``, ``day_high``, ``day_low``,
            ``volume``, ``open``, ``market_cap``, ``fifty_day_avg``,
            ``two_hundred_day_avg``, ``currency``, ``exchange``.
        """
        # Determine which tickers to fetch
        if symbol and symbol.lower() != "all":
            # Try matching by name first, then by raw ticker
            ticker = None
            for name, sym in _INDICES.items():
                if name.lower() == symbol.lower():
                    ticker = sym
                    break
            if ticker is None:
                # Assume raw ticker symbol
                ticker = symbol.upper()
            tickers_to_fetch = [ticker]
        else:
            tickers_to_fetch = list(_INDICES.values())

        logger.info("get_index_quote: fetching %d indices", len(tickers_to_fetch))

        def _fetch_index_quote() -> dict:
            import yfinance as yf

            try:
                # yfinance Tickers (plural) for batch download
                yf_tickers = yf.Tickers(" ".join(tickers_to_fetch))
            except Exception as exc:
                logger.exception("get_index_quote: yfinance Tickers failed")
                return {"error": f"Failed to fetch index data: {exc}"}

            quotes = []
            for sym in tickers_to_fetch:
                try:
                    t = getattr(yf_tickers, "tickers", {}).get(sym)
                    if t is None:
                        t = yf.Ticker(sym)
                    quote = _ticker_to_dict(t)
                    if not quote.get("name"):
                        quote["name"] = _INDEX_NAMES_BY_SYMBOL.get(sym, sym)
                    if quote.get("price") is None:
                        # Try fast_info
                        try:
                            fi = t.fast_info
                            quote["price"] = _safe_float(getattr(fi, "last_price", None))
                            quote["previous_close"] = _safe_float(
                                getattr(fi, "previous_close", None)
                            )
                            if (
                                quote["price"] is not None
                                and quote["previous_close"] is not None
                                and quote["previous_close"] != 0
                            ):
                                chg = quote["price"] - quote["previous_close"]
                                quote["change"] = round(chg, 2)
                                quote["change_pct"] = round(
                                    (chg / quote["previous_close"]) * 100, 2
                                )
                        except Exception:
                            pass
                    quotes.append(quote)
                except Exception as exc:
                    logger.warning("get_index_quote: failed for %s: %s", sym, exc)
                    quotes.append({
                        "symbol": sym,
                        "name": _INDEX_NAMES_BY_SYMBOL.get(sym, sym),
                        "error": str(exc),
                    })

            return {
                "quotes": quotes,
                "count": len(quotes),
                "source": "yfinance",
            }

        return await asyncio.to_thread(_fetch_index_quote)

    @tool
    async def get_market_movers(
        market: str = "US",
        count: int = 10,
    ) -> dict:
        """Get the day's top gainers and losers from major markets.

        Uses yfinance — free, no API key required.

        Args:
            market: Market identifier. One of "US", "DE", "GB", "JP",
                "HK", "IN", "AU", or "most_actives" for most active stocks.
                Defaults to "US".
            count: Number of gainers and losers to return (max 25). Default 10.

        Returns:
            A dict with:
            - ``gainers``: list of top gaining stocks
            - ``losers``: list of top losing stocks
            - ``market``: the market identifier
            - ``source``: "yfinance"
            Each stock dict includes: ``symbol``, ``name``, ``price``,
            ``change``, ``change_pct``, ``volume``.
        """
        import yfinance as yf

        # Map market to yfinance screener keys
        market_map = {
            "US": "day_gainers",
            "DE": "de_day_gainers",
            "GB": "gb_day_gainers",
            "JP": "jp_day_gainers",
            "HK": "hk_day_gainers",
            "IN": "in_day_gainers",
            "AU": "au_day_gainers",
            "most_actives": "most_actives",
        }

        loser_map = {
            "US": "day_losers",
            "DE": "de_day_losers",
            "GB": "gb_day_losers",
            "JP": "jp_day_losers",
            "HK": "hk_day_losers",
            "IN": "in_day_losers",
            "AU": "au_day_losers",
        }

        gainer_key = market_map.get(market, "day_gainers")
        loser_key = loser_map.get(market, "day_losers")

        count = min(count, 25)
        logger.info("get_market_movers: market=%s count=%d", market, count)

        def _fetch_screener(key: str, limit: int) -> list[dict]:
            try:
                result = yf.screen(query=key, count=limit)
                if result and isinstance(result, dict):
                    quotes = result.get("quotes", [])
                    return _parse_screener_quotes(quotes, limit)
            except Exception as exc:
                logger.warning("get_market_movers: screener %s failed: %s", key, exc)
            return []

        gainers = await asyncio.to_thread(_fetch_screener, gainer_key, count)
        losers = await asyncio.to_thread(_fetch_screener, loser_key, count)

        return {
            "market": market,
            "gainers": gainers[:count] if gainers else [],
            "losers": losers[:count] if losers else [],
            "source": "yfinance",
        }

    return [get_index_quote, get_market_movers]


def _parse_screener_quotes(quotes: list, limit: int) -> list[dict]:
    """Parse yfinance screen() quotes into clean dicts."""
    movers = []
    if not quotes:
        return movers
    for item in quotes[:limit]:
        if isinstance(item, dict):
            movers.append({
                "symbol": item.get("symbol", ""),
                "name": item.get("shortName") or item.get("longName") or "",
                "price": _safe_float(item.get("regularMarketPrice")),
                "change": _safe_float(item.get("regularMarketChange")),
                "change_pct": _safe_float(item.get("regularMarketChangePercent")),
                "volume": _safe_float(item.get("regularMarketVolume")),
            })
    return movers


async def _fallback_movers(market: str, count: int) -> tuple[list[dict], list[dict]]:
    """Fallback: use yfinance Screener with simpler queries."""
    import yfinance as yf

    gainers = []
    losers = []

    # Try with direct screener body
    screener_bodies = {
        "US": {"operator": "gt", "operand": 2, "quoteType": "EQUITY"},
    }

    def _fetch() -> tuple[list[dict], list[dict]]:
        for key, body in screener_bodies.items():
            try:
                scr = yf.Screener(body=body, count=count)
                quotes = getattr(scr, "quotes", []) or []
                # Sort by change percent
                sorted_quotes = sorted(
                    quotes,
                    key=lambda q: _safe_float(
                        q.get("regularMarketChangePercent", 0)
                        if isinstance(q, dict)
                        else getattr(q, "regularMarketChangePercent", 0)
                    )
                    or 0,
                    reverse=True,
                )
                for q in sorted_quotes[:count]:
                    if isinstance(q, dict):
                        gainers.append({
                            "symbol": q.get("symbol", ""),
                            "name": q.get("shortName", ""),
                            "price": _safe_float(q.get("regularMarketPrice")),
                            "change": _safe_float(q.get("regularMarketChange")),
                            "change_pct": _safe_float(q.get("regularMarketChangePercent")),
                            "volume": _safe_float(q.get("regularMarketVolume")),
                        })
            except Exception:
                pass
        return gainers, losers

    gainers, losers = await asyncio.to_thread(_fetch)
    return gainers, losers
