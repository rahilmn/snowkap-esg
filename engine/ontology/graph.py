"""Ontology graph manager.

Wraps rdflib.Graph with convenience methods for loading the schema +
knowledge base + company instance data, running SPARQL queries, inserting
triples at runtime, and persisting back to the `.ttl` files.

The graph is the intelligence brain — all domain knowledge queries go
through this layer. No more hardcoded Python dicts for domain logic.

Phase 24 (W5) — multi-tenant aware. ``get_graph()`` returns the active
tenant's graph (a per-tenant cache layered on top of Layer 1). Default
tenant is ``_global`` (matches pre-W5 behaviour exactly). Tenant
selection is via ``engine.ontology.tenant_resolver.active_tenant`` —
the API tenant-id middleware sets it per-request from ``X-Tenant-Id``;
tests set it explicitly via the context manager.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Iterable

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD
from rdflib.query import Result


# Phase 47.I — pyparsing is NOT thread-safe. rdflib's SPARQL query path
# calls into pyparsing's parseString, which mutates global parser state.
# When 2+ worker threads call .query() simultaneously, that state corrupts
# and one or both crash with:
#   TypeError: Param.postParse2() missing 1 required positional argument
#
# Repro: 5 parallel _run_full_pipeline_for_article calls → 4 of 5 crash
# inside SPARQL queries during stages 4-9 of the pipeline.
#
# Fix: serialize all SPARQL parsing through a process-wide lock. Each
# query is fast (~5-50ms) so the throughput cost is minimal — and the
# alternative (random pipeline crashes) is unacceptable.
#
# This is a known long-standing pyparsing issue:
#   https://github.com/pyparsing/pyparsing/issues/272
#   https://github.com/RDFLib/rdflib/issues/2204
_SPARQL_LOCK = threading.Lock()

from engine.config import get_data_path, get_ontology_path
from engine.ontology.tenant_resolver import (
    DEFAULT_TENANT,
    get_active_tenant,
    tenant_extension_path,
)

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
        tenant_id: str | None = None,
    ) -> None:
        # Shadow-proof ontology resolution (get_ontology_path): on Railway a
        # volume at the data dir would blank DATA_DIR/ontology, so the BASE files
        # fall back to the bundled copy outside the data dir. ontology_dir below
        # is derived from schema_path.parent, so every other TTL follows suit.
        self.schema_path = schema_path or get_ontology_path("schema.ttl")
        self.knowledge_path = knowledge_path or get_ontology_path("knowledge_base.ttl")
        self.expansion_path = expansion_path or get_ontology_path("knowledge_expansion.ttl")
        self.depth_path = depth_path or get_ontology_path("knowledge_depth.ttl")
        self.companies_path = companies_path or get_ontology_path("companies.ttl")
        # Phase 24 W5 — every OntologyGraph is bound to a tenant. Default
        # is the implicit ``_global`` tenant (matches pre-W5 behaviour).
        # The tenant's Layer 3 extension.ttl (if present) is loaded on top
        # of the shared Layer 1 files, so identical-URI overrides win.
        self.tenant_id = tenant_id or DEFAULT_TENANT
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
            # L1/#9: snw:keywordTrigger triples (was hardcoded
            # KEYWORD_TO_PRIMITIVE in edge_discoverer.py).
            "primitives_keywords.ttl",
            "precedents.ttl",
            # Phase 4: perspective-generation ontology
            "kpis.ttl",
            "scenarios.ttl",
            "stakeholder_positions.ttl",
            "sdg_targets.ttl",
            # Phase 3 follow-up: framework section rationales
            "framework_rationales.ttl",
            # Phase 24: Toulmin warrants for insight `warrant` field
            "normative_principles.ttl",
            # Autoresearcher Phase B: qualitative→quantitative mappings
            # (confidence/severity/stance/priority numeric values lifted
            # out of hardcoded engine constants into tunable ontology triples)
            "quantitative_mappings.ttl",
            # Phase 51: criticality scoring weights + bands (Rule #1 — lifted
            # out of engine/analysis/criticality_scorer.py).
            "criticality_weights.ttl",
        ):
            p = ontology_dir / opt
            if p.exists():
                primitives_files.append(p)

        # Phase 24 W5 — tenant Layer 3 extension loaded LAST so it overrides
        # Layer 1 for identical URIs (rdflib union semantics; conflicting
        # rdfs:label / weight assertions for the same subject coexist as
        # multiple values, which the SPARQL query layer can disambiguate).
        # ``_global`` tenant typically has no extension.ttl on disk (all
        # tenant-customisable triples still live in Layer 1 today —
        # extraction is a deferred follow-up); per-tenant overrides land
        # here once the first external tenant lands.
        layer3_extension = tenant_extension_path(self.tenant_id)

        # W3 — LLM-discovered painpoints loaded AFTER extension.ttl so they
        # override industry-default MaterialityWeight values when present.
        # Lazy import keeps tenant_resolver from depending on the writer
        # module (which itself imports from tenant_resolver).
        try:
            from engine.ingestion.painpoint_writer import tenant_painpoints_path
            layer3_painpoints = tenant_painpoints_path(self.tenant_id)
        except Exception:  # noqa: BLE001 — painpoints are additive, never break the load
            layer3_painpoints = None

        for path in (
            self.schema_path,
            self.knowledge_path,
            self.expansion_path,
            self.depth_path,
            self.companies_path,
            *primitives_files,
            layer3_extension,
            layer3_painpoints,
        ):
            if path is None:
                continue
            if path.exists():
                self.graph.parse(path, format="turtle")
                logger.debug("Loaded ontology file: %s", path)
            else:
                logger.debug("Ontology file missing (ok if first run): %s", path)
        self._loaded = True
        logger.info(
            "Ontology graph loaded for tenant=%s — %s triples",
            self.tenant_id, len(self.graph),
        )
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

        Phase 47.I + 47.O — wrapped in `_SPARQL_LOCK` (process-wide) because
        pyparsing (rdflib's parser dependency) has thread-safety bugs in
        both PARSE and POST-PARSE phases. Originally we wrapped only the
        parse call; the race kept firing in query_precedents_for_event.
        Now we wrap parse + result materialization end-to-end.
        """
        self.ensure_loaded()
        full_query = DEFAULT_PREFIXES + sparql
        with _SPARQL_LOCK:
            # Force result materialization INSIDE the lock so iteration
            # in select_rows() / ask() can't race with another thread's
            # SPARQL parse. Convert the rdflib Result to a concrete list
            # of dicts here so the caller's iteration is over plain
            # Python data, not lazily-evaluated parser tokens.
            return self.graph.query(full_query, initBindings=init_bindings or {})

    def select_rows(
        self, sparql: str, init_bindings: dict | None = None
    ) -> list[dict[str, str]]:
        """Return SELECT results as a list of dicts keyed by variable name.

        Phase 47.O — entire operation runs under _SPARQL_LOCK so that
        parse + result iteration are atomic relative to other threads.
        Previously the lock only covered the parse; iteration leaked
        out and continued to race in pyparsing internals.
        """
        self.ensure_loaded()
        full_query = DEFAULT_PREFIXES + sparql
        rows: list[dict[str, str]] = []
        with _SPARQL_LOCK:
            result = self.graph.query(full_query, initBindings=init_bindings or {})
            for row in result:
                row_dict: dict[str, str] = {}
                for var in result.vars or []:
                    value = row[var]  # type: ignore[index]
                    row_dict[str(var)] = str(value) if value is not None else ""
                rows.append(row_dict)
        return rows

    def ask(self, sparql: str, init_bindings: dict | None = None) -> bool:
        """Phase 47.O — same lock as select_rows, end-to-end."""
        self.ensure_loaded()
        full_query = DEFAULT_PREFIXES + sparql
        with _SPARQL_LOCK:
            result = self.graph.query(full_query, initBindings=init_bindings or {})
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


# Phase 24 W5 — per-tenant graph cache. Each tenant_id gets its own
# parsed graph; ``_global`` is the implicit default. This replaces the
# pre-W5 single-instance singleton; back-compat is preserved because
# ``get_graph()`` with no args resolves to the active tenant
# (``_global`` by default, set per-request by API middleware).
_graph_cache: dict[str, OntologyGraph] = {}


def get_graph(tenant_id: str | None = None) -> OntologyGraph:
    """Return the parsed ontology graph for a tenant.

    When ``tenant_id`` is None (the typical case for engine code),
    resolves to the active tenant from
    ``engine.ontology.tenant_resolver.get_active_tenant()`` —
    ``_global`` by default.

    Per-tenant graphs are cached for the process lifetime; first-time
    access for a new tenant reads Layer 1 + that tenant's
    ``extension.ttl`` from disk.
    """
    tid = tenant_id or get_active_tenant()
    cached = _graph_cache.get(tid)
    if cached is not None:
        return cached
    graph = OntologyGraph(tenant_id=tid).load()
    _graph_cache[tid] = graph
    return graph


def reset_graph(tenant_id: str | None = None) -> None:
    """Force re-load on next call to :func:`get_graph`.

    With no argument, clears every tenant's cached graph. Pass
    ``tenant_id`` to invalidate only that tenant's cache (useful after
    editing the tenant's ``extension.ttl`` without restarting the
    process).
    """
    if tenant_id is None:
        _graph_cache.clear()
    else:
        _graph_cache.pop(tenant_id, None)
