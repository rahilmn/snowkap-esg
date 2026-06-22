"""Phase 51 — normalize free-text topic/industry strings to canonical ontology labels.

``query_materiality_weight`` matches on the EXACT ``rdfs:label`` (case-insensitive).
The Stage-2 LLM theme tagger and the company resolver emit free-text that often
near-misses the canonical label — "Climate" vs "Climate Change", "Banking" vs
"Financials/Banking", "GHG Emissions" vs "Emissions" — silently collapsing
materiality to the 0.5 neutral default. This module maps those variants back to
the canonical labels so the ontology's real weights actually fire.

The canonical sets are loaded from the ontology itself (authoritative, no drift).
Matching order per input: exact label → curated alias → punctuation-insensitive
loose match → pass-through (so a genuine miss still surfaces in the
default-hit log downstream).
"""
from __future__ import annotations

import functools
import logging
import re

logger = logging.getLogger(__name__)


def _strip(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _load_labels(class_uris: tuple[str, ...]) -> dict[str, str]:
    """Return ``{lowercased_label: canonical_label}`` for the given ontology classes."""
    out: dict[str, str] = {}
    try:
        from engine.ontology.graph import get_graph
        g = get_graph()
        filt = " || ".join(f"?c = {c}" for c in class_uris)
        rows = g.select_rows(
            f"SELECT ?l WHERE {{ ?t a ?c ; rdfs:label ?l . FILTER({filt}) }}"
        )
        for r in rows:
            lbl = (r.get("l") or "").strip()
            if lbl:
                out[lbl.lower()] = lbl
    except Exception:  # never break scoring if the graph can't load
        logger.warning("materiality_aliases: failed to load canonical labels", exc_info=True)
    return out


@functools.lru_cache(maxsize=1)
def _canonical_topics() -> dict[str, str]:
    return _load_labels(
        ("snowkap:EnvironmentalTopic", "snowkap:SocialTopic", "snowkap:GovernanceTopic")
    )


@functools.lru_cache(maxsize=1)
def _canonical_industries() -> dict[str, str]:
    return _load_labels(("snowkap:Industry",))


# Curated free-text → canonical-label aliases (lowercased keys). Targets are
# resolved through the ontology set, so approximate spelling still lands exactly.
_TOPIC_ALIASES: dict[str, str] = {
    "climate": "Climate Change", "climate risk": "Climate Change",
    "climate transition": "Climate Change", "transition risk": "Climate Change",
    "global warming": "Climate Change",
    "ghg": "Emissions", "ghg emissions": "Emissions", "carbon": "Emissions",
    "carbon emissions": "Emissions", "greenhouse gas": "Emissions", "scope 3": "Emissions",
    "scope 1": "Emissions", "scope 2": "Emissions", "decarbonisation": "Emissions",
    "energy transition": "Energy", "energy efficiency": "Energy", "renewables mix": "Energy",
    "water management": "Water", "water stress": "Water", "water scarcity": "Water",
    "effluent": "Pollution", "air pollution": "Pollution", "air quality": "Pollution",
    "waste": "Waste & Circularity", "circular economy": "Waste & Circularity",
    "recycling": "Waste & Circularity", "circularity": "Waste & Circularity",
    "biodiversity loss": "Biodiversity", "nature": "Biodiversity", "deforestation": "Biodiversity",
    "dei": "Diversity, Equity & Inclusion", "diversity": "Diversity, Equity & Inclusion",
    "diversity & inclusion": "Diversity, Equity & Inclusion",
    "diversity and inclusion": "Diversity, Equity & Inclusion",
    "worker safety": "Health & Safety", "workplace safety": "Health & Safety",
    "occupational safety": "Health & Safety", "employee safety": "Health & Safety",
    "safety": "Health & Safety",
    "labour": "Supply Chain Labor", "labor": "Supply Chain Labor",
    "supply chain": "Supply Chain Labor", "supply chain labour": "Supply Chain Labor",
    "human rights": "Supply Chain Labor", "forced labour": "Supply Chain Labor",
    "child labour": "Supply Chain Labor",
    "data privacy": "Data Privacy & Security", "cybersecurity": "Data Privacy & Security",
    "data security": "Data Privacy & Security", "privacy": "Data Privacy & Security",
    "cyber": "Data Privacy & Security",
    "governance": "Stakeholder Governance", "corporate governance": "Stakeholder Governance",
    "board": "Board & Leadership", "board governance": "Board & Leadership",
    "leadership": "Board & Leadership", "executive compensation": "Board & Leadership",
    "executive pay": "Board & Leadership",
    "ethics": "Ethics & Compliance", "compliance": "Ethics & Compliance",
    "corruption": "Ethics & Compliance", "bribery": "Ethics & Compliance",
    "anti-corruption": "Ethics & Compliance", "fraud": "Ethics & Compliance",
    "disclosure": "Transparency & Disclosure", "transparency": "Transparency & Disclosure",
    "reporting": "Transparency & Disclosure", "esg disclosure": "Transparency & Disclosure",
    "tax": "Tax Transparency", "taxation": "Tax Transparency",
    "talent": "Human Capital", "workforce": "Human Capital", "employees": "Human Capital",
    "product quality": "Product Safety", "recall": "Product Safety",
    "community": "Community Impact", "csr": "Community Impact",
    "resilience": "Climate Adaptation", "physical risk": "Climate Adaptation",
    "adaptation": "Climate Adaptation",
}

_INDUSTRY_ALIASES: dict[str, str] = {
    "banking": "Financials/Banking", "bank": "Financials/Banking", "banks": "Financials/Banking",
    "financials": "Financials/Banking", "financial services": "Financials/Banking",
    "financial": "Financials/Banking", "commercial banks": "Financials/Banking",
    "private sector bank": "Financials/Banking", "public sector bank": "Financials/Banking",
    "nbfc": "Financials/Banking", "insurance": "Financials/Banking", "finance": "Financials/Banking",
    "asset manager": "Asset Management", "amc": "Asset Management",
    "mutual fund": "Asset Management", "investment management": "Asset Management",
    "wealth management": "Asset Management",
    "power": "Power/Energy", "energy": "Power/Energy", "utilities": "Power/Energy",
    "electric utilities": "Power/Energy", "power generation": "Power/Energy",
    "thermal power": "Power/Energy", "electricity": "Power/Energy",
    "renewables": "Renewable Energy", "solar": "Renewable Energy", "wind": "Renewable Energy",
    "clean energy": "Renewable Energy", "green energy": "Renewable Energy",
    "automobile": "Automotive", "auto": "Automotive", "auto parts": "Automotive",
    "auto components": "Automotive", "automotive components": "Automotive",
    "chemical": "Chemicals", "specialty chemicals": "Chemicals", "petrochemicals": "Chemicals",
    "pharma": "Pharmaceuticals", "pharmaceutical": "Pharmaceuticals",
    "life sciences": "Pharmaceuticals", "biotech": "Pharmaceuticals", "biosciences": "Pharmaceuticals",
    "metals": "Metals & Mining", "mining": "Metals & Mining", "metals and mining": "Metals & Mining",
    "oil": "Oil & Gas", "gas": "Oil & Gas", "oil and gas": "Oil & Gas", "petroleum": "Oil & Gas",
    "it": "Technology", "information technology": "Technology", "tech": "Technology",
    "software": "Technology", "it services": "Technology", "saas": "Technology",
    "consumer": "Consumer Goods", "fmcg": "Consumer Goods", "consumer staples": "Consumer Goods",
    "consumer durables": "Consumer Goods",
    "health care": "Healthcare", "hospitals": "Healthcare", "diagnostics": "Healthcare",
    "construction": "Infrastructure", "real estate": "Infrastructure", "engineering": "Infrastructure",
    "epc": "Infrastructure", "cement": "Infrastructure",
}


def _resolve_canon(label: str, canon_map: dict[str, str]) -> str | None:
    """Exact (case-insensitive) or punctuation-insensitive canonical label, else None."""
    low = (label or "").strip().lower()
    if low in canon_map:
        return canon_map[low]
    stripped = _strip(label)
    if stripped:
        for canon in canon_map.values():
            if _strip(canon) == stripped:
                return canon
    return None


def _normalize(value: str, canon_map: dict[str, str], alias_map: dict[str, str]) -> str:
    if not value or not value.strip():
        return value
    v = value.strip()
    hit = _resolve_canon(v, canon_map)
    if hit:
        return hit
    alias = alias_map.get(v.lower())
    if alias:
        return _resolve_canon(alias, canon_map) or alias
    return v  # unknown — pass through; the default-hit is logged downstream


def canonical_topic(label: str) -> str:
    """Map a free-text ESG topic to its canonical ontology label (best-effort)."""
    return _normalize(label, _canonical_topics(), _TOPIC_ALIASES)


def canonical_industry(label: str) -> str:
    """Map a free-text industry to its canonical ontology label (best-effort)."""
    return _normalize(label, _canonical_industries(), _INDUSTRY_ALIASES)


# Phase 53 (A1) — canonical ESG topic LABEL → SASB TTL topic-URI suffix.
# THE BUG: query_materiality_weight canonicalises a topic to its human rdfs:label
# ("Climate Change") and passed THAT to query_sasb_materiality, whose
# STRENDS(STR(?topic), <normalised label>) matched against snowkap:topic_<suffix>
# ("topic_climate"). "climate_change" never ends "...topic_climate" → 17/21 bank
# topics silently missed (incl. the two most material: Climate 0.95, Data Privacy
# 0.90), collapsing the SASB sector overlay to the 0.5/base default. The 4 that
# accidentally worked are those whose label==suffix (Emissions, Human Capital,
# Supply Chain Labor, Stakeholder Governance). This map converts the canonical
# LABEL to the snake_case SASB topic suffix so the STRENDS match fires.
# Keys are the canonical rdfs:labels (the alias targets above); values are the
# topic-URI suffixes in data/ontology/sasb_materiality.ttl.
_SASB_TOPIC_SUFFIX: dict[str, str] = {
    "Climate Change": "climate",
    "Climate Adaptation": "climate_adaptation",
    "Emissions": "emissions",
    "Energy": "energy",
    "Water": "water",
    "Pollution": "pollution",
    "Waste & Circularity": "waste",
    "Biodiversity": "biodiversity",
    "Health & Safety": "health_safety",
    "Supply Chain Labor": "supply_chain_labor",
    "Human Capital": "human_capital",
    "Diversity, Equity & Inclusion": "dei",
    "Community Impact": "community",
    "Data Privacy & Security": "data_privacy",
    "Product Safety": "product_safety",
    "Stakeholder Governance": "stakeholder_governance",
    "Board & Leadership": "board_leadership",
    "Ethics & Compliance": "ethics_compliance",
    "Transparency & Disclosure": "transparency",
    "Tax Transparency": "tax_transparency",
    "Risk Management": "risk_management",
}


def canonical_sasb_topic(label: str) -> str:
    """Map a free-text/canonical ESG topic to its SASB TTL topic-URI suffix
    (e.g. "Climate Change" -> "climate"), so query_sasb_materiality's
    STRENDS(STR(?topic), <suffix>) match against snowkap:topic_<suffix> fires.

    Canonicalises first (idempotent on an already-canonical label), then maps to
    the suffix. Falls back to the canonical label when the topic has no SASB
    counterpart — the loader then normalises it and (correctly) misses, landing
    on the base/neutral weight, exactly as before.
    """
    canon = canonical_topic(label)
    return _SASB_TOPIC_SUFFIX.get(canon, canon)


__all__ = ["canonical_topic", "canonical_industry", "canonical_sasb_topic"]
