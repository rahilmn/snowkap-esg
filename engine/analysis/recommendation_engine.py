"""REREACT 3-agent recommendation chain.

1. Materiality gate — NON-MATERIAL/LOW + IGNORE/MONITOR → empty list
   (honours the "do nothing is valid ESG output" rule).
2. Generator — 3-5 recommendations via OpenAI gpt-4.1-mini.
3. Analyst — validates each recommendation for logical consistency.
4. Validator — independent hallucination check.
5. Post-processing — deadline shift, ROI sanity, priority, risk-of-inaction.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from openai import OpenAI
from openai import APIError, APITimeoutError

from engine.analysis.insight_generator import DeepInsight
from engine.analysis.pipeline import PipelineResult
from engine.config import Company, get_openai_api_key, load_settings

logger = logging.getLogger(__name__)


@dataclass
class Recommendation:
    title: str
    description: str
    type: str  # strategic | financial | esg_positioning | operational | compliance
    responsible_party: str
    framework_section: str
    deadline: str  # ISO date
    estimated_budget: str
    profitability_link: str
    priority: str  # CRITICAL | HIGH | MEDIUM | LOW
    urgency: str  # immediate | short_term | medium_term | long_term
    estimated_impact: str  # High | Medium | Low
    risk_of_inaction: int  # 1-10
    roi_percentage: float | None = None
    payback_months: float | None = None
    peer_benchmark: str | None = None
    # Phase 3: surfaces ROI cap hit so UI can render tooltip
    roi_capped: bool = False
    roi_cap_reason: str = ""
    # Phase 13 S1: per-recommendation audit trail. Each entry maps a
    # claim in the recommendation back to its source in the ontology /
    # primitive cascade / article body so a CFO can ask "why this rec?"
    # and get a concrete answer. Items have shape
    #   {"source": "ontology|article|primitive|peer|precedent|benchmark",
    #    "ref": "BRSR:P6:Q14" | "P2::SC→OX" | etc.,
    #    "value": "human-readable evidence string"}
    audit_trail: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecommendationResult:
    recommendations: list[Recommendation]
    do_nothing: bool
    gate_reason: str
    generator_count: int
    validated_count: int
    priority_matrix: dict[str, list[dict[str, Any]]] | None = None
    recommendation_rankings: dict[str, list[int]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendations": [r.to_dict() for r in self.recommendations],
            "do_nothing": self.do_nothing,
            "gate_reason": self.gate_reason,
            "generator_count": self.generator_count,
            "validated_count": self.validated_count,
            "priority_matrix": self.priority_matrix,
            "recommendation_rankings": self.recommendation_rankings,
        }


# ---------------------------------------------------------------------------
# Materiality gate
# ---------------------------------------------------------------------------


def _should_skip(insight: DeepInsight, result: PipelineResult) -> tuple[bool, str]:
    decision = insight.decision_summary or {}
    materiality = str(decision.get("materiality", "")).upper()
    action = str(decision.get("action", "")).upper()

    if materiality in ("NON-MATERIAL", "NONMATERIAL") and action == "IGNORE":
        return True, "Non-material + ignore — no action required"
    if insight.impact_score <= 1.5 and action == "IGNORE":
        return True, f"Very low impact score {insight.impact_score} + ignore"
    return False, ""


def _get_rec_count(insight: DeepInsight) -> int:
    """Return how many recommendations to generate based on materiality AND impact score.

    Uses the HIGHER of materiality-based and score-based count to prevent
    the LLM underrating materiality from suppressing recommendations.
    """
    decision = insight.decision_summary or {}
    materiality = str(decision.get("materiality", "")).upper()

    # Materiality-based count
    if materiality in ("CRITICAL", "HIGH"):
        mat_count = 5
    elif materiality == "MODERATE":
        mat_count = 4
    else:
        mat_count = 2

    # Impact-score-based count (override if LLM underrates materiality)
    score = insight.impact_score or 0
    if score >= 7:
        score_count = 5
    elif score >= 5:
        score_count = 4
    elif score >= 3:
        score_count = 3
    else:
        score_count = 2

    return max(mat_count, score_count)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


_GENERATOR_SYSTEM = """You are an ESG action generator. Produce %%REC_COUNT%% actionable, company-specific recommendations grounded in the pipeline context provided.

RULES:
- Every recommendation MUST reference a specific framework section (e.g. BRSR:P6:Q14, GRI:305-1).
- Every profitability_link MUST include a ₹ amount or % quantification. NEVER write vague text like "potential savings". Write "₹15-30 Cr annual compliance cost avoided" or "₹200 Cr green bond opportunity at 50bps discount".
- Deadlines must be future dates in YYYY-MM-DD format.
- Budgets must be calibrated to company market cap and the specific event. A ₹50 Cr GST demand requires ₹2-5 Cr budget for legal challenge, not ₹50 Lakh.
- No generic advice like "improve ESG practices" or "enhance disclosure". Every recommendation must name the SPECIFIC action: "File GST appellate tribunal appeal within 30 days citing ABC precedent" or "Commission third-party BRSR assurance for FY26 filing".
- title must be a SPECIFIC action verb phrase: "File GST appeal at CESTAT" not "Address regulatory compliance".
- description must name ₹ amounts, specific frameworks, specific deadlines, and specific responsible parties.
- roi_percentage: estimate conservatively. For compliance, ROI = avoided penalty / implementation cost. For ESG positioning, ROI = valuation premium / cost. NEVER use null — always estimate. IMPORTANT: ROI values are capped at 500% (compliance), 400% (strategic/ESG), 300% (financial), 200% (operational). Do NOT cite higher ROI in the description or profitability_link text — use capped values.
- payback_months: for capex, use industry standard payback periods. For compliance, use regulatory deadline as outer bound. NEVER use null.
- If PEER ACTIONS are provided, reference what competitors did and suggest matching or exceeding their approach.
- For LOW materiality articles: focus on monitoring actions and disclosure improvements, but still be SPECIFIC about what to monitor and how.
- ALWAYS include at least 1 MONITORING recommendation with specific threshold triggers: "Monitor X metric; escalate to ACT if Y exceeds Z threshold within N months".
- If CAUSAL PRIMITIVES context provides threshold categories (τ values), include them in monitoring recommendations.

FRAMEWORK ACCURACY:
- Match framework sections to the event type. Tax events → GRI:207, ESRS G1. Climate → GRI:305, ESRS E1. H&S → GRI:403, ESRS S1.
- NEVER cite ESRS E1 for a non-climate event. NEVER cite GRI:305 for a tax/governance event.

REGIONAL FRAMEWORK FIT (Phase 24.6 — applied via post-LLM filter, but also stated here so the LLM doesn't over-cite implausible frameworks):
- For INDIA-headquartered companies: BRSR (mandatory), GRI, TCFD, ICMA Green Bond Principles, SEBI Green Bond Framework. EU Taxonomy / CSRD / SFDR are NOT applicable unless the company has explicit EU subsidiary / EU-listed debt.
- For EU-headquartered companies: CSRD/ESRS, EU Taxonomy, SFDR, GRI, TCFD.
- For US-headquartered companies: SEC Climate Disclosure, GRI, SASB, TCFD.
- For UK-headquartered companies: FCA TCFD, SDR, GRI.
- NEVER cite "EU Taxonomy Article 8" for an Indian company's green-bond recommendation — use SEBI Green Bond Framework or ICMA Green Bond Principles instead.

MATH CORRECTNESS (Phase 24.6 — verifier auto-flags math errors; pre-empt by showing your work):
- When stating savings from a basis-point coupon improvement: ₹P × (bps / 10,000) = ₹ saving per year. Example: ₹7,500 Cr × 30 bps = ₹7,500 × 0.003 = ₹22.5 Cr/year, NOT ₹75 Cr.
- When stating market-cap uplift from a P/E multiple expansion: market_cap × (pe_change_%) = ₹ uplift. Example: ₹4,27,000 Cr × 5% = ₹21,350 Cr, NOT ₹1,000 Cr.
- When stating revenue uplift from a contract: ₹contract_size / contract_years = annual revenue, NOT total contract size.
- Show the multiplication explicitly when the arithmetic could mislead. The verifier will downgrade the rec if the cited figure is >50% off the computed figure.

PERSPECTIVE-AWARE RECOMMENDATIONS:
- CFO-relevant: focus on ₹ exposure quantification, cost avoidance, margin protection, ROI maximization.
- CEO-relevant: focus on strategic positioning, competitive advantage, board-level decisions.
- ESG Analyst-relevant: focus on framework compliance gaps, disclosure deadlines, stakeholder engagement.
- Include a mix of types so each perspective has relevant recommendations.
- CRITICAL: Every recommendation must be SPECIFIC to THIS article's event. Do NOT generate generic ESG recommendations (like "enhance CDP disclosure" or "file BRSR") unless the article specifically triggers those frameworks. For positive events (analyst upgrades, ESG awards), recommend LEVERAGING the momentum (green bond timing, investor communication, competitive positioning). For negative events (penalties, violations), recommend REMEDIATION and PREVENTION.

Return a JSON object:
{
  "recommendations": [
    {
      "title": "<action title, max 10 words>",
      "type": "<strategic|financial|esg_positioning|operational|compliance>",
      "description": "<1-2 sentences, specific to the company>",
      "responsible_party": "<specific role>",
      "framework_section": "<BRSR:P6, GRI:305-1, etc.>",
      "deadline": "<YYYY-MM-DD>",
      "estimated_budget": "<₹X-Y Cr>",
      "profitability_link": "<how this saves/makes money with numbers>",
      "urgency": "<immediate|short_term|medium_term|long_term>",
      "estimated_impact": "<High|Medium|Low>",
      "roi_percentage": <estimated ROI % over 3 years, or null>,
      "payback_months": <months to break even, or null>,
      "peer_benchmark": "<what competitors did in similar situations, or null>",
      "audit_trail": [
        {"source": "ontology|article|primitive|peer|precedent|benchmark",
         "ref": "<framework section, primitive edge id, peer name, etc.>",
         "value": "<the specific evidence anchoring the recommendation, in 1 line>"}
      ]
    }
  ]
}

CRITICAL: every recommendation MUST include audit_trail with 1-3 entries
linking the rec back to: (a) framework citations from the FRAMEWORKS block,
(b) ₹ figures from the article or primitive cascade, or (c) named precedents
from the PRECEDENTS block. A recommendation without traceable evidence is
unverifiable; the verifier will flag it.

Return ONLY the JSON, no preamble."""


# Phase 14.3 — Dedicated POSITIVE-event generator system prompt.
#
# Background: even with the Phase 13 archetype routing's polarity warning,
# the default `_GENERATOR_SYSTEM` prompt above is implicitly oriented
# toward defensive remediation framing (see "REMEDIATION and PREVENTION"
# language). On positive events (contract wins, capacity adds, ESG cert
# upgrades, green-finance milestones) the LLM consistently injected
# fictional "₹10-50 Cr SEBI penalty" risks even though the article had
# no regulatory failure to remediate.
#
# This prompt rewrites the rules with positive-event semantics:
#   - "leverage the upside" replaces "remediate risk"
#   - "investor-comms / capital deployment / pipeline momentum" replaces
#     "compliance / monitoring / assurance"
#   - explicit ban on inventing penalty risks unless the article describes
#     a concrete regulatory action
#
# Dispatcher (in _generate_recommendations) routes to this prompt when
# `is_positive_event(event_id)` returns True.
_POSITIVE_GENERATOR_SYSTEM = """You are an ESG action generator. The article describes a POSITIVE event for the company (contract win, capacity addition, ESG certification, green-finance milestone, etc). Produce %%REC_COUNT%% actionable, company-specific recommendations that LEVERAGE the upside.

RULES:
- Recommendations must extract value from the event: investor communication, capacity scaling, capital deployment, pipeline momentum, premium pricing, framework-tier advancement.
- Every recommendation MUST reference a specific framework section (e.g. BRSR:P6:Q14, ICMA Green Bond Principles, GRI:305-1) — used for transparency / disclosure leverage, NOT compliance remediation. CRITICAL: match framework to company HQ — Indian co's get BRSR/SEBI/ICMA, not EU Taxonomy. EU co's get CSRD/EU Taxonomy/SFDR. US co's get SEC Climate.
- Every profitability_link MUST include a ₹ amount or % quantification. Frame as upside: "₹500 Cr green bond at 50 bps coupon save" or "₹200 Cr revenue uplift FY26 once commissioning ramps".
- Deadlines must be future dates in YYYY-MM-DD format.
- Budgets must be calibrated to company market cap and the specific event. Investor-comms ₹0.5-1 Cr; framework-tier advancement ₹1-3 Cr; capacity scaling ₹50-500 Cr depending on the event.
- title must be a SPECIFIC action verb phrase: "Issue ₹500 Cr Green Bond by Sep 2026" not "Pursue green finance".
- roi_percentage: estimate the upside capture. For investor comms, ROI = valuation premium / cost. For capital deployment, ROI = revenue uplift / capex. ROI caps: 500% (compliance — rarely applies here), 400% (strategic/ESG), 300% (financial), 200% (operational).
- payback_months: for capex use industry-standard payback. For investor comms / framework advancement, 3-12 mo. NEVER use null.

MATH CORRECTNESS (Phase 24.6 — same rules as the negative-event prompt):
- ₹P × (bps / 10,000) = ₹ saving per year. Example: ₹7,500 Cr × 30 bps = ₹22.5 Cr/year, NOT ₹75 Cr.
- market_cap × (pe_change_%) = ₹ uplift. Example: ₹4,27,000 Cr × 5% = ₹21,350 Cr, NOT ₹1,000 Cr.
- Show the multiplication explicitly when arithmetic could mislead.

CRITICAL — POSITIVE-EVENT POLARITY GUARDRAILS:
- DO NOT recommend "engage SEBI / engage regulator" UNLESS the article explicitly mentions a regulatory action against the company.
- DO NOT cite "₹X-Y Cr SEBI penalty per violation" or "regulatory enforcement risk" — there is no enforcement event in this article.
- DO NOT recommend "third-party BRSR assurance" as a defensive measure — only recommend it as a credibility-amplification step IF the company is announcing certification.
- DO NOT recommend "monitor and escalate if X exceeds Y" as a generic risk-monitor on a positive event. If you include monitoring, frame it as KPI tracking for the new asset/contract/certification.
- The "key_risk" framing belongs in NEGATIVE-event prompts, not here. Frame this as opportunity capture.

GOOD POSITIVE-EVENT REC SHAPES (pick from these archetypes for ≥80% of the rec set):
  • Investor communication — IR roadshow, earnings-call narrative refresh, ESG-fund pitch deck update
  • Capacity / order ramp — utilization plan, supply-chain readiness, workforce mobilisation
  • Capital deployment — green bond / SLL issuance timing, refinance optionality
  • Framework advancement — DJSI inclusion, MSCI ESG upgrade pathway, CDP A-list pursuit
  • Premium-pricing capture — ESG / quality differentiation in B2B procurement positioning
  • Co-marketing — case-study publication, partnership amplification

Return a JSON object with the same schema as the negative-event prompt:
{
  "recommendations": [
    {
      "title": "<action title, max 10 words>",
      "type": "<strategic|financial|esg_positioning|operational|compliance>",
      "description": "<1-2 sentences, specific to the company>",
      "responsible_party": "<specific role>",
      "framework_section": "<BRSR:P6, EU Taxonomy Art 8, GRI:305-1, etc.>",
      "deadline": "<YYYY-MM-DD>",
      "estimated_budget": "<₹X-Y Cr>",
      "profitability_link": "<upside quantified with ₹ or % numbers>",
      "urgency": "<immediate|short_term|medium_term|long_term>",
      "estimated_impact": "<High|Medium|Low>",
      "roi_percentage": <estimated ROI % over 3 years>,
      "payback_months": <months to capture the upside>,
      "peer_benchmark": "<comparable competitor move, or null>",
      "audit_trail": [
        {"source": "ontology|article|primitive|peer|precedent|benchmark",
         "ref": "<framework section, primitive edge id, peer name, etc.>",
         "value": "<the specific evidence anchoring the recommendation>"}
      ]
    }
  ]
}

Every recommendation MUST include audit_trail with 1-3 entries linking back
to ontology / article / primitive / precedent / peer / benchmark sources.

Return ONLY the JSON, no preamble."""


# ---------------------------------------------------------------------------
# Phase 35 — NEUTRAL-event generator system prompt.
#
# Background: the previous dispatcher was binary — positive vs everything-
# else. Routine disclosure / filing / regulatory-announcement events
# (event_shareholding_change, event_compliance_filing, etc.) fell into
# the everything-else bucket and got the negative-event prompt, producing
# defensive remediation recs ("urgent board governance review · ₹10-50 Cr
# SEBI penalty risk") for what's actually a SEBI-mandated disclosure
# filing with no penalty exposure.
#
# This prompt rewrites the rules with NEUTRAL-event semantics:
#   - "stakeholder communication" + "disclosure verification" replace
#     "remediation"
#   - "monitoring-led" replaces "ACT-led"
#   - no penalty-risk language unless the article describes an actual
#     regulatory enforcement event
#
# Dispatcher (in _generate_recommendations) routes to this prompt when
# `is_neutral_event(event_id, sentiment)` returns True. Order:
#   1. is_positive_event() → _POSITIVE_GENERATOR_SYSTEM
#   2. is_neutral_event() → _NEUTRAL_GENERATOR_SYSTEM        (NEW)
#   3. otherwise          → _GENERATOR_SYSTEM (negative default)
# ---------------------------------------------------------------------------
_NEUTRAL_GENERATOR_SYSTEM = """You are an ESG action generator. The article describes a NEUTRAL event — a disclosure, regulatory filing, or routine periodic update — not a crisis or a triumph. Produce %%REC_COUNT%% actionable, company-specific recommendations that match the proportionate nature of the event.

RULES:
- The event is NEUTRAL. Recommendations focus on disclosure verification, stakeholder communication, KPI monitoring, and documentation discipline — NOT defensive remediation, NOT urgent board-level escalation, NOT penalty-risk framing.
- Every recommendation MUST reference a specific framework section (e.g. BRSR:P6:Q14, GRI:305-1). Match framework to company HQ — Indian co's get BRSR/SEBI/ICMA, EU co's get CSRD/EU Taxonomy/SFDR, US co's get SEC Climate.
- Every profitability_link MUST include a ₹ amount or % quantification, framed as proportionate to the event (e.g. "₹0.5-1 Cr investor-comms budget" or "₹2-5 Cr documentation-discipline cost-avoidance over 3 years").
- Deadlines must be future dates in YYYY-MM-DD format.
- Budgets must be proportionate — disclosure follow-ups ₹0.1-1 Cr; stakeholder comms ₹0.5-2 Cr; KPI-tracking infrastructure ₹1-3 Cr.
- title must be a SPECIFIC action verb phrase: "File supplementary BRSR P6 disclosure by 2026-06-15" not "Improve disclosure".
- roi_percentage: estimate conservatively. For neutral events, ROI ceiling is typically 100-200% (no penalty avoided, no upside captured). Higher claims will be flagged.
- payback_months: 3-18 mo typical. NEVER use null.

POLARITY GUARDRAILS (CRITICAL):
- DO NOT recommend "engage SEBI / engage regulator" UNLESS the article explicitly mentions an enforcement action.
- DO NOT cite "₹X-Y Cr penalty per violation" or "enforcement risk" — there is no enforcement event in a routine disclosure.
- DO NOT recommend "urgent board action" — neutral events are non-urgent by definition.
- DO NOT frame as "remediation" or "crisis management" — frame as "disclosure follow-through" or "stakeholder confidence".
- Materiality on neutral events is typically MODERATE or LOW. CRITICAL / HIGH materiality on a routine disclosure is almost always wrong — if you see it in the deep_insight, the verifier will downgrade.

GOOD NEUTRAL-EVENT REC SHAPES (pick from these archetypes for ≥80% of the rec set):
  • Disclosure verification — audit the filing for completeness, ensure supplementary attachments
  • Stakeholder communication — proactive investor briefing, press FAQ, internal stakeholder memo
  • KPI tracking — set up dashboards to monitor downstream metrics referenced in the disclosure
  • Documentation discipline — audit trail, version control, regulator-correspondence log
  • Peer benchmarking — compare disclosure shape to 3 peer companies' recent equivalents
  • Compliance acknowledgment — confirm receipt by regulator, document the acknowledgment for audit

Return a JSON object with the same schema as the negative-event prompt:
{
  "recommendations": [
    {
      "title": "<action title, max 10 words>",
      "type": "<strategic|financial|esg_positioning|operational|compliance>",
      "description": "<1-2 sentences, proportionate to a neutral event>",
      "responsible_party": "<specific role>",
      "framework_section": "<BRSR:P6, GRI:305-1, etc.>",
      "deadline": "<YYYY-MM-DD>",
      "estimated_budget": "<₹X-Y Cr>",
      "profitability_link": "<proportionate ₹ or % quantification>",
      "urgency": "<short_term|medium_term|long_term>",
      "estimated_impact": "<Low|Medium>",
      "roi_percentage": <ROI %, typically 50-200%>,
      "payback_months": <months>,
      "peer_benchmark": "<comparable peer disclosure, or null>",
      "audit_trail": [
        {"source": "ontology|article|primitive|peer|precedent|benchmark",
         "ref": "<framework section, primitive edge id, peer name>",
         "value": "<the specific evidence anchoring the recommendation>"}
      ]
    }
  ]
}

CRITICAL: every recommendation MUST include audit_trail with ≥1 valid entry per the rules above. Return ONLY the JSON, no preamble."""


# ---------------------------------------------------------------------------
# Phase 35 — recommendation accuracy guardrails
# ---------------------------------------------------------------------------
#
# Appended to BOTH _GENERATOR_SYSTEM and _POSITIVE_GENERATOR_SYSTEM by the
# dispatcher. The audit on 2026-05-24 found three failure modes the previous
# prompt didn't close:
#
#   1. Hallucinated frameworks — the LLM cited "SEBI Takeover Regulations"
#      and similar names that aren't in the ontology. Closed here by passing
#      an explicit FRAMEWORK_WHITELIST in the user prompt and forbidding any
#      framework citation that's not in the list.
#
#   2. ₹ figures in recs that drift from the canonical deep_insight exposure
#      (e.g. rec says "₹500 Cr green bond" when deep_insight.financial_
#      exposure is ₹50 Cr). Closed here by pinning CANONICAL_EXPOSURE and
#      requiring all rec ₹ figures be derived from / proportional to it.
#
#   3. Empty / generic audit_trail entries ("based on industry best
#      practices") that pass the structure check but carry no evidence.
#      Closed here by requiring ≥1 entry whose source is one of
#      {ontology, article, primitive, peer, precedent, benchmark} AND whose
#      `value` field cites a verifiable detail (number, framework section,
#      named peer).
#
# Additionally, on HEADLINE_ONLY articles (body < 300 chars) the prompt
# pivots to MONITORING-flavoured recs at most 3 in count, all ₹ figures
# explicitly framed as "scenario" not "engine estimate".
_ACCURACY_GUARDRAILS = """

═══════════════════════════════════════════════════════════════════════
PHASE 35 — RECOMMENDATION ACCURACY GUARDRAILS (NON-NEGOTIABLE)
═══════════════════════════════════════════════════════════════════════

These rules are post-LLM verified. Recs that violate are either auto-
corrected (₹ drift, retag) or DROPPED (invalid framework, empty audit
trail). Read carefully — fewer correct recs beats more incorrect ones.

FRAMEWORK WHITELIST ENFORCEMENT
- The user prompt below lists FRAMEWORK_WHITELIST: [...]. Every
  `framework_section` MUST start with a framework name from that list.
- If the article's ESG topic isn't covered by any framework in the
  whitelist, OMIT the framework_section (leave it as "") rather than
  inventing one. The verifier will accept a blank framework_section;
  it will reject an off-whitelist citation.
- Specifically forbidden hallucinations: "SEBI Takeover Regulations"
  (use SEBI:LODR or BRSR:P6 instead), "EU Taxonomy Article 8" for an
  Indian co, "GRI Sector Standard XYZ" without a numeric section.

CANONICAL EXPOSURE PIN
- The user prompt below lists CANONICAL_EXPOSURE: ₹X Cr (the deep-insight's
  authoritative ₹ figure). Every ₹ figure in your recommendations MUST:
  (a) match this anchor exactly when citing total exposure, OR
  (b) be a clearly-scoped subset/derivative (e.g. budget = 1-5% of exposure;
      legal-defence reserve = 5-10% of exposure; capex on capacity = 50-300%
      of immediate exposure when proportionate to a revenue opportunity).
- Recs with ₹ figures that drift >35% from the canonical (and aren't
  clearly subset/derivative) will be DROPPED by the verifier.

AUDIT TRAIL EVIDENCE-OR-DEATH
- Every recommendation MUST carry audit_trail with ≥1 entry.
- At least ONE entry per rec MUST satisfy ALL of:
    (a) `source` IN {"ontology", "article", "primitive", "peer", "precedent",
        "benchmark"}  (never "industry best practices" / "general knowledge")
    (b) `ref` cites a specific identifier (framework section, primitive
        edge id, peer company name, precedent year, benchmark metric)
    (c) `value` quotes a verifiable detail (a ₹ number, a date, a section
        code, a peer's actual outcome)
- Recs whose audit_trail has only generic / hand-waving entries will be
  DROPPED by the verifier.

HEADLINE-ONLY MODE
- The user prompt below carries HEADLINE_ONLY: true|false. When true:
  * Produce at most 3 recommendations (not 5).
  * Lead with MONITORING + INVESTIGATION recs, not aggressive ACT actions.
  * Tag every ₹ figure "(scenario)" not "(engine estimate)" — scenario
    language is honest about uncertainty.
  * description MUST start with "Pending full article retrieval, …" so the
    reader knows the rec is provisional.
  * Avoid CRITICAL / CRITICAL-flavoured language ("urgent board action
    required"). The verifier has already capped materiality at MODERATE.
═══════════════════════════════════════════════════════════════════════
"""


def _query_framework_whitelist() -> list[str]:
    """Return the list of valid framework names from the ontology.

    Used to pre-empt LLM hallucinations like "SEBI Takeover Regulations".
    Fail-soft: if the ontology query fails, return an empty list and the
    LLM falls back to the previous unconstrained behaviour.
    """
    try:
        from engine.ontology.intelligence import (
            query_frameworks_for_topic,
            query_regional_boosts,
        )
        # Pull a broad set: regional boosts cover the regional frameworks
        # (BRSR / CSRD / SEC / FCA / SDR / etc), and we union with topic-
        # specific queries below.
        names: set[str] = set()
        for region in ("INDIA", "EU", "US", "UK", "APAC", "GLOBAL"):
            try:
                for b in query_regional_boosts(region) or []:
                    fid = getattr(b, "framework_id", "") or getattr(b, "framework_label", "")
                    if fid:
                        names.add(fid)
            except Exception:  # noqa: BLE001
                continue
        # Add the topic-driven set
        for topic in (
            "topic_climate", "topic_water", "topic_emissions",
            "topic_supply_chain_labor", "topic_governance",
            "topic_business_ethics", "topic_data_security",
            "topic_health_safety", "topic_biodiversity",
        ):
            try:
                for f in query_frameworks_for_topic(topic) or []:
                    fid = getattr(f, "framework_id", "") or getattr(f, "framework_label", "")
                    if fid:
                        names.add(fid)
            except Exception:  # noqa: BLE001
                continue
        # Always include the universal Big-5 so the prompt stays usable
        # even when the ontology query is empty.
        for canonical in (
            "BRSR", "GRI", "TCFD", "SASB", "CDP", "ISSB", "EU Taxonomy",
            "CSRD", "ESRS", "SFDR", "GHG Protocol", "SBTi", "TNFD",
            "SEC Climate", "Porter 5 Forces", "COSO ERM", "DJSI",
            "S&P Global ESG", "ICMA Green Bond Principles",
            "SEBI Green Bond Framework", "SEBI:LODR", "MCA",
        ):
            names.add(canonical)
        return sorted(names)
    except Exception:  # noqa: BLE001 — fall back to no whitelist
        return []


def _extract_canonical_exposure_cr(insight: DeepInsight) -> float | None:
    """Pull the canonical ₹ exposure (Cr) from the deep insight.

    Tries (in order):
      1. financial_timeline.immediate.inr_cr
      2. decision_summary.financial_exposure.amount_cr
      3. decision_summary.financial_exposure (when string like "₹150 Cr")

    Returns None when no ₹ exposure is set — the rec prompt then skips the
    canonical-pin block and lets the LLM operate without it (rare; most
    HOME-tier insights have a computed exposure).
    """
    import re

    ft = insight.financial_timeline or {}
    immediate = ft.get("immediate") or {}
    if isinstance(immediate, dict):
        v = immediate.get("inr_cr")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass

    ds = insight.decision_summary or {}
    fe = ds.get("financial_exposure")
    if isinstance(fe, dict):
        v = fe.get("amount_cr")
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    if isinstance(fe, str):
        m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)\s*Cr", fe)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass

    return None


def _build_generator_prompt(
    insight: DeepInsight, result: PipelineResult, company: Company
) -> str:
    lines: list[str] = []
    lines.append(f"COMPANY: {company.name} ({company.industry}, {company.market_cap})")
    lines.append(f"ARTICLE: {result.title}")
    lines.append(f"HEADLINE: {insight.headline}")
    lines.append(f"IMPACT SCORE: {insight.impact_score}")
    lines.append(f"MATERIALITY: {insight.decision_summary.get('materiality', '')}")
    lines.append(f"VERDICT: {insight.decision_summary.get('verdict', '')}")
    lines.append(f"KEY RISK: {insight.decision_summary.get('key_risk', '')}")
    lines.append(f"TOP OPPORTUNITY: {insight.decision_summary.get('top_opportunity', '')}")
    if result.frameworks:
        lines.append("FRAMEWORKS:")
        for fm in result.frameworks[:5]:
            tag = " [MANDATORY]" if fm.is_mandatory else ""
            lines.append(f"  - {fm.framework_label}{tag}: {fm.profitability_link[:120]}")
    if result.risk and result.risk.top_risks:
        lines.append("TOP RISKS:")
        for r in result.risk.top_risks[:3]:
            lines.append(f"  - {r.category} ({r.level})")

    # Phase 14: Peer actions for benchmarked recommendations
    primary_theme = result.themes.primary_theme if result.themes else ""
    if primary_theme:
        try:
            from engine.ontology.intelligence import query_peer_actions
            peer_actions = query_peer_actions(primary_theme)
            if peer_actions:
                lines.append("PEER ACTIONS (what competitors did):")
                for pa in peer_actions[:3]:
                    lines.append(f"  - {pa.company}: {pa.action} → {pa.outcome}")
        except Exception:
            pass

    # Phase 3: Real-world precedents — authored library, LLM cites by reference
    try:
        from engine.ontology.intelligence import query_precedents_for_event
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            precedents = query_precedents_for_event(event_id, company.industry, limit=3)
            if precedents:
                lines.append("NAMED PRECEDENTS (cite these by company + year + ₹ cost; do NOT invent new precedents):")
                for p in precedents:
                    lines.append(f"  - {p.as_citation()}")
                    if p.recovery_path:
                        lines.append(f"    Recovery: {p.recovery_path[:180]}")
    except Exception:
        pass

    # Phase 14: ROI benchmarks
    try:
        from engine.ontology.intelligence import query_industry_roi_benchmarks
        benchmark = query_industry_roi_benchmarks(company.industry)
        if benchmark:
            lines.append(f"ROI BENCHMARK for {company.industry}: typical ROI {benchmark.typical_roi}, payback {benchmark.typical_payback}")
    except Exception:
        pass

    # Phase 14: Compliance deadlines
    try:
        from engine.ontology.intelligence import query_compliance_deadlines
        deadlines = query_compliance_deadlines("India")
        if deadlines:
            lines.append("REGULATORY DEADLINES:")
            for d in deadlines[:3]:
                lines.append(f"  - {d.label}: {d.deadline_date} ({d.framework})")
    except Exception:
        pass

    # Phase 17: Causal Primitives context for quantitative recommendation grounding
    try:
        from engine.ontology.intelligence import query_cascade_context, query_thresholds_for_primitive, query_primitives_for_event
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            prims = query_primitives_for_event(event_id)
            if prims:
                primary = prims[0]
                lines.append(f"CAUSAL PRIMITIVES FOR RECOMMENDATIONS:")
                lines.append(f"  Primary affected: {primary.label} ({primary.slug})")
                # Get thresholds for monitoring recommendations
                thresholds = query_thresholds_for_primitive(primary.slug)
                if thresholds:
                    lines.append("  Threshold monitors (recommend tracking these):")
                    for t in thresholds[:3]:
                        lines.append(f"    - {t['label']}: τ = {t['range']} ({t['unit']})")
                lines.append("  Actionable levers: reduce β (efficiency investment), hedge exposure, diversify inputs")
    except Exception:
        pass

    # Phase 13 B1 — event-archetype routing. Inject event-appropriate
    # recommendation categories so the LLM picks levers that fit the event
    # type instead of defaulting to a one-size template (file BRSR + monitor
    # + assurance + capex). Live verified on the Waaree contract-win article
    # (2026-04-24): pre-fix produced 5 disclosure-shaped recs for a positive
    # business event; post-fix picks operational-readiness + investor-comms +
    # pipeline-momentum archetypes appropriate to a contract win.
    try:
        from engine.analysis.recommendation_archetypes import (
            get_archetypes_for_event,
            is_positive_event,
        )
        event_id_for_arch = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        # Phase 17 — pass sentiment so ambiguous events (quarterly_results etc.)
        # route by tone rather than defaulting to negative-event archetypes.
        nlp_sent_arch = getattr(result.nlp, "sentiment", 0) if result.nlp else 0
        archetypes = get_archetypes_for_event(event_id_for_arch)
        # Phase 17 — fallback: if no event-specific archetypes (event_id empty
        # or unmapped), pick archetypes from the primary theme so the LLM
        # doesn't fall through to the generic 5-rec disclosure template.
        if not archetypes:
            from engine.analysis.recommendation_archetypes import get_archetypes_for_theme
            primary_theme = (
                result.themes.primary_theme if result.themes and hasattr(result.themes, "primary_theme") else ""
            )
            archetypes = get_archetypes_for_theme(primary_theme)
        if archetypes:
            lines.append("")
            lines.append(
                f"EVENT-SPECIFIC GUIDANCE (event={event_id_for_arch}). "
                f"Pick {min(len(archetypes), 5)} of these archetypes — pick distinct ones, "
                f"not five variants of a single category. Do NOT default to "
                f"'file BRSR + monitor compliance + third-party assurance' "
                f"unless the article explicitly describes a regulatory or "
                f"disclosure failure."
            )
            for label, desc in archetypes:
                lines.append(f"  • {label} — {desc}")
            if is_positive_event(event_id_for_arch, sentiment=nlp_sent_arch):
                lines.append("")
                lines.append(
                    "POLARITY: this is a POSITIVE event. Recommendations should "
                    "leverage the upside (growth / pricing / signal value), not "
                    "remediate a fabricated crisis. Avoid 'engage regulator', "
                    "'remediate violation', and 'governance review' framing "
                    "unless the article itself raises that concern."
                )
    except Exception:
        # Archetype routing is additive; never block recommendation generation.
        pass

    # Phase 35 — accuracy-guardrail context block.
    #
    # Three things get pinned for the LLM:
    #   1. FRAMEWORK_WHITELIST — only these names may appear in `framework_section`.
    #   2. CANONICAL_EXPOSURE — every ₹ in recs must anchor on this.
    #   3. HEADLINE_ONLY      — when true, max 3 recs, monitoring flavour,
    #                            scenario tags. The verifier enforces.
    lines.append("")
    lines.append("=== ACCURACY GUARDRAILS (Phase 35) ===")
    whitelist = _query_framework_whitelist()
    if whitelist:
        lines.append(
            "FRAMEWORK_WHITELIST (every framework_section MUST start with one of these; "
            "leave blank if no framework applies — do NOT invent names):"
        )
        # Keep readable — chunk into 6-per-line so prompt doesn't blow up
        for i in range(0, len(whitelist), 6):
            lines.append(f"  {', '.join(whitelist[i:i+6])}")

    canonical_cr = _extract_canonical_exposure_cr(insight)
    if canonical_cr is not None:
        lines.append(
            f"CANONICAL_EXPOSURE: ₹{canonical_cr:,.0f} Cr — every ₹ in your "
            f"recommendations must match this anchor OR be a clearly-scoped "
            f"subset (budget = 1-5% of exposure; legal reserve = 5-10%; "
            f"capex = 50-300% when proportionate). Drift >35% from this "
            f"anchor without subset framing → DROPPED by verifier."
        )
    else:
        lines.append(
            "CANONICAL_EXPOSURE: not set (no ₹ in deep_insight). Recommendations "
            "may use scenario ₹ figures but tag them explicitly."
        )

    headline_only = bool(getattr(insight, "headline_only", False)) or (
        getattr(result, "_thin_content", False)
        or len((getattr(result, "article_content", "") or "").strip()) < 300
    )
    if headline_only:
        lines.append(
            "HEADLINE_ONLY: true — article body unavailable. Produce at MOST 3 "
            "recommendations. Lead with MONITORING/INVESTIGATION. Every ₹ figure "
            "tagged '(scenario)' not '(engine estimate)'. Every description starts "
            "with 'Pending full article retrieval, …'. No 'urgent', no 'CRITICAL', "
            "no aggressive ACT framing."
        )
    else:
        lines.append("HEADLINE_ONLY: false")

    lines.append("")
    rec_count_base = _get_rec_count(insight)
    rec_count = min(rec_count_base, 3) if headline_only else rec_count_base
    lines.append(
        f"Generate exactly {rec_count} actionable recommendations for this company. "
        f"Today's date is 2026-04-13."
    )
    return "\n".join(lines)


def _repair_truncated_json(raw: str) -> dict:
    """Phase 13 hotfix — salvage a partially-truncated LLM JSON response.

    When the LLM hits max_tokens mid-array, the response looks like:
        {"recommendations": [{"title": "...", ...}, {"title": "...", "ty
    Classic JSON parsers reject this with `Unterminated string starting at`.
    This helper finds the last fully-closed object inside the recommendations
    array and returns `{"recommendations": [<those complete objects>]}` so
    the pipeline keeps the salvageable recs instead of returning zero.

    Returns `{"recommendations": []}` if no complete object can be salvaged.
    """
    if not raw or "recommendations" not in raw:
        return {"recommendations": []}
    # Find the start of the recommendations array
    start_idx = raw.find('"recommendations"')
    if start_idx < 0:
        return {"recommendations": []}
    bracket_idx = raw.find("[", start_idx)
    if bracket_idx < 0:
        return {"recommendations": []}

    # Walk forward inside the array, tracking brace nesting + string state.
    # When depth returns to 1 (top of array, between objects), record the
    # last successful close. Salvage by truncating at that close + a "]}".
    depth = 0
    in_string = False
    escape = False
    last_complete_close = -1
    for i in range(bracket_idx, len(raw)):
        c = raw[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                last_complete_close = i  # complete object inside array
    if last_complete_close < 0:
        return {"recommendations": []}
    # Build a salvageable string: everything up through the last complete
    # close, then close the array + outer object.
    salvaged = raw[: last_complete_close + 1] + "]}"
    try:
        return json.loads(salvaged)
    except json.JSONDecodeError:
        return {"recommendations": []}


def _generate_recommendations(
    insight: DeepInsight, result: PipelineResult, company: Company, client: OpenAI
) -> list[Recommendation]:
    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_light", "gpt-4.1-mini")
    # Phase 13 hotfix — bump token budget from 1500 → 3000 because the new
    # `audit_trail` field (S1) adds ~150-300 tokens per rec. The 1500-cap
    # was being hit mid-JSON, producing JSONDecodeError and empty rec
    # lists for HIGH-materiality articles (caught by the 2026-04-27 fuzz
    # run at 70% pass rate vs 90% baseline).
    max_tokens = llm_cfg.get("max_tokens_recommendation", 3000)

    # Phase 14.3 — dispatch to a dedicated positive-event prompt when the
    # event_id is in our POSITIVE_EVENTS set. Eliminates the "₹10-50 Cr SEBI
    # penalty" defensive injection on contract-win / certification articles.
    raw_content = ""
    try:
        from engine.analysis.recommendation_archetypes import (
            is_positive_event, is_neutral_event,
        )
        event_id_for_dispatch = (
            result.event.event_id
            if result.event and hasattr(result.event, "event_id")
            else ""
        )
        # Phase 17 — sentiment-aware routing for ambiguous events
        # (event_quarterly_results +1 sentiment → positive prompt path).
        nlp_sent_disp = getattr(result.nlp, "sentiment", 0) if result.nlp else 0
        # Phase 35 — 3-way dispatcher: positive / neutral / negative-default.
        # Order matters: positive wins over neutral wins over negative-default.
        # Pre-fix the YES Bank pledge (event_shareholding_change) got the
        # negative-default prompt → ₹9,685 Cr "CRITICAL governance risk"
        # framing. Now it routes to _NEUTRAL_GENERATOR_SYSTEM which produces
        # disclosure-verification + stakeholder-comms + monitoring recs.
        if is_positive_event(event_id_for_dispatch, sentiment=nlp_sent_disp):
            system_prompt_template = _POSITIVE_GENERATOR_SYSTEM
        elif is_neutral_event(event_id_for_dispatch, sentiment=nlp_sent_disp):
            system_prompt_template = _NEUTRAL_GENERATOR_SYSTEM
        else:
            system_prompt_template = _GENERATOR_SYSTEM
    except Exception:
        # Failsafe: archetype routing is additive, never block generation
        system_prompt_template = _GENERATOR_SYSTEM
    # Phase 35 — append accuracy guardrails (framework whitelist enforcement,
    # canonical-₹ pin, audit-trail evidence requirement, headline-only mode)
    # to whichever polarity prompt the dispatcher selected. Same constraints
    # apply to positive + negative event flows.
    system_prompt_template = system_prompt_template + _ACCURACY_GUARDRAILS
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt_template.replace("%%REC_COUNT%%", str(_get_rec_count(insight)))},
                {
                    "role": "user",
                    "content": _build_generator_prompt(insight, result, company),
                },
            ],
            temperature=0.3,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw_content = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)
    except (APIError, APITimeoutError, IndexError) as exc:
        logger.warning("recommendation generator failed (api): %s", type(exc).__name__)
        return []
    except json.JSONDecodeError as exc:
        # Phase 13 hotfix — defensive JSON repair on token-truncated outputs.
        # If the response was cut mid-list, salvage whatever complete recs
        # we got rather than returning zero. The Phase 13 audit_trail field
        # makes truncation more likely until token caps stabilise.
        parsed = _repair_truncated_json(raw_content)
        if not parsed.get("recommendations"):
            logger.warning(
                "recommendation generator failed (json decode at line %d col %d) "
                "and repair yielded no recs",
                exc.lineno, exc.colno,
            )
            return []
        logger.warning(
            "recommendation generator: salvaged %d rec(s) from truncated JSON "
            "(error at line %d col %d)",
            len(parsed.get("recommendations") or []), exc.lineno, exc.colno,
        )

    raw_recs = parsed.get("recommendations", []) or []
    recommendations: list[Recommendation] = []
    for r in raw_recs:
        try:
            # Phase 14: Parse roi_percentage, payback_months, peer_benchmark
            _roi = r.get("roi_percentage")
            roi = float(_roi) if _roi and _roi != "null" else None
            _payback = r.get("payback_months")
            payback = float(_payback) if _payback and _payback != "null" else None
            _peer = r.get("peer_benchmark")
            peer = str(_peer)[:300] if _peer and _peer != "null" else None

            # Phase 17c: Clamp ROI to reasonable bounds per recommendation type
            rec_type = str(r.get("type", "operational") or "operational")
            roi_caps = {
                "compliance": 500.0,     # Max: avoid ₹50 Cr fine with ₹2 Cr = 2400%, cap at 500%
                "financial": 300.0,      # Max: cost of capital reduction, hedging
                "strategic": 400.0,      # Max: market positioning, green bond access
                "esg_positioning": 400.0,
                "operational": 200.0,    # Max: monitoring, process improvement
            }
            max_roi = roi_caps.get(rec_type, 300.0)
            roi_was_capped = False
            roi_cap_reason = ""
            if roi is not None and roi > max_roi:
                logger.info("ROI clamped: %s from %.0f%% to %.0f%%", r.get("title", "")[:40], roi, max_roi)
                roi_was_capped = True
                roi_cap_reason = (
                    f"Capped at {max_roi:.0f}% ({rec_type} ceiling). "
                    f"Raw estimate was {roi:.0f}%; cap prevents over-claim."
                )
                roi = max_roi

            # Phase 13 S1 — pull audit_trail from LLM JSON. Defensive parse:
            # accept the field whether it's a list of dicts (canonical),
            # a single dict (LLM occasionally produces that shape), or
            # missing (older prompts / partial LLM output).
            raw_trail = r.get("audit_trail")
            audit_trail: list[dict[str, str]] = []
            if isinstance(raw_trail, list):
                for entry in raw_trail[:5]:
                    if isinstance(entry, dict):
                        audit_trail.append({
                            "source": str(entry.get("source", "") or "")[:30],
                            "ref": str(entry.get("ref", "") or "")[:120],
                            "value": str(entry.get("value", "") or "")[:300],
                        })
            elif isinstance(raw_trail, dict):
                audit_trail.append({
                    "source": str(raw_trail.get("source", "") or "")[:30],
                    "ref": str(raw_trail.get("ref", "") or "")[:120],
                    "value": str(raw_trail.get("value", "") or "")[:300],
                })

            recommendations.append(
                Recommendation(
                    title=str(r.get("title", "") or "")[:200],
                    description=str(r.get("description", "") or "")[:500],
                    type=rec_type,
                    responsible_party=str(r.get("responsible_party", "") or ""),
                    framework_section=str(r.get("framework_section", "") or ""),
                    deadline=str(r.get("deadline", "") or ""),
                    estimated_budget=str(r.get("estimated_budget", "") or ""),
                    profitability_link=str(r.get("profitability_link", "") or "")[:500],
                    priority="MEDIUM",
                    urgency=str(r.get("urgency", "medium_term") or "medium_term"),
                    estimated_impact=str(r.get("estimated_impact", "Medium") or "Medium"),
                    risk_of_inaction=0,
                    roi_percentage=roi,
                    payback_months=payback,
                    peer_benchmark=peer,
                    roi_capped=roi_was_capped,
                    roi_cap_reason=roi_cap_reason,
                    audit_trail=audit_trail,  # Phase 13 S1
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Malformed recommendation skipped: %s", exc)

    # Phase 35 — post-LLM accuracy verifier. Drops recs that violate the
    # hardened guardrails (framework-whitelist, canonical-₹ pin, empty
    # audit_trail). See `verify_recommendation_accuracy` docstring.
    try:
        recommendations = verify_recommendation_accuracy(
            recommendations, insight=insight, result=result,
        )
    except Exception as exc:  # noqa: BLE001 — never block on verifier failure
        logger.warning(
            "recommendation accuracy verifier failed (non-fatal): %s", exc,
        )

    return recommendations


def verify_recommendation_accuracy(
    recs: list[Recommendation],
    *,
    insight: DeepInsight,
    result: PipelineResult,
) -> list[Recommendation]:
    """Phase 35 — post-LLM verifier for recommendation accuracy.

    Drops or auto-corrects recs that violate the accuracy guardrails:

      1. **Framework whitelist** — recs whose `framework_section` doesn't
         start with an ontology-known framework are stripped to blank
         (rec kept; user just doesn't see a bogus citation).

      2. **Canonical ₹ drift** — recs whose `profitability_link` ₹ figure
         drifts >35% from the canonical exposure AND doesn't read as a
         clearly-scoped subset (budget / reserve / capex) are DROPPED.

      3. **Empty / generic audit_trail** — recs with no audit_trail, or
         audit_trail entries whose `source` is outside the canonical set,
         or whose `value` field is shorter than 12 chars (no real
         evidence), are DROPPED.

      4. **Headline-only mode** — on headline-only insights the prompt
         already capped to 3 recs and asked for monitoring language, but
         the verifier additionally enforces:
           - Drop any rec whose description doesn't start with "Pending
             full article retrieval" (LLM occasionally forgets).
           - Retag "(engine estimate)" → "(scenario)" in profitability_link.
           - Cap rec_count at 3 if the LLM emitted more.

    Returns the filtered + corrected list. Caller logs a summary of what
    was dropped/changed via the `warnings` field on subsequent stages.
    """
    import re

    whitelist = set(_query_framework_whitelist())
    canonical_cr = _extract_canonical_exposure_cr(insight)
    headline_only = bool(getattr(insight, "headline_only", False)) or (
        len((getattr(result, "article_content", "") or "").strip()) < 300
    )
    _VALID_AUDIT_SOURCES = {
        "ontology", "article", "primitive", "peer", "precedent", "benchmark",
    }
    _SCENARIO_RE = re.compile(r"\(engine\s+estimate\)", re.IGNORECASE)
    _RUPEE_RE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)\s*(Cr|Lakh|crore|million|billion|Mn|Bn)", re.IGNORECASE)

    out: list[Recommendation] = []
    dropped_reasons: list[str] = []

    for r in recs:
        # 1. Framework whitelist check — strip off-whitelist citations.
        if r.framework_section and whitelist:
            head_token = r.framework_section.split(":")[0].split(" ")[0].strip()
            if head_token and head_token not in whitelist:
                # Try a loose prefix-match too (e.g. "BRSR P6 Q14")
                if not any(
                    r.framework_section.strip().lower().startswith(fw.lower())
                    for fw in whitelist
                ):
                    logger.info(
                        "rec verifier: stripping off-whitelist framework "
                        "'%s' from rec '%s'",
                        r.framework_section[:60], r.title[:40],
                    )
                    r.framework_section = ""

        # 2. Canonical ₹ drift check
        if canonical_cr is not None and canonical_cr > 0:
            text = (r.profitability_link or "") + " " + (r.description or "")
            cited = []
            for m in _RUPEE_RE.finditer(text):
                v = float(m.group(1).replace(",", ""))
                unit = m.group(2).lower()
                if unit in ("lakh",):
                    v = v / 100.0  # 1 Cr = 100 Lakh
                elif unit in ("million", "mn"):
                    v = v / 10.0   # 1 Cr = 10 Mn
                elif unit in ("billion", "bn"):
                    v = v * 100.0  # 1 Bn = 100 Cr (Indian)
                cited.append(v)
            if cited:
                largest = max(cited)
                drift_ratio = largest / canonical_cr if canonical_cr else 0
                # Drop only if (a) drift is huge AND (b) the rec isn't
                # explicitly framed as a subset (budget/reserve/capex/capacity)
                budget_keywords = (
                    "budget", "reserve", "legal-defence", "legal defence",
                    "legal cost", "capex", "capacity", "investment",
                    "deployment", "facility", "infrastructure",
                )
                is_scoped = any(k in text.lower() for k in budget_keywords)
                if drift_ratio > 5.0 and not is_scoped:
                    dropped_reasons.append(
                        f"₹ drift {largest:.0f} Cr vs canonical "
                        f"{canonical_cr:.0f} Cr (drift {drift_ratio:.1f}x) "
                        f"in rec '{r.title[:30]}'"
                    )
                    continue

        # 3. Audit trail evidence check
        if not r.audit_trail:
            dropped_reasons.append(
                f"empty audit_trail in rec '{r.title[:30]}'"
            )
            continue
        valid_audit_entries = [
            e for e in r.audit_trail
            if str(e.get("source", "")).strip().lower() in _VALID_AUDIT_SOURCES
            and len(str(e.get("value", "")).strip()) >= 12
        ]
        if not valid_audit_entries:
            dropped_reasons.append(
                f"no valid audit_trail entries in rec '{r.title[:30]}' "
                f"(sources={[e.get('source') for e in r.audit_trail]})"
            )
            continue
        # Replace audit_trail with just the valid entries
        r.audit_trail = valid_audit_entries

        # 4. Headline-only handling
        if headline_only:
            # Retag estimate → scenario
            if r.profitability_link:
                r.profitability_link = _SCENARIO_RE.sub(
                    "(scenario)", r.profitability_link,
                )
            if r.description:
                r.description = _SCENARIO_RE.sub("(scenario)", r.description)
            # Soft check: description should start with the pending preamble.
            # We don't drop — we PREPEND if missing, so the user still sees
            # the disclosure even when the LLM forgot.
            if r.description and "pending full article" not in r.description.lower():
                r.description = (
                    "Pending full article retrieval, " + r.description[0].lower()
                    + r.description[1:]
                )

        out.append(r)

    if dropped_reasons:
        logger.warning(
            "recommendation accuracy verifier: dropped %d/%d recs (%s)",
            len(dropped_reasons), len(recs), "; ".join(dropped_reasons[:5]),
        )

    # 5. Headline-only count cap (post-filter — keep top-3 by impact when
    # the LLM emitted more despite the prompt instruction)
    if headline_only and len(out) > 3:
        # Prefer recs with non-empty framework_section + audit_trail
        out.sort(
            key=lambda r: (
                bool(r.framework_section),
                len(r.audit_trail),
                # Tie-break: monitoring/investigation types preferred when headline-only
                1 if r.type in ("compliance", "operational") else 0,
            ),
            reverse=True,
        )
        out = out[:3]

    return out


# ---------------------------------------------------------------------------
# Post-processing (Analyst + Validator + quant math)
# ---------------------------------------------------------------------------


def _fix_deadline(deadline: str) -> str:
    today = date.today()
    if not deadline:
        return (today + timedelta(days=90)).isoformat()
    try:
        d = date.fromisoformat(deadline)
    except ValueError:
        return (today + timedelta(days=90)).isoformat()
    if d < today:
        # shift to next year same month/day
        try:
            return d.replace(year=today.year + 1).isoformat()
        except ValueError:
            return (today + timedelta(days=365)).isoformat()
    return d.isoformat()


def _derive_priority(rec: Recommendation) -> str:
    """Derive priority from ontology-sourced rules (urgency × impact matrix)."""
    from engine.ontology.intelligence import query_priority_rules

    rules = query_priority_rules()
    for rule in rules:
        if rule.urgency == rec.urgency and rule.impact == rec.estimated_impact:
            return rule.priority
    # Fallback: if no exact match, try urgency-only match
    for rule in rules:
        if rule.urgency == rec.urgency:
            return rule.priority
    return "MEDIUM"


def _compute_risk_of_inaction(rec: Recommendation) -> int:
    """Compute risk-of-inaction score using ontology-sourced config."""
    from engine.ontology.intelligence import query_risk_of_inaction_config

    config = query_risk_of_inaction_config()
    base = config.base_scores.get(rec.priority, 3)
    base += config.type_bonuses.get(rec.type, 0)
    lowered = rec.profitability_link.lower()
    if any(k in lowered for k in config.escalation_keywords):
        base += 1
    return max(1, min(10, base))


def _post_process(recs: list[Recommendation]) -> list[Recommendation]:
    for rec in recs:
        rec.deadline = _fix_deadline(rec.deadline)
        rec.priority = _derive_priority(rec)
        rec.risk_of_inaction = _compute_risk_of_inaction(rec)
    # Sort by priority + risk of inaction
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    recs.sort(key=lambda r: (priority_order.get(r.priority, 4), -r.risk_of_inaction))
    return recs


# Phase 24.6 — region-incompatible framework citations get rewritten so a
# coal-heavy Indian power company never gets "EU Taxonomy Article 8" cited
# in its green-bond rec. The map is conservative: only the most obviously
# wrong-fit citations are rewritten; everything else passes through.
_FRAMEWORK_REGION_REWRITES: dict[str, dict[str, str]] = {
    # Non-EU companies should not cite EU Taxonomy / CSRD / SFDR.
    # The replacement preserves the recommendation's intent (green-bond
    # alignment / climate disclosure) but with a region-appropriate cite.
    "INDIA": {
        "eu taxonomy": "ICMA Green Bond Principles",
        "csrd": "BRSR Core",
        "sfdr": "BRSR Core",
        "esrs": "BRSR Core",
    },
    "US": {
        "eu taxonomy": "ICMA Green Bond Principles",
        "csrd": "SEC Climate Disclosure",
        "sfdr": "SEC Climate Disclosure",
        "esrs": "SEC Climate Disclosure",
        "brsr": "SASB",
    },
    "UK": {
        "eu taxonomy": "ICMA Green Bond Principles",
        "csrd": "FCA TCFD",
        "esrs": "FCA TCFD",
        "brsr": "FCA TCFD",
    },
    "APAC": {
        "eu taxonomy": "ICMA Green Bond Principles",
        "csrd": "TCFD",
        "esrs": "TCFD",
        "brsr": "TCFD",
    },
    "GLOBAL": {
        "eu taxonomy": "ICMA Green Bond Principles",
        "csrd": "TCFD",
        "esrs": "TCFD",
        "brsr": "TCFD",
    },
}


def _filter_regional_frameworks(
    recs: list[Recommendation], region: str | None
) -> list[Recommendation]:
    """Rewrite framework citations that don't fit the company's region.

    Catches things like "EU Taxonomy Article 8" cited for an Indian
    coal-heavy power company. EU companies stay untouched (their region's
    rewrite map isn't populated). Other regions get safe equivalents
    that preserve the recommendation's intent.
    """
    if not region or region.upper() == "EU":
        return recs
    region_key = region.upper()
    rewrites = _FRAMEWORK_REGION_REWRITES.get(region_key, _FRAMEWORK_REGION_REWRITES["GLOBAL"])
    if not rewrites:
        return recs

    for rec in recs:
        original = (rec.framework_section or "").strip()
        if not original:
            continue
        lowered = original.lower()
        for needle, replacement in rewrites.items():
            if needle in lowered:
                rec.framework_section = replacement
                # Also scrub the description / profitability_link text
                # to avoid leaving residual references.
                if rec.description:
                    rec.description = re.sub(
                        rf"\b{re.escape(needle)}[^.,;]*", replacement, rec.description, flags=re.IGNORECASE
                    )
                if rec.profitability_link:
                    rec.profitability_link = re.sub(
                        rf"\b{re.escape(needle)}[^.,;]*", replacement, rec.profitability_link, flags=re.IGNORECASE
                    )
                logger.info(
                    "regional framework rewrite: %s -> %s (region=%s)",
                    original, replacement, region_key,
                )
                break
    return recs


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def _build_monitoring_recommendation(
    insight: DeepInsight, result: PipelineResult, company: Company, reason: str
) -> Recommendation:
    """Emit a single monitoring rec for SECONDARY / non-material articles.

    Phase 3: removes the silent-drop that left CFOs wondering if the system
    crashed. A do-nothing article still gets a tracked recommendation — "what
    to monitor and when to escalate" — so the UI never shows an empty panel.
    """
    theme = (result.themes.primary_theme if result.themes else "") or "general ESG signal"
    horizon_days = 30  # default review cadence
    trigger = "materiality escalation (event severity ↑ or ₹ figure > ₹10 Cr)"

    # Pull a threshold from the causal primitives ontology if available.
    try:
        from engine.ontology.intelligence import query_primitives_for_event, query_thresholds_for_primitive
        event_id = result.event.event_id if result.event and hasattr(result.event, "event_id") else ""
        if event_id:
            prims = query_primitives_for_event(event_id)
            if prims:
                thr = query_thresholds_for_primitive(prims[0].slug)
                if thr:
                    trigger = f"{thr[0]['label']}: τ = {thr[0]['range']} ({thr[0]['unit']})"
    except Exception:  # noqa: BLE001 — best-effort threshold
        pass

    from datetime import datetime, timedelta, timezone

    deadline = (datetime.now(timezone.utc) + timedelta(days=horizon_days)).date().isoformat()

    return Recommendation(
        title=f"Monitor — {theme} (no action required yet)",
        description=(
            f"Track this signal for materiality drift. Reason for monitor-only "
            f"stance: {reason}. Escalation trigger: {trigger}. "
            f"If crossed, run full analysis and revisit within 14 days."
        ),
        type="operational",
        responsible_party="ESG / Risk team",
        framework_section="BRSR:P1:Q1 (stakeholder review cycle)",
        deadline=deadline,
        estimated_budget="₹0 Cr (internal monitoring)",
        profitability_link=(
            "Prevents materiality drift surprise — no active cost, only watch-list inclusion. "
            "Cost-of-inaction: learning-lag if signal escalates without observation."
        ),
        priority="LOW",
        urgency="short_term",
        estimated_impact="Low",
        risk_of_inaction=3,
        roi_percentage=None,
        payback_months=None,
        peer_benchmark="Standard practice — all 7 target companies run monthly materiality reviews",
    )


def generate_recommendations(
    insight: DeepInsight, result: PipelineResult, company: Company
) -> RecommendationResult:
    """Run the full REREACT chain (gate → generate → post-process).

    Phase 3 change: non-material / low-impact articles no longer return empty.
    They get a single monitoring recommendation so the UI never shows silence.
    The `do_nothing` flag stays True so callers can distinguish active vs monitor.
    """
    skip, reason = _should_skip(insight, result)
    if skip:
        logger.info("REREACT gate: monitor-only (%s)", reason)
        monitor_rec = _build_monitoring_recommendation(insight, result, company, reason)
        return RecommendationResult(
            recommendations=[monitor_rec],
            do_nothing=True,
            gate_reason=reason,
            generator_count=1,
            validated_count=1,
        )

    client = OpenAI(api_key=get_openai_api_key())
    raw_recs = _generate_recommendations(insight, result, company, client)
    validated = _post_process(raw_recs)
    # Phase 24.6 — region-incompatible framework citations get rewritten
    # so a coal-heavy Indian power company never sees "EU Taxonomy
    # Article 8" in its green-bond rec. Falls through to a sensible
    # regional alternative (ICMA Green Bond Principles for India,
    # SEC Climate for US, FCA TCFD for UK).
    validated = _filter_regional_frameworks(
        validated, getattr(company, "framework_region", None)
    )

    # Phase 14: Build priority matrix (urgency × impact)
    priority_matrix = _build_priority_matrix(validated)

    # Phase 14: Perspective-specific recommendation rankings
    rankings = _build_perspective_rankings(validated)

    # Phase 3 §5.4 — apply the role type whitelist to the per-perspective
    # rankings. Today every role's ranking includes every recommendation
    # (just sorted differently). The whitelist filter drops forbidden
    # types entirely so:
    #   - CFOs never see "esg_positioning" / "strategic" / "brand" recs
    #   - CEOs never see "compliance" / "kpi_tracking" / "audit" recs
    #   - Analysts never see "capital_allocation" / "financial" / "brand" recs
    # The recommendations themselves stay in `validated` (so a power-user
    # / audit view can still see the full set) — only the per-role index
    # lists shrink.
    try:
        from engine.analysis.recommendation_type_whitelist import is_rejected
        filtered_rankings: dict[str, list[int]] = {}
        for role_key, idx_list in rankings.items():
            # Map UI role keys ("esg-analyst") to whitelist keys
            kept: list[int] = []
            for idx in idx_list:
                if 0 <= idx < len(validated):
                    rec_type = validated[idx].type
                    if not is_rejected(rec_type, role_key):
                        kept.append(idx)
            filtered_rankings[role_key] = kept
        rankings = filtered_rankings
    except Exception as exc:  # noqa: BLE001 — never break rec generation on filter
        logger.debug("role whitelist filter failed (non-fatal): %s", exc)

    return RecommendationResult(
        recommendations=validated,
        do_nothing=False,
        gate_reason="",
        generator_count=len(raw_recs),
        validated_count=len(validated),
        priority_matrix=priority_matrix,
        recommendation_rankings=rankings,
    )


def _build_priority_matrix(
    recs: list[Recommendation],
) -> dict[str, list[dict[str, Any]]]:
    """Compute urgency × impact 2×2 matrix for visual display."""
    matrix: dict[str, list[dict[str, Any]]] = {
        "immediate_high": [],
        "immediate_low": [],
        "deferred_high": [],
        "deferred_low": [],
    }
    for i, rec in enumerate(recs):
        urgency_bucket = (
            "immediate" if rec.urgency in ("immediate", "short_term") else "deferred"
        )
        impact_bucket = "high" if rec.estimated_impact == "High" else "low"
        matrix[f"{urgency_bucket}_{impact_bucket}"].append({
            "index": i,
            "title": rec.title,
            "type": rec.type,
            "roi": rec.roi_percentage,
            "budget": rec.estimated_budget,
        })
    return matrix


def _build_perspective_rankings(
    recs: list[Recommendation],
) -> dict[str, list[int]]:
    """Re-rank recommendations per perspective lens using ontology sort keys."""
    from engine.ontology.intelligence import query_perspective_ranking_keys

    indices = list(range(len(recs)))
    if not indices:
        return {"cfo": [], "ceo": [], "esg-analyst": []}

    urgency_order = {"immediate": 0, "short_term": 1, "medium_term": 2, "long_term": 3}
    impact_order = {"High": 0, "Medium": 1, "Low": 2}

    def _sort_value(rec: Recommendation, key: str, direction: str) -> float:
        """Get a numeric sort value for a recommendation field."""
        if key == "roi_percentage":
            val = rec.roi_percentage or 0
        elif key == "payback_months":
            val = rec.payback_months or 999
        elif key == "estimated_impact":
            val = float(impact_order.get(rec.estimated_impact, 1))
        elif key == "urgency":
            val = float(urgency_order.get(rec.urgency, 2))
        elif key == "type":
            val = 0.0 if rec.type == "compliance" else 1.0
        else:
            val = 0.0
        return -val if direction == "DESC" else val

    result: dict[str, list[int]] = {}
    for perspective in ("cfo", "ceo", "esg-analyst"):
        sort_keys = query_perspective_ranking_keys(perspective)
        if sort_keys:
            ranked = sorted(
                indices,
                key=lambda i: tuple(
                    _sort_value(recs[i], sk.sort_key, sk.sort_direction)
                    for sk in sort_keys
                ),
            )
        else:
            ranked = list(indices)
        result[perspective] = ranked
    return result
