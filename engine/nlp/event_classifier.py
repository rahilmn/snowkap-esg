"""Event classifier — rule-based with ontology-sourced event types.

Queries the ontology for ``EventType`` instances (each with a score floor,
score ceiling, keyword list, and financial transmission note) and matches
them against the article text. Returns the matching event type plus score
bounds used by the deep insight generator to clamp LLM scores.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from engine.ontology.intelligence import EventRule, query_event_rules

logger = logging.getLogger(__name__)

# Financial quantum parsing (₹500 Cr, Rs 200 crore, 50 lakh, etc.)
AMOUNT_PATTERNS = [
    re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(cr|crore|lakh|million|billion|mn|bn)", re.IGNORECASE),
    re.compile(r"([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh|million|billion)", re.IGNORECASE),
]

UNIT_TO_CRORE = {
    "cr": 1.0,
    "crore": 1.0,
    "lakh": 0.01,
    "million": 0.083,  # ~₹ (USD 1M ≈ ₹8.3 Cr, assume INR million → Cr factor 0.1)
    "mn": 0.083,
    "billion": 83.0,
    "bn": 83.0,
}


@dataclass
class EventClassification:
    event_id: str
    label: str
    score_floor: int
    score_ceiling: int
    financial_transmission: str
    matched_keywords: list[str] = field(default_factory=list)
    has_financial_quantum: bool = False
    financial_amount_cr: float | None = None  # in ₹ Crore

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=1)
def _cached_rules() -> list[EventRule]:
    return query_event_rules()


def _match_keywords(text: str, rules: list[EventRule]) -> list[tuple[EventRule, list[str]]]:
    import re
    lowered = text.lower()
    matches: list[tuple[EventRule, list[str]]] = []
    for rule in rules:
        hit = []
        for kw in rule.keywords:
            kw_lower = kw.lower().strip()
            if not kw_lower:
                continue
            # Use word-boundary matching to avoid false positives
            # (e.g., "award" matching inside "towards")
            try:
                if re.search(r'\b' + re.escape(kw_lower) + r'\b', lowered):
                    hit.append(kw)
            except re.error:
                # Fallback to substring if regex fails
                if kw_lower in lowered:
                    hit.append(kw)
        if hit:
            matches.append((rule, hit))
    return matches


def _extract_financial_quantum(text: str) -> tuple[bool, float | None]:
    """Return (has_quantum, amount_in_crore) from article text."""
    for pattern in AMOUNT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        amount_raw = m.group(1).replace(",", "")
        try:
            amount = float(amount_raw)
        except ValueError:
            continue
        unit = m.group(2).lower()
        factor = UNIT_TO_CRORE.get(unit, 1.0)
        return True, amount * factor
    return False, None


# Phase 12.1: minimum classification confidence.
#
# Without this guard, a single generic keyword (e.g. "accountability") in a
# 2000-char article can pick a specific event type — triggering the wrong
# primitive cascade and causing the LLM to hallucinate a crisis narrative.
# The Waaree solar-auction article (2026-04-24) was a real example: a
# positive contract-win classified as event_ngo_report on one weak match.
#
# Rules:
#   - A match qualifies as "confident" if it hit on ≥ 2 distinct keywords,
#     OR at least one "specific" multi-word phrase (2+ tokens, ≥ 10 chars).
#   - If no rule clears the confidence bar, fall through to the theme-based
#     default (which was the behaviour for un-matched articles pre-Phase 12).
#   - Single-word, short-word matches ("strike", "fine", "audit") no longer
#     alone determine the event; they must stack with ≥ 1 other keyword or
#     a phrase.
_SPECIFIC_PHRASE_MIN_CHARS = 10
_SPECIFIC_PHRASE_MIN_TOKENS = 2


def _is_specific_phrase(kw: str) -> bool:
    """A keyword is 'specific' if it's a multi-word phrase of decent length.

    Examples of specific: "strait of hormuz", "child labour", "consent order".
    Examples of generic: "fine", "audit", "strike", "emissions", "accountability".
    """
    kw_stripped = kw.strip()
    if len(kw_stripped) < _SPECIFIC_PHRASE_MIN_CHARS:
        return False
    tokens = [t for t in kw_stripped.split() if t]
    return len(tokens) >= _SPECIFIC_PHRASE_MIN_TOKENS


def _is_confident_match(keywords: list[str]) -> bool:
    """A rule match is 'confident' if it has ≥ 2 keyword hits OR at least
    one specific multi-word phrase."""
    if len(keywords) >= 2:
        return True
    if keywords and _is_specific_phrase(keywords[0]):
        return True
    return False


def classify_event(
    title: str, content: str, theme: str = ""
) -> EventClassification:
    """Classify an article against ontology event types.

    Picks the rule with the highest specificity (most keyword matches) that
    clears the Phase 12.1 confidence bar (≥2 hits OR 1 specific phrase).
    Falls back to theme-based default when nothing matches confidently.
    Returns a default routine/ambiguous classification as last resort.
    """
    text = f"{title}\n{content}"
    rules = _cached_rules()

    has_quantum, amount_cr = _extract_financial_quantum(text)

    _default = EventClassification(
        event_id="event_default",
        label="Unclassified",
        score_floor=2,
        score_ceiling=6,
        financial_transmission="",
        has_financial_quantum=has_quantum,
        financial_amount_cr=amount_cr,
    )

    if not rules:
        logger.warning("event_classifier: ontology returned no event rules")
        return _default

    all_matches = _match_keywords(text, rules)
    # Phase 12.1 — keep only confident matches
    confident_matches = [
        (rule, kws) for rule, kws in all_matches if _is_confident_match(kws)
    ]

    if not confident_matches:
        if all_matches:
            logger.debug(
                "event_classifier: dropped %d weak single-keyword match(es) "
                "below confidence bar: %s",
                len(all_matches),
                [(r.event_id, kws) for r, kws in all_matches[:3]],
            )
        # Theme-based fallback (unchanged behaviour — Phase 14)
        if theme:
            from engine.ontology.intelligence import query_default_event_for_theme

            fallback = query_default_event_for_theme(theme)
            if fallback:
                return EventClassification(
                    event_id=fallback.event_id,
                    label=fallback.label,
                    score_floor=fallback.score_floor,
                    score_ceiling=fallback.score_ceiling,
                    financial_transmission=fallback.financial_transmission,
                    matched_keywords=["[theme_fallback]"],
                    has_financial_quantum=has_quantum,
                    financial_amount_cr=amount_cr,
                )
        return _default

    # Pick the confident rule with the highest keyword hit count.
    # Tie-breaker: prefer the rule with the most specific phrase matches,
    # then the higher score floor (more-severe rule wins ties).
    def _rank(pair):
        _rule, kws = pair
        specific_count = sum(1 for k in kws if _is_specific_phrase(k))
        return (len(kws), specific_count, _rule.score_floor)

    best_rule, best_keywords = max(confident_matches, key=_rank)

    return EventClassification(
        event_id=best_rule.event_id,
        label=best_rule.label,
        score_floor=best_rule.score_floor,
        score_ceiling=best_rule.score_ceiling,
        financial_transmission=best_rule.financial_transmission,
        matched_keywords=best_keywords,
        has_financial_quantum=has_quantum,
        financial_amount_cr=amount_cr,
    )


def enforce_score_bounds(
    raw_score: float, classification: EventClassification
) -> tuple[float, str | None]:
    """Clamp an LLM-generated impact score to the event's floor/ceiling.

    Also applies the "financial quantum guard rail": scores ≥ 7 require a
    specific ₹ amount in the article or they get reduced to 6.5.
    """
    score = max(classification.score_floor, min(classification.score_ceiling, raw_score))
    warning = None
    if score >= 7 and not classification.has_financial_quantum:
        score = min(score, 6.5)
        warning = "Score capped at 6.5 — no specific ₹ amount found in article"
    return round(score, 1), warning
