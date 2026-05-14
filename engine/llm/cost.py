"""Cost calculation.

OpenRouter returns the actual billed cost in `usage.cost` (USD). When
present we use that authoritatively. When absent (legacy OpenAI or
older OpenRouter response shapes) we fall back to the per-1k pricing
estimate already in `engine.models.llm_calls._PRICING_USD_PER_1K`.
"""
from __future__ import annotations

from typing import Any


def parse_cost(usage: dict[str, Any] | None) -> float | None:
    """Extract USD cost from a chat-completion usage dict.

    Returns None when neither a direct `cost` nor a `cost_usd` field
    is present — caller falls back to the estimate.
    """
    if not usage or not isinstance(usage, dict):
        return None
    # OpenRouter ships `cost` (USD)
    direct = usage.get("cost")
    if direct is None:
        direct = usage.get("cost_usd")
    if direct is None:
        return None
    try:
        return float(direct)
    except (TypeError, ValueError):
        return None


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Pricing-table-based estimate. Delegates to engine.models.llm_calls."""
    try:
        from engine.models.llm_calls import _estimate_cost
        return _estimate_cost(model, prompt_tokens, completion_tokens)
    except Exception:
        return 0.0
