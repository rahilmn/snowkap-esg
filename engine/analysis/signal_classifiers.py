"""Deterministic signal classifiers for the criticality + recommendation layer.

``is_market_commentary(result)`` flags articles that are investor/market
commentary — stock comparisons, "which is a better bet" listicles, buy/sell
opinion — rather than material ESG events. Such articles must NOT force a
compliance/IR action (the product rule: macro/market signals → "monitor /
do nothing" is a valid ESG output) and must NOT outrank genuine ESG signals in
the feed.

The discriminator that protects genuine events: an article carrying a real
event (a contract win, penalty, violation, rating action, …) classifies to an
``event_id`` in ``ACTIONABLE_EVENT_TYPES``; market commentary lands on
``event_default``. So both the action gate and the materiality demotion are
gated on ``not is_actionable`` — a real multi-company event ("Adani Power wins
₹5000 Cr tender", "NTPC fined for emissions") is never suppressed.

Comparison-framing markers ("vs", "better bet", "which stock", …) are
linguistic/domain knowledge → seeded in the ontology (``query_comparison_markers``)
per CLAUDE.md Rule #1, with a small in-code default so a missing/edited TTL
degrades safely.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# In-code default — overridden by the ontology when the TTL provides markers.
_DEFAULT_COMPARISON_MARKERS: tuple[str, ...] = (
    " vs ", " vs. ", " versus ", "better bet", "better buy", "which stock",
    "which is better", "should you buy", "buy or sell", "stocks to watch",
    "stock to watch", "multibagger", "factors investors",
    "factors that investors", "is it a buy",
)

_markers_cache: tuple[str, ...] | None = None


def _comparison_markers() -> tuple[str, ...]:
    """Lowercased comparison markers — ontology first, in-code default otherwise.
    Cached per process; the marker set is static at runtime."""
    global _markers_cache
    if _markers_cache is not None:
        return _markers_cache
    markers: list[str] = []
    try:
        from engine.ontology.intelligence import query_comparison_markers
        markers = [m.lower() for m in (query_comparison_markers() or []) if m]
    except Exception:  # noqa: BLE001 — degrade to the built-in defaults
        logger.debug("comparison markers: ontology unavailable; using defaults", exc_info=True)
    _markers_cache = tuple(markers) if markers else _DEFAULT_COMPARISON_MARKERS
    return _markers_cache


def comparison_framing(title: str | None) -> bool:
    """True when the headline reads as a stock/market comparison or buy-sell
    opinion piece (e.g. "Adani Power vs NTPC — which is a better bet?")."""
    if not title:
        return False
    t = f" {title.lower()} "
    return any(m in t for m in _comparison_markers())


def _event_id(result: Any) -> str:
    event = getattr(result, "event", None)
    eid = getattr(event, "event_id", None) if event is not None else None
    return eid or "event_default"


def is_market_commentary(result: Any) -> bool:
    """True for investor/market commentary that must not force a compliance
    action or outrank genuine ESG signals.

    Tight + safe: requires BOTH a comparison-framing headline AND the absence
    of a real (actionable) event. A genuine multi-company event carries an
    ``event_id`` in ``ACTIONABLE_EVENT_TYPES`` → returns False → never
    suppressed/demoted.
    """
    from engine.analysis.criticality_scorer import ACTIONABLE_EVENT_TYPES

    if _event_id(result) in ACTIONABLE_EVENT_TYPES:
        return False
    return comparison_framing(getattr(result, "title", "") or "")
