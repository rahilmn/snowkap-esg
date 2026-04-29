"""Legacy-UI API adapter.

Exposes the old `/api/auth/*`, `/api/news/*`, `/api/agent/*`, `/api/preferences/*`,
`/api/predictions/*`, `/api/ontology/*`, `/api/admin/*` endpoints on top of the
new ontology-driven engine so the restored legacy React UI can run without
touching any code in `backend/` (which is ignored entirely at runtime).

Intelligence sourcing guarantee (hard requirement from the approved plan):
    Every field rendered by a legacy panel must come from one of:
      - data/snowkap.db (SQLite index over the new pipeline's JSON outputs)
      - data/outputs/{slug}/insights/*.json (12-stage pipeline output)
      - data/ontology/*.ttl (the 2950-triple rdflib graph)
      - config/companies.json (7 target-company metadata)
      - an OpenAI call (agent chat with ontology-built system prompt)

    This module MUST NOT import anything from `backend/`. A grep for
    `from backend` or `import backend` in this file must return zero hits.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth import require_auth
from api.auth_context import SUPER_ADMIN_PERMISSIONS, is_snowkap_super_admin, mint_bearer
from engine.config import Company, get_data_path, load_companies, load_settings
from engine.index import tenant_registry
from engine.index import sqlite_index
from engine.ontology import intelligence as onto_q
from engine.ontology.causal_engine import find_causal_chains
from engine.ontology.graph import OntologyGraph

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["legacy"])

# Phase 13 S3 — ontology graph is eager-loaded at app startup (see
# api/main.py::_startup) so a corrupt or missing TTL fails the boot health
# check rather than the FIRST user request mid-demo. The lazy fallback
# stays here for tests/scripts that import `_graph()` without booting the
# full FastAPI app.
_GRAPH: OntologyGraph | None = None


def _graph() -> OntologyGraph:
    """Return the ontology graph, loading it if not already cached.

    Phase 13 S3: in production this is a hot cache hit because
    `eager_load_ontology()` runs at startup. For test/script callers
    that haven't booted the API, falls back to lazy load.
    """
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = OntologyGraph().load()
    return _GRAPH


def eager_load_ontology() -> OntologyGraph:
    """Phase 13 S3 — explicit eager-load entry point invoked at FastAPI
    app startup. Surfaces TTL syntax errors / missing files at boot rather
    than at first request mid-demo. Returns the loaded graph for tests
    that want to assert load success."""
    global _GRAPH
    _GRAPH = OntologyGraph().load()
    return _GRAPH


# =============================================================================
# Helpers: build legacy Article shape from new pipeline output
# =============================================================================


# Per-perspective grid columns. Each lens emphasises different dimensions —
# CFO sees pure financial impact, CEO sees strategic positioning, ESG Analyst
# sees the canonical 3-column ESG view.
_PERSPECTIVE_GRID_COLUMNS = {
    "esg-analyst": ("financial", "regulatory", "strategic"),
    "cfo": ("financial", "cost", "regulatory"),
    "ceo": ("strategic", "brand", "growth"),
}


def _classify_grid_level(score: float) -> str:
    if score >= 7:
        return "HIGH"
    if score >= 4:
        return "MEDIUM"
    return "LOW"


def _reshape_perspective(
    view: dict[str, Any],
    insight: dict[str, Any],
    lens: str,
) -> dict[str, Any]:
    """Re-transform a stored perspective view to make it visibly distinct.

    The pipeline already populates ``active_impact_dimensions`` (from
    ontology SPARQL) and a perspective-aware ``what_matters`` bullet list.
    This function additionally:
      - relabels the impact_grid columns per lens (CFO sees Financial/Cost/
        Regulatory, CEO sees Strategic/Brand/Growth, ESG Analyst keeps the
        canonical 3-column ESG view)
      - rescores each grid column from ontology-sourced sub-scores
      - reframes the headline per lens so the user sees a meaningfully
        different brief on each toggle
    """
    if not isinstance(view, dict):
        return view
    sub_scores = (insight.get("esg_relevance_score") or {}) if isinstance(insight, dict) else {}

    def _score(*keys: str) -> float:
        best = 0.0
        for k in keys:
            entry = sub_scores.get(k) or {}
            try:
                v = float(entry.get("score", 0) or 0)
                if v > best:
                    best = v
            except (TypeError, ValueError):
                continue
        return best

    # Per-perspective grid scoring
    if lens == "cfo":
        grid = {
            "financial": _classify_grid_level(_score("financial_materiality")),
            "cost": _classify_grid_level(_score("financial_materiality", "regulatory_exposure")),
            "regulatory": _classify_grid_level(_score("regulatory_exposure")),
        }
    elif lens == "ceo":
        grid = {
            "strategic": _classify_grid_level(
                _score("environment", "social", "governance", "stakeholder_impact")
            ),
            "brand": _classify_grid_level(_score("stakeholder_impact", "social")),
            "growth": _classify_grid_level(_score("environment", "stakeholder_impact")),
        }
    else:  # esg-analyst — canonical 3-column view
        grid = {
            "financial": _classify_grid_level(_score("financial_materiality")),
            "regulatory": _classify_grid_level(_score("regulatory_exposure")),
            "strategic": _classify_grid_level(
                _score("environment", "social", "governance", "stakeholder_impact")
            ),
        }

    # Phase 15: Reframe headline using ontology HeadlineRules
    base_headline = view.get("headline") or insight.get("headline") or ""
    headline = base_headline

    if lens != "esg-analyst":
        from engine.ontology.intelligence import query_headline_rules

        _na = {"N/A", "None", "none", "null", ""}
        rules = query_headline_rules(lens)
        for rule in rules:
            if rule.is_fallback:
                headline = rule.template.replace("{value}", "").replace("{base}", base_headline).strip()
                break
            # Resolve dot-path field from insight dict
            parts = rule.source_field.split(".")
            obj: Any = insight if isinstance(insight, dict) else {}
            for part in parts:
                obj = obj.get(part) if isinstance(obj, dict) else None
                if obj is None:
                    break
            s = str(obj or "").strip() if obj else ""
            if s and s not in _na:
                headline = rule.template.replace("{value}", s).replace("{base}", base_headline)
                break

    return {
        **view,
        "perspective": lens,
        "headline": headline[:280],
        "impact_grid": grid,
    }



def _load_payload(json_path: str | None) -> dict[str, Any] | None:
    if not json_path:
        return None
    # SQLite index stores paths relative to the project root
    p = Path(json_path)
    if not p.is_absolute():
        p = Path.cwd() / json_path
    if not p.exists():
        # Fall back to trying it relative to the data folder parent
        alt = get_data_path().parent / json_path
        if alt.exists():
            p = alt
        else:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("legacy_adapter: bad json %s: %s", json_path, exc)
        return None


@functools.lru_cache(maxsize=1)
def _get_perspective_type_filters() -> dict[str, list[str]]:
    """Return ontology-driven recommendation type filters per perspective."""
    try:
        from engine.ontology.intelligence import query_perspective_rec_types
        return {
            p: query_perspective_rec_types(p)
            for p in ("cfo", "ceo", "esg-analyst")
        }
    except Exception:
        return {}


def build_legacy_article(row: dict[str, Any], payload: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten a SQLite index row + JSON payload into the legacy Article shape.

    Every field can be traced back to `engine/` or `data/` — zero legacy
    backend code paths are touched.
    """
    p = payload or {}
    art = p.get("article") or {}
    pipe = p.get("pipeline") or {}
    insight = p.get("insight") or {}
    recs = p.get("recommendations") or {}
    raw_perspectives = p.get("perspectives") or {}
    chains = pipe.get("causal_chains") or []
    frameworks = pipe.get("frameworks") or []
    risk = pipe.get("risk") or {}
    nlp = pipe.get("nlp") or {}
    themes = pipe.get("themes") or {}
    stakeholders = pipe.get("stakeholders") or []
    sdgs = pipe.get("sdgs") or []

    # Re-shape each perspective view to make the lens visibly distinct
    # (perspective-aware grid columns + reframed headlines on top of the
    # ontology-sourced active dimensions and what_matters bullets that the
    # pipeline already computed).
    perspectives = {
        lens: _reshape_perspective(raw_perspectives.get(lens) or {}, insight, lens)
        for lens in ("esg-analyst", "cfo", "ceo")
        if raw_perspectives.get(lens)
    }

    # Priority level — derive from the deep insight's decision_summary
    # (the ontology-driven materiality verdict). The SQLite tier is just a
    # routing flag, not a UI badge. SECONDARY tier articles can have LOW
    # materiality (the "do nothing is valid ESG output" rule).
    tier = row.get("tier") or ""
    decision = (insight.get("decision_summary") or {}) if isinstance(insight, dict) else {}
    materiality_to_priority = {
        "CRITICAL": "CRITICAL",
        "HIGH": "HIGH",
        "MODERATE": "MEDIUM",
        "LOW": "LOW",
        "NON-MATERIAL": "LOW",
        "NONMATERIAL": "LOW",
    }
    materiality = str(decision.get("materiality") or "").upper().strip()
    if materiality and materiality in materiality_to_priority:
        priority_level = materiality_to_priority[materiality]
    else:
        # Fall back to impact_score thresholds, then to tier as last resort
        try:
            impact_score = float(insight.get("impact_score") or 0)
        except (TypeError, ValueError):
            impact_score = 0.0
        if impact_score >= 8:
            priority_level = "CRITICAL"
        elif impact_score >= 6:
            priority_level = "HIGH"
        elif impact_score >= 4:
            priority_level = "MEDIUM"
        elif impact_score > 0:
            priority_level = "LOW"
        else:
            priority_level = {"HOME": "HIGH", "SECONDARY": "MEDIUM", "REJECTED": "LOW"}.get(tier, "MEDIUM")

    # Impact scores — build from causal chains (one per chain)
    impact_scores = []
    for c in chains[:5]:
        impact_scores.append({
            "id": f"{row.get('id')}_{c.get('hops', 0)}_{len(impact_scores)}",
            "company_id": row.get("company_slug"),
            "company_name": row.get("company_slug", "").replace("-", " ").title(),
            "impact_score": c.get("impact_score") or 0,
            "causal_hops": c.get("hops") or 0,
            "financial_exposure": None,
            "relationship_type": c.get("relationship_type") or "",
            "explanation": c.get("explanation") or "",
            "frameworks": [f.get("framework_id") for f in frameworks[:3]],
        })

    # Framework strings for the simple list (e.g. "GRI:305")
    framework_strings: list[str] = []
    for f in frameworks:
        fid = f.get("framework_id") or f.get("framework_label")
        sections = f.get("triggered_sections") or []
        if sections:
            for s in sections[:2]:
                framework_strings.append(f"{fid}:{s}" if ":" not in str(s) else str(s))
        elif fid:
            framework_strings.append(str(fid))

    # Framework hits (structured) for the detail view
    framework_hits = [
        {
            "framework": f.get("framework_id") or f.get("framework_label"),
            "indicator": (f.get("triggered_sections") or [None])[0],
            "indicator_name": f.get("framework_label"),
            "relevance": f.get("relevance") or f.get("relevance_score") or 0,
            "explanation": f.get("profitability_link") or "",
        }
        for f in frameworks
    ]

    # Framework matches (richer v2.0 shape)
    framework_matches = [
        {
            "framework_id": f.get("framework_id"),
            "framework_name": f.get("framework_label"),
            "triggered_sections": f.get("triggered_sections") or [],
            "triggered_questions": f.get("triggered_questions") or [],
            "compliance_implications": f.get("compliance_implications") or [],
            "cross_industry_metrics": f.get("cross_industry_metrics") or [],
            "relevance_score": f.get("relevance") or f.get("relevance_score") or 0,
            "alignment_notes": f.get("alignment_notes") or [],
            "profitability_link": f.get("profitability_link") or "",
            "is_mandatory": bool(f.get("is_mandatory")),
        }
        for f in frameworks
    ]

    # NLP extraction for the Narrative panel
    nlp_extraction = None
    if nlp:
        # Tone may be a list (new pipeline) or string (legacy). Coerce
        # consistently into {primary, secondary} so the legacy panel doesn't
        # render `["neutral","analytical"]` as the literal string
        # "neutralanalytical".
        raw_tone = nlp.get("tone")
        if isinstance(raw_tone, list):
            tone_primary = raw_tone[0] if raw_tone else "neutral"
            tone_secondary = raw_tone[1] if len(raw_tone) > 1 else None
        elif isinstance(raw_tone, str):
            tone_primary = raw_tone
            tone_secondary = nlp.get("tone_secondary")
        else:
            tone_primary = "neutral"
            tone_secondary = None

        nlp_extraction = {
            "sentiment": {
                "score": nlp.get("sentiment", 0),
                "label": nlp.get("sentiment_label", ""),
            },
            "tone": {
                "primary": tone_primary,
                "secondary": tone_secondary,
            },
            "narrative_arc": nlp.get("narrative_arc") or {
                "core_claim": nlp.get("core_claim") or insight.get("core_mechanism", ""),
                "supporting_evidence": nlp.get("supporting_evidence") or [],
                "implied_causation": nlp.get("implied_causation") or "",
                "stakeholder_framing": nlp.get("stakeholder_framing") or {},
                "temporal_framing": nlp.get("temporal_framing") or "",
            },
            "source_credibility": {
                "tier": nlp.get("source_tier") or 3,
                "signals": nlp.get("source_signals") or [],
            },
            "esg_signals": {
                "named_entities": nlp.get("named_entities") or nlp.get("entities") or [],
                "quantitative_claims": nlp.get("quantitative_claims") or [],
                "regulatory_references": nlp.get("regulatory_references") or [],
                "supply_chain_references": nlp.get("supply_chain_references") or [],
            },
        }

    # ESG themes for the theme bar
    esg_themes = None
    if themes:
        esg_themes = {
            "primary_theme": themes.get("primary_theme"),
            "primary_pillar": themes.get("primary_pillar"),
            "primary_sub_metrics": themes.get("primary_sub_metrics") or [],
            "secondary_themes": themes.get("secondary_themes") or [],
            "confidence": themes.get("confidence") or 0,
            "method": themes.get("method") or "llm",
        }

    # Risk matrix — wrap in the legacy spotlight / full shape
    risk_matrix = None
    # Merge ESG + TEMPLES risks for the full category grid
    all_risks = (risk.get("esg_risks") or []) + (risk.get("temples_risks") or [])
    top_risks = risk.get("top_risks") or []
    display_risks = all_risks if all_risks else top_risks

    def _remap_risk(r: dict) -> dict:
        prob = r.get("probability") or 0
        exp = r.get("exposure") or 0
        return {
            "category_id": r.get("category_id") or (r.get("category") or "").lower().replace(" ", "_").replace("&", "and"),
            "category_name": r.get("category") or r.get("category_name") or "",
            "probability": prob,
            "probability_label": r.get("probability_label") or "",
            "exposure": exp,
            "exposure_label": r.get("exposure_label") or "",
            "risk_score": r.get("raw_score") or r.get("risk_score") or (prob * exp),
            "industry_weight": r.get("industry_weight"),
            "adjusted_score": r.get("adjusted_score") or r.get("raw_score") or (prob * exp),
            "classification": r.get("level") or r.get("classification") or "LOW",
            "rationale": r.get("rationale") or "",
            "profitability_note": r.get("profitability_note") or "",
            "lead_indicators": r.get("lead_indicators") or [],
            "lag_indicators": r.get("lag_indicators") or [],
        }

    if display_risks:
        mapped = [_remap_risk(r) for r in display_risks]
        mapped_top = [_remap_risk(r) for r in top_risks[:10]] if top_risks else mapped[:5]
        total_score = sum(r["adjusted_score"] for r in mapped)
        mode = "full" if len(mapped) >= 5 else "spotlight"
        risk_matrix = {
            "mode": mode,
            "aggregate_score": risk.get("aggregate_score") or 0,
            "total_score": round(total_score, 1),
            "top_risks": mapped_top,
            "categories": mapped,
        }

    # REREACT recommendations
    rereact = None
    rec_list = recs.get("recommendations") or []
    if rec_list:
        rereact = {
            "validated_recommendations": [
                {
                    "type": r.get("type") or "action",
                    "title": r.get("title"),
                    "description": r.get("description"),
                    "framework": r.get("framework"),
                    "framework_section": r.get("framework_section"),
                    "responsible_party": r.get("responsible_party"),
                    "deadline": r.get("deadline"),
                    "estimated_budget": r.get("estimated_budget"),
                    "success_criterion": r.get("success_criterion"),
                    "urgency": r.get("urgency") or "medium",
                    "confidence": r.get("confidence") or "high",
                    "validation_notes": r.get("validation_notes"),
                    "profitability_link": r.get("profitability_link"),
                    "roi_percentage": r.get("roi_percentage"),
                    "payback_months": r.get("payback_months"),
                    "priority": r.get("priority") or "medium",
                }
                for r in rec_list
            ],
            "rejected": recs.get("rejected") or [],
            "validation_summary": recs.get("validation_summary") or "",
            "suggested_questions": recs.get("suggested_questions") or [],
            "recommendation_rankings": recs.get("recommendation_rankings") or {},
            "priority_matrix": recs.get("priority_matrix") or {},
            "perspective_type_filters": _get_perspective_type_filters(),
        }
    elif recs.get("do_nothing"):
        rereact = {
            "validated_recommendations": [],
            "rejected": [],
            "validation_summary": "No action required — low materiality macro signal",
            "suggested_questions": [],
        }

    # Geographic signal
    geographic_signal = None
    geo = pipe.get("geographic_signal") or {}
    if geo or chains:
        geographic_signal = {
            "locations": geo.get("locations") or [],
            "regulatory_jurisdictions": geo.get("regulatory_jurisdictions") or [],
            "climate_zones": geo.get("climate_zones") or [],
        }

    return {
        "id": row.get("id"),
        "title": row.get("title") or art.get("title") or "",
        "summary": (insight.get("headline") or insight.get("translation") or "")[:500],
        "source": row.get("source") or art.get("source"),
        "url": row.get("url") or art.get("url"),
        "image_url": art.get("image_url"),
        "published_at": row.get("published_at") or art.get("published_at"),
        "esg_pillar": row.get("esg_pillar"),
        "sentiment": nlp.get("sentiment_label") or ("negative" if (nlp.get("sentiment", 0) or 0) < 0 else "positive" if (nlp.get("sentiment", 0) or 0) > 0 else "neutral"),
        "entities": nlp.get("entities") or nlp.get("named_entities") or [],
        "impact_scores": impact_scores,
        "predictions": [],  # stubbed — predictions are empty in Hybrid scope
        "frameworks": framework_strings,
        "framework_hits": framework_hits,
        # Phase 1C
        "sentiment_score": nlp.get("sentiment_score"),
        "sentiment_confidence": nlp.get("sentiment_confidence"),
        "aspect_sentiments": nlp.get("aspect_sentiments"),
        "content_type": row.get("content_type") or "news",
        "urgency": nlp.get("urgency"),
        "time_horizon": nlp.get("time_horizon"),
        "reversibility": nlp.get("reversibility"),
        "priority_score": row.get("relevance_score"),
        "priority_level": priority_level,
        "financial_signal": {
            "type": "capex",
            "amount": nlp.get("financial_amount_cr") or 0,
            "currency": "INR",
            "confidence": 0.8,
        } if nlp.get("financial_amount_cr") else None,
        "executive_insight": insight.get("headline") or "",
        # Advanced Intelligence
        "relevance_score": row.get("relevance_score"),
        "relevance_breakdown": {
            "esg_correlation": pipe.get("relevance", {}).get("esg_correlation", 0) if isinstance(pipe.get("relevance"), dict) else 0,
            "financial_impact": pipe.get("relevance", {}).get("financial_impact", 0) if isinstance(pipe.get("relevance"), dict) else 0,
            "compliance_risk": pipe.get("relevance", {}).get("compliance_risk", 0) if isinstance(pipe.get("relevance"), dict) else 0,
            "supply_chain_impact": pipe.get("relevance", {}).get("supply_chain_impact", 0) if isinstance(pipe.get("relevance"), dict) else 0,
            "people_impact": pipe.get("relevance", {}).get("people_impact", 0) if isinstance(pipe.get("relevance"), dict) else 0,
            "total": row.get("relevance_score") or 0,
            "tier": tier,
        },
        "deep_insight": insight,  # 1:1 passthrough — new pipeline shape == legacy shape
        "scoring_metadata": {
            "stakeholders": stakeholders,
            "sdgs": sdgs,
            "ontology_query_count": pipe.get("ontology_query_count") or 0,
        },
        "rereact_recommendations": rereact,
        # v2.0 modules
        "nlp_extraction": nlp_extraction,
        "esg_themes": esg_themes,
        "framework_matches": framework_matches,
        "risk_matrix": risk_matrix,
        "geographic_signal": geographic_signal,
        # Phase 12 — ontology-driven perspective views
        "perspectives": perspectives,
        # Phase 14 — LLM intelligence layers (on-demand)
        "intelligence": p.get("intelligence") or {},
    }


def _load_row_and_payload(article_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    row = sqlite_index.get_by_id(article_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")
    payload = _load_payload(row.get("json_path"))
    return row, payload


# =============================================================================
# Auth (open shim)
# =============================================================================


def _mint_token(claims: dict[str, Any]) -> str:
    """Mint a signed JWT for this user's session.

    Phase 11A: signs with HS256 + JWT_SECRET via `mint_bearer`. Previously
    this was an unsigned base64 token — clients still holding those will be
    accepted for the 24h compat window (see `decode_bearer` + the
    REQUIRE_SIGNED_JWT env flag).
    """
    return mint_bearer(claims, exp_days=7)


def _tenant_has_indexed_articles(slug: str) -> bool:
    """Phase 11B: only consider a tenant "real" if the pipeline has
    produced ≥1 analysed article for it. Prevents the empty-shell
    pollution bug on the super-admin's CompanySwitcher."""
    try:
        return sqlite_index.count(company_slug=slug) > 0
    except Exception:
        return False


class ResolveDomainIn(BaseModel):
    domain: str


@router.post("/auth/resolve-domain")
def resolve_domain(body: ResolveDomainIn) -> dict[str, Any]:
    domain = (body.domain or "").lower().strip()
    if not domain:
        raise HTTPException(status_code=400, detail="domain required")

    # Match a target company by domain suffix
    for c in load_companies():
        if domain == c.domain or domain.endswith(c.domain):
            return {
                "domain": domain,
                "company_name": c.name,
                "industry": c.industry,
                "is_existing": True,
                "tenant_id": "snowkap-dev",
            }

    # Open shim — accept any domain. Prospects get auto-registered into
    # the tenant_registry at LOGIN time (below), not here — we don't want
    # someone probing the endpoint to pollute the list.
    guest = domain.split(".")[0].replace("-", " ").title()
    return {
        "domain": domain,
        "company_name": f"{guest} (Guest)",
        "industry": None,
        "is_existing": False,
        "tenant_id": "snowkap-dev",
    }


class LoginIn(BaseModel):
    email: str
    domain: str
    designation: str
    company_name: str
    name: str


def _slug_for_company(name: str | None, domain: str | None = None) -> str | None:
    """Return the matching target-company slug (e.g. "icici-bank") or None.

    Uses fuzzy matching: exact name → partial name → slug match → domain match.
    Falls back to None only when nothing matches.
    """
    companies = load_companies()
    if name:
        needle = name.lower().strip()
        # 1. Exact name match
        for c in companies:
            if needle == c.name.lower():
                return c.slug
        # 2. Partial / contains match (e.g. "ICICI" matches "ICICI Bank")
        for c in companies:
            cname = c.name.lower()
            if needle in cname or cname in needle:
                return c.slug
        # 3. Slug match (e.g. "icici-bank" or "jsw-energy")
        for c in companies:
            if needle == c.slug or needle.replace(" ", "-") == c.slug:
                return c.slug
    # 4. Domain match
    if domain:
        dom = domain.lower().strip()
        for c in companies:
            if c.domain and (dom == c.domain or dom.endswith(c.domain)):
                return c.slug
    return None


@router.post("/auth/login")
def auth_login(body: LoginIn) -> dict[str, Any]:
    # Phase 10: Snowkap internal emails on the allowlist get super-admin perms,
    # which unlock the CompanySwitcher, RoleViewSwitcher, and /settings/campaigns.
    is_super = is_snowkap_super_admin(body.email)
    permissions = (
        list(SUPER_ADMIN_PERMISSIONS)
        if is_super
        else ["read", "chat", "view_analysis", "view_news"]
    )

    # Phase 11B: only record tenants that already have analysed articles in
    # the index. This prevents random client logins from polluting the
    # super-admin's switcher with empty-shell rows (the Phase 10 bug).
    # Real prospects get onboarded via POST /api/admin/onboard which runs
    # the full pipeline — the tenant_registry write happens there, not here.
    #
    # Snowkap internal logins are still NOT registered (we're the sellers).
    if not is_super and body.domain:
        try:
            slug = tenant_registry._slug_from_domain(body.domain)
            if _tenant_has_indexed_articles(slug):
                tenant_registry.register_tenant(
                    domain=body.domain,
                    name=body.company_name or None,
                    industry=None,
                    source="onboarded",
                )
        except Exception as exc:  # never block login on a registry write failure
            logger.warning("tenant registry upsert failed: %s", exc)
    claims = {
        "sub": body.email,
        "name": body.name,
        "company": body.company_name,
        "designation": body.designation,
        "permissions": permissions,
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400 * 30,
    }
    token = _mint_token(claims)
    return {
        "token": token,
        "user_id": body.email,
        "tenant_id": "snowkap-dev",
        "company_id": _slug_for_company(body.company_name, body.domain),
        "designation": body.designation,
        "permissions": permissions,
        "domain": body.domain,
        "name": body.name,
    }


class ReturningUserIn(BaseModel):
    email: str


@router.post("/auth/returning-user")
def auth_returning_user(body: ReturningUserIn) -> dict[str, Any]:
    email = body.email.strip()
    domain = email.split("@")[-1] if "@" in email else ""
    is_super = is_snowkap_super_admin(email)
    permissions = (
        list(SUPER_ADMIN_PERMISSIONS)
        if is_super
        else ["read", "chat", "view_analysis", "view_news"]
    )
    claims = {
        "sub": email,
        "name": email.split("@")[0].title(),
        "permissions": permissions,
        "iat": int(time.time()),
        "exp": int(time.time()) + 86400 * 30,
    }
    return {
        "token": _mint_token(claims),
        "user_id": email,
        "tenant_id": "snowkap-dev",
        "company_id": _slug_for_company(None, domain),
        "designation": "analyst",
        "permissions": permissions,
        "domain": domain,
        "name": email.split("@")[0].title(),
    }


# =============================================================================
# Companies (legacy shape with `id` instead of `slug`)
# =============================================================================


def _company_to_legacy(c: Company) -> dict[str, Any]:
    return {
        "id": c.slug,
        "name": c.name,
        "slug": c.slug,
        "domain": c.domain,
        "industry": c.industry,
        "sasb_category": c.sasb_category,
        "status": "active",
    }


@router.get("/companies/")
def list_companies_legacy(limit: int = 50, _: None = Depends(require_auth)) -> dict[str, Any]:
    companies = load_companies()
    data = [_company_to_legacy(c) for c in companies[:limit]]
    return {"companies": data, "total": len(companies)}


@router.get("/companies/{company_id}")
def get_company_legacy(company_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
    for c in load_companies():
        if c.slug == company_id:
            return _company_to_legacy(c)
    raise HTTPException(status_code=404, detail=f"Company {company_id} not found")


# =============================================================================
# News
# =============================================================================


@router.get("/news/feed")
def news_feed(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: str | None = None,
    sort_by: str = "priority",
    pillar: str | None = None,
    content_type: str | None = None,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    rows = sqlite_index.query_feed(
        company_slug=company_id,
        tier=None,
        limit=limit * 2 if pillar or content_type else limit,  # overfetch for filter
        offset=offset,
    )
    articles: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        if pillar and row.get("esg_pillar") != pillar:
            continue
        if content_type and row.get("content_type") != content_type:
            continue
        payload = _load_payload(row.get("json_path"))
        articles.append(build_legacy_article(row, payload))
        if len(articles) >= limit:
            break
    total = sqlite_index.count(company_slug=company_id)
    return {"articles": articles, "total": total}


@router.get("/news/stats")
def news_stats(
    company_id: str | None = None,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    total = sqlite_index.count(company_slug=company_id if company_id else None)
    high_impact = sqlite_index.count_high_impact(company_slug=company_id if company_id else None)
    new_24h = sqlite_index.count_new_last_24h(company_slug=company_id if company_id else None)
    # Phase 13 B8 — Replace the always-zero "predictions_count" stub with
    # a real count of HOME-tier CRITICAL/HIGH articles in the last 7 days.
    # Frontend renders this under the label "Active Signals" so a journalist
    # sees a meaningful non-zero number across the dashboard. The original
    # `predictions_count` key is preserved for backwards compatibility but
    # now mirrors `active_signals_count`.
    active_signals = sqlite_index.count_active_signals(
        company_slug=company_id if company_id else None
    )
    return {
        "total": total,
        "high_impact_count": high_impact,
        "active_signals_count": active_signals,
        "predictions_count": active_signals,  # back-compat alias
        "new_last_24h": new_24h,
    }


@router.post("/news/{article_id}/bookmark")
def news_bookmark(article_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
    # savedStore is client-side localStorage — this is a no-op acknowledgement
    return {"status": "ok", "article_id": article_id}


def _bg_ingest_all(limit: int = 3) -> None:
    try:
        from engine.analysis.insight_generator import generate_deep_insight
        from engine.analysis.perspective_engine import transform_for_perspective
        from engine.analysis.pipeline import process_article
        from engine.analysis.recommendation_engine import generate_recommendations
        from engine.ingestion.news_fetcher import fetch_for_company
        from engine.output.writer import write_insight
    except Exception as exc:
        logger.error("legacy_adapter: engine imports failed: %s", exc)
        return

    for company in load_companies():
        try:
            fresh = fetch_for_company(company, max_per_query=5)
            for idx, article in enumerate(fresh, 1):
                if idx > limit:
                    break
                article_dict = {
                    "id": article.id,
                    "title": article.title,
                    "content": article.content,
                    "summary": article.summary,
                    "source": article.source,
                    "url": article.url,
                    "published_at": article.published_at,
                    "metadata": article.metadata,
                }
                result = process_article(article_dict, company)
                if result.rejected:
                    continue
                insight = generate_deep_insight(result, company)
                if not insight:
                    continue
                perspectives = {
                    lens: transform_for_perspective(insight, result, lens)
                    for lens in ("esg-analyst", "cfo", "ceo")
                }
                recs = generate_recommendations(insight, result, company)
                write_insight(result, insight, perspectives, recs)
        except Exception as exc:
            logger.exception("legacy_adapter: ingest failed for %s: %s", company.slug, exc)


@router.post("/news/refresh")
def news_refresh(background: BackgroundTasks, _: None = Depends(require_auth)) -> dict[str, Any]:
    background.add_task(_bg_ingest_all, 3)
    return {
        "status": "queued",
        "articles_fetched": 0,
        "articles_stored": 0,
        "sources": ["google_news_rss"],
    }


@router.post("/news/{article_id}/trigger-analysis")
def news_trigger_analysis(
    article_id: str,
    force: bool = Query(False, description="Force re-enrichment even if cached"),
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    """Phase 17b: On-demand enrichment — runs deep insight + perspectives +
    recommendations with primitive-enriched prompts when user opens an article."""
    row = sqlite_index.get_by_id(article_id)
    if not row:
        return {"status": "failed", "message": "Article not found"}

    CURRENT_SCHEMA = "2.0-primitives-l2"
    if not force:
        payload = _load_payload(row.get("json_path"))
        stored_version = ((payload or {}).get("meta") or {}).get("schema_version", "")
        existing_insight = (payload or {}).get("insight") or {}
        # Only return cached if schema is current AND insight has real content
        if (stored_version == CURRENT_SCHEMA
                and existing_insight.get("headline")
                and existing_insight.get("core_mechanism")):
            return {"status": "cached", "message": "Analysis already computed"}

    # Run enrichment in background thread — return immediately so frontend can poll.
    # Phase 13 B2: structured status tracking + error reporting via the
    # article_analysis_status table. Frontend polls
    # GET /news/{id}/analysis-status to render explicit pending/running/ready/
    # failed states instead of an indefinite spinner on crash.
    import threading
    import time as _time
    from engine.analysis.on_demand import enrich_on_demand
    from engine.models import article_analysis_status as analysis_status

    company_slug = row.get("company_slug", "")
    analysis_status.mark_pending(article_id, company_slug)

    def _bg_enrich() -> None:
        import logging
        log = logging.getLogger(__name__)
        t0 = _time.perf_counter()
        try:
            analysis_status.mark_running(article_id)
            enrich_on_demand(article_id, company_slug, force=force)
            analysis_status.mark_ready(article_id, t0)
        except Exception as exc:  # noqa: BLE001 — must record + classify
            klass = analysis_status.classify_pipeline_error(exc)
            log.exception("bg enrich failed (class=%s) for %s/%s", klass, company_slug, article_id)
            analysis_status.mark_failed(article_id, klass, str(exc), started_perf_counter=t0)

    thread = threading.Thread(target=_bg_enrich, daemon=True)
    thread.start()
    return {"status": "triggered", "message": "Enrichment started in background"}


@router.get("/news/{article_id}/analysis-status")
def news_analysis_status(
    article_id: str,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    """Phase 13 B2 — On-demand pipeline status poll.

    Returns the current state of the on-demand enrichment job for an article:
      { "state": "pending|running|ready|failed|unknown",
        "elapsed_seconds": 42.0,
        "error_class": "openai_rate_limit"|null,
        "error": "..."|null,
        "retry_after_seconds": 30 (only for transient failures) }

    Frontend polls every 2-3 seconds while state in {pending, running} and
    surfaces ready / failed states explicitly. Replaces the previous
    indefinite spinner that hung forever on pipeline crash.
    """
    from engine.models import article_analysis_status as analysis_status

    status = analysis_status.get_status(article_id)
    if not status:
        # No tracked job yet — caller may have hit /analysis directly.
        # Return 'unknown' so the UI knows it's not pending or in flight.
        return {
            "state": "unknown",
            "article_id": article_id,
            "elapsed_seconds": 0.0,
            "error_class": None,
            "error": None,
        }

    payload: dict[str, Any] = {
        "state": status.state,
        "article_id": status.article_id,
        "elapsed_seconds": status.elapsed_seconds,
        "error_class": status.error_class,
        "error": status.error,
    }
    # For transient failures, hint at retry timing so the UI can render
    # a specific banner ("Rate limited; retrying in 30s").
    if status.state == "failed":
        if status.error_class in {"openai_rate_limit", "openai_timeout"}:
            payload["retry_after_seconds"] = 30
        else:
            payload["retry_after_seconds"] = 0  # not retryable, manual fix needed
    return payload


@router.get("/news/{article_id}/analysis")
def news_analysis(article_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
    row, payload = _load_row_and_payload(article_id)
    if not payload:
        return {"status": "idle", "analysis": None}
    # Check if insight exists — if not, enrichment is still running in background
    insight = (payload.get("insight") or {})
    if not insight.get("headline"):
        return {"status": "pending", "analysis": None}
    legacy = build_legacy_article(dict(row), payload)
    return {
        "status": "done",
        "analysis": {
            "deep_insight": legacy["deep_insight"],
            "rereact_recommendations": legacy["rereact_recommendations"],
            "risk_matrix": legacy["risk_matrix"],
            "framework_matches": legacy["framework_matches"],
            "priority_score": legacy["priority_score"],
            "priority_level": legacy["priority_level"],
            "scoring_metadata": legacy.get("scoring_metadata"),
            "impact_scores": legacy.get("impact_scores"),
            "nlp_extraction": legacy.get("nlp_extraction"),
            "esg_themes": legacy.get("esg_themes"),
            "perspectives": legacy.get("perspectives"),
            "geographic_signal": legacy.get("geographic_signal"),
            "intelligence": legacy.get("intelligence"),
        },
    }


# =============================================================================
# Discovery API (Phase 19: Self-Evolving Ontology)
# =============================================================================


@router.get("/discovery/pending")
def discovery_pending(
    category: str | None = None,
    _: None = Depends(require_auth),
) -> dict[str, Any]:
    """Return pending discovery candidates for human review."""
    from engine.ontology.discovery.candidates import get_buffer, STATUS_PENDING

    buf = get_buffer()
    candidates = buf.get_all(category=category, status=STATUS_PENDING)
    return {
        "count": len(candidates),
        "candidates": [c.to_dict() for c in candidates],
    }


@router.get("/discovery/stats")
def discovery_stats(_: None = Depends(require_auth)) -> dict[str, Any]:
    """Return discovery buffer statistics."""
    from engine.ontology.discovery.candidates import get_buffer

    buf = get_buffer()
    all_candidates = buf.get_all()
    by_category: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for c in all_candidates:
        by_category[c.category] = by_category.get(c.category, 0) + 1
        by_status[c.status] = by_status.get(c.status, 0) + 1
    return {
        "total": buf.count,
        "pending": buf.pending_count,
        "by_category": by_category,
        "by_status": by_status,
    }


@router.post("/discovery/{candidate_id}/approve")
def discovery_approve(candidate_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Approve a pending discovery candidate → promote to ontology."""
    from engine.ontology.discovery.candidates import get_buffer, STATUS_PENDING
    from engine.ontology.discovery.promoter import _build_triples, _log_audit, _persist_discovered, STATUS_PROMOTED

    buf = get_buffer()
    # candidate_id format: "category:slug"
    parts = candidate_id.split(":", 1)
    if len(parts) != 2:
        return {"status": "error", "message": "Invalid candidate_id format (expected category:slug)"}

    category, slug = parts
    candidate = buf.get(category, slug)
    if not candidate:
        return {"status": "error", "message": "Candidate not found"}
    if candidate.status != STATUS_PENDING:
        return {"status": "error", "message": f"Candidate is {candidate.status}, not pending"}

    triples = _build_triples(candidate)
    if triples:
        from engine.ontology.graph import get_graph
        g = get_graph()
        g.insert_triples(triples)
        _persist_discovered()

    buf.update_status(category, slug, STATUS_PROMOTED)
    _log_audit("manually_approved", candidate, len(triples))
    return {"status": "approved", "label": candidate.label, "triples": len(triples)}


@router.post("/discovery/{candidate_id}/reject")
def discovery_reject(candidate_id: str, _: None = Depends(require_auth)) -> dict[str, Any]:
    """Reject a pending discovery candidate."""
    from engine.ontology.discovery.candidates import get_buffer, STATUS_PENDING
    from engine.ontology.discovery.promoter import _log_audit

    buf = get_buffer()
    parts = candidate_id.split(":", 1)
    if len(parts) != 2:
        return {"status": "error", "message": "Invalid candidate_id format"}

    category, slug = parts
    candidate = buf.get(category, slug)
    if not candidate:
        return {"status": "error", "message": "Candidate not found"}

    buf.update_status(category, slug, "rejected")
    _log_audit("rejected", candidate)
    return {"status": "rejected", "label": candidate.label}


@router.post("/discovery/promote")
def discovery_run_batch(_: None = Depends(require_auth)) -> dict[str, Any]:
    """Manually trigger batch promotion of qualifying candidates."""
    from engine.ontology.discovery.promoter import batch_promote
    return batch_promote()


class NewsChatIn(BaseModel):
    company_id: str
    message: str
    conversation_history: list[dict[str, Any]] = []
    context_sections: list[str] = []


@router.post("/news/{article_id}/chat")
def news_chat(article_id: str, body: NewsChatIn, _: None = Depends(require_auth)) -> dict[str, Any]:
    response = _run_agent_chat(
        question=body.message,
        agent_id="executive",
        article_id=article_id,
    )
    return {"response": response, "article_id": article_id}


# =============================================================================
# Preferences (in-memory stub — client persists to localStorage anyway)
# =============================================================================

_DEFAULT_PREFS = {
    "preferred_frameworks": [],
    "preferred_pillars": [],
    "preferred_topics": [],
    "alert_threshold": 4,
    "content_depth": "standard",
    "companies_of_interest": [],
    "dismissed_topics": [],
}
_PREFS_STORE: dict[str, Any] = dict(_DEFAULT_PREFS)


@router.get("/preferences/")
def prefs_get(_: None = Depends(require_auth)) -> dict[str, Any]:
    return dict(_PREFS_STORE)


@router.put("/preferences/")
def prefs_put(body: dict[str, Any] = Body(...), _: None = Depends(require_auth)) -> dict[str, Any]:
    _PREFS_STORE.update(body)
    return dict(_PREFS_STORE)


@router.patch("/preferences/")
def prefs_patch(body: dict[str, Any] = Body(...), _: None = Depends(require_auth)) -> dict[str, Any]:
    _PREFS_STORE.update(body)
    return dict(_PREFS_STORE)


# =============================================================================
# Predictions (Hybrid scope — empty stubs)
# =============================================================================


@router.get("/predictions/")
def predictions_list(
    company_id: str | None = None,
    limit: int = 10,
    _: None = Depends(require_auth),
) -> list[dict[str, Any]]:
    return []


@router.get("/predictions/stats")
def predictions_stats(_: None = Depends(require_auth)) -> dict[str, Any]:
    return {"total": 0, "completed": 0, "pending": 0, "failed": 0}


class PredictionTriggerIn(BaseModel):
    article_id: str
    company_id: str
    causal_chain_id: str | None = None


@router.post("/predictions/trigger")
def predictions_trigger(body: PredictionTriggerIn, _: None = Depends(require_auth)) -> dict[str, Any]:
    return {"status": "stubbed", "message": "Predictions are disabled in Hybrid scope"}


# =============================================================================
# Agent chat (real OpenAI with ontology context)
# =============================================================================


AGENT_ROSTER = [
    {
        "id": "supply_chain",
        "name": "Supply Chain Analyst",
        "keywords": ["scope 3", "supplier", "upstream", "procurement"],
        "system_prompt": (
            "You are a supply-chain ESG analyst specialising in Scope 3 emissions, "
            "supplier risk, and procurement due diligence. Give concrete, quantified "
            "recommendations grounded in the article and ontology context. Cite framework "
            "sections (BRSR:P6, GRI:305, ESRS:E1) where relevant. Stay under 250 words."
        ),
    },
    {
        "id": "compliance",
        "name": "Compliance Officer",
        "keywords": ["BRSR", "CSRD", "TCFD", "disclosure", "regulation", "penalty"],
        "system_prompt": (
            "You are a regulatory compliance officer. Focus on disclosure obligations, "
            "framework alignment, and penalty exposure. Cite exact framework section codes "
            "(BRSR:P6, CSRD:ESRS-E1, SEBI LODR). Flag immediate deadlines. Under 250 words."
        ),
    },
    {
        "id": "analytics",
        "name": "ESG Analytics",
        "keywords": ["metrics", "KPI", "score", "benchmark", "trend"],
        "system_prompt": (
            "You are a quantitative ESG analyst. Respond with data-backed claims, numeric "
            "metrics, peer benchmarks, and trend trajectories. Reference ontology-sourced "
            "indicators when available. Under 250 words."
        ),
    },
    {
        "id": "executive",
        "name": "C-Suite Advisor",
        "keywords": ["executive", "strategy", "board", "CFO", "CEO"],
        "system_prompt": (
            "You are a C-suite ESG advisor. Frame answers as board-ready intelligence with "
            "financial exposure (₹ Cr), strategic implications, and a 1-sentence verdict. "
            "Use the ontology-sourced perspective context. Under 200 words."
        ),
    },
]


def _build_agent_context(article_id: str | None) -> list[str]:
    """Construct ontology-enriched context blocks for the agent system prompt."""
    blocks: list[str] = []
    if not article_id:
        return blocks
    row = sqlite_index.get_by_id(article_id)
    if not row:
        return blocks
    payload = _load_payload(row.get("json_path"))
    if not payload:
        return blocks
    pipe = payload.get("pipeline") or {}
    insight = payload.get("insight") or {}
    themes = pipe.get("themes") or {}
    theme = themes.get("primary_theme") or ""

    blocks.append(f"ARTICLE: {row.get('title')}")
    blocks.append(f"COMPANY: {row.get('company_slug')}")
    blocks.append(f"THEME: {theme}")
    if insight.get("headline"):
        blocks.append(f"HEADLINE INSIGHT: {insight.get('headline')}")
    if insight.get("core_mechanism"):
        blocks.append(f"CORE MECHANISM: {insight.get('core_mechanism')}")

    chains = (pipe.get("causal_chains") or [])[:3]
    if chains:
        blocks.append(f"CAUSAL CHAINS: {json.dumps(chains)[:1500]}")

    frameworks = (pipe.get("frameworks") or [])[:5]
    if frameworks:
        blocks.append(
            "FRAMEWORKS: "
            + ", ".join(str(f.get("framework_label") or f.get("framework_id") or "") for f in frameworks)
        )

    # Ontology enrichment — SPARQL queries against the 2950-triple graph
    if theme:
        try:
            stakeholders = onto_q.query_stakeholders_for_topic(theme)
            if stakeholders:
                blocks.append(f"STAKEHOLDERS: {', '.join(stakeholders)}")
        except Exception as exc:
            logger.debug("stakeholder query failed: %s", exc)
        try:
            sdgs = onto_q.query_sdgs_for_topic(theme)
            if sdgs:
                blocks.append(f"SDGs: {', '.join(sdgs)}")
        except Exception as exc:
            logger.debug("sdg query failed: %s", exc)
    return blocks


def _run_agent_chat(
    question: str,
    agent_id: str | None,
    article_id: str | None,
) -> str:
    """Execute a single OpenAI call with ontology-built context."""
    agent = next((a for a in AGENT_ROSTER if a["id"] == agent_id), AGENT_ROSTER[3])
    ctx_blocks = _build_agent_context(article_id)

    system_prompt = agent["system_prompt"]
    if ctx_blocks:
        system_prompt += "\n\nCONTEXT (ontology-sourced):\n" + "\n".join(ctx_blocks)

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return "Agent chat unavailable — OPENAI_API_KEY not set on the server."

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        settings = load_settings()
        model = (settings.get("llm") or {}).get("model_light", "gpt-4.1-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.exception("agent chat failed: %s", exc)
        return f"Agent chat error: {type(exc).__name__}"


class AgentChatIn(BaseModel):
    question: str
    agent_id: str | None = "executive"
    conversation_id: str | None = None
    article_id: str | None = None


@router.post("/agent/chat")
def agent_chat(body: AgentChatIn, _: None = Depends(require_auth)) -> dict[str, Any]:
    answer = _run_agent_chat(body.question, body.agent_id, body.article_id)
    agent = next((a for a in AGENT_ROSTER if a["id"] == body.agent_id), AGENT_ROSTER[3])
    return {
        "response": answer,
        "agent": {"id": agent["id"], "name": agent["name"]},
        "classification": {"agent_id": agent["id"], "confidence": 0.9},
        "tools_used": ["ontology_query", "openai_chat"],
        "pending_actions": [],
        "conversation_id": body.conversation_id or str(uuid.uuid4()),
    }


class AskAboutNewsIn(BaseModel):
    article_id: str
    question: str | None = None


@router.post("/agent/ask-about-news")
def agent_ask_about_news(body: AskAboutNewsIn, _: None = Depends(require_auth)) -> dict[str, Any]:
    q = body.question or "What are the top ESG risks and opportunities for this company?"
    answer = _run_agent_chat(q, "executive", body.article_id)

    # Pull top causal chains for the CausalChainViz panel in chat UI
    row = sqlite_index.get_by_id(body.article_id) or {}
    payload = _load_payload(row.get("json_path"))
    chains_raw = ((payload or {}).get("pipeline") or {}).get("causal_chains") or []
    causal_chains = [
        {
            "id": f"{body.article_id}_{i}",
            "source_entity": (c.get("nodes") or [""])[0],
            "target_entity": (c.get("nodes") or [""])[-1],
            "relationship_type": c.get("relationship_type") or "",
            "hops": c.get("hops") or 0,
            "impact_score": c.get("impact_score") or 0,
            "explanation": c.get("explanation") or "",
        }
        for i, c in enumerate(chains_raw[:5])
    ]
    return {
        "response": answer,
        "agent": {"id": "executive", "name": "C-Suite Advisor"},
        "causal_chains": causal_chains,
        "prediction_available": False,
        "article_summary": {
            "title": row.get("title"),
            "source": row.get("source"),
            "tier": row.get("tier"),
            "relevance_score": row.get("relevance_score"),
        },
    }


@router.get("/agent/agents")
def list_agents(_: None = Depends(require_auth)) -> list[dict[str, Any]]:
    return [
        {"id": a["id"], "name": a["name"], "keywords": a["keywords"], "tools": ["ontology", "openai"]}
        for a in AGENT_ROSTER
    ]


_AGENT_HISTORY: dict[str, list[dict[str, Any]]] = {}


@router.get("/agent/history")
def agent_history(last_n: int = 20, _: None = Depends(require_auth)) -> dict[str, Any]:
    return {"messages": [], "context_summary": None}


@router.delete("/agent/history")
def agent_history_clear(_: None = Depends(require_auth)) -> dict[str, Any]:
    _AGENT_HISTORY.clear()
    return {"status": "cleared"}


class ConfirmActionIn(BaseModel):
    action_id: str
    conversation_id: str


@router.post("/agent/confirm-action")
def agent_confirm(body: ConfirmActionIn, _: None = Depends(require_auth)) -> dict[str, Any]:
    return {"status": "confirmed", "result": {"action_id": body.action_id}}


@router.post("/agent/reject-action")
def agent_reject(body: ConfirmActionIn, _: None = Depends(require_auth)) -> dict[str, Any]:
    return {"status": "rejected", "action_id": body.action_id}


# =============================================================================
# Ontology
# =============================================================================


@router.get("/ontology/stats")
def ontology_stats(_: None = Depends(require_auth)) -> dict[str, Any]:
    g = _graph()
    s = g.stats()
    return {
        "companies": s.get("companies") or 0,
        "facilities": s.get("facilities") or 0,
        "suppliers": s.get("suppliers") or 0,
        "commodities": s.get("commodities") or 0,
        "material_issues": s.get("material_issues") or 0,
        "frameworks": s.get("frameworks") or 0,
        "regulations": s.get("regulations") or 0,
        "causal_chains": s.get("causal_chains") or 0,
        "total_triples": s.get("total_triples") or 0,
        "esg_topics": s.get("esg_topics") or 0,
        "perspectives": s.get("perspectives") or 0,
        "risk_categories": s.get("risk_categories") or 0,
        "temples_categories": s.get("temples_categories") or 0,
        "event_types": s.get("event_types") or 0,
        "sdgs": s.get("sdgs") or 0,
    }


class SparqlIn(BaseModel):
    query: str


@router.post("/ontology/sparql")
def ontology_sparql(body: SparqlIn, _: None = Depends(require_auth)) -> dict[str, Any]:
    g = _graph()
    try:
        rows = g.select_rows(body.query)
        return {"results": rows, "count": len(rows)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"SPARQL error: {exc}") from exc


class ExploreIn(BaseModel):
    entity_text: str
    company_slug: str | None = None


@router.post("/ontology/explore")
def ontology_explore(body: ExploreIn, _: None = Depends(require_auth)) -> dict[str, Any]:
    target = body.company_slug or (load_companies()[0].slug if load_companies() else None)
    if not target:
        return {"chains": []}
    try:
        chains = find_causal_chains(body.entity_text, target)
        return {
            "entity": body.entity_text,
            "target": target,
            "chains": [
                {
                    "nodes": c.nodes,
                    "edges": c.edges,
                    "hops": c.hops,
                    "relationship_type": c.relationship_type,
                    "impact_score": c.impact_score,
                    "explanation": c.explanation,
                }
                for c in chains
            ],
        }
    except Exception as exc:
        logger.warning("ontology explore failed: %s", exc)
        return {"entity": body.entity_text, "target": target, "chains": []}


# =============================================================================
# Admin (users + usage stubs — /admin/tenants owned by api/routes/admin.py)
# =============================================================================


@router.get("/admin/users")
def admin_users(_: None = Depends(require_auth)) -> list[dict[str, Any]]:
    return []


@router.get("/admin/usage")
def admin_usage(_: None = Depends(require_auth)) -> dict[str, Any]:
    st = sqlite_index.stats()
    return {
        "total_insights": st.get("total") or 0,
        "ontology_triples": _graph().stats().get("total_triples") or 0,
        "companies": len(load_companies()),
    }
