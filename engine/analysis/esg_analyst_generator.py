"""Stage 11a — ESG Analyst Perspective Generator (Phase 4).

Replaces the cosmetic `transform_for_perspective("esg-analyst", ...)` path with
a dedicated LLM call that produces the artefacts a senior MSCI / Sustainalytics /
SES / CDP analyst expects:

  - Quantitative KPI table (Scope 1/2/3, LTIFR, women on board, etc.)
    with industry peer quartile positioning
  - Confidence bounds on every ₹ figure (β, lag, functional form)
  - Double materiality split (financial vs impact-on-world)
  - TCFD scenario framing (1.5°C / 2°C / 4°C)
  - SDG target mapping at sub-goal level (e.g., "SDG 8.7", not "SDG 8")
  - Audit trail linking each claim to an ontology triple + article span

The ontology feeds the prompt with pre-extracted KPI/scenario/stakeholder/SDG
context so the LLM writes narrative over structured facts rather than inventing
them. Every ₹ figure carries a source tag, applied by the output_verifier.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from openai import APIError, APITimeoutError, OpenAI

from engine.analysis.insight_generator import DeepInsight
from engine.analysis.output_verifier import enforce_source_tags
from engine.analysis.pipeline import PipelineResult
from engine.analysis.primitive_engine import compute_cascade
from engine.config import Company, get_openai_api_key, load_settings
from engine.ontology.intelligence import (
    query_esg_kpis_for_industry,
    query_precedents_for_event,
    query_scenario_framings,
    query_sdg_targets,
)

logger = logging.getLogger(__name__)


@dataclass
class ESGAnalystPerspective:
    """Phase 4 rich ESG Analyst output — replaces the cosmetic headline swap."""

    headline: str
    generated_by: str = "esg_analyst_generator_v1"
    kpi_table: list[dict[str, Any]] = field(default_factory=list)
    confidence_bounds: list[dict[str, Any]] = field(default_factory=list)
    double_materiality: dict[str, str] = field(default_factory=dict)
    tcfd_scenarios: dict[str, str] = field(default_factory=dict)
    sdg_targets: list[dict[str, str]] = field(default_factory=list)
    audit_trail: list[dict[str, str]] = field(default_factory=list)
    framework_citations: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # For UI compatibility with legacy CFO/CEO panels
    full_insight: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SYSTEM_PROMPT = """You are a senior ESG analyst at MSCI / Sustainalytics / CDP / SES India.
Your readers are institutional investors and senior sustainability professionals.
They expect rigor, specificity, and audit-trail transparency.

OUTPUT: a single JSON object with these fields (all required unless noted):

{
  "headline": "1 sentence, max 25 words, factually specific",
  "kpi_table": [
    {
      "kpi_name": "Scope 1 Emissions",
      "company_value": "~80 Mt CO2e (engine estimate based on industry benchmark + revenue calibration)",
      "unit": "Mt CO2e",
      "peer_quartile": "P75 (above peer median 60 Mt)",
      "peer_examples": "NTPC 250, Tata Power 20",
      "data_source": "CDP 2024 + BRSR FY24",
      "significance": "This KPI is material because..."
    }
  ],
  "confidence_bounds": [
    {
      "figure": "₹275 Cr direct penalty",
      "source_type": "from_article",
      "confidence": "high",
      "rationale": "stated in SEBI order text"
    },
    {
      "figure": "₹1,200 Cr indirect exposure",
      "source_type": "engine_estimate",
      "beta_range": "0.30-0.60",
      "lag": "2-8 quarters",
      "functional_form": "linear",
      "confidence": "medium",
      "rationale": "primitive cascade CL→RG with company remediation premium applied"
    }
  ],
  "double_materiality": {
    "financial_impact": "1-2 sentences on how this impacts the company's financials, WACC, cost of capital, etc.",
    "impact_on_world": "1-2 sentences on the company's impact on world — link to specific SDG target (e.g., 'SDG 8.7 forced labour eradication directly affected')"
  },
  "tcfd_scenarios": {
    "1_5c": "1-2 sentences on how this article's implications evolve in a 1.5C pathway",
    "2c": "1-2 sentences for 2C",
    "4c": "1-2 sentences for 4C"
  },
  "sdg_targets": [
    {"code": "8.7", "title": "Eradicate forced labour", "applicability": "direct|indirect|adjacent", "rationale": "why this target applies"}
  ],
  "audit_trail": [
    {
      "claim": "total exposure ₹294.2 Cr",
      "derivation": "primitive cascade event_regulatory_policy → CL (β 0.15-0.40) × Adani Power energy_share 0.40 × base remediation ₹650 Cr (from Vedanta 2020 precedent)",
      "sources": ["ontology: P2::CL→RG", "precedent: case_vedanta_child_labour_2020", "article: SEBI order text"]
    }
  ],
  "framework_citations": [
    {"code": "BRSR:P6:Q14", "rationale": "supply-chain labour audit mandatory for Large Cap", "region": "India", "deadline": "2026-05-30"}
  ]
}

HARD CONSTRAINTS:
- Every ₹ figure must carry (from article) or (engine estimate) tag. The verifier auto-appends if missing — but you should tag inline.
- Every framework code must have a rationale — not just the code.
- Every claim in audit_trail must name at least one source (ontology URI / precedent case name / article).
- Confidence bounds must be given for every engine-estimated ₹ figure (β, lag, functional form mandatory).
- SDG citations must be at sub-goal level ("8.7" not "8").
- Do not invent precedents not listed in the provided PRECEDENTS block.
- Do not invent peer numbers not listed in the provided KPI CONTEXT block.

OUTPUT ONLY the JSON object. No preamble, no markdown."""


def _build_user_prompt(
    insight: DeepInsight,
    result: PipelineResult,
    company: Company,
) -> str:
    lines: list[str] = []
    lines.append(f"ARTICLE: {result.title}")
    if insight.headline:
        lines.append(f"DEEP INSIGHT HEADLINE: {insight.headline}")
    lines.append(f"COMPANY: {company.name} (industry: {company.industry}, market_cap: {company.market_cap})")
    cal = company.primitive_calibration or {}
    lines.append(
        f"COMPANY FINANCIALS: revenue ₹{cal.get('revenue_cr', 0):,.0f} Cr, "
        f"opex ₹{cal.get('opex_cr', 0):,.0f} Cr, "
        f"energy_share {cal.get('energy_share_of_opex', 0):.1%}, "
        f"FY {cal.get('fy_year', '?')}"
    )
    lines.append("")

    # Deep insight excerpts so LLM has context to write over
    ds = insight.decision_summary or {}
    ft = insight.financial_timeline or {}
    lines.append("STAGE 10 DEEP INSIGHT (summary):")
    lines.append(f"  impact_score: {insight.impact_score}")
    lines.append(f"  materiality: {ds.get('materiality', '')}")
    lines.append(f"  financial_exposure: {ds.get('financial_exposure', '')}")
    lines.append(f"  key_risk: {ds.get('key_risk', '')}")
    imm = ft.get("immediate", {}) if isinstance(ft, dict) else {}
    if imm:
        lines.append(f"  immediate headline: {imm.get('headline', '')}")
        lines.append(f"  margin_pressure: {imm.get('margin_pressure', '')}")
    lines.append(f"  core_mechanism: {insight.core_mechanism[:300]}")

    # Phase 14.1 — canonical ₹ as a HARD CONSTRAINT. The Stage-10 deep
    # insight has already had its ₹ figures verified + sourced + drift-checked.
    # Pre-Phase-14, downstream perspective generators frequently emitted
    # different ₹ values (e.g. Waaree contract win: deep insight said ₹477.5 Cr,
    # ESG Analyst section invented ₹14.4 Cr from primitive cascade alone).
    # Forcing the canonical figure into every perspective prompt eliminates
    # cross-section drift at the source instead of relying on the verifier
    # to flag-but-not-fix.
    try:
        from engine.analysis.output_verifier import verify_cross_section_consistency
        canonical, _ = verify_cross_section_consistency(insight.to_dict() if hasattr(insight, "to_dict") else dict(insight.__dict__))
        if canonical and canonical > 0:
            lines.append(
                f"  CANONICAL_EXPOSURE: ₹{canonical:.1f} Cr "
                f"(REQUIRED: use this exact figure as the headline ₹ value in your "
                f"perspective. Do NOT recompute or substitute a smaller cascade-only "
                f"number. Phase-14 anti-drift constraint.)"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("canonical_exposure compute failed in ESG analyst prompt: %s", exc)
    lines.append("")

    # Ontology-driven KPI context (industry peer quartiles)
    try:
        kpis = query_esg_kpis_for_industry(company.industry, limit=8)
        if kpis:
            lines.append("KPI CONTEXT (use these exact peer numbers in kpi_table):")
            for k in kpis:
                peer_blurb = ""
                if k.peer_median:
                    peer_blurb = f" | peers p25={k.peer_p25}, median={k.peer_median}, p75={k.peer_p75} ({k.peer_examples})"
                lines.append(f"  - [{k.pillar}] {k.label} ({k.unit}){peer_blurb}")
                lines.append(f"      calculation: {k.calculation}")
                lines.append(f"      direction: {k.direction}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_esg_kpis_for_industry failed: %s", exc)

    # TCFD scenarios for the company's industry
    try:
        scenarios = query_scenario_framings(company.industry)
        if scenarios:
            lines.append("")
            lines.append("TCFD SCENARIO TEMPLATES (ground your 1.5C/2C/4C answers in these):")
            for s in scenarios:
                lines.append(f"  {s.path} [{s.timeframe}] ({s.reference}):")
                lines.append(f"    transition: {s.transition_risk[:200]}")
                lines.append(f"    physical: {s.physical_risk[:200]}")
                lines.append(f"    financial: {s.financial_impact[:200]}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_scenario_framings failed: %s", exc)

    # SDG targets — based on theme + event triggers
    try:
        theme = result.themes.primary_theme if result.themes else ""
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        keywords: list[str] = []
        if theme:
            keywords.append(theme.lower().replace(" ", "_"))
        if event_id:
            # Strip event_ prefix
            slug = event_id.replace("event_", "")
            keywords.append(slug)
        sdgs = query_sdg_targets(keywords, limit=5)
        if sdgs:
            lines.append("")
            lines.append("SDG TARGETS (cite these at sub-goal level, e.g. 'SDG 8.7'):")
            for s in sdgs:
                lines.append(f"  SDG {s.code} — {s.title}: {s.description[:150]}")
                lines.append(f"      corporate action: {s.corporate_action[:150]}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_sdg_targets failed: %s", exc)

    # Precedents (Phase 3)
    try:
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            precedents = query_precedents_for_event(event_id, company.industry, limit=3)
            if precedents:
                lines.append("")
                lines.append("PRECEDENTS (only cite these — do NOT invent others):")
                for p in precedents:
                    lines.append(f"  - {p.as_citation()}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("query_precedents_for_event failed: %s", exc)

    # Primitive cascade — provides β, lag for confidence bounds
    try:
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            cascade = compute_cascade(event_id, company)
            if cascade:
                lines.append("")
                lines.append("PRIMITIVE CASCADE (use these for confidence_bounds):")
                lines.append(cascade.to_prompt_block())
    except Exception as exc:  # noqa: BLE001
        logger.warning("compute_cascade failed in ESG analyst gen: %s", exc)

    lines.append("")
    lines.append("Produce the full JSON object now.")
    return "\n".join(lines)


def generate_esg_analyst_perspective(
    insight: DeepInsight,
    result: PipelineResult,
    company: Company,
) -> ESGAnalystPerspective:
    """Run Stage 11a — LLM-generated ESG Analyst perspective with ontology grounding."""
    if not insight or not insight.headline:
        return ESGAnalystPerspective(
            headline=result.title[:120],
            warnings=["insight missing — returning minimal analyst perspective"],
        )

    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_heavy", "gpt-4.1")
    max_tokens = llm_cfg.get("max_tokens_analyst", 2500)
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
            "esg_analyst_generator LLM failed (%s) — falling back to minimal output",
            type(exc).__name__,
        )
        return ESGAnalystPerspective(
            headline=insight.headline,
            warnings=[f"llm_error: {type(exc).__name__}"],
        )

    # Post-LLM: source tag enforcement on all ₹ figures
    try:
        article_excerpts = [
            result.title or "",
            getattr(result.nlp, "narrative_core_claim", "") or "",
            getattr(result.nlp, "narrative_implied_causation", "") or "",
        ]
        parsed, tags_added = enforce_source_tags(parsed, article_excerpts)
        warnings: list[str] = []
        if tags_added:
            warnings.append(f"verifier: added {tags_added} source tags on ₹ figures")
    except Exception as exc:  # noqa: BLE001 — non-fatal
        logger.warning("source tag enforcement failed (non-fatal): %s", exc)
        warnings = []

    # Hydrate into the dataclass (defensive defaults)
    return ESGAnalystPerspective(
        headline=str(parsed.get("headline", insight.headline))[:300],
        kpi_table=list(parsed.get("kpi_table", []) or []),
        confidence_bounds=list(parsed.get("confidence_bounds", []) or []),
        double_materiality=dict(parsed.get("double_materiality", {}) or {}),
        tcfd_scenarios=dict(parsed.get("tcfd_scenarios", {}) or {}),
        sdg_targets=list(parsed.get("sdg_targets", []) or []),
        audit_trail=list(parsed.get("audit_trail", []) or []),
        framework_citations=list(parsed.get("framework_citations", []) or []),
        warnings=warnings,
        full_insight=insight.to_dict() if hasattr(insight, "to_dict") else None,
    )
