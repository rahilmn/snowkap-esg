"""Stage 11b — CEO Narrative Perspective Generator (Phase 4).

Replaces the cosmetic `transform_for_perspective("ceo", ...)` headline swap
with a dedicated LLM call that produces board-grade content:

  - Board-ready paragraph (60-100 words, drops directly into a board pack)
  - Stakeholder map (named stakeholders with stance + precedent)
  - Analogous peer precedent with outcome
  - 3-year trajectory (do-nothing vs act-now)
  - Q&A drafts: earnings call / press / board / regulator

Ontology feeds prompt with stakeholder positions + precedents so narrative is
grounded, not invented. Every ₹ figure gets source-tagged by the verifier.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from openai import APIError, APITimeoutError, OpenAI

from engine.analysis.insight_generator import DeepInsight
from engine.analysis.output_verifier import enforce_source_tags, sanitise_cfo_headline
from engine.analysis.pipeline import PipelineResult
from engine.config import Company, get_openai_api_key, load_settings
from engine.ontology.intelligence import (
    query_peer_actions,
    query_precedents_for_event,
    query_stakeholder_positions,
)

logger = logging.getLogger(__name__)


@dataclass
class CEONarrativePerspective:
    """Phase 4 rich CEO narrative — replaces cosmetic reorder."""

    headline: str
    generated_by: str = "ceo_narrative_generator_v1"
    board_paragraph: str = ""
    stakeholder_map: list[dict[str, str]] = field(default_factory=list)
    analogous_precedent: dict[str, str] = field(default_factory=dict)
    three_year_trajectory: dict[str, str] = field(default_factory=dict)
    qna_drafts: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SYSTEM_PROMPT = """You are the Chief of Staff to the CEO of a large listed Indian company.
You prepare material that lands directly in board packs, earnings-call scripts,
and regulator responses. Your writing is crisp, specific, and defensive without
being evasive.

OUTPUT: a single JSON object with these fields (all required):

{
  "headline": "1 short line — the board-facing summary, max 20 words, no jargon",
  "board_paragraph": "60-100 words that could land on page 1 of a board brief. State the issue, name the specific action the board should take, name the timeline, name the cost of inaction. Specific ₹ figures.",
  "stakeholder_map": [
    {
      "stakeholder": "<name from STAKEHOLDER CONTEXT block>",
      "stance": "<stance drawn verbatim or near-verbatim from the block>",
      "precedent": "<short precedent phrase — 'Company YEAR → outcome' format, from the PRECEDENTS block>"
    }
    // 4-6 stakeholders total, ALL drawn from the STAKEHOLDER CONTEXT block above.
  ],
  "analogous_precedent": {
    "case_name": "<name from PRECEDENTS block>",
    "company": "<company from PRECEDENTS block>",
    "year": "<year, YYYY format>",
    "cost": "<₹X Cr from PRECEDENTS block>",
    "duration": "<N months to recovery, from PRECEDENTS block>",
    "outcome": "<outcome from PRECEDENTS block, verbatim>",
    "applicability": "1-2 sentences on why this is the closest match for THIS event (cite event_type and industry)"
  },
  // If the PRECEDENTS block is empty (no event-matched precedent available),
  // set analogous_precedent to null — do NOT invent one. An empty block means
  // the ontology has no canonical precedent for this event type yet.
  "three_year_trajectory": {
    "do_nothing": "FY horizon path (use the FISCAL_HORIZON given in the user prompt) if no remediation. State specific ₹ impacts, credit rating path, investor behavior.",
    "act_now": "Same FY horizon with specific intervention. State cost of intervention, benefit, and return timeline."
  },
  "qna_drafts": {
    "earnings_call": "2-3 sentences an investor-facing CEO could read aloud on the next earnings call. Direct, confident, transparent.",
    "press_statement": "2-3 sentences for press release. Acknowledge issue, state remedy, commit to outcome.",
    "board_qa": "Q: max penalty? A: ... Q: FY26 capex impact? A: ...  (2-3 Q/A pairs a board chair would prep)",
    "regulator_qa": "Text for direct engagement with the regulator (SEBI / RBI / MoEF / other). Cooperative tone, specific commitments."
  }
}

HARD CONSTRAINTS:
- Every ₹ figure must carry (from article) or (engine estimate) tag. Verifier auto-appends if missing.
- Do not invent stakeholder behaviors not listed in the provided STAKEHOLDER CONTEXT block.
- Do not invent precedents not listed in the provided PRECEDENTS block. Pick the single closest match for analogous_precedent.
- board_paragraph must be under 120 words — measured by whitespace split.
- Board-ready tone: no Greek letters, no framework IDs in the headline (frameworks belong in the body).
- Do not use bullet points inside string fields — write complete sentences.
- qna_drafts.board_qa can use "Q:" / "A:" inline but keep it flowing.

OUTPUT ONLY the JSON object. No preamble, no markdown."""


def _build_user_prompt(
    insight: DeepInsight,
    result: PipelineResult,
    company: Company,
) -> str:
    # Phase 13 S2 — dynamic fiscal horizon. Computed at call time so it
    # auto-rolls forward each calendar year. CFO/CEO prompts reference
    # FISCAL_HORIZON instead of a hardcoded "FY27-29".
    from datetime import datetime
    current_fy = datetime.now().year
    fiscal_horizon = f"FY{(current_fy + 1) % 100:02d}-{(current_fy + 3) % 100:02d}"

    lines: list[str] = []
    lines.append(f"ARTICLE: {result.title}")
    if insight.headline:
        lines.append(f"INSIGHT HEADLINE: {insight.headline}")
    lines.append(f"COMPANY: {company.name} (industry: {company.industry}, market_cap: {company.market_cap})")
    cal = company.primitive_calibration or {}
    lines.append(
        f"COMPANY FINANCIALS: revenue ₹{cal.get('revenue_cr', 0):,.0f} Cr, "
        f"FY {cal.get('fy_year', '?')}, debt/equity {cal.get('debt_to_equity', 0):.2f}"
    )
    lines.append(f"FISCAL_HORIZON: {fiscal_horizon} (use this exact label in three_year_trajectory)")
    lines.append("")

    ds = insight.decision_summary or {}
    lines.append("STAGE 10 DECISION SUMMARY:")
    lines.append(f"  materiality: {ds.get('materiality', '')}")
    lines.append(f"  action: {ds.get('action', '')}")
    lines.append(f"  financial_exposure: {ds.get('financial_exposure', '')}")
    lines.append(f"  key_risk: {ds.get('key_risk', '')}")
    lines.append(f"  top_opportunity: {ds.get('top_opportunity', '')}")
    lines.append(f"  timeline: {ds.get('timeline', '')}")

    # Phase 14.1 — canonical ₹ hard constraint (mirrors esg_analyst_generator).
    # The CEO narrative must reference the SAME exposure figure as the deep
    # insight + CFO + ESG Analyst sections. Pre-Phase-14, board paragraphs
    # occasionally dropped to a smaller "cascade-only" figure, producing
    # internal inconsistency that the cross-section drift checker flagged.
    try:
        from engine.analysis.output_verifier import verify_cross_section_consistency
        canonical, _ = verify_cross_section_consistency(
            insight.to_dict() if hasattr(insight, "to_dict") else dict(insight.__dict__)
        )
        if canonical and canonical > 0:
            lines.append(
                f"  CANONICAL_EXPOSURE: ₹{canonical:.1f} Cr "
                f"(REQUIRED: use this exact figure as the ₹ value in board_paragraph, "
                f"three_year_trajectory, and qna_drafts. Do NOT recompute or substitute "
                f"a different number. Phase-14 anti-drift constraint.)"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("canonical_exposure compute failed in CEO prompt: %s", exc)
    lines.append("")

    # Stakeholder context — drawn from StakeholderPosition ontology.
    # Phase 15: route to positive-polarity stance + precedent for upside
    # events (contract win, capacity addition, ESG cert, green-finance) so
    # the CEO narrative no longer cites "Vedanta 2020 SCN" or "Wells Fargo
    # 2016 BBB→B over fraud" alongside a 500 MW solar auction win.
    try:
        from engine.analysis.recommendation_archetypes import is_positive_event
        theme = result.themes.primary_theme if result.themes else ""
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        keywords: list[str] = []
        if theme:
            keywords.append(theme.lower().replace(" ", "_"))
        if event_id:
            keywords.append(event_id.replace("event_", ""))
        # Also add common governance/financial keywords that appear in decision_summary
        for field_text in (ds.get("key_risk", ""), ds.get("top_opportunity", "")):
            low = str(field_text).lower()
            for kw in ("governance", "regulatory", "disclosure", "fraud", "social", "safety", "climate"):
                if kw in low:
                    keywords.append(kw)
                    break
        # Phase 17 — pass sentiment so ambiguous events (quarterly_results,
        # dividend_policy, ma_deal, esg_rating_change, climate_disclosure_index)
        # route by NLP tone rather than the static positive set.
        nlp_sent = getattr(result.nlp, "sentiment", 0) if result.nlp else 0
        polarity_is_positive = is_positive_event(event_id, sentiment=nlp_sent)

        # Phase 15: also widen positive-event keyword set so the SPARQL trigger
        # match catches positive-event flavour stakeholders (transition, BRSR,
        # stewardship, climate_disclosure, esg_rating_change, sustainable_bonds).
        if polarity_is_positive:
            keywords.extend([
                "climate_disclosure", "transition_announcement",
                "esg_rating_change", "sustainable_bonds", "stewardship",
                "BRSR_filing", "TCFD_disclosure",
            ])

        polarity = "positive" if polarity_is_positive else "negative"
        positions = query_stakeholder_positions(keywords, event_polarity=polarity)
        if positions:
            polarity_label = "POSITIVE-EVENT FLAVOUR" if polarity == "positive" else "NEGATIVE-EVENT FLAVOUR"
            lines.append(f"STAKEHOLDER CONTEXT [{polarity_label}] (use these exact stance + precedent phrasings — do NOT invent others):")
            for p in positions[:8]:
                lines.append(f"  [{p.stakeholder_type}] {p.label}")
                lines.append(f"    stance: {p.stance[:250]}")
                lines.append(f"    precedent: {p.precedent[:200]}")
                if p.escalation_window:
                    lines.append(f"    window: {p.escalation_window}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_stakeholder_positions failed: %s", exc)

    # Precedents — Phase 3 library. Phase 12.6 addition: explicitly tell the
    # LLM when no event-matched precedent is available so it sets
    # analogous_precedent=null instead of inventing a default.
    try:
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            precedents = query_precedents_for_event(event_id, company.industry, limit=3)
            if precedents:
                lines.append("")
                lines.append(
                    f"PRECEDENTS (event={event_id}, industry={company.industry}) "
                    f"— pick ONE as analogous_precedent, do not invent others:"
                )
                for p in precedents:
                    lines.append(f"  case_key: {p.name}")
                    lines.append(f"    company: {p.company}, year: {p.date[:4]}, cost: ₹{p.cost_cr:.0f} Cr, duration: {p.duration_months:.0f}m")
                    lines.append(f"    outcome: {p.outcome[:250]}")
                    lines.append(f"    recovery: {p.recovery_path[:200]}")
            else:
                lines.append("")
                lines.append(
                    f"PRECEDENTS (event={event_id}, industry={company.industry}): "
                    f"NONE AVAILABLE. Set `analogous_precedent` to null in your "
                    f"output — do NOT invent one. It is fine to leave this empty."
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_precedents_for_event failed: %s", exc)

    # Peer actions — existing Phase 14 infrastructure
    try:
        theme = result.themes.primary_theme if result.themes else ""
        peer_actions = query_peer_actions(theme) if theme else []
        if peer_actions:
            lines.append("")
            lines.append("PEER ACTIONS (real competitor moves, useful for stakeholder map + trajectory):")
            for pa in peer_actions[:3]:
                lines.append(f"  - {pa.company}: {pa.action} → {pa.outcome}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_peer_actions failed: %s", exc)

    lines.append("")
    lines.append("Produce the full JSON object now. Cite stakeholders + precedents only from the blocks above.")
    return "\n".join(lines)


def generate_ceo_narrative_perspective(
    insight: DeepInsight,
    result: PipelineResult,
    company: Company,
) -> CEONarrativePerspective:
    """Run Stage 11b — LLM-generated CEO narrative with ontology grounding."""
    if not insight or not insight.headline:
        return CEONarrativePerspective(
            headline=result.title[:120],
            warnings=["insight missing — returning minimal CEO perspective"],
        )

    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_heavy", "gpt-4.1")
    max_tokens = llm_cfg.get("max_tokens_ceo", 2500)
    temperature = llm_cfg.get("temperature", 0.2)

    client = OpenAI(api_key=get_openai_api_key())
    user_prompt = _build_user_prompt(insight, result, company)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
    except (APIError, APITimeoutError, json.JSONDecodeError, IndexError) as exc:
        logger.warning(
            "ceo_narrative_generator LLM failed (%s) — falling back to minimal output",
            type(exc).__name__,
        )
        return CEONarrativePerspective(
            headline=insight.headline,
            warnings=[f"llm_error: {type(exc).__name__}"],
        )

    # Post-LLM: sanitise headline + enforce source tags
    warnings: list[str] = []
    headline_raw = str(parsed.get("headline", insight.headline))
    cleaned_headline, was_modified = sanitise_cfo_headline(headline_raw)
    if was_modified:
        warnings.append("verifier: CEO headline sanitised (stripped Greek or framework IDs)")

    try:
        article_excerpts = [
            result.title or "",
            getattr(result.nlp, "narrative_core_claim", "") or "",
            getattr(result.nlp, "narrative_implied_causation", "") or "",
        ]
        parsed, tags_added = enforce_source_tags(parsed, article_excerpts)
        if tags_added:
            warnings.append(f"verifier: added {tags_added} source tags on ₹ figures")
    except Exception as exc:  # noqa: BLE001
        logger.warning("source tag enforcement failed (non-fatal): %s", exc)

    return CEONarrativePerspective(
        headline=cleaned_headline[:300],
        board_paragraph=str(parsed.get("board_paragraph", ""))[:1500],
        stakeholder_map=list(parsed.get("stakeholder_map", []) or []),
        analogous_precedent=dict(parsed.get("analogous_precedent", {}) or {}),
        three_year_trajectory=dict(parsed.get("three_year_trajectory", {}) or {}),
        qna_drafts=dict(parsed.get("qna_drafts", {}) or {}),
        warnings=warnings,
    )
