# =============================================================================
# PH Agent Hub — Currency Exchange Tool Factory (frankfurter.dev)
# =============================================================================
# Builds MAF @tool-decorated async functions for currency conversion and
# exchange rates using the free frankfurter.dev API (ECB data).
# No API key required.  Uses httpx for async HTTP requests.
# =============================================================================

import logging

import httpx
from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FRANKFURTER_BASE: str = "https://api.frankfurter.dev"
DEFAULT_TIMEOUT: float = 15.0
DEFAULT_BASE_CURRENCY: str = "EUR"


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def build_currency_exchange_tools(tool_config: dict | None = None) -> list:
    """Return a list of MAF @tool-decorated currency exchange functions.

    Provides:
    - ``convert_currency``: convert an amount between two currencies
    - ``get_exchange_rates``: get latest rates for a base currency

    Args:
        tool_config: Optional ``Tool.config`` JSON dict.  May include:
            - ``base_currency`` (str): default base currency (default "EUR")
            - ``timeout`` (float): request timeout in seconds (default 15)

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}

    base_currency: str = config.get("base_currency", DEFAULT_BASE_CURRENCY).upper()
    timeout: float = float(config.get("timeout", DEFAULT_TIMEOUT))

    @tool
    async def convert_currency(
        amount: float,
        from_currency: str,
        to_currency: str,
    ) -> dict:
        """Convert an amount from one currency to another.

        Uses the European Central Bank's daily reference rates via
        frankfurter.dev — free, no API key required.

        Args:
            amount: The amount to convert (e.g. 100).
            from_currency: Source currency code (e.g. "USD", "EUR", "GBP").
            to_currency: Target currency code (e.g. "JPY", "CHF").

        Returns:
            A dict with:
            - ``amount``: the original amount
            - ``from_currency``: source currency code
            - ``to_currency``: target currency code
            - ``result``: the converted amount
            - ``rate``: the exchange rate used
            - ``date``: the date of the exchange rate
        """
        fc = from_currency.upper()
        tc = to_currency.upper()
        url = (
            f"{FRANKFURTER_BASE}/v1/latest"
            f"?amount={amount}&from={fc}&to={tc}"
        )
        logger.info("convert_currency: %.2f %s → %s", amount, fc, tc)

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "ph-agent-hub/1.0"},
                )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "convert_currency HTTP %d for %s→%s",
                exc.response.status_code,
                fc,
                tc,
            )
            return {
                "amount": amount,
                "from_currency": fc,
                "to_currency": tc,
                "error": (
                    f"Currency conversion failed (HTTP {exc.response.status_code}). "
                    "Check that the currency codes are valid."
                ),
            }
        except httpx.TimeoutException:
            logger.warning("convert_currency timeout for %s→%s", fc, tc)
            return {
                "amount": amount,
                "from_currency": fc,
                "to_currency": tc,
                "error": "Request timed out",
            }
        except Exception as exc:
            logger.exception("convert_currency failed for %s→%s", fc, tc)
            return {
                "amount": amount,
                "from_currency": fc,
                "to_currency": tc,
                "error": str(exc),
            }

        rates = data.get("rates", {})
        rate = rates.get(tc)
        result = amount * rate if rate else None

        return {
            "amount": amount,
            "from_currency": fc,
            "to_currency": tc,
            "result": round(result, 4) if result is not None else None,
            "rate": rate,
            "date": data.get("date", ""),
        }

    @tool
    async def get_exchange_rates(
        base: str | None = None,
    ) -> dict:
        """Get the latest exchange rates for a base currency.

        Uses the European Central Bank's daily reference rates via
        frankfurter.dev — free, no API key required.

        Args:
            base: Base currency code (e.g. "USD").
                Defaults to the tool's configured base currency.

        Returns:
            A dict with:
            - ``base``: the base currency
            - ``date``: the date of the rates
            - ``rates``: dict of currency code → rate
            - ``rate_count``: number of currencies available
        """
        b = (base or base_currency).upper()
        url = f"{FRANKFURTER_BASE}/v1/latest?from={b}"
        logger.info("get_exchange_rates: base=%s", b)

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "ph-agent-hub/1.0"},
                )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "get_exchange_rates HTTP %d for %s",
                exc.response.status_code,
                b,
            )
            return {
                "base": b,
                "error": (
                    f"Failed to get rates (HTTP {exc.response.status_code}). "
                    "Check that the base currency code is valid."
                ),
            }
        except httpx.TimeoutException:
            logger.warning("get_exchange_rates timeout for %s", b)
            return {"base": b, "error": "Request timed out"}
        except Exception as exc:
            logger.exception("get_exchange_rates failed for %s", b)
            return {"base": b, "error": str(exc)}

        rates = data.get("rates", {})
        return {
            "base": data.get("base", b),
            "date": data.get("date", ""),
            "rates": rates,
            "rate_count": len(rates),
        }

    return [convert_currency, get_exchange_rates]
