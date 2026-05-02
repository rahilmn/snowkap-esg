"""Causal chain engine.

BFS traversal over the ontology graph from a news entity to a company via
the 17 typed causal relationships (directlyImpacts, suppliesTo,
sharesWaterBasin, ownedBy, etc.). Decay factors are sourced from the
ontology via :func:`engine.ontology.intelligence.query_hop_decay`.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from rdflib import URIRef

from engine.ontology.graph import OntologyGraph, get_graph
from engine.ontology.intelligence import query_hop_decay

logger = logging.getLogger(__name__)

MAX_HOPS = 4
MAX_PATHS_PER_SOURCE = 5

# Predicate URI → human-readable relationship type.
PREDICATE_TO_RELATIONSHIP: dict[str, str] = {
    "http://snowkap.com/ontology/esg#directlyImpacts": "directOperational",
    "http://snowkap.com/ontology/esg#indirectlyImpacts": "indirectImpact",
    "http://snowkap.com/ontology/esg#suppliesTo": "supplyChainUpstream",
    "http://snowkap.com/ontology/esg#sourcesFrom": "supplyChainDownstream",
    "http://snowkap.com/ontology/esg#locatedIn": "geographicProximity",
    "http://snowkap.com/ontology/esg#regulatedBy": "regulatoryContagion",
    "http://snowkap.com/ontology/esg#competessWith": "industrySpillover",
    "http://snowkap.com/ontology/esg#employsWorkforce": "workforceIndirect",
    "http://snowkap.com/ontology/esg#dependsOnCommodity": "commodityChain",
    "http://snowkap.com/ontology/esg#sharesWaterBasin": "waterSharedBasin",
    "http://snowkap.com/ontology/esg#pollutionPathway": "pollutionDispersion",
    "http://snowkap.com/ontology/esg#climateExposure": "climateRiskExposure",
    "http://snowkap.com/ontology/esg#laborContractedBy": "laborContractor",
    "http://snowkap.com/ontology/esg#affectsCommunity": "communityAffected",
    "http://snowkap.com/ontology/esg#sameJurisdiction": "regulatoryJurisdiction",
    "http://snowkap.com/ontology/esg#ownedBy": "ownershipChain",
    "http://snowkap.com/ontology/esg#investedIn": "investorExposure",
    "http://snowkap.com/ontology/esg#customerOf": "customerConcentration",
    "http://snowkap.com/ontology/esg#hasFacility": "directOperational",
    "http://snowkap.com/ontology/esg#inClimateZone": "climateRiskExposure",
    "http://snowkap.com/ontology/esg#belongsToIndustry": "industrySpillover",
}


@dataclass
class CausalPath:
    nodes: list[str]  # human labels
    node_uris: list[str]
    edges: list[str]  # relationship names
    edge_uris: list[str]
    hops: int
    relationship_type: str
    impact_score: float
    explanation: str
    frameworks: list[str] = field(default_factory=list)


def classify_relationship(edge_uris: list[str]) -> str:
    for uri in edge_uris:
        rel = PREDICATE_TO_RELATIONSHIP.get(uri)
        if rel:
            return rel
    return "indirectImpact"


def _label_for_uri(g: OntologyGraph, uri: str) -> str:
    rows = g.select_rows(
        "SELECT ?label WHERE { ?s rdfs:label ?label } LIMIT 1",
        init_bindings={"s": URIRef(uri)},
    )
    if rows and rows[0].get("label"):
        return rows[0]["label"]
    return uri.split("#", 1)[-1].replace("_", " ")


def _resolve_entity(entity_text: str, graph: OntologyGraph) -> list[str]:
    """Find candidate node URIs whose label contains the entity text."""
    from rdflib import Literal

    sparql = """
    SELECT DISTINCT ?s WHERE {
        ?s rdfs:label ?label .
        FILTER(CONTAINS(LCASE(STR(?label)), ?needle))
    }
    LIMIT 5
    """
    needle = entity_text.strip().lower()
    if not needle:
        return []
    rows = graph.select_rows(sparql, init_bindings={"needle": Literal(needle)})
    return [row["s"] for row in rows]


def _find_company_uri(company_slug: str, graph: OntologyGraph) -> str | None:
    from rdflib import Literal

    sparql = """
    SELECT ?s WHERE {
        ?s a snowkap:Company .
        ?s snowkap:slug ?slug .
        FILTER(LCASE(STR(?slug)) = ?needle)
    }
    LIMIT 1
    """
    rows = graph.select_rows(
        sparql, init_bindings={"needle": Literal(company_slug.lower())}
    )
    return rows[0]["s"] if rows else None


def _neighbors(node_uri: str, graph: OntologyGraph) -> list[tuple[str, str]]:
    """Return list of ``(edge_uri, target_uri)`` reachable from ``node_uri``.

    Traverses both outgoing edges (``node -> target``) and incoming edges
    (``source -> node`` treated as reverse relationships). This ensures BFS
    can reach a company node even when the ontology models the relationship
    from the other side (e.g. ``company locatedIn region`` has no outgoing
    edge from the region back to the company).
    """
    from rdflib import URIRef as U

    out: list[tuple[str, str]] = []

    # Outgoing edges
    sparql_out = """
    SELECT ?rel ?target WHERE {
        ?s ?rel ?target .
        FILTER(isURI(?target))
        FILTER(STRSTARTS(STR(?rel), "http://snowkap.com/ontology/esg#"))
    }
    """
    for row in graph.select_rows(sparql_out, init_bindings={"s": U(node_uri)}):
        out.append((row["rel"], row["target"]))

    # Incoming edges (treated as reverse traversal hops)
    sparql_in = """
    SELECT ?rel ?source WHERE {
        ?source ?rel ?t .
        FILTER(isURI(?source))
        FILTER(STRSTARTS(STR(?rel), "http://snowkap.com/ontology/esg#"))
    }
    """
    for row in graph.select_rows(sparql_in, init_bindings={"t": U(node_uri)}):
        out.append((row["rel"], row["source"]))

    return out


def _find_topic_uri(topic_label: str, graph: OntologyGraph) -> str | None:
    """Resolve an ESG topic label (e.g. "Climate Change") to its URI."""
    from rdflib import Literal

    sparql = """
    SELECT ?t WHERE {
        ?t a ?cls .
        ?t rdfs:label ?lbl .
        FILTER(?cls IN (snowkap:EnvironmentalTopic, snowkap:SocialTopic, snowkap:GovernanceTopic))
        FILTER(LCASE(STR(?lbl)) = ?needle)
    }
    LIMIT 1
    """
    rows = graph.select_rows(
        sparql, init_bindings={"needle": Literal((topic_label or "").lower().strip())}
    )
    return rows[0]["t"] if rows else None


def find_theme_causal_chains(
    topic_label: str,
    company_slug: str,
    graph: OntologyGraph | None = None,
) -> list[CausalPath]:
    """Build a synthetic causal chain from an ESG topic to a company.

    This walks the ontology semantically rather than instance-graph BFS:

        Topic → materialFor → Industry → belongsToIndustry⁻¹ → Company
        Topic → triggersFramework → Framework → reportsUnder⁻¹ → Company
        Topic → hasImpactOn → ImpactDimension (intelligence path)

    Returns up to a few topical paths even when no entity-level match
    exists (e.g. a macro/sentiment article like an Oxfam ranking).
    """
    g = graph or get_graph()
    decay = query_hop_decay()

    target_uri = _find_company_uri(company_slug, g)
    topic_uri = _find_topic_uri(topic_label, g)
    if not target_uri or not topic_uri:
        return []

    company_label = _label_for_uri(g, target_uri)
    topic_lbl = _label_for_uri(g, topic_uri)
    paths: list[CausalPath] = []

    # Path 1: Topic → materialFor → Industry → company (semantic 2-hop)
    from rdflib import URIRef as U

    industry_rows = g.select_rows(
        """
        SELECT ?industry ?industry_label WHERE {
            ?topic snowkap:materialFor ?industry .
            ?industry rdfs:label ?industry_label .
            ?company snowkap:belongsToIndustry ?industry .
        }
        LIMIT 5
        """,
        init_bindings={"topic": U(topic_uri), "company": U(target_uri)},
    )
    for row in industry_rows:
        industry_label = row.get("industry_label", "")
        paths.append(
            CausalPath(
                nodes=[topic_lbl, industry_label, company_label],
                node_uris=[topic_uri, row["industry"], target_uri],
                edges=["materialFor", "industrySpillover"],
                edge_uris=[
                    "http://snowkap.com/ontology/esg#materialFor",
                    "http://snowkap.com/ontology/esg#belongsToIndustry",
                ],
                hops=2,
                relationship_type="industrySpillover",
                impact_score=round(decay.get(2, 0.4) * 1.5, 3),  # boost for materiality match
                explanation=f"{topic_lbl} is material for {industry_label} → impacts {company_label}",
            )
        )

    # Path 2: Topic → triggersFramework → Framework (1-hop framework relevance)
    fw_rows = g.select_rows(
        """
        SELECT ?fw ?fw_label WHERE {
            ?topic snowkap:triggersFramework ?fw .
            ?fw rdfs:label ?fw_label .
        }
        LIMIT 3
        """,
        init_bindings={"topic": U(topic_uri)},
    )
    for row in fw_rows:
        fw_label = row.get("fw_label", "")
        paths.append(
            CausalPath(
                nodes=[topic_lbl, fw_label, company_label],
                node_uris=[topic_uri, row["fw"], target_uri],
                edges=["triggersFramework", "regulatoryContagion"],
                edge_uris=[
                    "http://snowkap.com/ontology/esg#triggersFramework",
                    "http://snowkap.com/ontology/esg#reportsUnder",
                ],
                hops=2,
                relationship_type="regulatoryContagion",
                impact_score=round(decay.get(2, 0.4) * 1.2, 3),
                explanation=f"{topic_lbl} triggers {fw_label} disclosure obligation for {company_label}",
            )
        )

    return paths[:5]


def find_causal_chains(
    entity_text: str,
    company_slug: str,
    graph: OntologyGraph | None = None,
    max_hops: int = MAX_HOPS,
) -> list[CausalPath]:
    """BFS from any resolved entity URI toward the company URI.

    Returns paths sorted by impact_score descending, up to
    :data:`MAX_PATHS_PER_SOURCE` per source URI.
    """
    g = graph or get_graph()
    decay = query_hop_decay()

    target_uri = _find_company_uri(company_slug, g)
    if not target_uri:
        # Phase 22.3 — onboarded prospects exist in companies.json + the
        # SQLite tenant registry but the ontology TTL is built at deploy
        # time and doesn't yet have a node for them. Demote the noise to
        # DEBUG for known tenants (curated targets OR registered onboards)
        # and reserve WARNING for truly-unknown slugs (typo / bug). We
        # bust the lru_cache on load_companies() so a freshly-onboarded
        # slug becomes "known" without a process restart.
        is_known = False
        try:
            from engine.config import load_companies
            load_companies.cache_clear()
            for c in load_companies():
                if c.slug == company_slug:
                    is_known = True
                    break
        except Exception:  # noqa: BLE001 — defensive
            pass
        if not is_known:
            try:
                from engine.index import tenant_registry
                for t in tenant_registry.list_tenants():
                    if t.get("slug") == company_slug:
                        is_known = True
                        break
            except Exception:  # noqa: BLE001
                pass
        if is_known:
            logger.debug(
                "causal_engine: %s not in ontology yet — skipping causal chains",
                company_slug,
            )
        else:
            logger.warning("causal_engine: unknown company slug '%s'", company_slug)
        return []

    sources = _resolve_entity(entity_text, g)
    if not sources:
        logger.debug("causal_engine: no entity matches for '%s'", entity_text)
        return []

    seen_signatures: set[tuple[str, ...]] = set()
    paths: list[CausalPath] = []

    for source_uri in sources:
        # 0-hop case: entity resolves directly to the target company.
        if source_uri == target_uri:
            label = _label_for_uri(g, target_uri)
            paths.append(
                CausalPath(
                    nodes=[label],
                    node_uris=[target_uri],
                    edges=[],
                    edge_uris=[],
                    hops=0,
                    relationship_type="directOperational",
                    impact_score=decay.get(0, 1.0),
                    explanation=label,
                )
            )
            continue

        queue: deque[tuple[list[str], list[str]]] = deque()
        queue.append(([source_uri], []))
        visited: set[str] = {source_uri}

        per_source = 0
        while queue and per_source < MAX_PATHS_PER_SOURCE:
            node_chain, edge_chain = queue.popleft()
            current = node_chain[-1]

            if current == target_uri and len(node_chain) > 1:
                hops = len(edge_chain)
                signature = tuple(node_chain + edge_chain)
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                impact = decay.get(hops, decay.get(max(decay.keys()), 0.05))
                labels = [_label_for_uri(g, uri) for uri in node_chain]
                relationship = classify_relationship(edge_chain)
                paths.append(
                    CausalPath(
                        nodes=labels,
                        node_uris=node_chain,
                        edges=[
                            PREDICATE_TO_RELATIONSHIP.get(e, "indirectImpact")
                            for e in edge_chain
                        ],
                        edge_uris=edge_chain,
                        hops=hops,
                        relationship_type=relationship,
                        impact_score=round(impact, 3),
                        explanation=" -> ".join(labels),
                    )
                )
                per_source += 1
                continue

            if len(edge_chain) >= max_hops:
                continue

            for edge_uri, neighbor in _neighbors(current, g):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(
                    (node_chain + [neighbor], edge_chain + [edge_uri])
                )

    paths.sort(key=lambda p: p.impact_score, reverse=True)
    return paths[:MAX_PATHS_PER_SOURCE]
