"""W3 — LLM-driven sustainability painpoint discoverer.

Runs once per new tenant during `onboard_company`. Asks an LLM to research
the company's specific ESG profile (NOT just the industry default) and
returns structured painpoints that get written to a per-tenant
`painpoints.ttl` Layer 3 ontology overlay.

Cost ceiling: ONE `gpt-4.1` call per onboard, ~400-600 output tokens =
~$0.05. Idempotent — caller skips if `painpoints.ttl` exists and is < 90
days old.

Design:
  * Single function `discover_painpoints(domain, name, industry, sasb,
    region) -> PainpointReport`.
  * `PainpointReport` is a dataclass with `painpoints[]`, `primary_frameworks`,
    `stakeholder_concerns`, `headline_painpoints`.
  * Returns an empty report on any LLM error (caller continues with
    industry-default materiality).

Output shape (what the LLM must return):

    {
      "painpoints": [
        {
          "topic": "Scope 1 emissions",
          "topic_slug": "carbon",   # one of the canonical 21 ESG themes
          "severity": 0.95,         # 0.0-1.0; how material vs the industry baseline
          "evidence": "Tata Steel runs integrated coking-coal mills; FY24 BRSR P6
                       reports 2.3 tCO2e per tonne crude steel — top quartile of
                       global peers but still above the SBTi 1.5C pathway",
          "confidence": 0.9         # 0.0-1.0; LLM self-rated certainty
        },
        ...
      ],
      "primary_frameworks": ["BRSR", "GRI:305", "ESRS:E1", "SBTi"],
      "stakeholder_concerns": [
        "Regulatory pressure from CBAM EU border carbon levy",
        "Lender pressure on transition financing"
      ],
      "headline_painpoints": [
        "Tata Steel Jharkhand water stress",
        "Tata Steel CBAM exposure"
      ]
    }

The `headline_painpoints[]` array feeds into `_build_queries()` so the
news fetcher gets 3-5 EXTRA painpoint-flavoured queries on top of the
generic 25-28 industry/region terms.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Painpoint:
    """One material ESG painpoint for a specific company."""
    topic: str
    topic_slug: str
    severity: float  # 0.0 - 1.0
    evidence: str
    confidence: float  # 0.0 - 1.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PainpointReport:
    """Full per-tenant painpoint profile."""
    painpoints: list[Painpoint] = field(default_factory=list)
    primary_frameworks: list[str] = field(default_factory=list)
    stakeholder_concerns: list[str] = field(default_factory=list)
    headline_painpoints: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.painpoints

    def as_dict(self) -> dict[str, Any]:
        return {
            "painpoints": [p.as_dict() for p in self.painpoints],
            "primary_frameworks": self.primary_frameworks,
            "stakeholder_concerns": self.stakeholder_concerns,
            "headline_painpoints": self.headline_painpoints,
        }


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You are an ESG materiality analyst with deep knowledge of the SASB
Materiality Map, GRI Standards, and regional disclosure frameworks (BRSR/India,
CSRD-ESRS/EU, SEC Climate/US, SDR/UK).

Your job: given a single company's name, industry, region, and domain, return
a JSON profile of THIS company's most material ESG painpoints. Not the
industry default — the company-specific exposures that distinguish them
from peers in the same SASB category.

CRITICAL RULES:
- Use ONLY information you can defend from the company name + industry +
  region. If you don't know a specific operational fact, omit it. Do NOT
  invent ₹ figures, plant locations, or regulatory citations.
- Each painpoint MUST cite a topic_slug from this canonical list:
  carbon, water, waste, biodiversity, energy, air_pollution, land_use,
  climate_adaptation,
  labor_practices, human_rights, supply_chain_labor, community_impact,
  product_safety, customer_data, employee_health,
  governance, board_diversity, executive_pay, anti_corruption, business_ethics,
  cyber_risk
- `severity` is your judgement of how material this topic is for THIS
  company specifically (vs. the industry baseline). 1.0 = existential
  exposure; 0.7 = top-3 concern; 0.5 = peer-average; <0.4 = below average.
- `confidence` reflects how certain you are about your assessment.
  >0.8 = strong evidence; 0.5-0.8 = reasonable inference; <0.5 = speculative.
- Keep `evidence` to 1-2 sentences, anchored on a fact a sustainability
  analyst could verify (operational footprint, jurisdictional exposure,
  industry-specific regulation, or peer-benchmark gap).
- Return between 4 and 8 painpoints — no more, no less.
- `headline_painpoints` are 3-5 short search-query-shaped phrases that a
  news scraper could use to find articles about this company's specific
  ESG exposures (e.g. "Tata Steel CBAM exposure", "Adani Power coal
  Scope 3"). They will be appended to the news-query list verbatim.

Return ONLY a JSON object matching this exact schema:

{
  "painpoints": [
    {"topic": str, "topic_slug": str, "severity": float, "evidence": str, "confidence": float}
  ],
  "primary_frameworks": [str],
  "stakeholder_concerns": [str],
  "headline_painpoints": [str]
}
"""


def _build_user_prompt(
    *,
    domain: str,
    company_name: str,
    industry: str,
    sasb_category: str,
    region: str,
) -> str:
    return (
        "=== COMPANY ===\n"
        f"Name: {company_name}\n"
        f"Domain: {domain}\n"
        f"Industry: {industry}\n"
        f"SASB category: {sasb_category}\n"
        f"Headquartered region: {region}  "
        "(Use the region's primary disclosure framework — BRSR for INDIA, "
        "CSRD/ESRS for EU, SEC Climate for US, FCA SDR for UK.)\n\n"
        "Produce the painpoint profile for this specific company. Anchor "
        "each painpoint on something I could verify against their public "
        "disclosures, operational footprint, or industry-regulator filings — "
        "no speculation."
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def discover_painpoints(
    *,
    domain: str,
    company_name: str,
    industry: str,
    sasb_category: str = "Other / General",
    region: str = "GLOBAL",
) -> PainpointReport:
    """Single LLM call returning the structured painpoint report.

    Returns an empty report on any error so the caller can continue with
    the industry-default materiality weights from
    ``industry_materiality_defaults.py``.
    """
    try:
        from openai import APIError, APITimeoutError, OpenAI
        from engine.config import get_openai_api_key, load_settings
    except ImportError as exc:
        logger.warning("painpoint_discoverer: openai import failed: %s", exc)
        return PainpointReport()

    settings = load_settings()
    llm_cfg = settings.get("llm", {})
    model = llm_cfg.get("model_heavy", "gpt-4.1")
    user_prompt = _build_user_prompt(
        domain=domain,
        company_name=company_name,
        industry=industry,
        sasb_category=sasb_category,
        region=region,
    )

    try:
        from engine.llm import get_llm_client
        client = get_llm_client(task_class="classification").sync
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        from engine.models.llm_calls import log_openai_usage
        log_openai_usage(resp, model=model, stage="painpoint_discoverer")
        raw = resp.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            logger.warning("painpoint_discoverer: LLM returned non-dict — using empty")
            return PainpointReport()
    except (APIError, APITimeoutError, json.JSONDecodeError) as exc:
        logger.warning(
            "painpoint_discoverer: LLM failed (%s) — returning empty report",
            type(exc).__name__,
        )
        return PainpointReport()

    return _parse_report(parsed, company_name=company_name)


# ---------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------


_VALID_TOPIC_SLUGS = {
    "carbon", "water", "waste", "biodiversity", "energy", "air_pollution",
    "land_use", "climate_adaptation",
    "labor_practices", "human_rights", "supply_chain_labor", "community_impact",
    "product_safety", "customer_data", "employee_health",
    "governance", "board_diversity", "executive_pay", "anti_corruption",
    "business_ethics", "cyber_risk",
}


def _parse_report(raw: dict[str, Any], *, company_name: str) -> PainpointReport:
    """Validate + coerce the LLM output into a PainpointReport. Drops
    malformed entries silently (better than failing the whole onboard)."""
    painpoints: list[Painpoint] = []
    for entry in raw.get("painpoints", []) or []:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("topic_slug") or "").strip().lower()
        if slug not in _VALID_TOPIC_SLUGS:
            logger.debug(
                "painpoint_discoverer: dropped painpoint with unknown topic_slug=%r for %s",
                slug, company_name,
            )
            continue
        try:
            painpoints.append(Painpoint(
                topic=str(entry.get("topic", "")).strip()[:200],
                topic_slug=slug,
                severity=_clip01(entry.get("severity", 0.0)),
                evidence=str(entry.get("evidence", "")).strip()[:600],
                confidence=_clip01(entry.get("confidence", 0.5)),
            ))
        except Exception:  # noqa: BLE001
            continue

    return PainpointReport(
        painpoints=painpoints,
        primary_frameworks=[
            str(f).strip()[:60] for f in (raw.get("primary_frameworks") or []) if f
        ][:10],
        stakeholder_concerns=[
            str(s).strip()[:200] for s in (raw.get("stakeholder_concerns") or []) if s
        ][:8],
        headline_painpoints=[
            str(h).strip()[:120] for h in (raw.get("headline_painpoints") or []) if h
        ][:5],
    )


def _clip01(v: Any) -> float:
    """Coerce to float in [0.0, 1.0]. Returns 0.5 on parse failure."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f
