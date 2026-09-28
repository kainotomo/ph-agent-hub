"""Token-cost computation for workflow guardrails."""

from typing import Any


def prices_from_model(model: Any) -> dict[str, float]:
    """Return *model*'s per-1M prices as a plain, JSON-serialisable mapping.

    The workflow engine records this mapping in the cross-step shared state so
    that spend can be attributed to a step during a streaming run, where the
    live ``Model`` instance is not available.  Missing prices become ``0.0``.
    """
    if model is None:
        return {
            "input_price_per_1m": 0.0,
            "output_price_per_1m": 0.0,
            "cache_hit_price_per_1m": 0.0,
        }
    return {
        "input_price_per_1m": float(getattr(model, "input_price_per_1m", None) or 0),
        "output_price_per_1m": float(getattr(model, "output_price_per_1m", None) or 0),
        "cache_hit_price_per_1m": float(
            getattr(model, "cache_hit_price_per_1m", None) or 0
        ),
    }


def compute_run_cost_from_prices(
    prices: dict[str, float] | None,
    tokens_in: int,
    tokens_out: int,
    cache_hit_tokens: int,
) -> float:
    """Compute cost from a price mapping produced by :func:`prices_from_model`."""
    prices = prices or {}
    cost = (
        tokens_in * (prices.get("input_price_per_1m") or 0)
        + tokens_out * (prices.get("output_price_per_1m") or 0)
        + cache_hit_tokens * (prices.get("cache_hit_price_per_1m") or 0)
    ) / 1_000_000

    return float(cost)


def compute_run_cost(
    model: Any, tokens_in: int, tokens_out: int, cache_hit_tokens: int
) -> float:
    """Compute the monetary cost for a single agent run.

    Multiplies each token count by the corresponding per-1M price from the
    model, treating a missing/``None`` price as zero, and returns the sum
    divided by 1 000 000.  Returns a Python ``float``.

    Args:
        model: An ORM model instance (or similar object) exposing
            ``input_price_per_1m``, ``output_price_per_1m``, and
            ``cache_hit_price_per_1m`` attributes.
        tokens_in: Total input tokens (including cache hits).
        tokens_out: Total output tokens.
        cache_hit_tokens: Input tokens served from cache.

    Returns:
        The total cost as a float, or ``0.0`` if *model* is ``None``.
    """
    return compute_run_cost_from_prices(
        prices_from_model(model), tokens_in, tokens_out, cache_hit_tokens
    )
