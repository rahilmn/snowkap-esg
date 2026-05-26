"""Phase 28 / Feature 2 — Per-role analysis block.

For each role (cfo / ceo / esg-analyst), this module produces a 3-part
explainer answering the user's stated question:

  * ``why_important_for_me``  — why does THIS article matter to me?
  * ``how_it_impacts_business`` — concretely, how does it move the
                                 business (P&L, capital, reputation,
                                 compliance) — in their language
  * ``analysis_result``       — the recommended next action, one
                                 sentence

Deterministic baseline. Optional gpt-4.1-mini polish lives in
``role_generators/llm_upgrade.py`` (same pattern Phase 26 used for
the role-distinct view); when the env flag isn't set, the
deterministic strings ship.

Reads from the insight payload + EvidencePack + criticality components
+ personal_stakes_generator output (all of which are already stamped
on every HOME-tier insight). Pure-Python, no LLM cost in the default
path.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


ROLES = ("cfo", "ceo", "esg-analyst")


# ---------------------------------------------------------------------------
# Phase 29 — global criticality summary (role-agnostic top-line)
# ---------------------------------------------------------------------------


def _dominant_component(components: dict[str, Any]) -> tuple[str, float]:
    """Return ``(name, value)`` of the highest-scoring positive component.

    Used to anchor the criticality_summary on the strongest signal:
    "this is critical because financial_magnitude is at the top of the
    seven we score" reads better than a generic "this is critical
    because we computed a score."
    """
    positive_keys = (
        "materiality", "financial_magnitude", "actionability",
        "painpoint_match", "recency", "source_authority",
        "sentiment_trajectory",
    )
    best_name, best_val = "", -1.0
    for k in positive_keys:
        v = components.get(k)
        if isinstance(v, (int, float)) and float(v) > best_val:
            best_name, best_val = k, float(v)
    return best_name, max(best_val, 0.0)


_COMPONENT_LABELS = {
    "materiality": "the topic is highly material to the industry",
    "financial_magnitude": "the rupee impact is large relative to revenue",
    "actionability": "there is a concrete deadline you can act on",
    "painpoint_match": "it directly matches a tracked painpoint",
    "recency": "it just broke",
    "source_authority": "a top-tier source is reporting it",
    "sentiment_trajectory": "sentiment is trending the wrong way",
}


def build_criticality_summary(insight: dict[str, Any]) -> str:
    """Phase 29 — one-line global "why this is critical".

    Role-agnostic. Anchored on (a) the band (CRITICAL/HIGH/MEDIUM/LOW),
    (b) the dominant criticality component, and (c) the financial
    exposure when present. Never raises — empty inputs collapse to a
    short factual sentence.

    Example outputs:
      * "Critical — ₹150 Cr revenue at stake and financial_magnitude is
         the dominant signal."
      * "High priority — the topic is highly material to the industry."
      * "Worth reviewing — no urgent driver, but multiple weak signals
         agree."
    """
    crit = insight.get("criticality") or {}
    band = str(crit.get("band") or "").upper() or "MEDIUM"
    components = crit.get("components") or {}
    dom_name, dom_val = _dominant_component(components)
    exposure = _financial_exposure_text(insight)

    band_prefix = {
        "CRITICAL": "Critical",
        "HIGH": "High priority",
        "MEDIUM": "Worth reviewing",
        "LOW": "Low priority",
    }.get(band, "Worth reviewing")

    if exposure:
        # Lead with the exposure. The dominant signal is implied by the
        # band + the ₹ figure; appending "and X is the dominant signal"
        # produced grammatically broken sentences and added no value
        # (the reader knows financial magnitude is the driver because
        # they just read the rupee figure).
        sentence = f"{band_prefix} — {exposure}"
    elif dom_name and dom_val >= 0.5:
        sentence = f"{band_prefix} — {_COMPONENT_LABELS.get(dom_name, dom_name)}"
    else:
        sentence = f"{band_prefix} — multiple signals agree but no single dominant driver"

    # Ensure the sentence ends cleanly with a single full stop.
    sentence = sentence.rstrip(" .;,—-") + "."
    return sentence[:280]


def _financial_exposure_text(insight: dict[str, Any]) -> str:
    """Extract the ₹ exposure string from decision_summary, else ''.

    Strips the cross-section-drift normaliser's clarifier suffix
    (`(of ₹X Cr canonical event exposure)`) and any other dangling
    parens before returning, then truncates at the last full word so
    the criticality_summary never ends mid-sentence with an unbalanced
    paren. The clarifier is preserved on the underlying field for the
    audit trail — only the human-facing summary strips it.
    """
    import re
    decision = insight.get("decision_summary") or {}
    raw = ""
    for key in ("financial_exposure", "key_risk", "top_opportunity"):
        v = decision.get(key)
        if v:
            raw = str(v)
            break
    if not raw:
        return ""

    # Strip the cross-section-drift clarifier ("(of ₹X Cr canonical event exposure)").
    cleaned = re.sub(
        r"\s*\(of\s+₹[^)]*canonical[^)]*\)",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()

    # Collapse repeated "(engine estimate)" tags down to one trailing tag.
    cleaned = re.sub(
        r"(\s*\(engine estimate\))(\s*\(engine estimate\))+",
        r"\1",
        cleaned,
    )

    # Drop any unbalanced trailing open-paren clause (defensive — the
    # canonical-suffix stripper above should already catch this).
    open_count = cleaned.count("(")
    close_count = cleaned.count(")")
    if open_count > close_count:
        last_open = cleaned.rfind("(")
        if last_open > 0:
            cleaned = cleaned[:last_open].rstrip(" -—,;:")

    # Truncate at the last full word, hard cap 200 chars.
    if len(cleaned) > 200:
        cleaned = cleaned[:200].rsplit(" ", 1)[0]
    return cleaned


def _action_verb_for_role(role: str, polarity: str | None) -> str:
    """One-sentence action recommendation per role × event polarity."""
    polarity = (polarity or "neutral").lower()
    if role == "cfo":
        if polarity == "negative":
            return "Reserve contingency capital and brief the board within 30 days."
        if polarity == "positive":
            return "Quantify the upside in next earnings prep; consider capital re-allocation."
        return "Monitor financial exposure quarterly; no immediate action required."
    if role == "ceo":
        if polarity == "negative":
            return "Frame the response narrative for the next investor call; align with sustainability head."
        if polarity == "positive":
            return "Lead with this in stakeholder communications; reinforce competitive positioning."
        return "Track competitive movement; revisit at the next strategy review."
    if role == "esg-analyst":
        if polarity == "negative":
            return "File the disclosure update against the mapped framework section within the next reporting cycle."
        if polarity == "positive":
            return "Cite this as evidence in the upcoming framework disclosure narrative."
        return "Add to the watchlist; revisit on next ingest of the same theme."
    return ""


def _why_important_text(insight: dict[str, Any], role: str) -> str:
    """Short paragraph (one sentence, ≤140 chars). Reads from
    personal_stakes_generator output when available; falls back to
    criticality + EvidencePack values."""
    stakes = insight.get("stakes_for_company") or {}
    pct = stakes.get("revenue_pct_at_stake")
    exposure = _financial_exposure_text(insight)

    if role == "cfo":
        if exposure:
            return (
                f"{exposure} sits inside your direct P&L responsibility — "
                f"the financial signal is the strongest of the seven we score."
            )
        if pct is not None:
            return (
                f"~{pct:.1f}% of revenue is in scope. Direct line into your "
                "P&L and capital-allocation calls."
            )
        return "Financial magnitude is the dominant criticality signal here."
    if role == "ceo":
        ep = insight.get("event_polarity", "")
        if ep == "negative":
            return "Stakeholder + competitive positioning is at risk — board narrative needs framing."
        if ep == "positive":
            return "Reinforces your competitive moat — surface in next investor / board narrative."
        return "Strategic positioning signal — track for board-level optics next quarter."
    if role == "esg-analyst":
        return (
            "Framework disclosure obligation is the dominant lens here. "
            "Material under the mapped framework section."
        )
    return ""


def _how_it_impacts_text(insight: dict[str, Any], role: str) -> str:
    """2-3 sentences on the business mechanism."""
    impact = insight.get("impact_analysis") or {}
    if role == "cfo":
        valuation = impact.get("valuation_cashflow") or ""
        capital = impact.get("capital_allocation") or ""
        bits = [s.strip() for s in (valuation, capital) if s and s.strip()]
        if bits:
            return " ".join(bits[:2])[:280]
        return (
            "Direct hit on margins or revenue at risk; second-order effect on cost "
            "of capital via bondholder/lender pricing."
        )
    if role == "ceo":
        positioning = impact.get("esg_positioning") or ""
        people = impact.get("people_demand") or ""
        bits = [s.strip() for s in (positioning, people) if s and s.strip()]
        if bits:
            return " ".join(bits[:2])[:280]
        return (
            "Competitive positioning shifts; stakeholder confidence either lifts or "
            "erodes depending on response time."
        )
    if role == "esg-analyst":
        compliance = impact.get("compliance_regulatory") or ""
        supply = impact.get("supply_chain_transmission") or ""
        bits = [s.strip() for s in (compliance, supply) if s and s.strip()]
        if bits:
            return " ".join(bits[:2])[:280]
        return (
            "Disclosure deadline becomes load-bearing; framework section needs "
            "evidence alignment in the next reporting cycle."
        )
    return ""


def _simple_logic_text(insight: dict[str, Any], role: str) -> str:
    """One sentence: 'we flagged this because X was high for your role'."""
    crit = (insight.get("criticality") or {})
    components = crit.get("components") or {}
    role_score = (crit.get("role_scores") or {}).get(role)
    if role_score is None:
        return ""
    if role == "cfo":
        fm = components.get("financial_magnitude") or 0.0
        return (
            f"Flagged for you because financial_magnitude={fm:.2f} and the CFO "
            f"role weights financial 40%. Your role-specific score: {role_score:.2f}."
        )
    if role == "ceo":
        mat = components.get("materiality") or 0.0
        return (
            f"Flagged for you because materiality={mat:.2f} and the CEO role "
            f"weights materiality 25% + painpoint 25%. Your role score: {role_score:.2f}."
        )
    if role == "esg-analyst":
        act = components.get("actionability") or 0.0
        return (
            f"Flagged for you because actionability={act:.2f} and the Analyst "
            f"role weights actionability 15%. Your role score: {role_score:.2f}."
        )
    return ""


def build_role_explainer(insight: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Build the per-role explainer block.

    Returns ``{"cfo": {...}, "ceo": {...}, "esg-analyst": {...}}``
    where each role carries ``why_important_for_me``,
    ``how_it_impacts_business``, ``analysis_result``, and
    ``simple_logic``. Empty insight (REJECTED article, no criticality)
    produces empty strings rather than raising — the UI hides empty
    blocks.
    """
    out: dict[str, dict[str, str]] = {}
    polarity = insight.get("event_polarity")
    for role in ROLES:
        try:
            out[role] = {
                "why_important_for_me": _why_important_text(insight, role),
                "how_it_impacts_business": _how_it_impacts_text(insight, role),
                "analysis_result": _action_verb_for_role(role, polarity),
                "simple_logic": _simple_logic_text(insight, role),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "role_explainer: failed for role=%s (non-fatal): %s", role, exc,
            )
            out[role] = {
                "why_important_for_me": "",
                "how_it_impacts_business": "",
                "analysis_result": "",
                "simple_logic": "",
            }
    return out
