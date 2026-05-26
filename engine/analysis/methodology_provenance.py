"""Phase 28 / Feature 2 — Methodology provenance.

For each metric we surface in the UI (criticality, relevance,
persona_boost, sentiment_trajectory, framework_match), this module
returns:

  * ``source``         — which engine module produced it (clickable
                         reference in the drawer header)
  * ``simple_logic``   — plain-language sentence: *"We blend 7 signals…"*
  * ``formula_human``  — human-readable formula with role-specific weights
  * ``ontology_anchors`` — ontology predicates that backed the value
                         (e.g. ``material_for``, ``framework_section``)
  * ``your_inputs``    — actual per-article component values, so the
                         drawer can render "for THIS article you had
                         materiality=0.85, financial=0.62, …"

Pure-Python. Reads the insight payload as-stored on disk (no LLM call).
Designed to be cheap enough to compute on every drawer-open without
caching.

Consumed by ``api/routes/methodology.py``
(``GET /api/insights/{article_id}/methodology``) and rendered by the
``client/src/components/explainer/MethodologyDrawer.tsx`` side-drawer.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.analysis.criticality_scorer import (
    WEIGHTS_BY_ROLE,
    WEIGHTS_DEFAULT,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-metric methodology blocks
# ---------------------------------------------------------------------------


def _criticality_methodology(
    insight: dict[str, Any], role: str | None = None,
) -> dict[str, Any]:
    """The 7-component criticality scorer (Phase 27 added the 7th)."""
    components = (insight.get("criticality") or {}).get("components") or {}
    weights = WEIGHTS_BY_ROLE.get(role or "", WEIGHTS_DEFAULT)
    role_label = (role or "default").upper()

    formula_parts = [
        f"{weights['materiality']:.3f}×materiality",
        f"{weights['financial_magnitude']:.3f}×financial",
        f"{weights['actionability']:.3f}×actionability",
        f"{weights['painpoint_match']:.3f}×painpoint_match",
        f"{weights['recency']:.3f}×recency",
        f"{weights['source_authority']:.3f}×source_authority",
        f"{weights.get('sentiment_trajectory', 0.0):.3f}×sentiment_trajectory",
    ]

    return {
        "metric": "criticality",
        "source": "engine/analysis/criticality_scorer.py",
        "simple_logic": (
            "We blend 7 signals to score how critical this article is for you: "
            "how material the topic is to your industry, how big the rupee impact is, "
            "whether there's a deadline you can act on, whether it matches a topic "
            "you've told us to track, how fresh the article is, how authoritative "
            "the source is, and which way the company's sentiment is trending."
        ),
        "formula_human": f"score = {' + '.join(formula_parts)}   (role={role_label})",
        "ontology_anchors": ["material_for", "framework_section", "primitive_β"],
        "your_inputs": {
            "materiality": components.get("materiality"),
            "financial_magnitude": components.get("financial_magnitude"),
            "actionability": components.get("actionability"),
            "painpoint_match": components.get("painpoint_match"),
            "recency": components.get("recency"),
            "source_authority": components.get("source_authority"),
            "sentiment_trajectory": components.get("sentiment_trajectory"),
            "staleness_penalty": components.get("staleness_penalty"),
            "confidence_penalty": components.get("confidence_penalty"),
            "polarity_drift_penalty": components.get("polarity_drift_penalty"),
        },
        "band": (insight.get("criticality") or {}).get("band"),
        "final_score": (insight.get("criticality") or {}).get("score"),
    }


def _relevance_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    """The Stage-4 5-D relevance score."""
    pipeline = insight.get("pipeline") or {}
    rel = pipeline.get("relevance") or {}
    return {
        "metric": "relevance",
        "source": "engine/analysis/relevance_scorer.py",
        "simple_logic": (
            "We score every article on five dimensions — does it touch ESG? does it "
            "move the P&L? is there a compliance risk? does it ripple through the "
            "supply chain? does it affect people (staff, customers, community)? "
            "Each scores 0–2, total 0–10. Below 4 we drop the article."
        ),
        "formula_human": (
            "total = esg_correlation + financial_impact + compliance_risk + "
            "supply_chain_impact + people_impact  (each 0–2)"
        ),
        "ontology_anchors": ["material_for", "esg_pillar"],
        "your_inputs": {
            "esg_correlation": rel.get("esg_correlation"),
            "financial_impact": rel.get("financial_impact"),
            "compliance_risk": rel.get("compliance_risk"),
            "supply_chain_impact": rel.get("supply_chain_impact"),
            "people_impact": rel.get("people_impact"),
            "total": rel.get("total") or rel.get("adjusted_total"),
            "tier": rel.get("tier") or pipeline.get("tier"),
        },
    }


def _persona_boost_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    """Phase 6 persona × criticality multiplier."""
    persona_payload = insight.get("persona_score") or insight.get("persona_boost") or {}
    return {
        "metric": "persona_boost",
        "source": "engine/persona/persona_scorer.py",
        "simple_logic": (
            "On top of the base criticality, we re-rank articles by YOUR persona. "
            "If your tracked topics, frameworks, or geographies match the article, "
            "the score lifts up to ×1.4. Persona never hides an article — CRITICAL "
            "items always surface even on a full mismatch (0.65 floor preserved)."
        ),
        "formula_human": (
            "final = base_criticality × (1 + 0.40·esg_focus_overlap) "
            "× (1 + 0.30·framework_overlap) × (1 + 0.25·geo_overlap) "
            "× (1 + 0.20·memory_preference_overlap)"
        ),
        "ontology_anchors": ["persona_focus", "framework_jurisdiction"],
        "your_inputs": {
            "base_score": persona_payload.get("base_score"),
            "boost": persona_payload.get("persona_boost") or persona_payload.get("boost"),
            "final_score": persona_payload.get("score") or persona_payload.get("final_score"),
            "outside_focus": persona_payload.get("outside_focus"),
        },
    }


def _sentiment_trajectory_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    """Phase 27 — forecaster output stamped on every HOME insight."""
    traj = insight.get("sentiment_trajectory") or {}
    horizons = traj.get("horizons") or {}
    return {
        "metric": "sentiment_trajectory",
        "source": "engine/analysis/forecaster.py",
        "simple_logic": (
            "Looking at the last 24 months of ESG events for this company, we project "
            "where sentiment is heading at 3, 6, and 12 months out. Declining + "
            "high-confidence trajectories raise the criticality score; improving + "
            "high-confidence lower it."
        ),
        "formula_human": (
            "trajectory_score = max(score(3m), score(6m))  where each maps "
            "(direction × confidence) → [0,1]: declining/high=0.9, "
            "improving/high=0.1, stable=0.5, unknown=0.5"
        ),
        "ontology_anchors": [],
        "your_inputs": {
            "3m": horizons.get("3m") or {},
            "6m": horizons.get("6m") or {},
            "12m": horizons.get("12m") or {},
            "llm_used": traj.get("llm_used"),
        },
    }


def _framework_match_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    """Phase 15 — ontology-driven framework matching."""
    frameworks = (insight.get("pipeline") or {}).get("frameworks") or insight.get("frameworks") or []
    if isinstance(frameworks, dict):
        # Older shape: {"matches": [...]}
        frameworks = frameworks.get("matches") or frameworks.get("frameworks") or []
    framework_names = [
        (f.get("framework_id") or f.get("name") or "?")
        for f in frameworks
        if isinstance(f, dict)
    ]
    return {
        "metric": "framework_match",
        "source": "engine/analysis/framework_matcher.py",
        "simple_logic": (
            "We map every ESG event to the disclosure frameworks that govern it — "
            "BRSR for India, CSRD for the EU, SEC climate rule for the US, TCFD/GRI/"
            "SDR/SFDR globally. Regional boosts apply: BRSR adds +0.6 for India-listed "
            "companies, CSRD adds +0.6 for the EU."
        ),
        "formula_human": (
            "match_score = base_match × region_boost × cap_tier_adjustment"
        ),
        "ontology_anchors": ["framework_section", "mandatory_for", "region_boost"],
        "your_inputs": {
            "frameworks": framework_names[:6],
            "count": len(framework_names),
        },
    }


# ---------------------------------------------------------------------------
# Phase 29 — per-panel methodology entries
#
# The 5 metric entries above describe HOW THE SCORE WAS COMPUTED. The 7
# entries below describe HOW EACH UI PANEL WAS GENERATED — so the
# per-panel "i" icon can explain just that panel without showing all 5
# metrics. Same shape as the metric entries so the frontend popover
# can render them with the same component.
# ---------------------------------------------------------------------------


def _stakeholder_map_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    perspectives = insight.get("perspectives") or {}
    ceo = perspectives.get("ceo") or {}
    stakeholders = ceo.get("stakeholder_map") or []
    return {
        "metric": "stakeholder_map",
        "source": "engine/analysis/ceo_narrative_generator.py",
        "simple_logic": (
            "We list the five stakeholders whose stance moves most when "
            "this event lands, pulled from the ontology's stakeholder×topic "
            "edges and stamped with a one-line precedent or expected reaction."
        ),
        "formula_human": (
            "stakeholders = query_stakeholders_for_topic(topic) × top-5 by "
            "stance_magnitude  (ontology SPARQL)"
        ),
        "ontology_anchors": ["affects_stakeholder", "stance_magnitude", "precedent_for"],
        "your_inputs": {
            "count": len(stakeholders) if isinstance(stakeholders, list) else 0,
            "names": [s.get("stakeholder") if isinstance(s, dict) else str(s)
                      for s in (stakeholders[:5] if isinstance(stakeholders, list) else [])],
        },
    }


def _board_paragraph_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    perspectives = insight.get("perspectives") or {}
    ceo = perspectives.get("ceo") or {}
    board = ceo.get("board_paragraph") or ""
    word_count = len(board.split()) if board else 0
    return {
        "metric": "board_paragraph",
        "source": "engine/analysis/ceo_narrative_generator.py",
        "simple_logic": (
            "A 60-100 word board-narrative draft generated by gpt-4.1, "
            "polarity-aware (positive events read differently from negative). "
            "The draft cites the rupee exposure + recommended action verbatim "
            "from the deep-insight payload — never invented numbers."
        ),
        "formula_human": (
            "LLM(prompt = stakeholders + financial_exposure + event_polarity + "
            "frameworks); validated against the article body (no claims that "
            "don't appear in the source)."
        ),
        "ontology_anchors": ["framework_section", "stakeholder_stance"],
        "your_inputs": {
            "word_count": word_count,
            "polarity": insight.get("event_polarity", "neutral"),
            "llm_used": True,
        },
    }


def _kpi_table_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    perspectives = insight.get("perspectives") or {}
    analyst = perspectives.get("esg-analyst") or {}
    kpi_table = analyst.get("kpi_table") or []
    return {
        "metric": "kpi_table",
        "source": "engine/analysis/esg_analyst_generator.py",
        "simple_logic": (
            "KPIs relevant to this event, sourced from the ontology's KPI "
            "registry, with the company's value, the peer quartile they sit "
            "in, and the top-3 peer references for context."
        ),
        "formula_human": (
            "kpis = query_kpis_for_topic(topic, industry) × peer_quartile_lookup"
        ),
        "ontology_anchors": ["kpi_for_topic", "peer_quartile", "industry_baseline"],
        "your_inputs": {
            "count": len(kpi_table) if isinstance(kpi_table, list) else 0,
            "names": [k.get("name") if isinstance(k, dict) else str(k)
                      for k in (kpi_table[:5] if isinstance(kpi_table, list) else [])],
        },
    }


def _risk_matrix_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    risk = insight.get("risk_assessment") or insight.get("risk") or {}
    temples = risk.get("temples_risks") if isinstance(risk, dict) else None
    if not isinstance(temples, list):
        temples = []
    return {
        "metric": "risk_matrix",
        "source": "engine/analysis/risk_assessor.py",
        "simple_logic": (
            "Seven TEMPLES risk categories (Technological, Economic, "
            "Market, Political, Legal/Regulatory, Environmental, Social). "
            "Each is scored Probability × Exposure on a 1-5 × 1-5 grid "
            "for a max of 25 per category and 175 total. Industry-specific "
            "weights bump the categories the sector cares about most."
        ),
        "formula_human": (
            "score_per_category = probability × exposure   (each 1-5, max 25); "
            "total = Σ scores  (max 175)"
        ),
        "ontology_anchors": ["temples_category", "industry_weight"],
        "your_inputs": {
            "categories_scored": len(temples),
            "total_score": risk.get("total_score") if isinstance(risk, dict) else None,
        },
    }


def _esg_relevance_score_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    esg = insight.get("esg_relevance_score") or {}
    return {
        "metric": "esg_relevance_score",
        "source": "engine/analysis/relevance_scorer.py + insight_generator.py",
        "simple_logic": (
            "Six dimensions scored 0–10 by gpt-4.1: Environment, Social, "
            "Governance, Financial, Regulatory, Stakeholders. Each carries "
            "a one-line rationale that cites the article facts the score "
            "anchored on."
        ),
        "formula_human": (
            "score_per_dim = LLM(article_facts × industry_lens × dimension_rubric)  "
            "→ 0..10 per dim, average over 6 dims"
        ),
        "ontology_anchors": ["esg_pillar", "industry_baseline"],
        "your_inputs": {
            "dimensions_scored": len(esg) if isinstance(esg, dict) else 0,
            "average": (
                sum((d.get("score") or 0) for d in esg.values()
                    if isinstance(d, dict)) / max(1, len(esg))
                if isinstance(esg, dict) and esg else None
            ),
        },
    }


def _ai_recommendations_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    recs = insight.get("recommendations") or []
    if not isinstance(recs, list):
        recs = []
    return {
        "metric": "ai_recommendations",
        "source": "engine/analysis/recommendation_engine.py",
        "simple_logic": (
            "The REREACT 3-agent chain: a Generator drafts 5-8 recommendations, "
            "an Analyzer challenges each on financial logic + ROI math, a "
            "Validator confirms framework citations + deadlines. Only "
            "survivors ship. Each rec carries owner, deadline, budget, ROI, "
            "payback, and the framework section it satisfies."
        ),
        "formula_human": (
            "recs = Validator(Analyzer(Generator(insight × industry × ontology))); "
            "ROI capped at +400% to flag implausible draws."
        ),
        "ontology_anchors": ["framework_section", "mandatory_for", "industry_benchmark"],
        "your_inputs": {
            "count": len(recs),
            "by_type": {
                t: sum(1 for r in recs
                       if isinstance(r, dict) and r.get("type") == t)
                for t in ("strategic", "financial", "esg_positioning",
                          "operational", "compliance")
            },
        },
    }


def _impact_analysis_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    impact = insight.get("impact_analysis") or {}
    return {
        "metric": "impact_analysis",
        "source": "engine/analysis/insight_generator.py",
        "simple_logic": (
            "Six concrete impact dimensions written by gpt-4.1: ESG positioning, "
            "capital allocation, valuation/cash-flow, compliance/regulatory, "
            "supply-chain transmission, and people/demand. Each is 1-3 sentences "
            "linking the event mechanism to that dimension's downstream effect."
        ),
        "formula_human": (
            "for each dim in 6_dims: LLM(prompt = event + cascade_for_dim + "
            "industry_examples)  → 1-3 sentences"
        ),
        "ontology_anchors": ["primitive_cascade", "framework_section"],
        "your_inputs": {
            "dimensions_filled": sum(1 for v in impact.values()
                                     if isinstance(v, str) and v.strip())
                                  if isinstance(impact, dict) else 0,
        },
    }


def _financial_timeline_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    """Phase 31 — methodology block for the Financial Impact & Timeline panel.

    The financial_timeline row is computed deterministically by the
    primitive engine (Phase 17c) before the LLM ever sees the prompt.
    Surface the cascade math + per-horizon bps so the (i) drawer shows
    the user exactly where the rupee figures come from.
    """
    ft = insight.get("financial_timeline") or {}
    immediate = ft.get("immediate") or {}
    short = ft.get("short_term") or {}
    medium = ft.get("medium_term") or {}
    return {
        "metric": "financial_timeline",
        "source": "engine/analysis/primitive_engine.py (cascade) + insight_generator.py (narrative)",
        "simple_logic": (
            "The ₹ exposure for each horizon (immediate / short_term / medium_term) "
            "is computed BEFORE the LLM runs by walking the primitive cascade graph "
            "(e.g. RG→OX→FCF). β coefficients are calibrated per company, then "
            "multiplied through to a margin-bps number that the LLM cannot override."
        ),
        "formula_human": (
            "ΔTarget = β_calibrated × Δ_source × base_value\n"
            "margin_bps = (ΔTarget / revenue) × 10000\n"
            "horizon-specific β and lag come from primitives_edges_p2p.ttl"
        ),
        "ontology_anchors": ["CausalEdge", "OutcomeEdge", "primitive_calibration"],
        "your_inputs": {
            "immediate_bps": immediate.get("margin_bps"),
            "immediate_inr_cr": immediate.get("inr_cr"),
            "short_term_bps": short.get("margin_bps"),
            "medium_term_bps": medium.get("margin_bps"),
            "currency": ft.get("currency") or "INR",
        },
    }


# ---------------------------------------------------------------------------
# Phase 33 — Per-bullet methodology for the UnifiedAnalysisCard
#
# Each builder reads the actual insight payload + composes a short,
# article-specific paragraph explaining how *that* bullet was built. The
# old fallback in unified_analysis._build_methodology_block was generic
# ("we blend 7 signals..."); these read the real component values + the
# real event_type + the real framework list so the user can see the
# concrete reasoning for THIS article.
# ---------------------------------------------------------------------------


def _company_label(insight: dict[str, Any]) -> str:
    """Best-effort company-name resolver for the methodology copy."""
    # Insight payload doesn't carry a name directly; the article block
    # might. Caller passes the slug via the pipeline.company_slug — we
    # title-case it for readability.
    article = insight.get("article") or {}
    slug = article.get("company_slug") or ""
    if slug:
        return slug.replace("-", " ").title()
    return "this company"


def _what_changed_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    """Plain-English explanation of how Stage 3 classified the event."""
    pipeline = insight.get("pipeline") or {}
    event = pipeline.get("event") or {}
    event_id = event.get("event_id") or event.get("event_type") or "—"
    matched = event.get("matched_keywords") or event.get("matched") or []
    if isinstance(matched, list):
        keyword_count = len(matched)
        sample = ", ".join(str(k) for k in matched[:3]) or "no specific keywords"
    else:
        keyword_count = 0
        sample = "no specific keywords"
    score_floor = event.get("score_floor") or 0
    polarity = insight.get("event_polarity") or "neutral"
    event_label = event_id.replace("event_", "").replace("_", " ").title()

    confidence_note = (
        "high confidence (multi-keyword match)" if keyword_count >= 2
        else "low confidence (theme fallback — no keyword match)" if keyword_count == 0
        else "borderline confidence (single keyword match)"
    )

    return {
        "metric": "what_changed",
        "source": "Snowkap event classifier (keyword + theme matching against the ESG ontology)",
        "simple_logic": (
            f"Snowkap read this story as a {event_label.lower()} event — "
            f"{confidence_note}. The classifier matched {keyword_count} keyword(s)"
            f"{(': ' + sample) if sample and keyword_count else ''}. "
            f"The story is framed "
            f"{'as a positive development for the company' if polarity == 'positive' else 'as a negative signal' if polarity == 'negative' else 'as a neutral / factual disclosure'}, "
            f"so we calibrate downstream weights accordingly."
        ),
        "formula_human": (
            f"The event is the best keyword match in the ontology that clears the "
            f"confidence bar (2+ hits). Score floor for this event type is {score_floor}, "
            f"and the polarity tag is '{polarity}'."
        ),
        "ontology_anchors": ["Event-type catalogue", "score floor / ceiling", "polarity tags"],
        "your_inputs": {
            "event_id": event_id,
            "matched_keyword_count": keyword_count,
            "sample_keywords": sample,
            "polarity": polarity,
            "score_floor": score_floor,
        },
    }


def _why_it_matters_methodology(
    insight: dict[str, Any], role: str | None = None,
) -> dict[str, Any]:
    """Plain-English explanation of the materiality + criticality math
    for this specific article + company."""
    criticality = insight.get("criticality") or {}
    components = criticality.get("components") or {}
    band = (criticality.get("band") or "MEDIUM").upper()
    score = criticality.get("score") or 0.0

    # Top component (dominant signal)
    positive_keys = (
        "materiality", "financial_magnitude", "actionability",
        "painpoint_match", "recency", "source_authority", "sentiment_trajectory",
    )
    best_name, best_val = "", -1.0
    for k in positive_keys:
        v = components.get(k)
        if isinstance(v, (int, float)) and float(v) > best_val:
            best_name, best_val = k, float(v)

    dom_phrase = {
        "materiality": "the topic is highly material to this industry",
        "financial_magnitude": "the rupee impact is large relative to revenue",
        "actionability": "there is a concrete deadline you can act on",
        "painpoint_match": "it matches a topic you've told us to track",
        "recency": "the story is very fresh",
        "source_authority": "a top-tier source is reporting it",
        "sentiment_trajectory": "company-wide sentiment is trending the wrong way",
    }.get(best_name, "multiple weak signals agree")

    company = _company_label(insight)
    exposure_block = (
        (insight.get("analysis") or {}).get("why_it_matters") or {}
    ).get("financial_exposure") or {}
    exposure_label = exposure_block.get("label") or ""
    exposure_source = exposure_block.get("source") or "engine_estimate"

    return {
        "metric": "why_it_matters",
        "source": "Snowkap criticality scorer + SASB industry-materiality overlay",
        "simple_logic": (
            f"We rated this {band.lower()} priority — {dom_phrase} "
            f"(composite score {score:.2f} out of 1.00). "
            f"{exposure_label or 'No specific ₹ exposure was quoted in the article'} — "
            f"{'this figure is taken directly from the article body' if exposure_source == 'article' else 'this figure is an engine estimate (not quoted in the article body) based on the company’s P&L profile'}. "
            f"Industry materiality weights come from {company}'s SASB sector mapping."
        ),
        "formula_human": (
            "Priority is a weighted blend of seven signals (materiality, "
            "financial magnitude, actionability, painpoint match, recency, "
            "source authority, sentiment trajectory). The dominant signal is "
            "the largest individual component."
        ),
        "ontology_anchors": ["SASB sector mapping", "direct vs asset-based materiality", "company painpoint overlay"],
        "your_inputs": {
            "band": band,
            "score": round(float(score), 3),
            "dominant_signal": best_name,
            "dominant_value": round(best_val, 3),
            "exposure_label": exposure_label,
            "exposure_source": exposure_source,
        },
    }


def _what_it_triggers_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    """Plain-English explanation of which obligations + actions surface."""
    pipeline = insight.get("pipeline") or {}
    frameworks = pipeline.get("frameworks") or insight.get("frameworks") or []
    if isinstance(frameworks, dict):
        frameworks = frameworks.get("matches") or []
    fw_codes: list[str] = []
    mandatory_count = 0
    for f in (frameworks or [])[:5]:
        if not isinstance(f, dict):
            continue
        code = f.get("framework_id") or f.get("code") or "?"
        fw_codes.append(code)
        if f.get("is_mandatory"):
            mandatory_count += 1
    recs = insight.get("recommendations") or {}
    if isinstance(recs, dict):
        rec_list = recs.get("recommendations") or []
    else:
        rec_list = recs if isinstance(recs, list) else []
    rec_count = len(rec_list)
    event = pipeline.get("event") or {}
    event_id = event.get("event_id") or "—"

    return {
        "metric": "what_it_triggers",
        "source": "Snowkap framework matcher + event-archetype recommendation library",
        "simple_logic": (
            f"This story touches {len(fw_codes)} disclosure framework(s)"
            f"{(': ' + ', '.join(fw_codes)) if fw_codes else ''}"
            f"{(', ' + str(mandatory_count) + ' of which are mandatory for the company’s region and cap tier') if mandatory_count else ''}. "
            f"The {rec_count} recommended action(s) come from the {event_id.replace('event_', '').replace('_', ' ')} "
            f"playbook and are vetted by a three-step review (proposal → analysis → validation), "
            f"then ranked by compliance urgency and expected return."
        ),
        "formula_human": (
            "Frameworks are pulled from the topic→framework map in the ontology, "
            "boosted by region (India / EU / US / UK / APAC / Global) and filtered by "
            "the company's cap tier. Recommendations run through a three-step review "
            "(proposal → analysis → validation) and are sorted by compliance urgency × ROI."
        ),
        "ontology_anchors": ["21-framework taxonomy", "framework sections", "mandatory disclosure rules"],
        "your_inputs": {
            "framework_codes": fw_codes,
            "mandatory_count": mandatory_count,
            "recommendation_count": rec_count,
            "event_id": event_id,
        },
    }


def _what_to_watch_methodology(insight: dict[str, Any]) -> dict[str, Any]:
    """Plain-English explanation of the trajectory + lead-indicator picks."""
    traj = insight.get("sentiment_trajectory") or {}
    horizons = traj.get("horizons") or {}
    h3 = (horizons.get("3m") or {}).get("direction") or "—"
    h6 = (horizons.get("6m") or {}).get("direction") or "—"
    h12 = (horizons.get("12m") or {}).get("direction") or "—"
    confidence = traj.get("confidence") or (horizons.get("3m") or {}).get("confidence") or "low"
    llm_used = bool(traj.get("llm_used"))
    risk = insight.get("risk_assessment") or insight.get("risk") or {}
    temples = risk.get("temples_risks") if isinstance(risk, dict) else None
    risk_cats = []
    if isinstance(temples, list):
        sorted_temples = sorted(
            temples,
            key=lambda r: float((r.get("score") if isinstance(r, dict) else 0) or 0),
            reverse=True,
        )
        risk_cats = [
            (r.get("category") if isinstance(r, dict) else "")
            for r in sorted_temples[:3]
            if (r.get("category") if isinstance(r, dict) else "")
        ]
    benchmarks = (
        (insight.get("analysis") or {}).get("what_to_watch") or {}
    ).get("benchmarks") or []
    bm_count = len(benchmarks)
    company = _company_label(insight)

    return {
        "metric": "what_to_watch",
        "source": "Snowkap sentiment forecaster + TEMPLES risk model + external-benchmark loader",
        "simple_logic": (
            f"Looking ahead, sentiment is expected to be "
            f"{h3 if h3 != '—' else 'flat'} at 3 months, "
            f"{h6 if h6 != '—' else 'flat'} at 6 months, and "
            f"{h12 if h12 != '—' else 'flat'} at 12 months (confidence: {confidence}). "
            f"{'The forecast blends Snowkap’s rolling polarity series for ' + company + ' with a short LLM polish step.' if llm_used else 'The trajectory is derived deterministically from the rolling polarity series for ' + company + '.'} "
            f"Top risk categories to track: {', '.join(risk_cats) if risk_cats else 'none flagged on this story'}. "
            f"{bm_count} external benchmark(s) (MSCI / SBTI / CRISIL / Sustainalytics) are currently loaded for context."
        ),
        "formula_human": (
            "Sentiment trajectory is a forecast of the rolling polarity series "
            "at three horizons (3 / 6 / 12 months). Risks are the top three "
            "TEMPLES categories scored by probability × exposure × industry weight. "
            "Benchmarks are the most-recent values loaded into the company benchmark store."
        ),
        "ontology_anchors": ["TEMPLES risk taxonomy", "industry risk-weight table", "rolling polarity series"],
        "your_inputs": {
            "horizon_3m": h3,
            "horizon_6m": h6,
            "horizon_12m": h12,
            "confidence": confidence,
            "top_risk_categories": risk_cats,
            "benchmark_count": bm_count,
            "llm_used": llm_used,
        },
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


METRIC_DISPATCH = {
    "criticality": _criticality_methodology,
    "relevance": _relevance_methodology,
    "persona_boost": _persona_boost_methodology,
    "sentiment_trajectory": _sentiment_trajectory_methodology,
    "framework_match": _framework_match_methodology,
    # Phase 29 — per-panel entries
    "stakeholder_map": _stakeholder_map_methodology,
    "board_paragraph": _board_paragraph_methodology,
    "kpi_table": _kpi_table_methodology,
    "risk_matrix": _risk_matrix_methodology,
    "esg_relevance_score": _esg_relevance_score_methodology,
    "ai_recommendations": _ai_recommendations_methodology,
    "impact_analysis": _impact_analysis_methodology,
    "financial_timeline": _financial_timeline_methodology,
    # Phase 33 — per-bullet entries for the UnifiedAnalysisCard. Each
    # builder reads the insight + result and emits article-specific,
    # plain-English explanation for that bullet's (i) drawer. Replaces
    # the formula-heavy generic fallback that lived in
    # unified_analysis._build_methodology_block.
    "what_changed": _what_changed_methodology,
    "why_it_matters": _why_it_matters_methodology,
    "what_it_triggers": _what_it_triggers_methodology,
    "what_to_watch": _what_to_watch_methodology,
}


# Panels that take a ``role`` argument (only criticality currently does;
# others are role-agnostic). Future panels can register here.
ROLE_AWARE_PANELS = frozenset({"criticality"})


def build_methodology(
    insight: dict[str, Any], *, role: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the per-metric + per-panel methodology blocks for one insight.

    Returns a dict keyed by metric / panel id. The 5 original entries
    (``criticality``, ``relevance``, ``persona_boost``,
    ``sentiment_trajectory``, ``framework_match``) describe how the
    SCORE was computed. The 7 Phase-29 entries (``stakeholder_map``,
    ``board_paragraph``, ``kpi_table``, ``risk_matrix``,
    ``esg_relevance_score``, ``ai_recommendations``, ``impact_analysis``)
    describe how each UI panel was GENERATED.

    Empty / missing components collapse to ``None`` in ``your_inputs``
    so the UI can render an "n/a" pill instead of erroring.

    ``role`` (optional) selects role-specific criticality weights —
    "cfo", "ceo", or "esg-analyst". When None, default weights are used.
    """
    out: dict[str, dict[str, Any]] = {}
    for panel_id, builder in METRIC_DISPATCH.items():
        try:
            if panel_id in ROLE_AWARE_PANELS:
                out[panel_id] = builder(insight, role=role)  # type: ignore[call-arg]
            else:
                out[panel_id] = builder(insight)
        except Exception as exc:  # noqa: BLE001 — never crash the API
            logger.warning(
                "methodology_provenance: %s builder failed (%s)",
                panel_id, exc,
            )
            out[panel_id] = {
                "metric": panel_id,
                "source": "(builder error — see server logs)",
                "simple_logic": "Methodology unavailable for this panel.",
                "formula_human": "",
                "ontology_anchors": [],
                "your_inputs": {},
            }
    return out


def build_panel_methodology(
    insight: dict[str, Any], panel_id: str, *, role: str | None = None,
) -> dict[str, Any] | None:
    """Phase 29 — return methodology for ONE panel only.

    Used by the ``?panel=<id>`` API path so the per-panel popover
    fetches just what it needs (smaller payload + faster paint).
    Returns ``None`` for unknown ``panel_id``.
    """
    builder = METRIC_DISPATCH.get(panel_id)
    if builder is None:
        return None
    try:
        if panel_id in ROLE_AWARE_PANELS:
            return builder(insight, role=role)  # type: ignore[call-arg]
        return builder(insight)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "methodology_provenance: %s builder failed (%s)", panel_id, exc,
        )
        return None


