# =============================================================================
# PH Agent Hub — Stock Data Tool Factory (yfinance)
# =============================================================================
# Builds MAF @tool-decorated async functions for stock quotes, historical
# prices, financial statements, key metrics, company info, dividends,
# earnings history, and analyst ratings. Uses yfinance — free, no API key.
# =============================================================================

import asyncio
import logging
from datetime import datetime, timezone

from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(val) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _df_to_dicts(df, date_key: str = "date") -> list[dict]:
    """Convert a pandas DataFrame to a list of dicts, with date index as key."""
    import pandas as pd

    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return []
    records = df.reset_index().to_dict(orient="records")
    # Convert Timestamps to strings
    clean = []
    for r in records:
        item = {}
        for k, v in r.items():
            if isinstance(v, pd.Timestamp):
                item[k] = v.isoformat()
            elif isinstance(v, (int, float)):
                # Replace NaN/Inf with None
                if pd.isna(v) or (isinstance(v, float) and not pd.isna(v) and v == float("inf")):
                    item[k] = None
                else:
                    item[k] = v
            else:
                item[k] = v
        clean.append(item)
    return clean


def _strip_null_fields(rows: list[dict]) -> tuple[list[dict], int]:
    """Drop line items whose value is ``None`` and count them.

    yfinance materialises every line item for every period, so a balance
    sheet is roughly a fifth ``null`` — keys that carry no value but still
    cost characters, which is what pushed ``get_financials`` past the
    stored-output cap in production.  The caller reports the dropped count
    so the model does not read an omitted key as "this field does not
    exist".
    """
    cleaned: list[dict] = []
    omitted = 0
    for row in rows:
        kept = {}
        for key, value in row.items():
            if value is None:
                omitted += 1
            else:
                kept[key] = value
        cleaned.append(kept)
    return cleaned, omitted


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_stock_data_tools(tool_config: dict | None = None) -> list:
    """Return a list of MAF @tool-decorated stock data functions.

    Provides (6 tools, consolidated from 8):
    - ``get_stock_snapshot``: price quote, key metrics, company profile,
      and analyst consensus in a single call (merges 4 old tools)
    - ``get_financials``: income statement, balance sheet, cash flow
    - ``get_earnings``: past EPS history, earnings calendar with reported
      EPS, upcoming dates, and trailing/forward EPS
    - ``get_historical_prices``: OHLCV historical data
    - ``get_dividends``: dividend history and yield
    - ``get_company_news``: recent news articles from Yahoo Finance

    Args:
        tool_config: Optional ``Tool.config`` JSON dict. Currently unused.

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    _ = tool_config

    # =========================================================================
    # 1. get_stock_snapshot — merges quote, metrics, company info, analyst
    # =========================================================================
    @tool
    async def get_stock_snapshot(symbol: str) -> dict:
        """Get a comprehensive stock snapshot: price, key metrics,
        company profile, and analyst consensus.

        Uses yfinance — free, no API key required.  Reads ``ticker.info``
        once internally so the four data sections are served in a single
        HTTP call.

        Args:
            symbol: Stock ticker symbol (e.g. "AAPL", "MSFT", "0700.HK").

        Returns:
            A dict with four sections:

            ``quote``:
                ``price``, ``previous_close``, ``change``, ``change_pct``,
                ``day_high``, ``day_low``, ``day_open``, ``volume``,
                ``avg_volume``, ``market_cap``, ``bid``, ``ask``,
                ``fifty_two_week_high``, ``fifty_two_week_low``,
                ``currency``, ``exchange``, ``market_state``.

            ``metrics``:
                ``pe_ratio``, ``forward_pe``, ``peg_ratio``, ``pb_ratio``,
                ``ps_ratio``, ``roe``, ``roa``, ``debt_to_equity``,
                ``current_ratio``, ``quick_ratio``, ``gross_margins``,
                ``operating_margins``, ``profit_margins``,
                ``eps_trailing``, ``eps_forward``, ``revenue_per_share``,
                ``dividend_yield``, ``payout_ratio``, ``beta``,
                ``revenue_growth``, ``earnings_growth``,
                ``free_cashflow``, ``enterprise_value``, ``ev_to_revenue``,
                ``ev_to_ebitda``, ``short_ratio``, ``short_pct_float``.

            ``company``:
                ``name``, ``description``, ``sector``, ``industry``,
                ``website``, ``country``, ``state``, ``city``,
                ``employees``, ``ipo_date``, ``exchange``, ``currency``,
                ``phone``, ``fax``, ``address``, ``officers`` (list of
                dicts with ``name``, ``title``, ``age``, ``year_born``).

            ``analyst``:
                ``recommendation``, ``target_mean``, ``target_high``,
                ``target_low``, ``target_median``, ``number_of_analysts``,
                ``recommendations`` (list of rating changes with ``date``,
                ``firm``, ``to_grade``, ``from_grade``, ``action``).

            Top-level keys: ``symbol``, ``name``, ``source``.
        """
        import yfinance as yf

        sym = symbol.upper().strip()
        logger.info("get_stock_snapshot: %s", sym)

        try:
            t = await asyncio.to_thread(yf.Ticker, sym)
            info = {}
            try:
                info = t.info or {}
            except Exception:
                pass

            # ---- quote ----
            price = (
                _safe_float(info.get("regularMarketPrice"))
                or _safe_float(info.get("currentPrice"))
                or _safe_float(info.get("previousClose"))
            )
            prev_close = _safe_float(info.get("previousClose")) or _safe_float(
                info.get("regularMarketPreviousClose")
            )
            change = None
            change_pct = None
            if price is not None and prev_close is not None and prev_close != 0:
                change = round(price - prev_close, 2)
                change_pct = round((change / prev_close) * 100, 2)

            quote = {
                "price": price,
                "previous_close": prev_close,
                "change": change,
                "change_pct": change_pct,
                "day_high": _safe_float(info.get("regularMarketDayHigh") or info.get("dayHigh")),
                "day_low": _safe_float(info.get("regularMarketDayLow") or info.get("dayLow")),
                "day_open": _safe_float(info.get("regularMarketOpen") or info.get("open")),
                "volume": _safe_float(info.get("regularMarketVolume") or info.get("volume")),
                "avg_volume": _safe_float(info.get("averageVolume") or info.get("averageDailyVolume10Day")),
                "market_cap": _safe_float(info.get("marketCap")),
                "bid": _safe_float(info.get("bid")),
                "ask": _safe_float(info.get("ask")),
                "fifty_two_week_high": _safe_float(info.get("fiftyTwoWeekHigh")),
                "fifty_two_week_low": _safe_float(info.get("fiftyTwoWeekLow")),
                "currency": info.get("currency", ""),
                "exchange": info.get("exchange") or info.get("fullExchangeName") or "",
                "market_state": info.get("marketState", ""),
            }

            # ---- metrics ----
            metrics = {
                "pe_ratio": _safe_float(info.get("trailingPE")),
                "forward_pe": _safe_float(info.get("forwardPE")),
                "peg_ratio": _safe_float(info.get("pegRatio")),
                "pb_ratio": _safe_float(info.get("priceToBook")),
                "ps_ratio": _safe_float(info.get("priceToSalesTrailing12Months")),
                "roe": _safe_float(info.get("returnOnEquity")),
                "roa": _safe_float(info.get("returnOnAssets")),
                "debt_to_equity": _safe_float(info.get("debtToEquity")),
                "current_ratio": _safe_float(info.get("currentRatio")),
                "quick_ratio": _safe_float(info.get("quickRatio")),
                "gross_margins": _safe_float(info.get("grossMargins")),
                "operating_margins": _safe_float(info.get("operatingMargins")),
                "profit_margins": _safe_float(info.get("profitMargins")),
                "eps_trailing": _safe_float(info.get("trailingEps")),
                "eps_forward": _safe_float(info.get("forwardEps")),
                "revenue_per_share": _safe_float(info.get("revenuePerShare")),
                "dividend_yield": _safe_float(info.get("dividendYield")),
                "payout_ratio": _safe_float(info.get("payoutRatio")),
                "beta": _safe_float(info.get("beta")),
                "revenue_growth": _safe_float(info.get("revenueGrowth")),
                "earnings_growth": _safe_float(info.get("earningsGrowth")),
                "free_cashflow": _safe_float(info.get("freeCashflow")),
                "enterprise_value": _safe_float(info.get("enterpriseValue")),
                "ev_to_revenue": _safe_float(info.get("enterpriseToRevenue")),
                "ev_to_ebitda": _safe_float(info.get("enterpriseToEbitda")),
                "short_ratio": _safe_float(info.get("shortRatio")),
                "short_pct_float": _safe_float(info.get("shortPercentOfFloat")),
            }

            # ---- company ----
            officers = []
            raw_officers = info.get("companyOfficers") or []
            if isinstance(raw_officers, list):
                for o in raw_officers:
                    if isinstance(o, dict):
                        officers.append({
                            "name": o.get("name", ""),
                            "title": o.get("title", ""),
                            "age": o.get("age"),
                            "year_born": o.get("yearBorn"),
                        })

            company = {
                "name": info.get("shortName") or info.get("longName") or sym,
                "description": info.get("longBusinessSummary", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "website": info.get("website", ""),
                "country": info.get("country", ""),
                "state": info.get("state", ""),
                "city": info.get("city", ""),
                "employees": _safe_float(info.get("fullTimeEmployees")),
                "ipo_date": str(info.get("ipoDate", "")),
                "exchange": info.get("exchange") or info.get("fullExchangeName") or "",
                "currency": info.get("currency", ""),
                "phone": info.get("phone", ""),
                "fax": info.get("fax", ""),
                "address": (
                    f"{info.get('address1', '')} {info.get('address2', '')}".strip()
                    if info.get("address1")
                    else ""
                ),
                "officers": officers,
            }

            # ---- analyst ----
            recommendations = []
            try:
                recs = t.recommendations
                if recs is not None:
                    import pandas as pd
                    if isinstance(recs, pd.DataFrame) and not recs.empty:
                        for _, row in recs.head(20).iterrows():
                            recommendations.append({
                                "date": str(row.get("Date", row.get("date", ""))),
                                "firm": str(row.get("Firm", row.get("firm", ""))),
                                "to_grade": str(row.get("To Grade", row.get("toGrade", ""))),
                                "from_grade": str(row.get("From Grade", row.get("fromGrade", ""))),
                                "action": str(row.get("Action", row.get("action", ""))),
                            })
            except Exception:
                pass

            analyst = {
                "recommendation": info.get("recommendationKey", info.get("recommendationMean", "")),
                "target_mean": _safe_float(info.get("targetMeanPrice")),
                "target_high": _safe_float(info.get("targetHighPrice")),
                "target_low": _safe_float(info.get("targetLowPrice")),
                "target_median": _safe_float(info.get("targetMedianPrice")),
                "number_of_analysts": _safe_float(info.get("numberOfAnalystOpinions")),
                "recommendations": recommendations,
            }

            return {
                "symbol": sym,
                "name": company["name"],
                "quote": quote,
                "metrics": metrics,
                "company": company,
                "analyst": analyst,
                "source": "yfinance",
            }

        except Exception as exc:
            logger.exception("get_stock_snapshot failed for %s", sym)
            return {"symbol": sym, "error": str(exc)}

    # =========================================================================
    # 2. get_financials — kept unchanged
    # =========================================================================
    @tool
    async def get_financials(
        symbol: str,
        statement_type: str = "income",
        period: str = "annual",
    ) -> dict:
        """Get financial statements for a company.

        Uses yfinance — free, no API key required.

        Args:
            symbol: Stock ticker symbol (e.g. "AAPL").
            statement_type: "income" (income statement), "balance"
                (balance sheet), or "cash" (cash flow). Default "income".
            period: "annual" or "quarterly". Default "annual".

        Returns:
            A dict with:
            - ``symbol``: the stock ticker
            - ``statement_type``: the type of statement
            - ``period``: annual or quarterly
            - ``statements``: list of statement dicts, each with ``date``
              and financial line items (transposed so dates are rows).
              Line items the provider has no value for are omitted rather
              than sent as ``null`` — see ``null_fields_omitted``.
            - ``count``: number of statement periods
            - ``null_fields_omitted``: how many line items were dropped
              because the provider reported no value for that period
            - ``source``: "yfinance"
        """
        import yfinance as yf

        sym = symbol.upper().strip()
        logger.info(
            "get_financials: %s type=%s period=%s", sym, statement_type, period
        )

        valid_types = {"income", "balance", "cash"}
        stype = statement_type.lower().strip()
        if stype not in valid_types:
            return {
                "symbol": sym,
                "error": f"Invalid statement_type '{statement_type}'. "
                f"Must be one of: income, balance, cash",
            }

        is_quarterly = period.lower().strip() == "quarterly"

        try:
            t = await asyncio.to_thread(yf.Ticker, sym)

            if stype == "income":
                attr = "quarterly_income_stmt" if is_quarterly else "income_stmt"
            elif stype == "balance":
                attr = "quarterly_balance_sheet" if is_quarterly else "balance_sheet"
            else:
                attr = "quarterly_cashflow" if is_quarterly else "cashflow"

            df = await asyncio.to_thread(getattr, t, attr)
            if df is None:
                return {
                    "symbol": sym,
                    "statement_type": stype,
                    "period": period,
                    "statements": [],
                    "count": 0,
                    "null_fields_omitted": 0,
                    "source": "yfinance",
                }

            # Transpose so dates are rows and line items are columns
            import pandas as pd
            if isinstance(df, pd.DataFrame):
                df_t = df.transpose()
                statements = _df_to_dicts(df_t)
                statements, null_fields_omitted = _strip_null_fields(statements)
            else:
                statements = []
                null_fields_omitted = 0

            return {
                "symbol": sym,
                "statement_type": stype,
                "period": period,
                "statements": statements,
                "count": len(statements),
                "null_fields_omitted": null_fields_omitted,
                "source": "yfinance",
            }

        except Exception as exc:
            logger.exception("get_financials failed for %s", sym)
            return {"symbol": sym, "error": str(exc)}

    # =========================================================================
    # 3. get_earnings — merges old get_earnings_history + ticker.earnings_dates
    # =========================================================================
    @tool
    async def get_earnings(symbol: str) -> dict:
        """Get earnings data for a stock: past EPS actuals, earnings calendar
        with reported EPS, upcoming dates, and trailing/forward EPS.

        Uses three yfinance data pathways in a single Ticker instance:
        ``.earnings_history`` (QuoteSummary API), ``.earnings_dates``
        (HTML-scraped calendar with reported EPS), and ``.calendar``
        (upcoming estimates).

        Args:
            symbol: Stock ticker symbol (e.g. "AAPL", "0700.HK").

        Returns:
            A dict with:
            - ``symbol``: the stock ticker
            - ``past_earnings``: list of past quarters with ``date``,
              ``eps_estimate``, ``eps_actual``, ``surprise_pct``
              (from earnings_history API — may lag for non-US stocks)
            - ``calendar``: list of earnings dates with ``date``,
              ``eps_estimate``, ``reported_eps``, ``surprise_pct``
              (from earnings_dates HTML scrape — typically fresher
              for international stocks; reported_eps is null for
              future dates where earnings haven't been released yet)
            - ``upcoming_earnings``: list of future earnings dates
              with ``date``, ``eps_estimate``, ``revenue_estimate``
            - ``eps_trailing``: trailing 12-month EPS
            - ``eps_forward``: forward EPS estimate
            - ``source``: "yfinance"
        """
        import yfinance as yf

        sym = symbol.upper().strip()
        logger.info("get_earnings: %s", sym)

        try:
            t = await asyncio.to_thread(yf.Ticker, sym)
            info = {}
            try:
                info = t.info or {}
            except Exception:
                pass

            # ---- past_earnings (from earnings_history API) ----
            past_earnings = []
            try:
                raw_history = t.earnings_history
                if raw_history is not None:
                    if hasattr(raw_history, "to_dict"):
                        import pandas as pd
                        if isinstance(raw_history, pd.DataFrame) and not raw_history.empty:
                            for _, row in raw_history.iterrows():
                                past_earnings.append({
                                    "date": str(row.get("EPSReportDate", row.get("date", ""))),
                                    "eps_estimate": _safe_float(row.get("EPSEstimate", row.get("epsEstimate"))),
                                    "eps_actual": _safe_float(row.get("EPSActual", row.get("epsActual"))),
                                    "surprise_pct": _safe_float(row.get("Surprise(%)", row.get("surprisePercent"))),
                                })
                    elif isinstance(raw_history, dict):
                        past_earnings = raw_history.get("earnings", [])
            except Exception:
                pass

            # ---- calendar (from earnings_dates HTML scrape — fresher pathway) ----
            calendar = []
            try:
                ed = t.earnings_dates
                if ed is not None:
                    import pandas as pd
                    import math
                    if isinstance(ed, pd.DataFrame) and not ed.empty:
                        for dt_idx, row in ed.iterrows():
                            reported = _safe_float(row.get("Reported EPS"))
                            # Treat NaN as None (not yet reported)
                            if reported is not None and math.isnan(reported):
                                reported = None
                            surprise = _safe_float(row.get("Surprise(%)"))
                            if surprise is not None and math.isnan(surprise):
                                surprise = None
                            calendar.append({
                                "date": str(dt_idx),
                                "eps_estimate": _safe_float(row.get("EPS Estimate")),
                                "reported_eps": reported,
                                "surprise_pct": surprise,
                            })
            except Exception:
                pass

            # ---- upcoming_earnings (from calendar API) ----
            upcoming = []
            try:
                cal = t.calendar
                if cal is not None:
                    if hasattr(cal, "to_dict"):
                        import pandas as pd
                        if isinstance(cal, pd.DataFrame) and not cal.empty:
                            for _, row in cal.iterrows():
                                upcoming.append({
                                    "date": str(row.get("Earnings Date", "")),
                                    "eps_estimate": _safe_float(row.get("Earnings Average", row.get("epsEstimate"))),
                                    "revenue_estimate": _safe_float(row.get("Revenue Average", row.get("revenueEstimate"))),
                                })
                    elif isinstance(cal, dict):
                        raw_date = cal.get("Earnings Date", "")
                        # Normalise: yfinance returns [datetime.date, ...] list
                        if isinstance(raw_date, (list, tuple)):
                            formatted_dates = []
                            for d in raw_date:
                                if hasattr(d, "isoformat"):
                                    formatted_dates.append(d.isoformat())
                                else:
                                    formatted_dates.append(str(d))
                            date_str = ", ".join(formatted_dates) if formatted_dates else ""
                        elif hasattr(raw_date, "isoformat"):
                            date_str = raw_date.isoformat()
                        else:
                            date_str = str(raw_date)
                        upcoming.append({
                            "date": date_str,
                            "eps_estimate": _safe_float(cal.get("Earnings Average")),
                            "revenue_estimate": _safe_float(cal.get("Revenue Average")),
                        })
            except Exception:
                pass

            return {
                "symbol": sym,
                "past_earnings": past_earnings,
                "calendar": calendar,
                "upcoming_earnings": upcoming,
                "eps_trailing": _safe_float(info.get("trailingEps")),
                "eps_forward": _safe_float(info.get("forwardEps")),
                "source": "yfinance",
            }

        except Exception as exc:
            logger.exception("get_earnings failed for %s", sym)
            return {"symbol": sym, "error": str(exc)}

    # =========================================================================
    # 4. get_historical_prices — kept unchanged
    # =========================================================================
    @tool
    async def get_historical_prices(
        symbol: str,
        period: str = "1mo",
        interval: str = "1d",
        fields: str = "all",
    ) -> dict:
        """Get historical OHLCV price data for a stock.

        Uses yfinance — free, no API key required.

        Args:
            symbol: Stock ticker symbol (e.g. "AAPL").
            period: Time period — "1d", "5d", "1mo", "3mo", "6mo", "1y",
                "2y", "5y", "10y", "ytd", "max". Default "1mo".
            interval: Data interval — "1m", "5m", "15m", "30m", "1h",
                "1d", "1wk", "1mo". Default "1d".
                Note: minute-level intervals only available for recent periods.
            fields: Which columns to return — "all" (default), "ohlc" or
                "close". Use "close" when you only need the series for
                valuation multiples: a long period such as "2y" at "1wk"
                stays well under the tool output cap instead of being
                clipped.

        Returns:
            A dict with:
            - ``symbol``: the stock ticker
            - ``period``, ``interval``: as requested
            - ``prices``: list of OHLCV dicts with ``date``, ``open``,
              ``high``, ``low``, ``close``, ``volume``, ``dividends``,
              ``stock_splits`` — narrowed to ``date`` + ``close`` for
              ``fields="close"``
            - ``count``: number of data points
            - ``source``: "yfinance"
        """
        import yfinance as yf

        sym = symbol.upper().strip()
        logger.info(
            "get_historical_prices: %s period=%s interval=%s fields=%s",
            sym, period, interval, fields,
        )

        wanted_fields = {
            "all": None,
            "close": {"date", "close"},
            "ohlc": {"date", "open", "high", "low", "close"},
        }
        selection = fields.strip().lower()
        if selection not in wanted_fields:
            return {
                "symbol": sym,
                "error": (
                    f"Invalid fields '{fields}'. "
                    "Must be one of: all, close, ohlc"
                ),
            }
        keep = wanted_fields[selection]

        try:
            t = await asyncio.to_thread(yf.Ticker, sym)
            df = await asyncio.to_thread(
                t.history, period=period, interval=interval
            )
            prices = _df_to_dicts(df)
            if keep is not None:
                prices = [
                    {k: v for k, v in row.items() if k.lower() in keep}
                    for row in prices
                ]
            return {
                "symbol": sym,
                "period": period,
                "interval": interval,
                "fields": selection,
                "prices": prices,
                "count": len(prices),
                "source": "yfinance",
            }

        except Exception as exc:
            logger.exception("get_historical_prices failed for %s", sym)
            return {"symbol": sym, "error": str(exc)}

    # =========================================================================
    # 5. get_dividends — kept unchanged
    # =========================================================================
    @tool
    async def get_dividends(symbol: str, period: str = "5y") -> dict:
        """Get dividend history for a stock.

        Uses yfinance — free, no API key required.

        Args:
            symbol: Stock ticker symbol (e.g. "AAPL").
            period: Time period for dividend history: "1y", "2y",
                "5y", "10y", "max". Default "5y".

        Returns:
            A dict with:
            - ``symbol``: the stock ticker
            - ``dividend_yield``: current dividend yield
            - ``dividend_rate``: annual dividend rate
            - ``payout_ratio``: dividend payout ratio
            - ``ex_dividend_date``: next ex-dividend date
            - ``history``: list of dicts with ``date`` and ``dividend``
            - ``count``: number of dividend payments
            - ``source``: "yfinance"
        """
        import yfinance as yf

        sym = symbol.upper().strip()
        logger.info("get_dividends: %s period=%s", sym, period)

        try:
            t = await asyncio.to_thread(yf.Ticker, sym)
            info = {}
            try:
                info = t.info or {}
            except Exception:
                pass

            divs = await asyncio.to_thread(lambda: t.dividends)
            import pandas as pd

            history = []
            if isinstance(divs, pd.Series) and not divs.empty:
                # Filter by period
                if period != "max":
                    now = pd.Timestamp.now(tz=divs.index.tz)
                    if period == "1y":
                        cutoff = now - pd.DateOffset(years=1)
                    elif period == "2y":
                        cutoff = now - pd.DateOffset(years=2)
                    elif period == "5y":
                        cutoff = now - pd.DateOffset(years=5)
                    elif period == "10y":
                        cutoff = now - pd.DateOffset(years=10)
                    else:
                        cutoff = None
                    if cutoff is not None:
                        divs = divs[divs.index >= cutoff]

                for dt, val in divs.items():
                    history.append({
                        "date": dt.isoformat() if hasattr(dt, "isoformat") else str(dt),
                        "dividend": _safe_float(val),
                    })

            return {
                "symbol": sym,
                "dividend_yield": _safe_float(info.get("dividendYield")),
                "dividend_rate": _safe_float(info.get("dividendRate")),
                "payout_ratio": _safe_float(info.get("payoutRatio")),
                "ex_dividend_date": str(info.get("exDividendDate", "")),
                "history": history,
                "count": len(history),
                "source": "yfinance",
            }

        except Exception as exc:
            logger.exception("get_dividends failed for %s", sym)
            return {"symbol": sym, "error": str(exc)}

    # =========================================================================
    # 6. get_company_news — new tool wrapping ticker.get_news()
    # =========================================================================
    @tool
    async def get_company_news(
        symbol: str,
        count: int | None = None,
        tab: str | None = None,
    ) -> dict:
        """Get recent news articles for a company from Yahoo Finance.

        Uses yfinance — free, no API key required.  Pulls from Yahoo
        Finance's news feed via an XHR endpoint.

        Args:
            symbol: Stock ticker symbol (e.g. "AAPL", "0700.HK").
            count: Number of articles to return (1-25, default 10).
            tab: News category — "news" (latest news, default),
                "press releases" (press releases only), or "all"
                (everything). Default "news".

        Returns:
            A dict with:
            - ``symbol``: the stock ticker
            - ``count``: number of articles returned
            - ``articles``: list of article dicts, each with:
              ``title``, ``published`` (ISO 8601), ``url``,
              ``provider`` (display name), ``summary`` (first
              300 characters of the article body)
            - ``source``: "yfinance"
        """
        import yfinance as yf

        sym = symbol.upper().strip()
        resolved_count = count if count is not None else 10
        resolved_count = max(1, min(resolved_count, 25))
        resolved_tab = tab if tab else "news"

        valid_tabs = {"news", "all", "press releases"}
        if resolved_tab.lower() not in valid_tabs:
            return {
                "symbol": sym,
                "error": f"Invalid tab '{tab}'. Must be one of: news, all, press releases",
            }

        logger.info(
            "get_company_news: %s count=%d tab=%s", sym, resolved_count, resolved_tab
        )

        try:
            t = await asyncio.to_thread(yf.Ticker, sym)
            raw_news = await asyncio.to_thread(
                t.get_news, count=resolved_count, tab=resolved_tab
            )

            articles = []
            if isinstance(raw_news, list):
                for item in raw_news:
                    content = item.get("content") if isinstance(item, dict) else {}
                    if not content:
                        continue
                    title = content.get("title") or ""
                    pub_date = content.get("pubDate") or ""
                    provider = ""
                    provider_obj = content.get("provider") if isinstance(content.get("provider"), dict) else {}
                    if isinstance(provider_obj, dict):
                        provider = provider_obj.get("displayName", "")
                    url = ""
                    ctu = content.get("clickThroughUrl") if isinstance(content.get("clickThroughUrl"), dict) else {}
                    if isinstance(ctu, dict):
                        url = ctu.get("url", "")
                    summary = (content.get("summary") or "")[:300]

                    articles.append({
                        "title": str(title),
                        "published": str(pub_date),
                        "url": str(url),
                        "provider": str(provider),
                        "summary": str(summary),
                    })

            return {
                "symbol": sym,
                "count": len(articles),
                "articles": articles,
                "source": "yfinance",
            }

        except Exception as exc:
            logger.exception("get_company_news failed for %s", sym)
            return {"symbol": sym, "error": str(exc)}

    return [
        get_stock_snapshot,
        get_financials,
        get_earnings,
        get_historical_prices,
        get_dividends,
        get_company_news,
    ]
