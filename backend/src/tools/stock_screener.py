# =============================================================================
# PH Agent Hub — Stock Screener Tool Factory (yfinance)
# =============================================================================
# Builds a MAF @tool-decorated async function for stock screening
# using yfinance's EquityQuery + screen() API. No API key required.
# =============================================================================

import asyncio
import logging

from agent_framework import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_float(val) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_screener_quotes(quotes: list, limit: int) -> list[dict]:
    """Parse yfinance screen() response quotes into clean dicts.

    Extracts both basic price info and screening-relevant fields
    (valuation, growth, yield, sector, etc.) so the LLM can present
    meaningful screening results.
    """
    results = []
    if not quotes:
        return results
    for item in quotes[:limit]:
        if not isinstance(item, dict):
            continue
        entry = {
            "symbol": item.get("symbol", ""),
            "name": item.get("shortName") or item.get("longName") or "",
            "price": _safe_float(item.get("regularMarketPrice")),
            "change": _safe_float(item.get("regularMarketChange")),
            "change_pct": _safe_float(item.get("regularMarketChangePercent")),
            "volume": _safe_float(item.get("regularMarketVolume")),
            "market_cap": _safe_float(
                item.get("intradaymarketcap") or item.get("marketCap")
            ),
            "sector": item.get("sector", ""),
            "industry": item.get("industry", ""),
            "exchange": item.get("exchange", ""),
            "pe_ratio": _safe_float(
                item.get("peratio.lasttwelvemonths")
                or item.get("trailingPE")
                or item.get("forwardPE")
            ),
            "forward_dividend_yield": _safe_float(
                item.get("forward_dividend_yield")
                or item.get("dividendYield")
                or item.get("trailingAnnualDividendYield")
            ),
            "beta": _safe_float(item.get("beta")),
        }
        # Calculate yield as percentage if raw value was decimal
        if (
            entry["forward_dividend_yield"] is not None
            and entry["forward_dividend_yield"] < 1
        ):
            # yfinance sometimes returns decimal, sometimes percentage
            entry["forward_dividend_yield"] = round(
                entry["forward_dividend_yield"] * 100, 2
            )
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Supported filter field names (EquityQuery operands)
# ---------------------------------------------------------------------------

_FIELD_REGION = "region"
_FIELD_SECTOR = "sector"
_FIELD_INDUSTRY = "industry"
_FIELD_EXCHANGE = "exchange"

_FIELD_PE = "peratio.lasttwelvemonths"
_FIELD_PEG = "pegratio_5y"
_FIELD_MARKET_CAP = "intradaymarketcap"
_FIELD_DIVIDEND_YIELD = "forward_dividend_yield"
_FIELD_EPS_GROWTH = "epsgrowth.lasttwelvemonths"
_FIELD_REVENUE_GROWTH = "totalrevenues1yrgrowth.lasttwelvemonths"
_FIELD_BETA = "beta"
_FIELD_PRICE = "intradayprice"
_FIELD_VOLUME = "dayvolume"

# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_stock_screener_tools(tool_config: dict | None = None) -> list:
    """Return a list of MAF @tool-decorated stock screener functions.

    Provides:
    - ``stock_screener``: screen stocks by custom criteria

    Args:
        tool_config: Optional ``Tool.config`` JSON dict. Currently unused.

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    _ = tool_config

    @tool
    async def stock_screener(
        region: str | None = None,
        sector: str | None = None,
        industry: str | None = None,
        exchange: str | None = None,
        pe_max: float | None = None,
        pe_min: float | None = None,
        peg_max: float | None = None,
        market_cap_min: float | None = None,
        market_cap_max: float | None = None,
        dividend_yield_min: float | None = None,
        eps_growth_min: float | None = None,
        revenue_growth_min: float | None = None,
        beta_max: float | None = None,
        beta_min: float | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        volume_min: int | None = None,
        sort_by: str | None = None,
        sort_asc: bool = False,
        limit: int = 25,
    ) -> dict:
        """Screen stocks by custom criteria using yfinance's EquityQuery API.

        Supports worldwide region filtering (60+ countries), global exchanges,
        sector/industry filters, valuation criteria (P/E, PEG, market cap),
        growth criteria (EPS growth, revenue growth), and technical criteria
        (price, volume, beta).

        Use this tool when the user wants to **discover** stocks matching
        specific criteria — e.g. "find Energy stocks in Japan with P/E < 15".

        Args:
            region: Country/region code (e.g. "us", "jp", "gb", "de", "au",
                "in", "hk", "br"). 60+ codes available. Pass ``None`` for
                global/all regions.
            sector: Sector name (e.g. "Energy", "Technology", "Healthcare",
                "Financial Services", "Consumer Cyclical", "Industrials",
                "Real Estate", "Basic Materials", "Communication Services",
                "Consumer Defensive", "Utilities").
            industry: Industry name (e.g. "Oil & Gas E&P", "Software -
                Application", "Banks - Diversified"). See ``sector`` to narrow
                down first.
            exchange: Exchange code (e.g. "ASX", "LSE", "TOR", "FRA", "HKG",
                "JPX", "BSE", "PAR", "NMS", "NYQ").
            pe_max: Maximum trailing P/E ratio.
            pe_min: Minimum trailing P/E ratio.
            peg_max: Maximum PEG ratio (5-year expected).
            market_cap_min: Minimum market cap in USD.
            market_cap_max: Maximum market cap in USD.
            dividend_yield_min: Minimum forward dividend yield (percentage,
                e.g. 2.0 for 2%).
            eps_growth_min: Minimum EPS growth over last 12 months
                (percentage, e.g. 10 for 10%).
            revenue_growth_min: Minimum revenue growth over last 12 months
                (percentage, e.g. 15 for 15%).
            beta_max: Maximum beta (volatility relative to market).
            beta_min: Minimum beta.
            price_min: Minimum stock price in USD.
            price_max: Maximum stock price in USD.
            volume_min: Minimum daily trading volume.
            sort_by: Field to sort results by (e.g. "percentchange",
                "dayvolume", "intradaymarketcap", "peratio.lasttwelvemonths").
            sort_asc: Sort ascending (default descending).
            limit: Maximum number of results to return (max 250). Default 25.

        Returns:
            A dict with:
            - ``results``: list of matching stock dicts
            - ``count``: number of results returned
            - ``total``: total matches (if available from yfinance)
            - ``filters_applied``: dict of the criteria that were used
            - ``source``: "yfinance"
            Each stock dict includes: ``symbol``, ``name``, ``price``,
            ``change``, ``change_pct``, ``volume``, ``market_cap``,
            ``sector``, ``industry``, ``exchange``, ``pe_ratio``,
            ``forward_dividend_yield``, ``beta``.
        """
        from yfinance import EquityQuery
        import yfinance as yf

        count = min(max(limit, 1), 250)
        logger.info(
            "stock_screener: region=%s sector=%s industry=%s exchange=%s limit=%d",
            region, sector, industry, exchange, count,
        )

        # ---- Build EquityQuery conditions dynamically --------------------
        conditions: list = []

        # Categorical filters (is-in)
        if region:
            conditions.append(EquityQuery("is-in", [_FIELD_REGION, region]))
        if sector:
            conditions.append(EquityQuery("is-in", [_FIELD_SECTOR, sector]))
        if industry:
            conditions.append(EquityQuery("is-in", [_FIELD_INDUSTRY, industry]))
        if exchange:
            conditions.append(EquityQuery("is-in", [_FIELD_EXCHANGE, exchange]))

        # Numeric filters (comparison ops)
        if pe_max is not None:
            conditions.append(EquityQuery("lte", [_FIELD_PE, pe_max]))
        if pe_min is not None:
            conditions.append(EquityQuery("gte", [_FIELD_PE, pe_min]))
        if peg_max is not None:
            conditions.append(EquityQuery("lte", [_FIELD_PEG, peg_max]))
        if market_cap_min is not None:
            conditions.append(EquityQuery("gte", [_FIELD_MARKET_CAP, market_cap_min]))
        if market_cap_max is not None:
            conditions.append(EquityQuery("lte", [_FIELD_MARKET_CAP, market_cap_max]))
        if dividend_yield_min is not None:
            conditions.append(
                EquityQuery("gte", [_FIELD_DIVIDEND_YIELD, dividend_yield_min])
            )
        if eps_growth_min is not None:
            conditions.append(
                EquityQuery("gte", [_FIELD_EPS_GROWTH, eps_growth_min])
            )
        if revenue_growth_min is not None:
            conditions.append(
                EquityQuery("gte", [_FIELD_REVENUE_GROWTH, revenue_growth_min])
            )
        if beta_max is not None:
            conditions.append(EquityQuery("lte", [_FIELD_BETA, beta_max]))
        if beta_min is not None:
            conditions.append(EquityQuery("gte", [_FIELD_BETA, beta_min]))
        if price_min is not None:
            conditions.append(EquityQuery("gte", [_FIELD_PRICE, price_min]))
        if price_max is not None:
            conditions.append(EquityQuery("lte", [_FIELD_PRICE, price_max]))
        if volume_min is not None:
            conditions.append(EquityQuery("gte", [_FIELD_VOLUME, volume_min]))

        filters_applied = {
            "region": region,
            "sector": sector,
            "industry": industry,
            "exchange": exchange,
            "pe_max": pe_max,
            "pe_min": pe_min,
            "peg_max": peg_max,
            "market_cap_min": market_cap_min,
            "market_cap_max": market_cap_max,
            "dividend_yield_min": dividend_yield_min,
            "eps_growth_min": eps_growth_min,
            "revenue_growth_min": revenue_growth_min,
            "beta_max": beta_max,
            "beta_min": beta_min,
            "price_min": price_min,
            "price_max": price_max,
            "volume_min": volume_min,
        }
        # Remove None values for cleaner response
        filters_applied = {k: v for k, v in filters_applied.items() if v is not None}

        if not conditions:
            return {
                "error": "At least one filter criterion is required. "
                "Specify a region, sector, exchange, or numeric filter.",
            }

        # Wrap conditions — EquityQuery('and', ...) requires >= 2 items
        if len(conditions) == 1:
            query = conditions[0]
        else:
            query = EquityQuery("and", conditions)

        logger.debug("stock_screener: query built, running screen...")

        try:

            def _run_screen():
                return yf.screen(
                    query=query,
                    count=count,
                    sortField=sort_by,
                    sortAsc=sort_asc,
                )

            result = await asyncio.to_thread(_run_screen)

            if not result or not isinstance(result, dict):
                logger.warning("stock_screener: empty or invalid response")
                return {
                    "results": [],
                    "count": 0,
                    "filters_applied": filters_applied,
                    "source": "yfinance",
                }

            quotes = result.get("quotes", [])
            total = result.get("total", len(quotes))
            parsed = _parse_screener_quotes(quotes, count)

            logger.info(
                "stock_screener: got %d results (total=%s)",
                len(parsed), total,
            )

            return {
                "results": parsed,
                "count": len(parsed),
                "total": total,
                "filters_applied": filters_applied,
                "source": "yfinance",
            }

        except Exception as exc:
            logger.exception(
                "stock_screener failed: %s", exc,
            )
            return {"error": f"Stock screening failed: {exc}"}

    return [stock_screener]
