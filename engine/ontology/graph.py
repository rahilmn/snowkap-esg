"""Ontology graph manager.

Wraps rdflib.Graph with convenience methods for loading the schema +
knowledge base + company instance data, running SPARQL queries, inserting
triples at runtime, and persisting back to the `.ttl` files.

The graph is the intelligence brain — all domain knowledge queries go
through this layer. No more hardcoded Python dicts for domain logic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD
from rdflib.query import Result

from engine.config import get_data_path

logger = logging.getLogger(__name__)

SNOWKAP = Namespace("http://snowkap.com/ontology/esg#")

DEFAULT_PREFIXES = """
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX snowkap: <http://snowkap.com/ontology/esg#>
"""


class OntologyGraph:
    """In-process RDF graph with persistence to ``data/ontology/*.ttl``."""

    def __init__(
        self,
        schema_path: Path | None = None,
        knowledge_path: Path | None = None,
        companies_path: Path | None = None,
        expansion_path: Path | None = None,
        depth_path: Path | None = None,
    ) -> None:
        self.schema_path = schema_path or get_data_path("ontology", "schema.ttl")
        self.knowledge_path = knowledge_path or get_data_path(
            "ontology", "knowledge_base.ttl"
        )
        self.expansion_path = expansion_path or get_data_path(
            "ontology", "knowledge_expansion.ttl"
        )
        self.depth_path = depth_path or get_data_path(
            "ontology", "knowledge_depth.ttl"
        )
        self.companies_path = companies_path or get_data_path(
            "ontology", "companies.ttl"
        )
        self.graph = Graph()
        self.graph.bind("snowkap", SNOWKAP)
        self.graph.bind("owl", OWL)
        self.graph.bind("rdf", RDF)
        self.graph.bind("rdfs", RDFS)
        self.graph.bind("xsd", XSD)
        self._loaded = False

    def load(self) -> "OntologyGraph":
        """Load schema + knowledge + expansion + depth + company + primitives."""
        # Primitives layer (Layer 7) — causal graph
        ontology_dir = self.schema_path.parent
        primitives_files = [
            ontology_dir / "primitives_schema.ttl",
            ontology_dir / "primitives_edges_p2p.ttl",
            ontology_dir / "primitives_thresholds.ttl",
        ]
        # Optional files (may not exist yet)
        for opt in (
            "primitives_indicators.ttl",
            "primitives_order3.ttl",
            "precedents.ttl",
            # Phase 4: perspective-generation ontology
            "kpis.ttl",
            "scenarios.ttl",
            "stakeholder_positions.ttl",
            "sdg_targets.ttl",
            # Phase 3 follow-up: framework section rationales
            "framework_rationales.ttl",
        ):
            p = ontology_dir / opt
            if p.exists():
                primitives_files.append(p)

        for path in (
            self.schema_path,
            self.knowledge_path,
            self.expansion_path,
            self.depth_path,
            self.companies_path,
            *primitives_files,
        ):
            if path.exists():
                self.graph.parse(path, format="turtle")
                logger.debug("Loaded ontology file: %s", path)
            else:
                logger.debug("Ontology file missing (ok if first run): %s", path)
        self._loaded = True
        logger.info("Ontology graph loaded — %s triples", len(self.graph))
        return self

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ------------------------------------------------------------------
    # SPARQL
    # ------------------------------------------------------------------

    def query(self, sparql: str, init_bindings: dict | None = None) -> Result:
        """Run a SPARQL SELECT / ASK / CONSTRUCT query.

        ``init_bindings`` lets callers pass parameter values safely, avoiding
        string interpolation and protecting against malformed inputs.
        """
        self.ensure_loaded()
        full_query = DEFAULT_PREFIXES + sparql
        return self.graph.query(full_query, initBindings=init_bindings or {})

    def select_rows(
        self, sparql: str, init_bindings: dict | None = None
    ) -> list[dict[str, str]]:
        """Return SELECT results as a list of dicts keyed by variable name."""
        result = self.query(sparql, init_bindings=init_bindings)
        rows: list[dict[str, str]] = []
        for row in result:
            row_dict: dict[str, str] = {}
            for var in result.vars or []:
                value = row[var]  # type: ignore[index]
                row_dict[str(var)] = str(value) if value is not None else ""
            rows.append(row_dict)
        return rows

    def ask(self, sparql: str, init_bindings: dict | None = None) -> bool:
        result = self.query(sparql, init_bindings=init_bindings)
        return bool(result.askAnswer)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def insert_triples(self, triples: Iterable[tuple]) -> None:
        """Add triples to the graph at runtime."""
        self.ensure_loaded()
        for triple in triples:
            self.graph.add(triple)

    def persist_companies(self) -> None:
        """Serialize company-scoped triples back to ``companies.ttl``.

        We keep the schema and knowledge base read-only in the repo and only
        write company instance data (which changes as new companies onboard).
        """
        self.ensure_loaded()
        # Extract only triples that mention a company/facility/supplier URI.
        # For the first version we just dump the entire graph minus schema
        # + knowledge triples. A smarter split lives in Phase 7 if needed.
        temp_graph = Graph()
        temp_graph.bind("snowkap", SNOWKAP)
        schema_triples = set()
        if self.schema_path.exists():
            schema_graph = Graph()
            schema_graph.parse(self.schema_path, format="turtle")
            schema_triples = set(schema_graph)
        knowledge_triples = set()
        if self.knowledge_path.exists():
            knowledge_graph = Graph()
            knowledge_graph.parse(self.knowledge_path, format="turtle")
            knowledge_triples = set(knowledge_graph)
        known = schema_triples | knowledge_triples
        for triple in self.graph:
            if triple not in known:
                temp_graph.add(triple)
        self.companies_path.parent.mkdir(parents=True, exist_ok=True)
        temp_graph.serialize(destination=self.companies_path, format="turtle")
        logger.info(
            "Persisted %s company triples → %s",
            len(temp_graph),
            self.companies_path,
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def triple_count(self) -> int:
        self.ensure_loaded()
        return len(self.graph)

    def stats(self) -> dict[str, int]:
        """Return basic graph stats useful for validation gates."""
        self.ensure_loaded()
        counts = {"total_triples": len(self.graph)}

        def count_class(label: str, sparql: str) -> None:
            rows = self.select_rows(sparql)
            counts[label] = int(rows[0]["count"]) if rows else 0

        # ESG Topics count — includes subclass instances (EnvironmentalTopic, etc.)
        count_class(
            "esg_topics",
            """
            SELECT (COUNT(DISTINCT ?t) AS ?count) WHERE {
                ?t a ?cls .
                FILTER(?cls IN (snowkap:EnvironmentalTopic, snowkap:SocialTopic, snowkap:GovernanceTopic))
            }
            """,
        )

        for klass, label in [
            (SNOWKAP.ESGFramework, "frameworks"),
            (SNOWKAP.Industry, "industries"),
            (SNOWKAP.PerspectiveLens, "perspectives"),
            (SNOWKAP.ImpactDimension, "impact_dimensions"),
            (SNOWKAP.RiskCategory, "risk_categories"),
            (SNOWKAP.TEMPLESCategory, "temples_categories"),
            (SNOWKAP.EventType, "event_types"),
            (SNOWKAP.ComplianceDeadline, "compliance_deadlines"),
            (SNOWKAP.ClimateZone, "climate_zones"),
            (SNOWKAP.GeographicRegion, "geographic_regions"),
            (SNOWKAP.SDG, "sdgs"),
            (SNOWKAP.Stakeholder, "stakeholders"),
            (SNOWKAP.Company, "companies"),
            (SNOWKAP.Facility, "facilities"),
        ]:
            counts[label] = sum(1 for _ in self.graph.subjects(RDF.type, klass))
        return counts


# Module-level singleton — cheaper than re-parsing on every query.
_graph_singleton: OntologyGraph | None = None


def get_graph() -> OntologyGraph:
    global _graph_singleton
    if _graph_singleton is None:
        _graph_singleton = OntologyGraph().load()
    return _graph_singleton


def reset_graph() -> None:
    """Force re-load on next call to :func:`get_graph`."""
    global _graph_singleton
    _graph_singleton = None
