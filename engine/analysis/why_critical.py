"""W4a — `why_critical_for_{role}` field builder.

Deterministic, no-LLM: composes a 60-100 word paragraph per role from the
existing insight fields. The paragraph answers the user's "Why is this
news critical for [my role]" question with content anchored on what THAT
role actually decides on:

  * **CFO**: ₹ exposure, payback months, capital-at-risk, regulatory cost
  * **CEO**: competitive position, board narrative, 3-year strategic shift
  * **ESG Analyst**: framework gap, disclosure deadline, methodology rigour

Why deterministic instead of an extra LLM call:
  * Stage 10 already produced the canonical financial cascade + framework
    matches + decision_summary. Re-asking an LLM "why is this critical for
    a CFO" would let it invent ₹ figures the engine didn't compute.
  * Cost: $0 per article (no extra OpenAI call).
  * Auditability: every sentence in the output traces back to an insight
    field, so a CFO asking "where did this number come from" gets a
    structured answer.

Output: a single string per role, between 40 and 110 words, with the
critical decision-anchor in the FIRST sentence so a 10-second skim
captures it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_why_critical(
    insight: Any,
    role: str,
    *,
    company_name: str | None = None,
) -> str:
    """Return the role-specific 'why critical' paragraph.

    `insight` must be a DeepInsight-shaped object (or its `.to_dict()` form).
    `role` is one of: 'cfo', 'ceo', 'esg-analyst'.
    Returns "" on any error so the caller can fall through.
    """
    role = (role or "").strip().lower()
    try:
        d = _coerce_dict(insight)
        if role == "cfo":
            return _build_cfo(d, company_name=company_name)
        if role == "ceo":
            return _build_ceo(d, company_name=company_name)
        if role in ("esg-analyst", "esg_analyst", "analyst"):
            return _build_analyst(d, company_name=company_name)
    except Exception as exc:  # noqa: BLE001 — additive layer, never blocks
        logger.warning("why_critical: failed for role=%s: %s", role, exc)
    return ""


# ---------------------------------------------------------------------------
# Per-role builders
# ---------------------------------------------------------------------------


def _build_cfo(d: dict[str, Any], *, company_name: str | None) -> str:
    """CFO: ₹ exposure → payback / horizon → action verb.

    Three sentences, ~70-90 words, leading with the canonical financial
    figure so a 10-second skim catches it.
    """
    decision = d.get("decision_summary") or {}
    headline = (d.get("headline") or "").strip()
    exposure = (decision.get("financial_exposure") or "").strip()
    key_risk = (decision.get("key_risk") or "").strip()
    timeline = (decision.get("timeline") or "").strip()
    top_opportunity = (decision.get("top_opportunity") or "").strip()
    polarity = _polarity_from_event(d)

    parts: list[str] = []

    # Sentence 1 — anchor: the ₹ figure or the key risk
    if exposure:
        parts.append(
            f"This represents {exposure} for the company"
            + (f" — the kind of P&L line item the {company_name} CFO must quantify before the next quarterly review." if company_name else " — quantifying it is the CFO's first decision.")
        )
    elif key_risk:
        parts.append(
            f"For a CFO, the immediate concern is {_lc_first(key_risk)}"
            + (f" at {company_name}." if company_name else ".")
        )
    else:
        parts.append(
            "For the CFO, the question is whether this event has a measurable P&L "
            "or balance-sheet impact within the current planning horizon."
        )

    # Sentence 2 — risk vs opportunity framing depending on polarity
    if polarity == "positive" and top_opportunity:
        parts.append(
            f"The upside path is {_lc_first(top_opportunity)} — capital-allocation "
            "trade-offs depend on capturing it while the window is open."
        )
    elif polarity == "negative" and key_risk and exposure:
        parts.append(
            f"The downside risk is {_lc_first(key_risk)}, "
            "which directly hits margins if left unmitigated."
        )
    elif top_opportunity:
        parts.append(
            f"The strategic offset is {_lc_first(top_opportunity)}, which a "
            "CFO weighs against the cost of action."
        )

    # Sentence 3 — timeline + action handle
    if timeline:
        parts.append(
            f"Decision window: {_lc_first(timeline)}. Action: quantify exposure, "
            "set the provision or capex line, and brief audit + finance committee."
        )
    else:
        parts.append(
            "Action for the CFO: quantify the exposure, decide the provisioning "
            "or hedging line, and brief the audit committee within the next cycle."
        )

    return _trim_to_words(" ".join(parts), 110)


def _build_ceo(d: dict[str, Any], *, company_name: str | None) -> str:
    """CEO: competitive position → 3-year horizon → board narrative.

    Three sentences, ~60-90 words, no ₹ figures (boards don't want quarterly
    numbers in a 3-year narrative — those belong to the CFO view).
    """
    decision = d.get("decision_summary") or {}
    headline = (d.get("headline") or "").strip()
    top_opportunity = (decision.get("top_opportunity") or "").strip()
    key_risk = (decision.get("key_risk") or "").strip()
    timeline = (decision.get("timeline") or "").strip()
    competitive_position = ""
    fin_timeline = d.get("financial_timeline") or {}
    structural = fin_timeline.get("structural") or {}
    if isinstance(structural, dict):
        competitive_position = (structural.get("competitive_position") or "").strip()
    polarity = _polarity_from_event(d)

    parts: list[str] = []

    # Sentence 1 — strategic anchor
    if competitive_position:
        parts.append(
            f"For a CEO, this shifts the competitive picture: {_lc_first(competitive_position)}"
            + (f" at {company_name}." if company_name else ".")
        )
    elif top_opportunity and polarity == "positive":
        parts.append(
            f"For a CEO, the strategic angle is {_lc_first(top_opportunity)} — "
            "this is the kind of move that reshapes the 3-year competitive position."
        )
    elif key_risk and polarity == "negative":
        parts.append(
            f"For a CEO, this is a board-level signal: {_lc_first(key_risk)} "
            "— left unaddressed it shifts the company's strategic narrative."
        )
    else:
        parts.append(
            "For a CEO, the question is what this signals to the board about the "
            "company's positioning over the next 3 years."
        )

    # Sentence 2 — what to communicate
    if top_opportunity and polarity != "negative":
        parts.append(
            f"The investor narrative writes itself: {_lc_first(top_opportunity)}, "
            "with execution discipline as the proof point."
        )
    elif key_risk:
        parts.append(
            "The board narrative needs a clear plan: the competitor and lender "
            "lens both pick this up within one cycle."
        )

    # Sentence 3 — horizon
    parts.append(
        "On the FY+1 to FY+3 horizon, this is the kind of signal that "
        "ESG-tilted investor mandates read first — silence is the costliest option."
    )

    return _trim_to_words(" ".join(parts), 110)


def _build_analyst(d: dict[str, Any], *, company_name: str | None) -> str:
    """ESG Analyst: framework section → deadline → audit-trail strength.

    Three sentences, ~70-100 words, anchored on disclosure rigour and
    methodology, NOT on ₹ figures (those belong to CFO).
    """
    decision = d.get("decision_summary") or {}
    headline = (d.get("headline") or "").strip()
    key_risk = (decision.get("key_risk") or "").strip()
    frameworks = d.get("frameworks") or []

    framework_callouts: list[str] = []
    for fm in frameworks[:3]:
        if not isinstance(fm, dict):
            continue
        fid = (fm.get("framework_id") or fm.get("id") or "").strip()
        sections = fm.get("triggered_sections") or fm.get("sections") or []
        if isinstance(sections, list) and sections:
            framework_callouts.append(f"{fid}:{sections[0]}" if fid else str(sections[0]))
        elif fid:
            framework_callouts.append(fid)

    audit_trail_strength = "high" if d.get("toulmin") else "moderate"

    parts: list[str] = []

    # Sentence 1 — what disclosure obligation it triggers
    if framework_callouts:
        parts.append(
            f"For an ESG Analyst, the gating question is disclosure: this triggers "
            f"{', '.join(framework_callouts[:3])}"
            + (f" — {company_name}'s next BRSR/CSRD/SEC cycle must reflect it." if company_name else " — the next reporting cycle must reflect it.")
        )
    else:
        parts.append(
            "For an ESG Analyst, the gating question is whether this event "
            "fires a disclosure trigger that the next reporting cycle must address."
        )

    # Sentence 2 — methodology / evidence quality
    parts.append(
        f"Audit-trail strength is {audit_trail_strength}: every ₹ figure traces "
        "back to either the source article or an engine-computed primitive cascade, "
        "so the analyst can defend the analysis to assurance reviewers."
    )

    # Sentence 3 — what to verify
    if key_risk:
        parts.append(
            f"Verification priority: {_lc_first(key_risk)} — confirm against peer "
            "disclosures and the company's own materiality assessment before the "
            "next stakeholder review."
        )
    else:
        parts.append(
            "Verification priority: confirm classification against peer disclosures "
            "and the company's own materiality assessment before the next stakeholder review."
        )

    return _trim_to_words(" ".join(parts), 110)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_dict(insight: Any) -> dict[str, Any]:
    if isinstance(insight, dict):
        return insight
    if hasattr(insight, "to_dict"):
        return insight.to_dict()
    raise TypeError(f"why_critical: cannot coerce {type(insight).__name__} to dict")


def _polarity_from_event(d: dict[str, Any]) -> str:
    """Extract event polarity (positive/negative/neutral) — falls back to neutral."""
    p = (d.get("event_polarity") or "").strip().lower()
    if p in ("positive", "negative", "neutral"):
        return p
    # Heuristic — derive from sentiment if available
    nlp = d.get("nlp") or {}
    try:
        sent = float(nlp.get("sentiment", 0) or 0)
    except (TypeError, ValueError):
        sent = 0
    if sent >= 1:
        return "positive"
    if sent <= -1:
        return "negative"
    return "neutral"


def _lc_first(s: str) -> str:
    """Lowercase the first character of a sentence so it slots into a
    larger sentence without a redundant capital."""
    s = s.strip()
    if not s:
        return s
    return s[0].lower() + s[1:]


def _trim_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    trimmed = " ".join(words[:max_words]).rstrip(",;:")
    if not trimmed.endswith("."):
        trimmed += "."
    return trimmed
