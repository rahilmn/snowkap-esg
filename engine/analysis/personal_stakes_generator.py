"""Phase 25 W9 — "Why this matters to YOU" personal-stakes generator.

The user's #7 ask: "It first needs to explain why this sustainability
news article is critical or it matters to you. What are its impact on
compliance, risk, supply chain, other factors impacting business."

Today the pipeline emits ``deep_insight`` (event-level analysis) and
3 perspective views (CFO/CEO/Analyst) — but NONE of them say "given
your specific company's exposure, here's why this single article
matters to YOU specifically." That gap is what W9 fills.

Output shape (added to ``DeepInsight.stakes_for_company``):
    {
      "personal_stakes_paragraph": str,       # 80-120 words, prose
      "revenue_pct_at_stake": float | None,   # deterministic: event_₹ ÷ company_revenue_cr × 100
      "peer_action_summary": str,             # 1-2 sentences citing 2-3 named peers
      "do_nothing_risk_paragraph": str,       # 60-80 words, what happens if you ignore this
    }

Cost: ~400 tokens via gpt-4.1 = $0.008/article × 1,530 articles/month
= ~$12/month at the 17-tenant scale. Budgeted in Section 6.3 of the
Phase 25 plan.

Determinism guards:
  * ``revenue_pct_at_stake`` is computed PYTHON-side from
    ``decision_summary.financial_exposure`` ÷ ``company.revenue_cr`` —
    NOT asked of the LLM. The LLM would hallucinate the percentage.
  * Polarity-aware system prompt (positive event → "opportunity capture"
    framing, negative → "risk exposure" framing). Mirrors the
    Phase 14.4 + Phase 22.4 polarity guards already in
    ``insight_generator.py``.
  * Hard fail-safe: any exception returns an empty dict so the insight
    still ships without the personal-stakes block.
  * Low temperature (0.2) so two re-runs of the same input land within
    drift tolerance.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompts — polarity-aware
# ---------------------------------------------------------------------------

_BASE_SYSTEM = """You are a senior ESG analyst writing a "why this matters to YOU"
briefing for a specific company. The reader knows the article exists; they want
to know what it means for THEIR business — but THREE different readers (CFO,
CEO, ESG Analyst) have THREE different lenses, and each must get a paragraph
written specifically for them.

Output a single JSON object with exactly these keys:

  personal_stakes_paragraphs (object with 3 string keys — REQUIRED, all 3 must
  be present, ALL 3 MUST BE TEXT-DISTINCT — share no 20+ char phrases verbatim):

    cfo (60-90 words):
      Anchor on ₹ exposure, payback months, regulatory cost, capital-at-risk.
      Use second-person ("Your company is exposed to ₹X Cr…"). The CFO needs
      to know whether this hits the next quarterly close. NO strategic
      positioning, NO governance philosophy.

    ceo (60-90 words):
      Anchor on competitive position, board narrative, 3-year horizon.
      Second-person, but framed as "How does this shift YOUR competitive
      story?". NO ₹ figures (those belong to CFO). The CEO is choosing
      what to tell the board.

    analyst (70-100 words):
      Anchor on framework gaps, disclosure deadlines, methodology rigour.
      Cite specific framework sections (BRSR P6:Q14, GRI:303, ESRS:E1).
      The analyst is writing the next disclosure — they need to know
      which sections this event affects + when the filing window opens.

  peer_action_summary (string, 30-60 words, ROLE-AGNOSTIC — shared by all 3):
    Cite 2-3 named peer companies — what they did when faced with a similar
    event. Pull from the precedent context. If no precedents provided, say
    "No peer precedent yet" — do NOT invent peer names.

  do_nothing_risk_paragraph (string, 60-80 words, ROLE-AGNOSTIC):
    What happens if this company ignores the article. Cite specific risks
    (regulator action, MSCI rating downgrade, lender questioning, investor
    divestment, supply-chain ripple). Quantify ONLY using figures already
    in the input context.

CRITICAL RULES:
  - DO NOT invent ₹ figures, peer names, deadlines, or framework section codes.
    Every number, name, and code must trace to the input context.
  - DO NOT repeat the same sentence across cfo/ceo/analyst paragraphs. Each
    role gets DIFFERENT vocabulary anchored on their decision criteria.
  - If the context is thin for a particular role, write a shorter paragraph
    that says so explicitly — better than padding with invented detail.

Return ONLY the JSON object. No markdown, no preamble."""

_NEGATIVE_DIRECTIVE = """

NEGATIVE-EVENT DIRECTIVE:
This is a negative event (regulatory action, violation, risk surfacing).
Frame as "risk exposure" — the company is in jeopardy, here's how
much, here's what to monitor. The do_nothing_risk_paragraph names
escalation triggers (regulator escalation, peer ratings movement,
investor divestment)."""

_POSITIVE_DIRECTIVE = """

POSITIVE-EVENT DIRECTIVE:
This is a positive event (contract win, capacity addition, ESG
certification, green-finance milestone). Frame as "opportunity
capture" — the company has a window to lead, here's the upside, here's
how to extract it. The do_nothing_risk_paragraph names execution risks
(slow ramp-up, dilution, peer catch-up) — NEVER fictional regulatory
penalties."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_personal_stakes(
    parsed_insight: dict[str, Any],
    *,
    company,
    event_polarity: str = "neutral",
    peer_precedents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate the ``stakes_for_company`` block for a single article.

    Inputs:
      * ``parsed_insight`` — the dict returned by Stage 10 (decision_summary,
        core_mechanism, financial_exposure, etc.)
      * ``company`` — the ``engine.config.Company`` instance with
        ``revenue_cr`` + ``industry`` + ``framework_region`` etc.
      * ``event_polarity`` — "positive" | "negative" | "neutral"
      * ``peer_precedents`` — list of precedent dicts pulled by caller
        from ``query_precedents_for_event``

    Returns the dict suitable for stamping onto
    ``DeepInsight.stakes_for_company``. Returns ``{}`` on any failure
    (the caller continues with insight generation; the W10 UI hides
    the PersonalStakesCard when the dict is empty).
    """
    try:
        # Computed deterministically — never asked of LLM
        revenue_pct = _compute_revenue_pct_at_stake(parsed_insight, company)

        llm_dict = _call_llm(
            parsed_insight=parsed_insight,
            company=company,
            event_polarity=event_polarity,
            peer_precedents=peer_precedents or [],
        )
        if not llm_dict:
            return {}

        # W4b — accept the new role-aware shape `personal_stakes_paragraphs:
        # {cfo, ceo, analyst}` AND fall back to the legacy single-paragraph
        # output (`personal_stakes_paragraph`) for back-compat with cached
        # insights generated before W4b shipped.
        legacy_single = (llm_dict.get("personal_stakes_paragraph") or "").strip()
        role_paragraphs_raw = llm_dict.get("personal_stakes_paragraphs") or {}
        if not isinstance(role_paragraphs_raw, dict):
            role_paragraphs_raw = {}
        role_paragraphs = {
            "cfo": str(role_paragraphs_raw.get("cfo") or "").strip(),
            "ceo": str(role_paragraphs_raw.get("ceo") or "").strip(),
            "analyst": str(role_paragraphs_raw.get("analyst") or "").strip(),
        }
        # Back-compat: if the LLM only returned the legacy single paragraph,
        # use it as the CFO paragraph (closest semantic match) and leave the
        # other two empty so the UI can hide them per role.
        if legacy_single and not any(role_paragraphs.values()):
            role_paragraphs["cfo"] = legacy_single

        return {
            # New W4b field — role-aware
            "personal_stakes_paragraphs": role_paragraphs,
            # Legacy field kept for back-compat (UI fallback to first non-empty role)
            "personal_stakes_paragraph": (
                role_paragraphs["cfo"]
                or role_paragraphs["ceo"]
                or role_paragraphs["analyst"]
                or legacy_single
            ),
            "revenue_pct_at_stake": revenue_pct,
            "peer_action_summary": llm_dict.get("peer_action_summary", ""),
            "do_nothing_risk_paragraph": llm_dict.get("do_nothing_risk_paragraph", ""),
        }
    except Exception as exc:  # noqa: BLE001 — never block insight generation
        logger.warning("personal_stakes_generator: skipped (non-fatal): %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Deterministic computation
# ---------------------------------------------------------------------------

# Reuse the same regex pattern from output_verifier so the two extract
# the same numeric values from prose
_RUPEE_AMOUNT_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(crore|Lakh|Lkh|Cr|L)?\b",
    re.IGNORECASE,
)


def _compute_revenue_pct_at_stake(
    parsed_insight: dict[str, Any],
    company,
) -> float | None:
    """Pull the canonical ₹ exposure from decision_summary, divide by
    the company's annual revenue, return the percentage. None when
    either side is missing or zero."""
    revenue_cr = float(getattr(company, "revenue_cr", 0) or 0)
    if revenue_cr <= 0:
        return None
    decision = parsed_insight.get("decision_summary") or {}
    exposure = (decision.get("financial_exposure") or "").strip()
    if not exposure:
        return None
    match = _RUPEE_AMOUNT_RE.search(exposure)
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(",", ""))
    except (TypeError, ValueError):
        return None
    unit = (match.group(2) or "").lower()
    if unit.startswith("l"):
        amount = amount / 100  # 1 Cr = 100 Lakh
    if amount <= 0:
        return None
    pct = (amount / revenue_cr) * 100
    return round(pct, 2)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _call_llm(
    *,
    parsed_insight: dict[str, Any],
    company,
    event_polarity: str,
    peer_precedents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Single OpenAI call. Returns parsed JSON dict or empty dict on
    any error."""
    try:
        from openai import APIError, APITimeoutError, OpenAI
        from engine.config import get_openai_api_key, load_settings
    except ImportError as exc:
        logger.warning("personal_stakes: openai import failed: %s", exc)
        return {}

    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_heavy", "gpt-4.1")

    # Build polarity-aware system prompt
    system_prompt = _BASE_SYSTEM
    if event_polarity == "positive":
        system_prompt = _BASE_SYSTEM + _POSITIVE_DIRECTIVE
    elif event_polarity == "negative":
        system_prompt = _BASE_SYSTEM + _NEGATIVE_DIRECTIVE

    user_prompt = _build_user_prompt(parsed_insight, company, peer_precedents)

    try:
        from engine.llm import get_llm_client
        client = get_llm_client(task_class="composition").sync
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            # W4b — bumped 400 -> 900 to accommodate 3 role-specific
            # paragraphs (60-100 words each) + peer_action + do_nothing.
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        from engine.models.llm_calls import log_openai_usage
        log_openai_usage(resp, model=model, stage="personal_stakes")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return parsed
    except (APIError, APITimeoutError, json.JSONDecodeError) as exc:
        logger.warning(
            "personal_stakes LLM failed (%s) — returning empty",
            type(exc).__name__,
        )
        return {}


def _build_user_prompt(
    parsed_insight: dict[str, Any],
    company,
    peer_precedents: list[dict[str, Any]],
) -> str:
    decision = parsed_insight.get("decision_summary") or {}
    headline = parsed_insight.get("headline") or ""
    core_mechanism = parsed_insight.get("core_mechanism") or ""
    profitability = parsed_insight.get("profitability_connection") or ""

    revenue_cr = float(getattr(company, "revenue_cr", 0) or 0)
    name = getattr(company, "name", "Unknown Company")
    industry = getattr(company, "industry", "Unknown Industry")
    region = getattr(company, "framework_region", "GLOBAL")

    lines = [
        "=== COMPANY ===",
        f"Name: {name}",
        f"Industry: {industry}",
        f"Framework region: {region}",
    ]
    if revenue_cr > 0:
        lines.append(f"Annual revenue: ₹{revenue_cr:,.0f} Cr")
    lines.append("")
    lines.append("=== INSIGHT (already analysed) ===")
    lines.append(f"Headline: {headline}")
    lines.append(f"Verdict: {decision.get('verdict', '')}")
    lines.append(f"Materiality: {decision.get('materiality', '')}")
    lines.append(f"Action: {decision.get('action', '')}")
    lines.append(f"Financial exposure: {decision.get('financial_exposure', '')}")
    lines.append(f"Key risk: {decision.get('key_risk', '')}")
    lines.append(f"Top opportunity: {decision.get('top_opportunity', '')}")
    if core_mechanism:
        lines.append(f"Core mechanism: {core_mechanism}")
    if profitability:
        lines.append(f"Profitability link: {profitability}")
    lines.append("")
    lines.append("=== PEER PRECEDENTS ===")
    if peer_precedents:
        for p in peer_precedents[:3]:
            company_name = p.get("company") or p.get("name") or "?"
            outcome = p.get("outcome") or p.get("recovery_path") or ""
            lines.append(f"  - {company_name}: {outcome[:180]}")
    else:
        lines.append("  (none provided)")
    lines.append("")
    lines.append("Now generate the JSON.")
    return "\n".join(lines)
