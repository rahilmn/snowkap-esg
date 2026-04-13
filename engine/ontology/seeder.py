"""Seed the 7 target companies into the ontology graph.

Reads ``config/companies.json`` and inserts:
- ``snowkap:Company`` nodes with labels, slugs, capitalization tiers
- ``snowkap:belongsToIndustry`` links
- ``snowkap:locatedIn`` links to headquarter regions
- ``snowkap:hasCapitalization`` links
- (Optional) auto-discovered facilities / suppliers / competitors via OpenAI

After inserting, the seeder persists the new triples to
``data/ontology/companies.ttl`` so they survive restarts.

Usage:
    python -m engine.ontology.seeder
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow `python -m engine.ontology.seeder` without PYTHONPATH.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS

from engine.config import Company, load_companies
from engine.ontology.graph import SNOWKAP, OntologyGraph, get_graph, reset_graph

logger = logging.getLogger(__name__)

INDUSTRY_SLUG_MAP = {
    "Financials/Banking": "industry_banking",
    "Asset Management": "industry_asset_mgmt",
    "Power/Energy": "industry_power",
    "Renewable Energy": "industry_renewable",
    "Oil & Gas": "industry_oil_gas",
    "Metals & Mining": "industry_mining",
    "Steel": "industry_steel",
    "Chemicals": "industry_chemicals",
    "Pharmaceuticals": "industry_pharma",
    "Technology": "industry_technology",
    "Automotive": "industry_auto",
    "Consumer Goods": "industry_fmcg",
    "Retail": "industry_retail",
    "Healthcare": "industry_healthcare",
    "Infrastructure": "industry_infrastructure",
}

CAP_TIER_MAP = {
    "Large Cap": "tier_large_cap",
    "Mid Cap": "tier_mid_cap",
    "Small Cap": "tier_small_cap",
}


def _company_uri(slug: str) -> URIRef:
    return URIRef(f"http://snowkap.com/ontology/esg#company_{slug.replace('-', '_')}")


def _industry_uri(industry: str) -> URIRef:
    local = INDUSTRY_SLUG_MAP.get(industry, "industry_other")
    return URIRef(f"http://snowkap.com/ontology/esg#{local}")


def _tier_uri(tier: str) -> URIRef:
    local = CAP_TIER_MAP.get(tier, "tier_mid_cap")
    return URIRef(f"http://snowkap.com/ontology/esg#{local}")


def _region_uri(city: str) -> URIRef:
    slug = city.strip().lower().replace(" ", "_")
    return URIRef(f"http://snowkap.com/ontology/esg#region_{slug}")


def _facility_uri(company_slug: str, facility_slug: str) -> URIRef:
    c = company_slug.replace("-", "_")
    f = facility_slug.replace("-", "_")
    return URIRef(f"http://snowkap.com/ontology/esg#facility_{c}_{f}")


# Seed data for facilities per company. Keeps Phase 1 deterministic — in
# Phase 2 we can run OpenAI auto-discovery, but for now we encode what we
# know about the 7 target companies to give the causal engine a graph to
# traverse.
COMPANY_FACILITIES: dict[str, list[dict]] = {
    "icici-bank": [
        {"slug": "hq_mumbai", "name": "ICICI Bank HQ", "region": "mumbai", "type": "headquarter"},
        {"slug": "bangalore_ops", "name": "ICICI Bank Bangalore Ops", "region": "bangalore", "type": "operations"},
        {"slug": "hyderabad_ops", "name": "ICICI Bank Hyderabad Ops", "region": "hyderabad", "type": "operations"},
    ],
    "yes-bank": [
        {"slug": "hq_mumbai", "name": "YES Bank HQ", "region": "mumbai", "type": "headquarter"},
        {"slug": "delhi_ops", "name": "YES Bank Delhi Branch", "region": "delhi", "type": "branch"},
    ],
    "idfc-first-bank": [
        {"slug": "hq_mumbai", "name": "IDFC First Bank HQ", "region": "mumbai", "type": "headquarter"},
        {"slug": "chennai_ops", "name": "IDFC First Bank Chennai", "region": "chennai", "type": "branch"},
    ],
    "waaree-energies": [
        {"slug": "hq_mumbai", "name": "Waaree HQ", "region": "mumbai", "type": "headquarter"},
        {"slug": "chikhli_plant", "name": "Waaree Chikhli Manufacturing", "region": "ahmedabad", "type": "factory"},
    ],
    "singularity-amc": [
        {"slug": "hq_mumbai", "name": "Singularity AMC HQ", "region": "mumbai", "type": "headquarter"},
    ],
    "adani-power": [
        {"slug": "hq_ahmedabad", "name": "Adani Power HQ", "region": "ahmedabad", "type": "headquarter"},
        {"slug": "tiroda_plant", "name": "Tiroda Thermal Power", "region": "tiroda", "type": "power_plant"},
        {"slug": "mundra_plant", "name": "Mundra Power Plant", "region": "mundra", "type": "power_plant"},
    ],
    "jsw-energy": [
        {"slug": "hq_mumbai", "name": "JSW Energy HQ", "region": "mumbai", "type": "headquarter"},
        {"slug": "ratnagiri_plant", "name": "Ratnagiri Power Plant", "region": "ratnagiri", "type": "power_plant"},
        {"slug": "vijayanagar_plant", "name": "Vijayanagar Captive Power", "region": "vijayanagar", "type": "power_plant"},
    ],
}


def seed_company(graph: OntologyGraph, company: Company) -> int:
    """Insert triples for a single company. Returns count of triples added."""
    uri = _company_uri(company.slug)
    triples: list[tuple] = [
        (uri, RDF.type, SNOWKAP.Company),
        (uri, RDFS.label, Literal(company.name)),
        (uri, SNOWKAP.slug, Literal(company.slug)),
        (uri, SNOWKAP.belongsToIndustry, _industry_uri(company.industry)),
        (uri, SNOWKAP.sasbCategory, Literal(company.sasb_category)),
        (uri, SNOWKAP.hasCapitalization, _tier_uri(company.market_cap)),
        (uri, SNOWKAP.country, Literal(company.headquarter_country)),
        (uri, SNOWKAP.region, Literal(company.headquarter_region)),
        (uri, SNOWKAP.locatedIn, _region_uri(company.headquarter_city)),
    ]

    for facility in COMPANY_FACILITIES.get(company.slug, []):
        fac_uri = _facility_uri(company.slug, facility["slug"])
        triples.extend(
            [
                (fac_uri, RDF.type, SNOWKAP.Facility),
                (fac_uri, RDFS.label, Literal(facility["name"])),
                (fac_uri, SNOWKAP.locatedIn, _region_uri(facility["region"])),
                (uri, SNOWKAP.hasFacility, fac_uri),
            ]
        )

    graph.insert_triples(triples)
    return len(triples)


def seed_all(persist: bool = True) -> dict[str, int]:
    """Seed every company from config/companies.json. Returns a summary dict."""
    reset_graph()
    graph = get_graph()
    summary: dict[str, int] = {}
    for company in load_companies():
        added = seed_company(graph, company)
        summary[company.slug] = added
        logger.info("Seeded %s (+%s triples)", company.slug, added)
    if persist:
        graph.persist_companies()
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    summary = seed_all(persist=True)
    graph = get_graph()
    print(f"\nTotal triples after seeding: {graph.triple_count()}")
    stats = graph.stats()
    for key in (
        "companies",
        "esg_topics",
        "frameworks",
        "industries",
        "perspectives",
        "risk_categories",
        "temples_categories",
    ):
        print(f"  {key}: {stats.get(key, 0)}")
    print("\nCompanies seeded:")
    for slug, count in summary.items():
        print(f"  {slug}: +{count} triples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
