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


def _llm_subject(company: str, insight: dict[str, Any], article: dict[str, Any]) -> str | None:
    """One gpt-4.1-mini call. Cached by article_id. Returns None on failure."""
    article_id = str(article.get("id") or "")
    if article_id and article_id in _LLM_CACHE:
        return _LLM_CACHE[article_id]

    if not os.environ.get("OPENAI_API_KEY"):
        return None

    try:
        from openai import OpenAI
    except ImportError:
        return None

    decision = insight.get("decision_summary") or {}
    headline = insight.get("headline") or article.get("title") or ""
    bottom = decision.get("key_risk") or insight.get("net_impact_summary") or ""
    exposure = decision.get("financial_exposure") or ""

    system = (
        "You are an editorial newsletter writer at the Economic Times "
        "Sustainability desk. Write a single subject line (<= 85 characters) "
        "for an ESG intelligence brief that opens with the stakes: "
        "₹-figures first, regulator / deadline second, company name third. "
        "No colons at the start. No 'Snowkap' prefix. No emoji. No quotation "
        "marks. One line only."
    )
    user = (
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
    if article_id:
        _LLM_CACHE[article_id] = text
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

    # 1. LLM path for high-stakes articles (where open-rate matters most)
    if materiality in {"HIGH", "CRITICAL"}:
        llm = _llm_subject(company, insight, article)
        if llm:
            return _truncate(llm)

    # 2. Template cascade
    for tmpl in (_template_compliance, _template_disclosure, _template_opportunity):
        s = tmpl(company, insight)
        if s:
            return _truncate(s)

    # 3. Generic catch-all
    return _truncate(_template_generic(company, insight))
