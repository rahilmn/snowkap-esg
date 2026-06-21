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
# Keep in sync with data/ontology/criticality_weights.ttl ComparisonMarkers; this
# is the volume-shadow fallback when the bundled TTL is masked in prod.
_DEFAULT_COMPARISON_MARKERS: tuple[str, ...] = (
    " vs ", " vs. ", " versus ", "better bet", "better buy", "which stock",
    "which is better", "should you buy", "buy or sell", "stocks to watch",
    "stock to watch", "multibagger", "factors investors",
    "factors that investors", "is it a buy",
    # Phase 53.G — broker/price-target/stock-listicle market-speak.
    "target price", "price target", "raises target", "cuts target", "hikes target",
    "top picks", "power picks", "stock picks", "top stock",
    "turns bullish", "turns bearish", "bullish on", "bearish on", "stays bullish",
    "shares gain", "shares fall", "shares jump", "shares slump", "shares surge",
    "shares rise", "shares decline", "stock market news", "stocks riding",
    "stocks to buy", "stocks in news", "losing streak", "winning streak",
    "buy rating", "sell rating", "outperform", "underperform", "brokerage",
    # Phase 53.J — stock-update / broker / commodity / macro / IR-calendar noise
    # the live gpt-5 audit found in critical ("Brent Crude", "Shares Gain 0.94%
    # in Morning Trade", "Stock Target From Jefferies", "Chief backs RBI rate
    # pause", "CFO macro impact", "to participate in JM Financial Forum").
    "stock update", "morning trade", "stock target", "check the upside",
    "sees upside", "fair value", "initiates coverage", "initiate coverage",
    "maintains buy", "maintain buy", "stocks have declined", "stock declines",
    "brent crude", "crude oil", "crude prices", "rate pause", "rate hike",
    "rate cut", "repo rate", "mpc minutes", "monetary policy", "macro impact",
    "to participate in", "finance forum", "investor conference", "analyst day",
)

# Phase 53.G — "soft" market events that listicles/analyst pieces routinely
# mis-classify as. These ARE in ACTIONABLE_EVENT_TYPES (a genuine quarterly /
# dividend disclosure can trigger a real BRSR action), but they must NOT shield
# a market-framed headline from the commentary cap — otherwise "Five power grid
# stocks riding…" classified event_quarterly_results scores HIGH and crowds out
# genuine sector-ESG. A GENUINE quarterly/dividend article (no market framing)
# still returns False below and keeps its actionability.
_SOFT_MARKET_EVENTS: frozenset[str] = frozenset({
    "event_quarterly_results", "event_dividend_policy",
})

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
    of a real (HARD) actionable event. A genuine hard event (penalty, violation,
    contract, rating action, criminal/regulatory) carries an ``event_id`` in
    ``ACTIONABLE_EVENT_TYPES`` → returns False → never suppressed/demoted.

    Phase 53.G — the SOFT market events (quarterly_results / dividend_policy) are
    actionable too, but listicles routinely mis-classify as them, so they do NOT
    short-circuit: a market-framed headline on a soft event is still commentary,
    while a genuine quarterly/dividend article (no market framing) falls through
    comparison_framing → False and keeps its actionability.
    """
    from engine.analysis.criticality_scorer import ACTIONABLE_EVENT_TYPES

    eid = _event_id(result)
    if eid in ACTIONABLE_EVENT_TYPES and eid not in _SOFT_MARKET_EVENTS:
        return False
    return comparison_framing(getattr(result, "title", "") or "")
