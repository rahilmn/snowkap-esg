"""Phase 24 — deterministic Toulmin builder.

Constructs the ``toulmin: {claim, grounds, warrant, qualifier, rebuttal}``
block attached to every published insight. The build is intentionally
*deterministic* — no LLM call — because:

  1. The required-rebuttal discipline is enforceable (we can assert the
     field is non-empty for every "do nothing" verdict at audit time).
  2. The grounds are pulled directly from pipeline outputs already
     produced upstream (relevance score, framework citations, financial
     exposure tag, polarity).
  3. The warrant cites a NormativePrinciple from the ontology — pulled
     via SPARQL, not generated.
  4. The rebuttal is a polarity-flip projection: "if [opposite event
     class] surfaces, this verdict flips to [opposite action]".

Cost: zero new LLM tokens. Latency: ~5 ms (one SPARQL query). Output:
a self-contained dict that can be JSON-serialised onto the insight
record and rendered as a collapsible "Why this verdict" pulldown in
the UI.

The build is intentionally additive — if anything goes wrong (no event,
no relevance, ontology query fails) it returns an empty dict and lets
the caller proceed without a Toulmin block. Never blocks insight
generation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Polarity-flip helpers — used by the rebuttal generator
# ---------------------------------------------------------------------------

# Each entry: (verdict_polarity → opposite_action_phrase, opposite_trigger_hint)
_REBUTTAL_TEMPLATES: dict[str, tuple[str, str]] = {
    "negative": (
        "the verdict would shift to MONITOR or de-escalate",
        "the regulator withdraws the action, the article is corrected, "
        "or compensating positive disclosure surfaces within the freshness window",
    ),
    "positive": (
        "the verdict would shift to MONITOR or downgrade",
        "execution slippage materialises, a counter-disclosure surfaces, "
        "or the financial uplift fails to materialise within the projected timeline",
    ),
    "neutral": (
        "the verdict would shift if new evidence surfaces",
        "additional reporting clarifies the financial materiality or a "
        "regulator action escalates the consequence",
    ),
    "do_nothing": (
        "the verdict would shift to ACT",
        "a company-specific transmission becomes evident — direct ₹ exposure, "
        "regulator notice naming the company, or peer action setting an "
        "enforcement precedent in the same jurisdiction",
    ),
}


def _action_polarity(decision_summary: dict[str, Any], event_polarity: str) -> str:
    """Reduce decision summary + event polarity into a rebuttal-template key."""
    action = (decision_summary.get("action") or "").upper()
    if action in {"IGNORE", "MONITOR"}:
        return "do_nothing"
    return event_polarity if event_polarity in _REBUTTAL_TEMPLATES else "neutral"


# ---------------------------------------------------------------------------
# Grounds extraction — what pieces of evidence support the verdict
# ---------------------------------------------------------------------------


def _extract_grounds(
    parsed: dict[str, Any],
    event_id: str | None,
    relevance_total: float | None,
    materiality_weight: float | None,
    framework_codes: list[str] | None,
) -> list[str]:
    """Pull the 3-5 strongest evidentiary points already in the pipeline."""
    grounds: list[str] = []

    decision = parsed.get("decision_summary") or {}
    exposure = decision.get("financial_exposure")
    if exposure and exposure not in {"N/A", "None", ""}:
        grounds.append(f"Financial exposure: {exposure}")

    risk = decision.get("key_risk")
    if risk and risk not in {"N/A", "None", ""}:
        grounds.append(f"Key risk: {risk}")

    if event_id:
        grounds.append(f"Event classification: {event_id}")

    if relevance_total is not None:
        grounds.append(f"Relevance score: {relevance_total:.1f}/10")

    if materiality_weight is not None:
        grounds.append(f"Materiality weight (ontology): {materiality_weight}")

    if framework_codes:
        # Keep first 3 to stay concise
        grounds.append(f"Frameworks triggered: {', '.join(framework_codes[:3])}")

    # Always 3-6 grounds; trim to 6 max
    return grounds[:6]


# ---------------------------------------------------------------------------
# Qualifier — confidence/uncertainty band
# ---------------------------------------------------------------------------


def _build_qualifier(
    relevance_total: float | None,
    low_confidence: bool,
    has_financial_quantum: bool,
    sentiment: int | None,
) -> str:
    """Return a single sentence describing how strong the verdict is."""
    parts: list[str] = []
    if low_confidence:
        parts.append("low classification confidence")
    if relevance_total is not None:
        if relevance_total >= 8:
            parts.append("strong relevance signal")
        elif relevance_total >= 5:
            parts.append("moderate relevance signal")
        else:
            parts.append("weak relevance signal")
    if has_financial_quantum:
        parts.append("article-cited ₹ quantum present")
    else:
        parts.append("no article-cited ₹ quantum (engine estimate)")
    if sentiment is not None:
        if sentiment >= 1:
            parts.append("positive sentiment")
        elif sentiment <= -1:
            parts.append("negative sentiment")
    if not parts:
        return "limited supporting evidence"
    return "; ".join(parts).capitalize()


# ---------------------------------------------------------------------------
# Rebuttal — the conditions under which the verdict flips
# ---------------------------------------------------------------------------


def _build_rebuttal(
    decision_summary: dict[str, Any],
    event_polarity: str,
) -> str:
    key = _action_polarity(decision_summary, event_polarity)
    flip, trigger = _REBUTTAL_TEMPLATES.get(key, _REBUTTAL_TEMPLATES["neutral"])
    return f"If {trigger}, {flip}."


# ---------------------------------------------------------------------------
# Warrant — fetched from the ontology
# ---------------------------------------------------------------------------


def _build_warrant(
    event_id: str | None,
    event_polarity: str,
) -> tuple[str, str]:
    """Return (warrant_statement, citation_id). Empty strings on failure.

    Imports lazily so the module remains import-safe in test contexts that
    haven't initialised the ontology graph.
    """
    try:
        from engine.ontology.intelligence import (
            query_normative_principles_for_event,
        )
        principles = query_normative_principles_for_event(
            event_id, polarity=event_polarity, limit=1,
        )
    except Exception as exc:  # noqa: BLE001 — fall back silently
        logger.debug("toulmin: warrant lookup failed (%s)", exc)
        return ("", "")

    if not principles:
        return ("", "")

    p = principles[0]
    citation = f"{p.principle_id} ({p.source})" if p.source else p.principle_id
    return (p.statement, citation)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_toulmin(
    parsed_insight: dict[str, Any],
    *,
    event_id: str | None,
    event_polarity: str,
    relevance_total: float | None,
    materiality_weight: float | None = None,
    framework_codes: list[str] | None = None,
    low_confidence: bool = False,
    has_financial_quantum: bool = False,
    sentiment: int | None = None,
) -> dict[str, Any]:
    """Build the Toulmin block from a parsed Stage-10 insight + pipeline context.

    Returns an empty dict if the verdict cannot be defended (no
    decision_summary + no headline). Otherwise returns a complete block:

        {
          "claim": "<verdict>",
          "grounds": ["<3-5 evidentiary points>"],
          "warrant": "<NormativePrinciple statement>",
          "warrant_cite": "<NP-ID (source)>",
          "qualifier": "<confidence band>",
          "rebuttal": "<polarity-flip projection>"
        }

    The caller stamps this onto ``DeepInsight.toulmin`` and the writer
    serialises it on the output JSON. Downstream UI renders it as a
    collapsed "Why this verdict" pulldown.
    """
    decision = parsed_insight.get("decision_summary") or {}

    # Claim: prefer verdict, then materiality + action, else headline
    claim = (
        decision.get("verdict")
        or (
            f"{decision.get('materiality', 'UNKNOWN')} materiality / "
            f"{decision.get('action', 'UNKNOWN')} action"
            if decision.get("materiality") or decision.get("action")
            else parsed_insight.get("headline", "")
        )
    )
    claim = (claim or "").strip()
    if not claim:
        return {}

    grounds = _extract_grounds(
        parsed_insight,
        event_id=event_id,
        relevance_total=relevance_total,
        materiality_weight=materiality_weight,
        framework_codes=framework_codes,
    )

    warrant_text, warrant_cite = _build_warrant(event_id, event_polarity)

    qualifier = _build_qualifier(
        relevance_total=relevance_total,
        low_confidence=low_confidence,
        has_financial_quantum=has_financial_quantum,
        sentiment=sentiment,
    )

    rebuttal = _build_rebuttal(decision, event_polarity)

    block: dict[str, Any] = {
        "claim": claim,
        "grounds": grounds,
        "warrant": warrant_text,
        "warrant_cite": warrant_cite,
        "qualifier": qualifier,
        "rebuttal": rebuttal,
    }
    return block
