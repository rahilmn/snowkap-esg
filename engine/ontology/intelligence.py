"""SPARQL intelligence layer.

Provides typed query functions that replace the hardcoded Python dicts
scattered across the legacy codebase. Every function runs SPARQL against the
ontology graph and returns plain-Python results.

These functions are the ONLY correct way for the pipeline to ask intelligence
questions. Do not read TTL files directly and do not hardcode domain knowledge
here — put the knowledge in ``data/ontology/knowledge_base.ttl`` instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from rdflib import Literal, URIRef

from engine.ontology.graph import OntologyGraph, get_graph

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes — return types for the query helpers
# ---------------------------------------------------------------------------


@dataclass
class FrameworkRef:
    id: str  # e.g. "BRSR", "GRI"
    label: str
    profitability_link: str


@dataclass
class MaterialityResult:
    topic_label: str
    industry_label: str
    weight: float


@dataclass
class RiskWeight:
    industry_label: str
    risk_category: str
    weight: float


@dataclass
class RiskIndicators:
    category: str
    lead_indicators: list[str]
    lag_indicators: list[str]


@dataclass
class PerspectiveConfig:
    slug: str
    label: str
    output_depth: str
    financial_framing: str
    max_words: int | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graph(graph: OntologyGraph | None) -> OntologyGraph:
    return graph or get_graph()


def _lower(value: str) -> str:
    return (value or "").strip().lower()


# ---------------------------------------------------------------------------
# Topic ↔ Framework (replaces _TOPIC_FRAMEWORK_MAP)
# ---------------------------------------------------------------------------


def query_frameworks_for_topic(
    topic: str, graph: OntologyGraph | None = None
) -> list[str]:
    """Return framework labels triggered by an ESG topic.

    Matches on topic label OR slug (case-insensitive). Returns distinct
    framework labels sorted for deterministic output.

    Replaces ``_TOPIC_FRAMEWORK_MAP`` in ``backend/services/ontology_service.py``.
    """
    g = _graph(graph)
    needle = _lower(topic)
    sparql = """
    SELECT DISTINCT ?fw_label WHERE {
        ?topic a/rdfs:subClassOf* snowkap:ESGTopic .
        ?topic snowkap:triggersFramework ?fw .
        ?fw rdfs:label ?fw_label .
        {
            ?topic rdfs:label ?label .
            FILTER(LCASE(STR(?label)) = ?needle)
        } UNION {
            ?topic snowkap:slug ?slug .
            FILTER(LCASE(STR(?slug)) = ?needle)
        }
    }
    ORDER BY ?fw_label
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [row["fw_label"] for row in rows]


def query_frameworks_detail(
    topic: str, graph: OntologyGraph | None = None
) -> list[FrameworkRef]:
    """Return full framework refs (with profitability link) for a topic."""
    g = _graph(graph)
    needle = _lower(topic)
    sparql = """
    SELECT DISTINCT ?fw ?fw_label ?profitability WHERE {
        ?topic snowkap:triggersFramework ?fw .
        ?fw rdfs:label ?fw_label .
        OPTIONAL { ?fw snowkap:profitabilityLink ?profitability }
        {
            ?topic rdfs:label ?label .
            FILTER(LCASE(STR(?label)) = ?needle)
        } UNION {
            ?topic snowkap:slug ?slug .
            FILTER(LCASE(STR(?slug)) = ?needle)
        }
    }
    ORDER BY ?fw_label
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [
        FrameworkRef(
            id=row["fw"].split("#", 1)[-1],
            label=row["fw_label"],
            profitability_link=row.get("profitability", ""),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Materiality weight (replaces MATERIALITY_MAP)
# ---------------------------------------------------------------------------


def query_materiality_weight(
    topic: str, industry: str, graph: OntologyGraph | None = None
) -> float:
    """Return the materiality weight for a (topic, industry) pair (0.0–1.0).

    Returns 0.5 as a neutral default if no explicit weight triple exists.
    """
    g = _graph(graph)
    topic_n = _lower(topic)
    industry_n = _lower(industry)
    sparql = """
    SELECT ?weight WHERE {
        ?statement rdf:subject ?topic ;
                   rdf:predicate snowkap:materialFor ;
                   rdf:object ?industry ;
                   snowkap:materialityWeight ?weight .
        ?topic rdfs:label ?topic_label .
        ?industry rdfs:label ?industry_label .
        FILTER(LCASE(STR(?topic_label)) = ?topic_n)
        FILTER(LCASE(STR(?industry_label)) = ?industry_n)
    }
    LIMIT 1
    """
    rows = g.select_rows(
        sparql,
        init_bindings={
            "topic_n": Literal(topic_n),
            "industry_n": Literal(industry_n),
        },
    )
    if rows:
        try:
            return float(rows[0]["weight"])
        except (TypeError, ValueError):
            pass
    return 0.5


# ---------------------------------------------------------------------------
# Industry risk weight (replaces INDUSTRY_RISK_WEIGHTS)
# ---------------------------------------------------------------------------


def query_risk_weight(
    industry: str, risk_category: str, graph: OntologyGraph | None = None
) -> float:
    """Return the amplification weight for (industry, risk_category).

    Weights >1.0 amplify the risk; <1.0 dampen. Returns 1.0 as neutral default.
    """
    g = _graph(graph)
    industry_n = _lower(industry)
    risk_n = _lower(risk_category)
    sparql = """
    SELECT ?weight WHERE {
        ?statement rdf:subject ?industry ;
                   rdf:predicate snowkap:amplifiesRisk ;
                   rdf:object ?risk ;
                   snowkap:riskWeight ?weight .
        ?industry rdfs:label ?industry_label .
        ?risk rdfs:label ?risk_label .
        FILTER(LCASE(STR(?industry_label)) = ?industry_n)
        FILTER(CONTAINS(LCASE(STR(?risk_label)), ?risk_n))
    }
    LIMIT 1
    """
    rows = g.select_rows(
        sparql,
        init_bindings={
            "industry_n": Literal(industry_n),
            "risk_n": Literal(risk_n),
        },
    )
    if rows:
        try:
            return float(rows[0]["weight"])
        except (TypeError, ValueError):
            pass
    return 1.0


# ---------------------------------------------------------------------------
# Risk lead/lag indicators
# ---------------------------------------------------------------------------


def query_risk_indicators(
    category: str, graph: OntologyGraph | None = None
) -> RiskIndicators:
    """Return lead and lag indicators for a risk category (ESG or TEMPLES)."""
    g = _graph(graph)
    needle = _lower(category)
    sparql = """
    SELECT ?label ?lead ?lag WHERE {
        { ?cat a snowkap:RiskCategory } UNION { ?cat a snowkap:TEMPLESCategory }
        ?cat rdfs:label ?label .
        OPTIONAL { ?cat snowkap:hasLeadIndicator ?lead }
        OPTIONAL { ?cat snowkap:hasLagIndicator ?lag }
        FILTER(CONTAINS(LCASE(STR(?label)), ?needle))
    }
    LIMIT 1
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    if not rows:
        return RiskIndicators(category=category, lead_indicators=[], lag_indicators=[])
    row = rows[0]
    return RiskIndicators(
        category=row["label"],
        lead_indicators=[s.strip() for s in row.get("lead", "").split(",") if s.strip()],
        lag_indicators=[s.strip() for s in row.get("lag", "").split(",") if s.strip()],
    )


# ---------------------------------------------------------------------------
# Perspective → relevant impact dimensions
# ---------------------------------------------------------------------------


def query_perspective_impacts(
    topic: str, perspective: str, graph: OntologyGraph | None = None
) -> list[str]:
    """Return impact-dimension slugs that matter for a topic from a perspective.

    Core of the perspective transformation layer: queries the chain
    ``ESGTopic → hasImpactOn → ImpactDimension → relevantTo → PerspectiveLens``.
    Returns the impact slugs (financial, regulatory, brand, etc.).
    """
    g = _graph(graph)
    topic_n = _lower(topic)
    persp_n = _lower(perspective)
    sparql = """
    SELECT DISTINCT ?slug WHERE {
        ?topic snowkap:hasImpactOn ?impact .
        ?impact snowkap:relevantTo ?lens .
        ?impact snowkap:slug ?slug .
        ?topic ?label_pred ?topic_label .
        ?lens ?lens_pred ?lens_label .
        FILTER(?label_pred IN (rdfs:label, snowkap:slug))
        FILTER(?lens_pred IN (rdfs:label, snowkap:slug))
        FILTER(LCASE(STR(?topic_label)) = ?topic_n)
        FILTER(LCASE(STR(?lens_label)) = ?persp_n)
    }
    ORDER BY ?slug
    """
    rows = g.select_rows(
        sparql,
        init_bindings={
            "topic_n": Literal(topic_n),
            "persp_n": Literal(persp_n),
        },
    )
    return [row["slug"] for row in rows]


def get_perspective_config(
    perspective: str, graph: OntologyGraph | None = None
) -> PerspectiveConfig | None:
    g = _graph(graph)
    needle = _lower(perspective)
    sparql = """
    SELECT ?slug ?label ?depth ?framing ?max WHERE {
        ?lens a snowkap:PerspectiveLens .
        ?lens rdfs:label ?label .
        ?lens snowkap:slug ?slug .
        OPTIONAL { ?lens snowkap:outputDepth ?depth }
        OPTIONAL { ?lens snowkap:financialFraming ?framing }
        OPTIONAL { ?lens snowkap:maxWords ?max }
        FILTER(LCASE(STR(?slug)) = ?needle || LCASE(STR(?label)) = ?needle)
    }
    LIMIT 1
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    if not rows:
        return None
    row = rows[0]
    max_words_raw = row.get("max", "")
    max_words = int(max_words_raw) if max_words_raw else None
    return PerspectiveConfig(
        slug=row["slug"],
        label=row["label"],
        output_depth=row.get("depth", "standard"),
        financial_framing=row.get("framing", ""),
        max_words=max_words,
    )


# ---------------------------------------------------------------------------
# Climate zones for a location
# ---------------------------------------------------------------------------


def query_climate_zones(
    location: str, graph: OntologyGraph | None = None
) -> list[str]:
    """Return climate-zone labels that affect a geographic region (by label)."""
    g = _graph(graph)
    needle = _lower(location)
    sparql = """
    SELECT DISTINCT ?zone_label WHERE {
        ?region a snowkap:GeographicRegion .
        ?region rdfs:label ?region_label .
        ?region snowkap:inClimateZone ?zone .
        ?zone rdfs:label ?zone_label .
        FILTER(CONTAINS(LCASE(STR(?region_label)), ?needle))
    }
    ORDER BY ?zone_label
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [row["zone_label"] for row in rows]


# ---------------------------------------------------------------------------
# Event type classification (replaces event_classifier.py rule list)
# ---------------------------------------------------------------------------


@dataclass
class EventRule:
    event_id: str
    label: str
    score_floor: int
    score_ceiling: int
    keywords: list[str]
    financial_transmission: str


def query_event_rules(graph: OntologyGraph | None = None) -> list[EventRule]:
    g = _graph(graph)
    sparql = """
    SELECT ?event ?label ?floor ?ceiling ?keywords ?transmission WHERE {
        ?event a snowkap:EventType .
        ?event rdfs:label ?label .
        OPTIONAL { ?event snowkap:scoreFloor ?floor }
        OPTIONAL { ?event snowkap:scoreCeiling ?ceiling }
        OPTIONAL { ?event snowkap:eventKeyword ?keywords }
        OPTIONAL { ?event snowkap:financialTransmission ?transmission }
    }
    """
    rules: list[EventRule] = []
    for row in g.select_rows(sparql):
        keywords = [k.strip() for k in row.get("keywords", "").split(",") if k.strip()]
        try:
            floor = int(row.get("floor") or 0)
        except ValueError:
            floor = 0
        try:
            ceiling = int(row.get("ceiling") or 10)
        except ValueError:
            ceiling = 10
        rules.append(
            EventRule(
                event_id=row["event"].split("#", 1)[-1],
                label=row["label"],
                score_floor=floor,
                score_ceiling=ceiling,
                keywords=keywords,
                financial_transmission=row.get("transmission", ""),
            )
        )
    return rules


def query_default_event_for_theme(
    theme_label: str, graph: OntologyGraph | None = None
) -> EventRule | None:
    """Return the fallback event type for a given ESG theme.

    Uses ``snowkap:defaultEventForTheme`` triples added in Phase 14.
    """
    g = _graph(graph)
    needle = theme_label.strip().lower()
    sparql = """
    SELECT ?event ?label ?floor ?ceiling ?keywords ?transmission WHERE {
        ?topic snowkap:defaultEventForTheme ?event .
        ?topic rdfs:label ?topic_label .
        ?event rdfs:label ?label .
        OPTIONAL { ?event snowkap:scoreFloor ?floor }
        OPTIONAL { ?event snowkap:scoreCeiling ?ceiling }
        OPTIONAL { ?event snowkap:eventKeyword ?keywords }
        OPTIONAL { ?event snowkap:financialTransmission ?transmission }
        FILTER(LCASE(STR(?topic_label)) = LCASE(?needle))
    }
    LIMIT 1
    """
    from rdflib import Literal

    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    if not rows:
        return None
    row = rows[0]
    keywords = [k.strip() for k in row.get("keywords", "").split(",") if k.strip()]
    try:
        floor = int(row.get("floor") or 2)
    except ValueError:
        floor = 2
    try:
        ceiling = int(row.get("ceiling") or 6)
    except ValueError:
        ceiling = 6
    return EventRule(
        event_id=row["event"].split("#", 1)[-1] if "#" in row.get("event", "") else row.get("event", ""),
        label=row["label"],
        score_floor=floor,
        score_ceiling=ceiling,
        keywords=keywords,
        financial_transmission=row.get("transmission", ""),
    )


# ---------------------------------------------------------------------------
# Causal chain decay
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def query_hop_decay() -> dict[int, float]:
    """Return {hop_count: decay_factor} map sourced from the ontology."""
    g = get_graph()
    sparql = """
    SELECT ?hops ?decay WHERE {
        ?entry snowkap:hopCount ?hops .
        ?entry snowkap:decayFactor ?decay .
    }
    ORDER BY ?hops
    """
    out: dict[int, float] = {}
    for row in g.select_rows(sparql):
        try:
            out[int(row["hops"])] = float(row["decay"])
        except (TypeError, ValueError):
            continue
    if not out:
        # Safe fallback matching legacy values
        out = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.2, 4: 0.1}
    return out


# ---------------------------------------------------------------------------
# Capitalization tier (replaces cap_parameters.py)
# ---------------------------------------------------------------------------


@dataclass
class CapTierConfig:
    label: str
    financial_impact_floor: float
    investor_sensitivity: float
    regulatory_scrutiny: float


def query_cap_tier(
    tier_label: str, graph: OntologyGraph | None = None
) -> CapTierConfig | None:
    g = _graph(graph)
    needle = _lower(tier_label)
    sparql = """
    SELECT ?label ?floor ?sens ?scrut WHERE {
        ?tier a snowkap:CapitalizationTier .
        ?tier rdfs:label ?label .
        OPTIONAL { ?tier snowkap:financialImpactFloor ?floor }
        OPTIONAL { ?tier snowkap:investorSensitivity ?sens }
        OPTIONAL { ?tier snowkap:regulatoryScrutiny ?scrut }
        FILTER(LCASE(STR(?label)) = ?needle)
    }
    LIMIT 1
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    if not rows:
        return None
    row = rows[0]
    return CapTierConfig(
        label=row["label"],
        financial_impact_floor=float(row.get("floor") or 0.0),
        investor_sensitivity=float(row.get("sens") or 1.0),
        regulatory_scrutiny=float(row.get("scrut") or 1.0),
    )


# ---------------------------------------------------------------------------
# Compliance deadlines
# ---------------------------------------------------------------------------


@dataclass
class ComplianceDeadlineInfo:
    label: str
    framework: str
    jurisdiction: str
    deadline_date: str
    recurrence: str
    applicability: str
    penalty: str


def query_compliance_deadlines(
    jurisdiction: str | None = None, graph: OntologyGraph | None = None
) -> list[ComplianceDeadlineInfo]:
    g = _graph(graph)
    sparql = """
    SELECT ?label ?framework ?jurisdiction ?date ?recurrence ?cond ?penalty WHERE {
        ?d a snowkap:ComplianceDeadline .
        ?d rdfs:label ?label .
        OPTIONAL { ?d snowkap:appliesFramework ?fw . ?fw rdfs:label ?framework }
        OPTIONAL { ?d snowkap:jurisdiction ?jurisdiction }
        OPTIONAL { ?d snowkap:deadlineDate ?date }
        OPTIONAL { ?d snowkap:recurrence ?recurrence }
        OPTIONAL { ?d snowkap:applicabilityCondition ?cond }
        OPTIONAL { ?d snowkap:penaltyForNonCompliance ?penalty }
    }
    ORDER BY ?date
    """
    rows = g.select_rows(sparql)
    out: list[ComplianceDeadlineInfo] = []
    jur_n = _lower(jurisdiction) if jurisdiction else None
    for row in rows:
        if jur_n and _lower(row.get("jurisdiction", "")) != jur_n:
            continue
        out.append(
            ComplianceDeadlineInfo(
                label=row["label"],
                framework=row.get("framework", ""),
                jurisdiction=row.get("jurisdiction", ""),
                deadline_date=row.get("date", ""),
                recurrence=row.get("recurrence", ""),
                applicability=row.get("cond", ""),
                penalty=row.get("penalty", ""),
            )
        )
    return out


# ---------------------------------------------------------------------------
# SDG mapping
# ---------------------------------------------------------------------------


def query_sdgs_for_topic(
    topic: str, graph: OntologyGraph | None = None
) -> list[str]:
    g = _graph(graph)
    needle = _lower(topic)
    sparql = """
    SELECT DISTINCT ?sdg_label WHERE {
        ?topic snowkap:contributesToSDG ?sdg .
        ?sdg rdfs:label ?sdg_label .
        {
            ?topic rdfs:label ?label .
            FILTER(LCASE(STR(?label)) = ?needle)
        } UNION {
            ?topic snowkap:slug ?slug .
            FILTER(LCASE(STR(?slug)) = ?needle)
        }
    }
    ORDER BY ?sdg_label
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [row["sdg_label"] for row in rows]


# ---------------------------------------------------------------------------
# Stakeholder interest
# ---------------------------------------------------------------------------


def query_stakeholders_for_topic(
    topic: str, graph: OntologyGraph | None = None
) -> list[str]:
    g = _graph(graph)
    needle = _lower(topic)
    sparql = """
    SELECT DISTINCT ?stakeholder_label WHERE {
        ?stakeholder a snowkap:Stakeholder .
        ?stakeholder rdfs:label ?stakeholder_label .
        ?stakeholder snowkap:careAbout ?topic .
        {
            ?topic rdfs:label ?label .
            FILTER(LCASE(STR(?label)) = ?needle)
        } UNION {
            ?topic snowkap:slug ?slug .
            FILTER(LCASE(STR(?slug)) = ?needle)
        }
    }
    ORDER BY ?stakeholder_label
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [row["stakeholder_label"] for row in rows]


# ---------------------------------------------------------------------------
# Phase 14 — New intelligence queries
# ---------------------------------------------------------------------------


def query_framework_sections(
    framework_id: str, topic: str, graph: OntologyGraph | None = None
) -> list[str]:
    """Return section codes from a framework relevant to a topic.

    Walks ``Framework → hasSection → FrameworkSection`` and filters by
    topic keyword match against ``sectionTitle``.
    """
    g = _graph(graph)
    fw_needle = framework_id.strip().lower()
    sparql = """
    SELECT ?code ?title WHERE {
        ?fw snowkap:hasSection ?section .
        ?section snowkap:sectionCode ?code .
        OPTIONAL { ?section snowkap:sectionTitle ?title }
        FILTER(CONTAINS(LCASE(STR(?fw)), ?fw_needle))
    }
    ORDER BY ?code
    """
    rows = g.select_rows(sparql, init_bindings={"fw_needle": Literal(fw_needle)})
    if not rows:
        return []
    # Filter sections whose title contains a topic keyword (>3 chars)
    topic_words = {w.lower() for w in topic.replace("&", "").split() if len(w) > 3}
    matched = []
    for row in rows:
        title = (row.get("title") or "").lower()
        code = row["code"]
        if topic_words and any(w in title for w in topic_words):
            matched.append(code)
    return matched if matched else [row["code"] for row in rows[:3]]


def query_competitors(
    company_slug: str, graph: OntologyGraph | None = None
) -> list[str]:
    """Return labels of companies linked via ``competessWith``."""
    g = _graph(graph)
    sparql = """
    SELECT ?peer_label WHERE {
        ?company snowkap:slug ?slug .
        ?company snowkap:competessWith ?peer .
        ?peer rdfs:label ?peer_label .
    }
    """
    rows = g.select_rows(sparql, init_bindings={"slug": Literal(company_slug)})
    return [row["peer_label"] for row in rows]


@dataclass
class PenaltyPrecedent:
    label: str
    regulator: str
    median_fine_range: str
    jurisdiction: str


def query_penalty_precedents(
    jurisdiction: str | None = None, graph: OntologyGraph | None = None
) -> list[PenaltyPrecedent]:
    """Return regulatory penalty precedents from the ontology."""
    g = _graph(graph)
    sparql = """
    SELECT ?label ?regulator ?fine ?jurisdiction WHERE {
        ?p a snowkap:RegulatoryPenalty .
        ?p rdfs:label ?label .
        OPTIONAL { ?p snowkap:regulatorBody ?regulator }
        OPTIONAL { ?p snowkap:medianFineRange ?fine }
        OPTIONAL { ?p snowkap:jurisdiction ?jurisdiction }
    }
    """
    rows = g.select_rows(sparql)
    jur_n = jurisdiction.strip().lower() if jurisdiction else None
    results = []
    for row in rows:
        if jur_n and jur_n not in (row.get("jurisdiction") or "").lower():
            continue
        results.append(PenaltyPrecedent(
            label=row.get("label", ""),
            regulator=row.get("regulator", ""),
            median_fine_range=row.get("fine", ""),
            jurisdiction=row.get("jurisdiction", ""),
        ))
    return results


@dataclass
class PeerAction:
    company: str
    topic: str
    action: str
    outcome: str
    year: int


def query_peer_actions(
    topic: str, industry: str = "", graph: OntologyGraph | None = None
) -> list[PeerAction]:
    """Return notable peer actions for a given ESG topic."""
    g = _graph(graph)
    needle = topic.strip().lower()
    sparql = """
    SELECT ?company ?topic ?action ?outcome ?year WHERE {
        ?pa a snowkap:PeerAction .
        ?pa snowkap:company ?company .
        ?pa snowkap:topic ?topic .
        ?pa snowkap:action ?action .
        OPTIONAL { ?pa snowkap:outcome ?outcome }
        OPTIONAL { ?pa snowkap:year ?year }
        FILTER(CONTAINS(LCASE(STR(?topic)), ?needle))
    }
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    results = []
    for row in rows:
        try:
            year = int(row.get("year", 0))
        except (ValueError, TypeError):
            year = 0
        results.append(PeerAction(
            company=row.get("company", ""),
            topic=row.get("topic", ""),
            action=row.get("action", ""),
            outcome=row.get("outcome", ""),
            year=year,
        ))
    return results


@dataclass
class ROIBenchmark:
    industry: str
    action_type: str
    typical_roi: str
    typical_payback: str


def query_industry_roi_benchmarks(
    industry: str, action_type: str = "", graph: OntologyGraph | None = None
) -> ROIBenchmark | None:
    """Return ROI benchmark for an industry + action type."""
    g = _graph(graph)
    ind_n = industry.strip().lower()
    sparql = """
    SELECT ?industry ?action_type ?roi ?payback WHERE {
        ?b a snowkap:ROIBenchmark .
        ?b snowkap:forIndustry ?industry .
        OPTIONAL { ?b snowkap:forActionType ?action_type }
        OPTIONAL { ?b snowkap:typicalROI ?roi }
        OPTIONAL { ?b snowkap:typicalPayback ?payback }
        FILTER(CONTAINS(LCASE(STR(?industry)), ?needle))
    }
    LIMIT 1
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(ind_n)})
    if not rows:
        return None
    row = rows[0]
    return ROIBenchmark(
        industry=row.get("industry", ""),
        action_type=row.get("action_type", ""),
        typical_roi=row.get("roi", ""),
        typical_payback=row.get("payback", ""),
    )


# ---------------------------------------------------------------------------
# Phase 15 — Ontology-driven configuration queries
# (Replace all hardcoded Python dicts)
# ---------------------------------------------------------------------------


def query_esg_risk_categories(graph: OntologyGraph | None = None) -> list[str]:
    """Return all ESG risk category labels from ontology."""
    g = _graph(graph)
    sparql = """
    SELECT ?label WHERE {
        ?cat a snowkap:RiskCategory .
        ?cat rdfs:label ?label .
    }
    ORDER BY ?label
    """
    return [row["label"] for row in g.select_rows(sparql)]


def query_temples_categories(graph: OntologyGraph | None = None) -> list[str]:
    """Return all TEMPLES category labels from ontology."""
    g = _graph(graph)
    sparql = """
    SELECT ?label WHERE {
        ?cat a snowkap:TEMPLESCategory .
        ?cat rdfs:label ?label .
    }
    ORDER BY ?label
    """
    return [row["label"] for row in g.select_rows(sparql)]


def query_theme_risk_map(
    theme: str, graph: OntologyGraph | None = None
) -> list[str]:
    """Return risk category labels triggered by an ESG theme."""
    g = _graph(graph)
    needle = _lower(theme)
    sparql = """
    SELECT DISTINCT ?risk_label WHERE {
        ?topic snowkap:triggersRiskCategory ?risk .
        ?risk rdfs:label ?risk_label .
        {
            ?topic rdfs:label ?label .
            FILTER(LCASE(STR(?label)) = ?needle)
        } UNION {
            ?topic snowkap:slug ?slug .
            FILTER(LCASE(STR(?slug)) = ?needle)
        }
    }
    ORDER BY ?risk_label
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [row["risk_label"] for row in rows]


def query_theme_temples_map(
    theme: str, graph: OntologyGraph | None = None
) -> list[str]:
    """Return TEMPLES category labels triggered by an ESG theme."""
    g = _graph(graph)
    needle = _lower(theme)
    sparql = """
    SELECT DISTINCT ?temples_label WHERE {
        ?topic snowkap:triggersTEMPLES ?temples .
        ?temples rdfs:label ?temples_label .
        {
            ?topic rdfs:label ?label .
            FILTER(LCASE(STR(?label)) = ?needle)
        } UNION {
            ?topic snowkap:slug ?slug .
            FILTER(LCASE(STR(?slug)) = ?needle)
        }
    }
    ORDER BY ?temples_label
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [row["temples_label"] for row in rows]


@dataclass
class RiskLevelThreshold:
    level: str
    min_score: float


@lru_cache(maxsize=1)
def query_risk_level_thresholds(
    graph: OntologyGraph | None = None,
) -> list[RiskLevelThreshold]:
    """Return risk level thresholds sorted by minScore DESC."""
    g = _graph(graph) if graph else get_graph()
    sparql = """
    SELECT ?level ?min_score WHERE {
        ?t a snowkap:RiskLevelThreshold .
        ?t snowkap:riskLevel ?level .
        ?t snowkap:minScore ?min_score .
    }
    ORDER BY DESC(?min_score)
    """
    rows = g.select_rows(sparql)
    return [
        RiskLevelThreshold(level=row["level"], min_score=float(row["min_score"]))
        for row in rows
    ]


@dataclass
class RegionalBoost:
    framework_id: str
    boost_value: float


def query_regional_boosts(
    region: str, graph: OntologyGraph | None = None
) -> list[RegionalBoost]:
    """Return framework boost values for a region."""
    g = _graph(graph)
    sparql = """
    SELECT ?fw ?boost WHERE {
        ?b a snowkap:RegionalFrameworkBoost .
        ?b snowkap:forRegion ?region .
        ?b snowkap:boostsFramework ?fw .
        ?b snowkap:boostValue ?boost .
    }
    """
    rows = g.select_rows(sparql, init_bindings={"region": Literal(region.upper())})
    return [
        RegionalBoost(
            framework_id=row["fw"].split("#", 1)[-1],
            boost_value=float(row["boost"]),
        )
        for row in rows
    ]


@dataclass
class MandatoryRuleInfo:
    framework_id: str
    region: str
    cap_tier: str


def query_mandatory_rules(
    region: str, graph: OntologyGraph | None = None
) -> list[MandatoryRuleInfo]:
    """Return mandatory framework rules for a region."""
    g = _graph(graph)
    sparql = """
    SELECT ?fw ?region ?cap WHERE {
        ?r a snowkap:MandatoryRule .
        ?r snowkap:mandatoryFramework ?fw .
        ?r snowkap:mandatoryRegion ?region .
        ?r snowkap:mandatoryCapTier ?cap .
    }
    """
    rows = g.select_rows(sparql, init_bindings={"region": Literal(region.upper())})
    return [
        MandatoryRuleInfo(
            framework_id=row["fw"].split("#", 1)[-1],
            region=row["region"],
            cap_tier=row["cap"],
        )
        for row in rows
    ]


@dataclass
class PriorityRuleInfo:
    urgency: str
    impact: str
    priority: str


@lru_cache(maxsize=1)
def query_priority_rules(
    graph: OntologyGraph | None = None,
) -> list[PriorityRuleInfo]:
    """Return all priority derivation rules."""
    g = _graph(graph) if graph else get_graph()
    sparql = """
    SELECT ?urgency ?impact ?priority WHERE {
        ?r a snowkap:PriorityRule .
        ?r snowkap:ifUrgency ?urgency .
        ?r snowkap:ifImpact ?impact .
        ?r snowkap:thenPriority ?priority .
    }
    """
    return [
        PriorityRuleInfo(
            urgency=row["urgency"], impact=row["impact"], priority=row["priority"]
        )
        for row in g.select_rows(sparql)
    ]


@dataclass
class RiskOfInactionConfig:
    base_scores: dict[str, int]
    type_bonuses: dict[str, int]
    escalation_keywords: list[str]


@lru_cache(maxsize=1)
def query_risk_of_inaction_config(
    graph: OntologyGraph | None = None,
) -> RiskOfInactionConfig:
    """Return risk-of-inaction config from ontology."""
    g = _graph(graph) if graph else get_graph()
    # Base scores per priority
    base_sparql = """
    SELECT ?priority ?score WHERE {
        ?c a snowkap:RiskOfInactionConfig .
        ?c snowkap:forPriority ?priority .
        ?c snowkap:baseRiskScore ?score .
    }
    """
    base_scores: dict[str, int] = {}
    for row in g.select_rows(base_sparql):
        base_scores[row["priority"]] = int(row["score"])

    # Type bonuses
    type_sparql = """
    SELECT ?rec_type ?bonus WHERE {
        ?c a snowkap:RiskOfInactionConfig .
        ?c snowkap:forRecType ?rec_type .
        ?c snowkap:recTypeBonus ?bonus .
    }
    """
    type_bonuses: dict[str, int] = {}
    for row in g.select_rows(type_sparql):
        type_bonuses[row["rec_type"]] = int(row["bonus"])

    # Escalation keywords
    kw_sparql = """
    SELECT ?kw WHERE {
        ?c a snowkap:RiskOfInactionConfig .
        ?c snowkap:escalationKeyword ?kw .
    }
    """
    keywords: list[str] = []
    for row in g.select_rows(kw_sparql):
        keywords.extend(k.strip() for k in row["kw"].split(",") if k.strip())

    return RiskOfInactionConfig(
        base_scores=base_scores or {"CRITICAL": 8, "HIGH": 6, "MEDIUM": 4, "LOW": 2},
        type_bonuses=type_bonuses or {"compliance": 2, "esg_positioning": 1},
        escalation_keywords=keywords or ["penalty", "fine", "enforcement", "litigation"],
    )


def query_grid_column_map(
    graph: OntologyGraph | None = None,
) -> dict[str, str]:
    """Return impact dimension slug → grid column mapping."""
    g = _graph(graph)
    sparql = """
    SELECT ?slug ?column WHERE {
        ?dim a snowkap:ImpactDimension .
        ?dim snowkap:slug ?slug .
        ?dim snowkap:gridColumn ?column .
    }
    """
    rows = g.select_rows(sparql)
    result = {row["slug"]: row["column"] for row in rows}
    if not result:
        # Fallback if ontology not loaded yet
        result = {
            "financial": "financial", "cost": "financial", "value": "financial",
            "volume": "financial", "regulatory": "regulatory", "operational": "regulatory",
            "strategic": "strategic", "reputational": "strategic", "brand": "strategic",
            "growth": "strategic",
        }
    return result


def query_dim_to_insight_keys(
    graph: OntologyGraph | None = None,
) -> dict[str, list[str]]:
    """Return impact dimension slug → insight analysis keys mapping."""
    g = _graph(graph)
    sparql = """
    SELECT ?slug ?keys WHERE {
        ?dim a snowkap:ImpactDimension .
        ?dim snowkap:slug ?slug .
        ?dim snowkap:insightKey ?keys .
    }
    """
    rows = g.select_rows(sparql)
    result: dict[str, list[str]] = {}
    for row in rows:
        keys = [k.strip() for k in row["keys"].split(",") if k.strip()]
        result[row["slug"]] = keys
    return result


@dataclass
class HeadlineRuleInfo:
    priority: int
    source_field: str
    template: str
    is_fallback: bool


def query_headline_rules(
    perspective: str, graph: OntologyGraph | None = None
) -> list[HeadlineRuleInfo]:
    """Return headline reframing rules for a perspective, sorted by priority."""
    g = _graph(graph)
    needle = _lower(perspective)
    sparql = """
    SELECT ?priority ?source ?template ?fallback WHERE {
        ?rule a snowkap:HeadlineRule .
        ?rule snowkap:forPerspective ?lens .
        ?rule snowkap:headlinePriority ?priority .
        ?rule snowkap:sourceField ?source .
        ?rule snowkap:headlineTemplate ?template .
        OPTIONAL { ?rule snowkap:isFallback ?fallback }
        ?lens snowkap:slug ?slug .
        FILTER(LCASE(STR(?slug)) = ?needle)
    }
    ORDER BY ?priority
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [
        HeadlineRuleInfo(
            priority=int(row["priority"]),
            source_field=row.get("source", ""),
            template=row.get("template", "{base}"),
            is_fallback=str(row.get("fallback", "false")).lower() == "true",
        )
        for row in rows
    ]


@dataclass
class RankingSortKey:
    sort_key: str
    sort_direction: str
    sort_priority: int


def query_perspective_ranking_keys(
    perspective: str, graph: OntologyGraph | None = None
) -> list[RankingSortKey]:
    """Return sort keys for perspective-specific recommendation ranking."""
    g = _graph(graph)
    needle = _lower(perspective)
    sparql = """
    SELECT ?key ?direction ?priority WHERE {
        ?r snowkap:forPerspective ?lens .
        ?r snowkap:sortKey ?key .
        ?r snowkap:sortDirection ?direction .
        ?r snowkap:sortPriority ?priority .
        ?lens snowkap:slug ?slug .
        FILTER(LCASE(STR(?slug)) = ?needle)
    }
    ORDER BY ?priority
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [
        RankingSortKey(
            sort_key=row["key"],
            sort_direction=row["direction"],
            sort_priority=int(row["priority"]),
        )
        for row in rows
    ]


def query_perspective_rec_types(
    perspective: str, graph: OntologyGraph | None = None
) -> list[str]:
    """Return the recommendation types visible for a given perspective lens.

    Returns an ordered list of type strings (e.g. ['financial', 'compliance', 'operational']).
    Empty list means show all types (no filter).
    """
    g = _graph(graph)
    needle = _lower(perspective)
    sparql = """
    SELECT ?recType WHERE {
        ?lens snowkap:showsRecType ?recType .
        ?lens snowkap:slug ?slug .
        FILTER(LCASE(STR(?slug)) = ?needle)
    }
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    types: list[str] = []
    for row in rows:
        val = str(row["recType"])
        # Handle comma-separated values from ontology
        for t in val.split(","):
            t = t.strip().strip('"')
            if t and t not in types:
                types.append(t)
    return types


# ---------------------------------------------------------------------------
# LAYER 7: Causal Primitives Queries
# ---------------------------------------------------------------------------


@dataclass
class PrimitiveInfo:
    slug: str
    label: str


@dataclass
class CausalEdgeInfo:
    edge_id: str
    source_slug: str
    target_slug: str
    direction: str
    functional_form: str
    elasticity: str
    lag: str
    aggregation: str
    confidence: str
    notes: str


@dataclass
class FeedbackLoopInfo:
    loop_id: str
    loop_type: str
    path: str
    notes: str


def query_primitives_for_event(
    event_type: str, graph: OntologyGraph | None = None
) -> list[PrimitiveInfo]:
    """Return primitives affected by a Snowkap EventType (primary + secondary).

    Handles both URI-style ('event_heavy_penalty') and label-style
    ('Heavy Regulatory Penalty') inputs by normalizing to lowercase underscore.
    """
    g = _graph(graph)
    # Normalize: "Heavy Regulatory Penalty" → "heavy_regulatory_penalty"
    # Also handles: "event_heavy_penalty" → "event_heavy_penalty"
    needle = _lower(event_type).replace(" ", "_")
    sparql = """
    SELECT ?slug ?label ?primary WHERE {
        {
            ?event snowkap:affectsPrimitive ?prim .
            ?prim snowkap:slug ?slug .
            ?prim rdfs:label ?label .
            BIND("primary" AS ?primary)
            FILTER(CONTAINS(LCASE(STR(?event)), ?needle))
        }
        UNION
        {
            ?event snowkap:affectsPrimitiveSecondary ?prim .
            ?prim snowkap:slug ?slug .
            ?prim rdfs:label ?label .
            BIND("secondary" AS ?primary)
            FILTER(CONTAINS(LCASE(STR(?event)), ?needle))
        }
    }
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    # Deduplicate, primary first
    seen: set[str] = set()
    result: list[PrimitiveInfo] = []
    for row in sorted(rows, key=lambda r: str(r.get("primary", "z"))):
        slug = str(row["slug"])
        if slug not in seen:
            seen.add(slug)
            result.append(PrimitiveInfo(slug=slug, label=str(row["label"])))
    return result


def query_p2p_edges(
    source_primitive: str, graph: OntologyGraph | None = None
) -> list[CausalEdgeInfo]:
    """Return all outgoing P→P causal edges from a source primitive."""
    g = _graph(graph)
    needle = _lower(source_primitive)
    sparql = """
    SELECT ?edgeId ?targetSlug ?direction ?form ?elasticity ?lag ?agg ?conf ?notes WHERE {
        ?edge a snowkap:CausalEdge .
        ?edge snowkap:cause ?source .
        ?edge snowkap:effect ?target .
        ?source snowkap:slug ?srcSlug .
        ?target snowkap:slug ?targetSlug .
        ?edge snowkap:edgeId ?edgeId .
        ?edge snowkap:directionSign ?direction .
        ?edge snowkap:functionalForm ?form .
        ?edge snowkap:elasticityOrWeight ?elasticity .
        ?edge snowkap:lagK ?lag .
        ?edge snowkap:aggregationRule ?agg .
        ?edge snowkap:confidenceLevel ?conf .
        OPTIONAL { ?edge snowkap:edgeNotes ?notes }
        FILTER(LCASE(STR(?srcSlug)) = ?needle)
    }
    ORDER BY ?conf ?targetSlug
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [
        CausalEdgeInfo(
            edge_id=str(row["edgeId"]),
            source_slug=source_primitive.upper(),
            target_slug=str(row["targetSlug"]),
            direction=str(row["direction"]),
            functional_form=str(row["form"]),
            elasticity=str(row["elasticity"]),
            lag=str(row["lag"]),
            aggregation=str(row["agg"]),
            confidence=str(row["conf"]),
            notes=str(row.get("notes", "")),
        )
        for row in rows
    ]


def query_cascade_context(
    event_type: str, graph: OntologyGraph | None = None
) -> str:
    """Build a human-readable cascade context string for LLM prompt enrichment.

    Returns a formatted text block showing:
    - Primary + secondary primitives affected by the event
    - All outgoing P→P edges from the primary primitive with β, lag, form
    - Relevant feedback loops
    """
    prims = query_primitives_for_event(event_type, graph)
    if not prims:
        return ""

    lines: list[str] = []
    lines.append("CAUSAL PRIMITIVES CONTEXT:")
    lines.append(f"  Event type: {event_type}")
    primary = prims[0] if prims else None
    secondary = prims[1:] if len(prims) > 1 else []

    if primary:
        lines.append(f"  Primary primitive: {primary.label} ({primary.slug})")
    if secondary:
        sec_str = ", ".join(f"{p.label} ({p.slug})" for p in secondary[:4])
        lines.append(f"  Secondary primitives: {sec_str}")

    # Get edges from primary primitive
    if primary:
        edges = query_p2p_edges(primary.slug, graph)
        if edges:
            lines.append("  Direct causal edges (order-2):")
            for e in edges[:8]:  # Limit to top 8 edges
                lines.append(
                    f"    {e.edge_id}: {e.source_slug}→{e.target_slug} "
                    f"(β={e.elasticity}, {e.functional_form}, lag={e.lag}, "
                    f"direction={e.direction}, agg={e.aggregation}, conf={e.confidence})"
                )
                if e.notes:
                    lines.append(f"      Notes: {e.notes[:120]}")

    # Get relevant feedback loops
    loops = query_feedback_loops(primary.slug if primary else "", graph)
    if loops:
        lines.append("  Feedback loops involving this primitive:")
        for loop in loops[:3]:
            lines.append(
                f"    [{loop.loop_id}] ({loop.loop_type}): {loop.path}"
            )

    lines.append("  USE THESE PARAMETERS to compute financial_exposure. Do not guess ranges.")
    return "\n".join(lines)


def query_feedback_loops(
    primitive_slug: str, graph: OntologyGraph | None = None
) -> list[FeedbackLoopInfo]:
    """Return feedback loops that involve the given primitive."""
    g = _graph(graph)
    needle = _lower(primitive_slug)
    sparql = """
    SELECT ?loopId ?loopType ?loopPath ?notes WHERE {
        ?arc a snowkap:FeedbackArc .
        ?arc snowkap:loopId ?loopId .
        ?arc snowkap:loopType ?loopType .
        ?arc snowkap:loopPath ?loopPath .
        OPTIONAL { ?arc snowkap:edgeNotes ?notes }
        FILTER(CONTAINS(LCASE(STR(?loopPath)), ?needle))
    }
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [
        FeedbackLoopInfo(
            loop_id=str(row["loopId"]),
            loop_type=str(row["loopType"]),
            path=str(row["loopPath"]),
            notes=str(row.get("notes", "")),
        )
        for row in rows
    ]


def query_thresholds_for_primitive(
    primitive_slug: str, graph: OntologyGraph | None = None
) -> list[dict[str, str]]:
    """Return threshold categories relevant to edges involving this primitive."""
    g = _graph(graph)
    needle = _lower(primitive_slug)
    sparql = """
    SELECT ?label ?range ?unit ?edges WHERE {
        ?tau a snowkap:ThresholdCategory .
        ?tau rdfs:label ?label .
        ?tau snowkap:thresholdRange ?range .
        ?tau snowkap:thresholdUnit ?unit .
        ?tau snowkap:applicableEdges ?edges .
        FILTER(CONTAINS(LCASE(STR(?edges)), ?needle))
    }
    """
    rows = g.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [
        {
            "label": str(row["label"]),
            "range": str(row["range"]),
            "unit": str(row["unit"]),
            "edges": str(row["edges"]),
        }
        for row in rows
    ]
