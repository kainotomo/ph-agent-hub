# =============================================================================
# PH Agent Hub — Portfolio Analysis Tool Factory (numpy + scipy)
# =============================================================================
# Builds MAF @tool-decorated async functions for portfolio analysis,
# optimization, and efficient frontier computation.
# Pure computation — no API keys, no external data sources required.
# Uses numpy + scipy for numerical optimization.
# =============================================================================

import asyncio
import logging

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


def _get_returns(
    symbols: list[str],
    period: str = "1y",
) -> tuple:
    """Download historical prices and compute daily returns.

    Handles tickers from different exchanges (US, EU, etc.) by
    forward-filling missing dates so that non-trading days yield
    0% return rather than dropping the entire row.

    Returns:
        (returns_df, ticker_names, ticker_results) where:
        - returns_df: DataFrame of daily returns (rows=dates, cols=tickers),
          or None if no data at all.
        - ticker_names: dict mapping symbol -> company name.
        - ticker_results: list of dicts with ``symbol``, ``status``
          ("success" | "failed"), and ``reason`` (None on success, or
          "invalid_ticker", "no_data_for_period", "network_timeout").
    """
    import yfinance as yf

    prices_data = {}
    names = {}
    results = []

    def _fetch_one(sym: str):
        """Fetch closing prices for one symbol. Returns (sym, series, name, reason)."""
        try:
            t = yf.Ticker(sym)
            df = t.history(period=period)
            if not df.empty and "Close" in df.columns:
                return sym, df["Close"], t.info.get("shortName", sym), None
            return sym, None, sym, "no_data_for_period"
        except Exception:
            return sym, None, sym, "network_timeout"

    for sym in symbols:
        _, closes, name, reason = _fetch_one(sym)
        if closes is not None and len(closes) > 1:
            prices_data[sym] = closes
            names[sym] = name
            results.append({"symbol": sym, "status": "success", "reason": None})
        else:
            results.append({"symbol": sym, "status": "failed", "reason": reason or "no_data_for_period"})

    if not prices_data:
        return None, {}, results

    import pandas as pd
    prices_df = pd.DataFrame(prices_data)

    # Forward-fill for calendar alignment:
    # When tickers trade on different exchanges (NYSE, Xetra, LSE, etc.),
    # non-trading days get their last known price carried forward,
    # yielding 0% daily return rather than dropping the entire row.
    prices_df = prices_df.ffill()

    returns_df = prices_df.pct_change().dropna(how="all")

    return returns_df, names, results


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_portfolio_tools(tool_config: dict | None = None) -> list:
    """Return a list of MAF @tool-decorated portfolio analysis functions.

    Provides:
    - ``analyze_portfolio``: compute returns, volatility, Sharpe, Sortino,
      drawdown, VaR, beta, alpha, correlation, diversification
    - ``optimize_portfolio``: find optimal weights for Sharpe, min vol,
      or max return
    - ``efficient_frontier``: generate points on the efficient frontier

    Args:
        tool_config: Optional ``Tool.config`` JSON dict. May include:
            - ``risk_free_rate`` (float): annual risk-free rate (default 0.05)
            - ``default_period`` (str): default historical period (default "1y")

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}
    risk_free_rate: float = float(config.get("risk_free_rate", 0.05))
    default_period: str = config.get("default_period", "1y")

    @tool
    async def analyze_portfolio(
        symbols: list[str],
        weights: list[float] | None = None,
        benchmark_symbol: str = "^GSPC",
        period: str | None = None,
    ) -> dict:
        """Analyze a portfolio: returns, volatility, risk metrics, and more.

        Uses historical price data from yfinance (free) and pure numpy/scipy
        computation. No API key required.

        Args:
            symbols: List of stock/ETF ticker symbols (e.g. ["AAPL","MSFT"]).
            weights: Portfolio weights (must sum to ~1.0). If omitted,
                equal weight is used.
            benchmark_symbol: Benchmark ticker for beta/alpha (default "^GSPC"
                for S&P 500).
            period: Historical period (default "1y"). Options: "1mo", "3mo",
                "6mo", "1y", "2y", "5y", "max".

        Returns:
            A dict with:
            - ``symbols``, ``weights`` (used), ``benchmark``
            - ``annual_return``: annualized portfolio return
            - ``annual_volatility``: annualized standard deviation
            - ``sharpe_ratio``: excess return per unit of risk
            - ``sortino_ratio``: excess return per unit of downside risk
            - ``max_drawdown``: maximum peak-to-trough decline
            - ``var_95``: 95% Value at Risk (daily)
            - ``var_99``: 99% Value at Risk (daily)
            - ``beta``: vs benchmark
            - ``alpha``: vs benchmark (annualized)
            - ``correlation``: correlation with benchmark
            - ``diversification_score``: 0-1 measure of diversification
            - ``daily_returns``: list of daily portfolio returns (last 20)
            - ``source``: "yfinance + numpy/scipy"
        """
        import numpy as np

        p = period or default_period
        syms = [s.upper().strip() for s in symbols]

        if len(syms) == 0:
            return {"error": "At least one symbol is required"}

        # Default to equal weight
        w = weights
        if w is None:
            w = [1.0 / len(syms)] * len(syms)
        elif len(w) != len(syms):
            return {"error": f"Number of weights ({len(w)}) must match number of symbols ({len(syms)})"}

        # Normalize weights to sum to 1
        w_sum = sum(w)
        if w_sum == 0:
            return {"error": "Weights sum to zero"}
        w = [wi / w_sum for wi in w]

        # Get returns
        returns_df, names, ticker_results = await asyncio.to_thread(_get_returns, syms, p)

        if returns_df is None or returns_df.empty:
            succeeded = [r["symbol"] for r in ticker_results if r["status"] == "success"]
            failed = [{"symbol": r["symbol"], "reason": r["reason"]} for r in ticker_results if r["status"] == "failed"]
            return {
                "error": f"Failed to fetch price data for {len(failed)} of {len(syms)} symbols.",
                "succeeded": succeeded,
                "failed": failed,
            }

        # Align columns - only use symbols that have data
        available_syms = [s for s in syms if s in returns_df.columns]
        if not available_syms:
            return {"error": "No valid price data available for the given symbols"}

        # Adjust weights for available symbols
        idx_map = {s: i for i, s in enumerate(syms)}
        available_weights = [w[idx_map[s]] for s in available_syms]
        w_sum2 = sum(available_weights)
        if w_sum2 == 0:
            return {"error": "All weights for available symbols are zero"}
        available_weights = [wi / w_sum2 for wi in available_weights]

        returns_df = returns_df[available_syms]
        daily_returns = returns_df.dot(available_weights)
        mean_daily = float(daily_returns.mean())
        std_daily = float(daily_returns.std())

        # Annualize (252 trading days)
        ann_return = float((1 + mean_daily) ** 252 - 1)
        ann_vol = float(std_daily * np.sqrt(252))

        # Sharpe ratio
        excess_daily = mean_daily - (risk_free_rate / 252)
        sharpe = float(excess_daily / std_daily * np.sqrt(252)) if std_daily > 0 else 0.0

        # Sortino ratio (downside deviation)
        downside = daily_returns[daily_returns < 0]
        downside_std = float(downside.std()) if len(downside) > 0 else std_daily
        sortino = float(excess_daily / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

        # Max drawdown
        cumulative = (1 + daily_returns).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max
        max_dd = float(drawdown.min())

        # VaR
        var_95 = float(np.percentile(daily_returns, 5))
        var_99 = float(np.percentile(daily_returns, 1))

        # Beta and Alpha vs benchmark
        beta = None
        alpha = None
        correlation = None
        try:
            bench_ret_df, _, _ = await asyncio.to_thread(
                _get_returns, [benchmark_symbol], p
            )
            if bench_ret_df is not None and not bench_ret_df.empty:
                bench_col = bench_ret_df.columns[0]
                bench_returns = bench_ret_df[bench_col]
                # Align dates
                aligned = np.column_stack([
                    daily_returns.reindex(bench_returns.index).dropna(),
                    bench_returns.reindex(daily_returns.index).dropna(),
                ])
                # Use intersection
                common_idx = daily_returns.index.intersection(bench_returns.index)
                if len(common_idx) > 1:
                    port_aligned = daily_returns[common_idx]
                    bench_aligned = bench_returns[common_idx]
                    cov = np.cov(port_aligned, bench_aligned)
                    bench_var = float(bench_aligned.var())
                    if bench_var > 0:
                        beta = float(cov[0, 1] / bench_var)
                        # Annualized alpha
                        port_ann = float((1 + port_aligned.mean()) ** 252 - 1)
                        bench_ann = float((1 + bench_aligned.mean()) ** 252 - 1)
                        alpha = port_ann - risk_free_rate - beta * (bench_ann - risk_free_rate)
                    correlation = float(port_aligned.corr(bench_aligned))
        except Exception as exc:
            logger.warning("analyze_portfolio: benchmark calc failed: %s", exc)

        # Diversification score (1 - avg pairwise correlation of assets)
        div_score = 1.0
        if len(available_syms) > 1:
            import pandas as pd
            corr_matrix = returns_df.corr()
            # Average off-diagonal correlations
            n = len(available_syms)
            if n > 1:
                off_diag = []
                for i in range(n):
                    for j in range(i + 1, n):
                        off_diag.append(corr_matrix.iloc[i, j])
                avg_corr = float(np.mean(off_diag)) if off_diag else 1.0
                div_score = round(1.0 - avg_corr, 4)

        # Last 20 daily returns
        last_returns = [
            {"date": str(d), "return": round(float(r), 6)}
            for d, r in daily_returns.tail(20).items()
        ]

        # Map names
        symbol_names = {s: names.get(s, s) for s in available_syms}

        # Track any tickers that failed
        failed_tickers = [
            {"symbol": r["symbol"], "reason": r["reason"]}
            for r in ticker_results
            if r["status"] == "failed"
        ]

        return {
            "symbols": available_syms,
            "symbol_names": symbol_names,
            "weights": [round(wi, 4) for wi in available_weights],
            "benchmark": benchmark_symbol,
            "period": p,
            "annual_return": round(ann_return, 4),
            "annual_volatility": round(ann_vol, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "max_drawdown": round(max_dd, 4),
            "var_95": round(var_95, 4),
            "var_99": round(var_99, 4),
            "beta": round(beta, 4) if beta is not None else None,
            "alpha": round(alpha, 4) if alpha is not None else None,
            "correlation": round(correlation, 4) if correlation is not None else None,
            "diversification_score": div_score,
            "daily_returns_sample": last_returns,
            "risk_free_rate": risk_free_rate,
            "failed_tickers": failed_tickers or None,
            "source": "yfinance + numpy/scipy",
        }

    @tool
    async def optimize_portfolio(
        symbols: list[str],
        objective: str = "sharpe",
        constraints: dict | None = None,
        period: str | None = None,
    ) -> dict:
        """Find optimal portfolio weights via numerical optimization.

        Uses scipy.optimize under the hood. Pure computation — no API key.

        Args:
            symbols: List of stock/ETF ticker symbols (e.g. ["AAPL","MSFT"]).
            objective: "sharpe" (maximize Sharpe ratio), "min_vol" (minimize
                volatility), or "max_return" (maximize expected return).
                Default "sharpe".
            constraints: Optional dict with:
                - ``bounds``: tuple (min_weight, max_weight) per asset,
                  default (0.0, 1.0).
                - ``target_return``: required for min_vol when you want
                  to target a specific return.
                - ``target_vol``: required for max_return when you want
                  to cap volatility.
            period: Historical period (default "1y").

        Returns:
            A dict with:
            - ``symbols``, ``optimal_weights``
            - ``expected_annual_return``
            - ``expected_annual_volatility``
            - ``sharpe_ratio``
            - ``objective``
            - ``source``: "scipy.optimize"
        """
        import numpy as np
        from scipy.optimize import minimize

        p = period or default_period
        syms = [s.upper().strip() for s in symbols]

        if len(syms) < 2:
            return {"error": "At least 2 symbols are required for optimization"}

        # Get returns
        returns_df, names, ticker_results = await asyncio.to_thread(_get_returns, syms, p)

        if returns_df is None or returns_df.empty:
            succeeded = [r["symbol"] for r in ticker_results if r["status"] == "success"]
            failed = [{"symbol": r["symbol"], "reason": r["reason"]} for r in ticker_results if r["status"] == "failed"]
            return {
                "error": f"Failed to fetch price data for {len(failed)} of {len(syms)} symbols.",
                "succeeded": succeeded,
                "failed": failed,
            }

        available_syms = [s for s in syms if s in returns_df.columns]
        if len(available_syms) < 2:
            return {"error": "Need at least 2 symbols with valid data for optimization"}

        returns_df = returns_df[available_syms]
        mean_returns = returns_df.mean().values
        cov_matrix = returns_df.cov().values
        n = len(available_syms)

        # Constraints
        cons = constraints or {}
        bounds = cons.get("bounds", (0.0, 1.0))
        if isinstance(bounds, tuple):
            bounds = [bounds] * n

        target_return = cons.get("target_return")
        target_vol = cons.get("target_vol")

        # Constraint: weights sum to 1
        constraints_list = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

        if objective == "min_vol" and target_return is not None:
            constraints_list.append({
                "type": "eq",
                "fun": lambda w: np.dot(w, mean_returns) - target_return,
            })
        elif objective == "max_return" and target_vol is not None:
            constraints_list.append({
                "type": "ineq",
                "fun": lambda w: target_vol - np.sqrt(np.dot(w.T, np.dot(cov_matrix, w))),
            })

        def portfolio_volatility(w):
            return np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))

        def portfolio_return(w):
            return np.dot(w, mean_returns)

        def neg_sharpe(w):
            port_ret = portfolio_return(w)
            port_vol = portfolio_volatility(w)
            if port_vol == 0:
                return 0.0
            return -(port_ret - risk_free_rate / 252) / port_vol

        # Initial guess: equal weight
        w0 = np.array([1.0 / n] * n)

        try:
            if objective == "sharpe":
                result = minimize(
                    neg_sharpe, w0, bounds=bounds,
                    constraints=constraints_list, method="SLSQP"
                )
                opt_weights = result.x
            elif objective == "min_vol":
                result = minimize(
                    portfolio_volatility, w0, bounds=bounds,
                    constraints=constraints_list, method="SLSQP"
                )
                opt_weights = result.x
            elif objective == "max_return":
                result = minimize(
                    lambda w: -portfolio_return(w),
                    w0, bounds=bounds,
                    constraints=constraints_list, method="SLSQP"
                )
                opt_weights = result.x
            else:
                return {"error": f"Unknown objective '{objective}'. Use: sharpe, min_vol, max_return"}
        except Exception as exc:
            logger.exception("optimize_portfolio: optimization failed")
            return {"error": f"Optimization failed: {exc}"}

        # Clean up near-zero weights
        opt_weights = np.round(opt_weights, 8)
        opt_weights[opt_weights < 1e-6] = 0.0
        opt_weights = opt_weights / opt_weights.sum()

        opt_ret = float(portfolio_return(opt_weights))
        opt_vol = float(portfolio_volatility(opt_weights))
        opt_sharpe = float((opt_ret - risk_free_rate / 252) / opt_vol) if opt_vol > 0 else 0.0

        # Annualize
        ann_ret = float((1 + opt_ret) ** 252 - 1)
        ann_vol = float(opt_vol * np.sqrt(252))
        ann_sharpe = float(opt_sharpe * np.sqrt(252))

        return {
            "symbols": available_syms,
            "optimal_weights": [round(float(w), 4) for w in opt_weights],
            "expected_annual_return": round(ann_ret, 4),
            "expected_annual_volatility": round(ann_vol, 4),
            "sharpe_ratio": round(ann_sharpe, 4),
            "objective": objective,
            "risk_free_rate": risk_free_rate,
            "source": "scipy.optimize",
        }

    @tool
    async def efficient_frontier(
        symbols: list[str],
        num_portfolios: int = 50,
        period: str | None = None,
    ) -> dict:
        """Generate points on the Markowitz efficient frontier.

        Uses Monte Carlo simulation with numpy/scipy. Pure computation.

        Args:
            symbols: List of stock/ETF ticker symbols (e.g. ["AAPL","MSFT","GOOGL"]).
            num_portfolios: Number of simulated portfolios (default 50).
            period: Historical period (default "1y").

        Returns:
            A dict with:
            - ``symbols``
            - ``frontier``: list of dicts with ``volatility`` (annualized),
              ``return`` (annualized), ``sharpe_ratio``, ``weights``
            - ``max_sharpe_portfolio``: the portfolio with the highest Sharpe
            - ``min_vol_portfolio``: the minimum variance portfolio
            - ``source``: "numpy/scipy Monte Carlo"
        """
        import numpy as np

        p = period or default_period
        syms = [s.upper().strip() for s in symbols]

        if len(syms) < 2:
            return {"error": "At least 2 symbols are required"}

        returns_df, names, ticker_results = await asyncio.to_thread(_get_returns, syms, p)

        if returns_df is None or returns_df.empty:
            succeeded = [r["symbol"] for r in ticker_results if r["status"] == "success"]
            failed = [{"symbol": r["symbol"], "reason": r["reason"]} for r in ticker_results if r["status"] == "failed"]
            return {
                "error": f"Failed to fetch price data for {len(failed)} of {len(syms)} symbols.",
                "succeeded": succeeded,
                "failed": failed,
            }

        available_syms = [s for s in syms if s in returns_df.columns]
        if len(available_syms) < 2:
            return {"error": "Need at least 2 symbols with valid data"}

        returns_df = returns_df[available_syms]
        mean_returns = returns_df.mean().values
        cov_matrix = returns_df.cov().values
        n = len(available_syms)

        np.random.seed(42)
        results = []

        for _ in range(num_portfolios):
            w = np.random.random(n)
            w = w / w.sum()

            port_ret = np.dot(w, mean_returns)
            port_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))

            # Annualize
            ann_ret = (1 + port_ret) ** 252 - 1
            ann_vol = port_vol * np.sqrt(252)
            sharpe = (ann_ret - risk_free_rate) / ann_vol if ann_vol > 0 else 0.0

            results.append({
                "volatility": round(float(ann_vol), 4),
                "return": round(float(ann_ret), 4),
                "sharpe_ratio": round(float(sharpe), 4),
                "weights": [round(float(wi), 4) for wi in w],
            })

        # Sort by volatility
        results.sort(key=lambda r: r["volatility"])

        # Find max Sharpe and min vol
        max_sharpe = max(results, key=lambda r: r["sharpe_ratio"])
        min_vol = min(results, key=lambda r: r["volatility"])

        return {
            "symbols": available_syms,
            "frontier": results,
            "max_sharpe_portfolio": max_sharpe,
            "min_vol_portfolio": min_vol,
            "num_portfolios": num_portfolios,
            "risk_free_rate": risk_free_rate,
            "source": "numpy/scipy Monte Carlo",
        }

    return [analyze_portfolio, optimize_portfolio, efficient_frontier]
