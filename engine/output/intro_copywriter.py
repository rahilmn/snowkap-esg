"""Phase 11C — Stakes-first intro paragraph for the email body.

The Phase 10 default (`"An ESG signal on {company} worth your two minutes.
Below: the bottom line, why it's material, and a link to the full brief."`)
reads like a polite newsletter. CFOs skim past it.

Stakes-first version opens with the ₹ exposure + materiality + deadline
the recipient would otherwise miss. Editorial voice, no self-referencing
("our ontology-driven engine"), no salesy padding.

Falls back gracefully when the insight is missing fields — we never want to
render a half-complete sentence, so each missing field triggers a shorter
but still-punchy variant.
"""

from __future__ import annotations

import re
from typing import Any

from engine.output.subject_line import _detect_regulator, _extract_exposure_cr, _extract_margin_bps


def _pick_timeline(insight: dict[str, Any]) -> str | None:
    """Return a human-readable timeline like 'within 4 weeks' if one is
    implied by the insight. Otherwise None."""
    decision = insight.get("decision_summary") or {}
    timeline = decision.get("timeline") or ""
    if timeline:
        return str(timeline).strip()

    text = " ".join([
        insight.get("headline") or "",
        decision.get("key_risk") or "",
        insight.get("net_impact_summary") or "",
    ])
    m = re.search(r"within\s+(\d+)\s+(days?|weeks?|months?|quarters?)", text, flags=re.I)
    if m:
        return m.group(0)
    m = re.search(r"by\s+Q[1-4]\s+\d{4}", text)
    if m:
        return m.group(0)
    return None


def _one_line_why_now(insight: dict[str, Any], company: str) -> str:
    """Single follow-on sentence. Pull core_mechanism or top_opportunity."""
    decision = insight.get("decision_summary") or {}
    mech = insight.get("core_mechanism") or ""
    top = decision.get("top_opportunity") or ""
    if mech:
        # Clamp to ~140 chars, ending on a word boundary
        if len(mech) > 160:
            mech = mech[:160].rsplit(" ", 1)[0] + "…"
        return mech
    if top:
        return f"Meanwhile, {top}"
    return f"Here's what {company} is signalling and why it matters now."


def build_intro(
    company_name: str,
    insight: dict[str, Any],
    article: dict[str, Any] | None = None,
) -> str:
    """Return a stakes-first intro paragraph for the email body.

    Pattern (all pieces optional — the final string skips absent ones):
        "{₹-exposure} in {materiality} impact — {regulator}/{bps}/{timeline}.
         {one-line why-now}. Here's the brief."

    Example:
        "₹275 Cr in HIGH impact — SEBI enforcement within 4 weeks,
         49 bps margin compression likely. The board must approve a
         remediation plan within four weeks to avoid precedent risk.
         Here's the brief."
    """
    article = article or {}
    decision = insight.get("decision_summary") or {}
    materiality = (decision.get("materiality") or "").upper() or None

    exposure = _extract_exposure_cr(insight)
    bps = _extract_margin_bps(insight)
    timeline = _pick_timeline(insight)
    text = " ".join([
        insight.get("headline") or article.get("title") or "",
        decision.get("key_risk") or "",
    ])
    regulator = _detect_regulator(text)

    # Build the stakes sentence — compose from what we have, skip what we don't
    stakes_parts: list[str] = []
    if exposure and materiality:
        stakes_parts.append(f"**{exposure} in {materiality.title()} impact**")
    elif exposure:
        stakes_parts.append(f"**{exposure} in ESG impact**")
    elif materiality:
        stakes_parts.append(f"**{materiality.title()} materiality flagged**")

    qualifiers: list[str] = []
    if regulator and timeline:
        qualifiers.append(f"{regulator} action {timeline}")
    elif regulator:
        qualifiers.append(f"{regulator} enforcement risk")
    elif timeline:
        qualifiers.append(timeline)
    if bps:
        qualifiers.append(f"{bps} margin compression")

    stakes_sentence = ""
    if stakes_parts:
        stakes_sentence = stakes_parts[0]
        if qualifiers:
            stakes_sentence += " — " + ", ".join(qualifiers)
        stakes_sentence += "."

    why_now = _one_line_why_now(insight, company_name)

    if stakes_sentence:
        return f"{stakes_sentence} {why_now} Here's the brief."
    # No usable signal (shouldn't happen since the runner's accuracy gate
    # catches this) — fall back to the original Phase 10 copy.
    return (
        f"An ESG signal on {company_name} worth your two minutes. "
        f"Below: the bottom line, why it's material, and a link to the full brief."
    )
