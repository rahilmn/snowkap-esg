"""Phase 11C — Editorial subject line generator.

The default Phase 10 pattern (`Snowkap ESG · {Company} · {Headline}`) reads
like a table-of-contents; CFOs scan past it. Editorial subject lines open
with stakes (₹ exposure, regulator, deadline) so the reader feels the
cost-of-ignoring BEFORE deciding whether to click.

Hybrid strategy (per the approved Phase 11 plan):
  * **Ontology templates** (free, deterministic) for materiality ∈
    {LOW, MODERATE, NON-MATERIAL} and for any article where the LLM path
    hits a quota / error.
  * **LLM subject line** (`gpt-4.1-mini`, ~$0.0005/call) for
    HIGH + CRITICAL articles where open-rate matters most. Cached per
    article_id so the cost is paid once per article regardless of resends.

Both paths cap output at 90 chars (iPhone preview limit).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

MAX_LEN = 90


# ---------------------------------------------------------------------------
# Template patterns — ordered by event type preference
# ---------------------------------------------------------------------------


_REGULATOR_ALIASES = {
    "SEBI": "SEBI", "RBI": "RBI", "EPA": "EPA", "FTC": "FTC",
    "MoEFCC": "MoEFCC", "NGT": "NGT", "CPCB": "CPCB",
    "SEC": "SEC", "FCA": "FCA", "ESMA": "ESMA",
    "SBTi": "SBTi", "ISSB": "ISSB",
}


def _detect_regulator(text: str) -> str | None:
    """Pick the first regulator acronym mentioned in text (title/bottom-line)."""
    for key in _REGULATOR_ALIASES:
        # Word-boundary match so we don't grab 'RBI' from 'scrubbing'
        if re.search(rf"\b{re.escape(key)}\b", text):
            return _REGULATOR_ALIASES[key]
    return None


def _extract_exposure_cr(insight: dict[str, Any]) -> str | None:
    """Return the ₹ figure from decision_summary.financial_exposure if
    it looks like a Cr number (e.g. '₹275 Cr', '₹1.2K Cr'). Trims noise."""
    decision = insight.get("decision_summary") or {}
    raw = decision.get("financial_exposure") or ""
    if not raw:
        # Fall back to the first ₹ figure in the headline / bottom-line
        for candidate in (insight.get("headline", ""), decision.get("key_risk", "")):
            m = re.search(r"₹[\d,.]+\s*(Cr|cr|crore|Crore|K\s*Cr)", candidate)
            if m:
                return m.group(0).strip()
        return None
    m = re.search(r"₹[\d,.]+\s*(Cr|cr|crore|Crore|K\s*Cr)", raw)
    return m.group(0).strip() if m else None


def _extract_margin_bps(insight: dict[str, Any]) -> str | None:
    """First 'N bps' figure from the insight, if any."""
    for candidate in (
        insight.get("headline", ""),
        (insight.get("decision_summary") or {}).get("key_risk", ""),
        insight.get("net_impact_summary", ""),
    ):
        m = re.search(r"(\d+(?:\.\d+)?)\s*bps", candidate)
        if m:
            return m.group(0).strip()
    return None


def _truncate(s: str, limit: int = MAX_LEN) -> str:
    s = s.strip().rstrip(" .-—")
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Template patterns
# ---------------------------------------------------------------------------


def _template_compliance(company: str, insight: dict[str, Any]) -> str | None:
    """₹X Cr exposure flagged — {regulator} enforcement."""
    ex = _extract_exposure_cr(insight)
    text = (insight.get("headline", "") + " "
            + (insight.get("decision_summary") or {}).get("key_risk", ""))
    reg = _detect_regulator(text)
    if ex and reg:
        return f"{company}: {ex} exposure flagged — {reg} enforcement risk"
    if ex:
        return f"{company}: {ex} compliance exposure flagged — material risk"
    if reg:
        return f"{company}: {reg} enforcement action — material ESG risk flagged"
    return None


def _template_disclosure(company: str, insight: dict[str, Any]) -> str | None:
    """{Framework} deadline: {Company} missing {section} — {bps} bps margin risk."""
    bps = _extract_margin_bps(insight)
    # Probe for a common framework reference in headline / core_mechanism
    text = " ".join([
        insight.get("headline", ""),
        insight.get("core_mechanism", ""),
        (insight.get("decision_summary") or {}).get("key_risk", ""),
    ])
    for fw in ("BRSR", "TCFD", "CSRD", "ESRS", "GRI", "SASB", "ISSB", "SBTi"):
        if re.search(rf"\b{fw}\b", text):
            if bps:
                return f"{company}: {fw} disclosure gap — {bps} margin risk"
            return f"{company}: {fw} compliance gap flagged — board-level ESG risk"
    return None


def _template_opportunity(company: str, insight: dict[str, Any]) -> str | None:
    ex = _extract_exposure_cr(insight)
    if ex:
        return f"{company}'s {ex} bet on sustainability — CFO angle"
    return None


def _template_positive_rating(company: str, insight: dict[str, Any]) -> str | None:
    """Phase 22.3 — positive-event subject: ESG rating upgrade, certification,
    capacity addition, contract win, green-finance milestone. Frames the ₹
    figure as upside (valuation benefit / capital uplift) rather than risk.
    """
    ex = _extract_exposure_cr(insight)
    headline = insight.get("headline") or ""
    decision = insight.get("decision_summary") or {}
    text_blob = " ".join([
        headline,
        decision.get("top_opportunity", ""),
        insight.get("core_mechanism", ""),
    ]).lower()
    # Sniff the kind of positive event for tighter wording
    if any(w in text_blob for w in ("rating", "esg score", "esg 1+", "esg 2+", "djsi", "msci")):
        kind = "ESG rating upgrade"
    elif any(w in text_blob for w in ("contract", "ppa", "auction", "tender", "order book")):
        kind = "contract win"
    elif any(w in text_blob for w in ("capacity", "commission", "plant", "mw ", "gw ")):
        kind = "capacity addition"
    elif any(w in text_blob for w in ("green bond", "sustainability-linked", "ssa")):
        kind = "green finance milestone"
    elif any(w in text_blob for w in ("certification", "iso 14001", "platinum", "gold standard")):
        kind = "ESG certification"
    else:
        kind = "ESG signal"

    if ex:
        return f"{company}: {ex} valuation upside · {kind}"
    return f"{company}: positive {kind} — investor brief"


def _is_positive_event(insight: dict[str, Any]) -> bool:
    """Phase 22.3 — read the polarity flag set by insight_generator.
    Falls back to inspecting decision_summary if the flag is missing
    (e.g. on cached pre-Phase-22 articles)."""
    p = (insight.get("event_polarity") or "").lower()
    if p == "positive":
        return True
    if p in ("negative", "neutral"):
        return False
    # Fallback: positive if action="ACT" + non-empty top_opportunity AND
    # NO crisis language in key_risk
    decision = insight.get("decision_summary") or {}
    top_opp = (decision.get("top_opportunity") or "").strip()
    key_risk = (decision.get("key_risk") or "").lower()
    crisis_markers = ("penalty", "fine", "violation", "breach", "lawsuit",
                      "scn", "downgrade", "exposure", "regulator")
    has_crisis = any(m in key_risk for m in crisis_markers)
    return bool(top_opp) and not has_crisis


def _template_generic(company: str, insight: dict[str, Any]) -> str:
    """Last-resort template — always produces a string."""
    headline = insight.get("headline") or ""
    if headline:
        # Keep the editorial ring: `{Company}: {headline-ex-company}`
        short = re.sub(rf"^{re.escape(company)}[\s:\-–—]+", "", headline, flags=re.I)
        return f"{company}: {short}"
    return f"Snowkap signal: {company} — ESG brief"


# ---------------------------------------------------------------------------
# LLM path (HOME only) — cached per article_id
# ---------------------------------------------------------------------------


_LLM_CACHE: dict[str, str] = {}


def _llm_subject(
    company: str,
    insight: dict[str, Any],
    article: dict[str, Any],
    is_positive: bool = False,
) -> str | None:
    """One gpt-4.1-mini call. Cached by article_id. Returns None on failure.

    Phase 22.3: when `is_positive=True`, the prompt instructs upside framing
    so the LLM produces a benefit/upside subject line instead of a risk one.
    Same article will get DIFFERENT cached subjects for positive vs negative
    runs since the article_id is the cache key (cache is wiped only via
    full restart — the rare case where a re-analysis flips the polarity).
    """
    article_id = str(article.get("id") or "")
    cache_key = f"{article_id}:{'pos' if is_positive else 'neg'}"
    if cache_key in _LLM_CACHE:
        return _LLM_CACHE[cache_key]

    if not os.environ.get("OPENAI_API_KEY"):
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    decision = insight.get("decision_summary") or {}
    headline = insight.get("headline") or article.get("title") or ""
    if is_positive:
        # On positive events, surface the OPPORTUNITY field as the bottom
        # line since key_risk is usually a soft execution-risk note.
        bottom = decision.get("top_opportunity") or insight.get("net_impact_summary") or ""
    else:
        bottom = decision.get("key_risk") or insight.get("net_impact_summary") or ""
    exposure = decision.get("financial_exposure") or ""

    if is_positive:
        system = (
            "You are an editorial newsletter writer at the Economic Times "
            "Sustainability desk. Write a single subject line (<= 85 chars) "
            "for an ESG intelligence brief about a POSITIVE event "
            "(rating upgrade, contract win, capacity addition, ESG "
            "certification, green-finance milestone). Frame the ₹ figure "
            "as UPSIDE (valuation benefit, capital uplift, margin gain), "
            "NOT risk. Open with the upside ₹, then the upgrade/win, "
            "then company name. NEVER use 'risk', 'exposure', 'penalty', "
            "'flagged', 'compliance' for a positive event. No colons at "
            "the start. No 'Snowkap' prefix. No emoji. No quotation marks. "
            "One line only."
        )
    else:
        system = (
            "You are an editorial newsletter writer at the Economic Times "
            "Sustainability desk. Write a single subject line (<= 85 characters) "
            "for an ESG intelligence brief that opens with the stakes: "
            "₹-figures first, regulator / deadline second, company name third. "
            "No colons at the start. No 'Snowkap' prefix. No emoji. No quotation "
            "marks. One line only."
        )
    polarity_label = "POSITIVE / upside" if is_positive else "NEGATIVE / risk"
    user = (
        f"Polarity: {polarity_label}\n"
        f"Company: {company}\n"
        f"Headline: {headline}\n"
        f"Bottom line: {bottom}\n"
        f"Financial exposure: {exposure}\n"
        "Write the subject line."
    )

    try:
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.3,
            max_tokens=60,
        )
        text = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
    except Exception as exc:
        logger.warning("subject_line LLM failed: %s", exc)
        return None

    # Strip leading "Subject:" if the model emitted it
    text = re.sub(r"^\s*(Subject|SUBJECT)\s*:?\s*", "", text)
    text = _truncate(text, MAX_LEN)
    if cache_key:
        _LLM_CACHE[cache_key] = text
    return text or None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_subject(
    company: str,
    insight: dict[str, Any],
    article: dict[str, Any] | None = None,
) -> str:
    """Return an editorial subject line for a single-article email.

    Selection logic:
      1. For HIGH / CRITICAL materiality → try LLM path (cached per article).
      2. Fall back through templates in order: compliance → disclosure →
         opportunity → generic.
      3. Always cap at 90 chars.
    """
    article = article or {}
    decision = insight.get("decision_summary") or {}
    materiality = (decision.get("materiality") or "").upper()

    # Phase 22.3 — read polarity FIRST so we can route to upside templates
    # before falling into the defensive compliance/disclosure cascade.
    is_positive = _is_positive_event(insight)

    # 1. LLM path for high-stakes articles (where open-rate matters most)
    if materiality in {"HIGH", "CRITICAL"}:
        llm = _llm_subject(company, insight, article, is_positive=is_positive)
        if llm:
            return _truncate(llm)

    # 2. Template cascade — polarity-aware
    if is_positive:
        # Positive events: try the upside-framed template FIRST. If it can't
        # produce a string (no ₹ figure + no event-kind detected), fall
        # through to opportunity (also non-defensive). Skip compliance +
        # disclosure entirely — they always frame the ₹ as a risk.
        cascade = (_template_positive_rating, _template_opportunity)
    else:
        # Negative / neutral: original cascade.
        cascade = (_template_compliance, _template_disclosure, _template_opportunity)
    for tmpl in cascade:
        s = tmpl(company, insight)
        if s:
            return _truncate(s)

    # 3. Generic catch-all
    return _truncate(_template_generic(company, insight))
