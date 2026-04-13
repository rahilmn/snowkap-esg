"""In-process RDF knowledge graph backed by rdflib.

Drop-in replacement for the former Apache Jena Fuseki HTTP client.
All public method signatures and return formats are identical so the 50+
call sites across causal_engine, entity_extractor, tenant_provisioner,
ontology_service, etc. require ZERO changes.

Per CLAUDE.md:
- Rule #5: NEVER expose SPARQL endpoint directly — always proxy through FastAPI
- Each tenant gets a named graph: urn:snowkap:tenant:{tenant_id}
- Base ontology: sustainability.ttl (OWL2)
"""

import asyncio
import threading
from pathlib import Path

import structlog
from rdflib import BNode, Dataset, Graph, Literal, URIRef

logger = structlog.get_logger()


class JenaQueryError(Exception):
    """Raised when a SPARQL query fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class JenaClient:
    """In-process RDF graph store backed by rdflib Dataset.

    Uses rdflib.Dataset for named-graph support.  Every public method is
    async (wraps synchronous rdflib via asyncio.to_thread) so existing
    await-based call sites work unchanged.
    """

    def __init__(self) -> None:
        self._dataset = Dataset()
        self._lock = threading.RLock()
        self._dirty_graphs: set[str] = set()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tenant_graph(self, tenant_id: str) -> str:
        """Return the named graph URI for a tenant."""
        return f"urn:snowkap:tenant:{tenant_id}"

    def _convert_result(self, result) -> dict:
        """Convert rdflib query result → Jena-compatible SPARQL Results JSON."""
        # ASK queries return a boolean
        if isinstance(result, bool):
            return {"boolean": result}

        bindings: list[dict] = []
        var_names = [str(v) for v in result.vars] if result.vars else []
        for row in result:
            binding: dict = {}
            for i, var in enumerate(var_names):
                val = row[i]
                if val is None:
                    continue
                if isinstance(val, URIRef):
                    binding[var] = {"type": "uri", "value": str(val)}
                elif isinstance(val, Literal):
                    entry: dict = {"type": "literal", "value": str(val)}
                    if val.datatype:
                        entry["datatype"] = str(val.datatype)
                    if val.language:
                        entry["xml:lang"] = val.language
                    binding[var] = entry
                elif isinstance(val, BNode):
                    binding[var] = {"type": "bnode", "value": str(val)}
            bindings.append(binding)
        return {"results": {"bindings": bindings}}

    # ------------------------------------------------------------------
    # Public API — async wrappers around sync rdflib
    # ------------------------------------------------------------------

    async def query(self, sparql: str, tenant_id: str | None = None) -> dict:
        """Execute a SPARQL SELECT/ASK query, optionally scoped to a tenant graph."""
        return await asyncio.to_thread(self._query_sync, sparql, tenant_id)

    def _query_sync(self, sparql: str, tenant_id: str | None) -> dict:
        try:
            # If tenant_id is given and query has no explicit GRAPH clause,
            # execute against the specific named graph as default graph.
            if tenant_id and "GRAPH" not in sparql.upper().split("WHERE")[0] if "WHERE" in sparql.upper() else tenant_id and "GRAPH" not in sparql.upper():
                graph_uri = self._tenant_graph(tenant_id)
                named_graph = self._dataset.graph(URIRef(graph_uri))
                result = named_graph.query(sparql)
            else:
                result = self._dataset.query(sparql)
            return self._convert_result(result)
        except Exception as e:
            logger.error("rdflib_query_failed", error=str(e), query_preview=sparql[:100])
            raise JenaQueryError(f"Query failed: {e}") from e

    async def construct(self, sparql: str, tenant_id: str | None = None) -> str:
        """Execute a SPARQL CONSTRUCT query, returns Turtle RDF."""
        return await asyncio.to_thread(self._construct_sync, sparql, tenant_id)

    def _construct_sync(self, sparql: str, tenant_id: str | None) -> str:
        try:
            result = self._dataset.query(sparql)
            return result.serialize(format="turtle")
        except Exception as e:
            raise JenaQueryError(f"Construct failed: {e}") from e

    async def update(self, sparql_update: str, tenant_id: str | None = None) -> bool:
        """Execute a SPARQL UPDATE (INSERT/DELETE) on a tenant graph."""
        return await asyncio.to_thread(self._update_sync, sparql_update, tenant_id)

    def _update_sync(self, sparql_update: str, tenant_id: str | None) -> bool:
        if tenant_id:
            graph_uri = self._tenant_graph(tenant_id)
            # Wrap in GRAPH clause if not already specified
            if "GRAPH" not in sparql_update.upper():
                sparql_update = sparql_update.replace(
                    "INSERT DATA {",
                    f"INSERT DATA {{ GRAPH <{graph_uri}> {{",
                ).rstrip("}") + "} }"
            self._dirty_graphs.add(graph_uri)

        try:
            with self._lock:
                self._dataset.update(sparql_update)
            logger.info("rdflib_update_success", tenant_id=tenant_id)
            return True
        except Exception as e:
            logger.error("rdflib_update_failed", error=str(e))
            raise JenaQueryError(f"Update failed: {e}") from e

    async def upload_ttl(self, ttl_content: str, graph_uri: str | None = None) -> bool:
        """Upload Turtle (TTL) data, optionally into a named graph."""
        return await asyncio.to_thread(self._upload_ttl_sync, ttl_content, graph_uri)

    def _upload_ttl_sync(self, ttl_content: str, graph_uri: str | None) -> bool:
        try:
            temp = Graph()
            temp.parse(data=ttl_content, format="turtle")
            with self._lock:
                if graph_uri:
                    target = self._dataset.graph(URIRef(graph_uri))
                    self._dirty_graphs.add(graph_uri)
                else:
                    target = self._dataset.default_context
                for s, p, o in temp:
                    target.add((s, p, o))
            logger.info("rdflib_ttl_uploaded", graph=graph_uri, triples=len(temp))
            return True
        except Exception as e:
            logger.error("rdflib_ttl_upload_failed", error=str(e))
            raise JenaQueryError(f"TTL upload failed: {e}") from e

    async def delete_graph(self, graph_uri: str) -> bool:
        """Delete an entire named graph."""
        return await asyncio.to_thread(self._delete_graph_sync, graph_uri)

    def _delete_graph_sync(self, graph_uri: str) -> bool:
        try:
            with self._lock:
                self._dataset.remove_graph(URIRef(graph_uri))
                self._dirty_graphs.discard(graph_uri)
            logger.info("rdflib_graph_deleted", graph=graph_uri)
            return True
        except Exception as e:
            logger.error("rdflib_graph_delete_failed", error=str(e))
            raise JenaQueryError(f"Graph delete failed: {e}") from e

    async def graph_exists(self, tenant_id: str) -> bool:
        """Check if a tenant's named graph exists and has data."""
        graph_uri = self._tenant_graph(tenant_id)
        try:
            g = self._dataset.graph(URIRef(graph_uri))
            return len(g) > 0
        except Exception:
            return False

    async def count_triples(self, tenant_id: str) -> int:
        """Count triples in a tenant's named graph."""
        graph_uri = self._tenant_graph(tenant_id)
        try:
            g = self._dataset.graph(URIRef(graph_uri))
            return len(g)
        except Exception:
            return 0

    async def insert_triples(self, triples: list[tuple[str, str, str]], tenant_id: str) -> bool:
        """Insert a batch of (subject, predicate, object) triples into a tenant graph."""
        if not triples:
            return True

        graph_uri = self._tenant_graph(tenant_id)
        triple_lines = "\n".join(f"  {s} {p} {o} ." for s, p, o in triples)
        sparql = (
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
            f"INSERT DATA {{ GRAPH <{graph_uri}> {{\n{triple_lines}\n}} }}"
        )

        try:
            with self._lock:
                self._dataset.update(sparql)
                self._dirty_graphs.add(graph_uri)
            logger.info("rdflib_triples_inserted", count=len(triples), tenant_id=tenant_id)
            return True
        except Exception as e:
            logger.error("rdflib_insert_failed", error=str(e), count=len(triples))
            raise JenaQueryError(f"Insert failed: {e}") from e

    async def find_neighbors(
        self, entity_uri: str, tenant_id: str, max_depth: int = 2,
    ) -> list[dict]:
        """Find all neighbors of an entity up to max_depth hops."""
        graph_uri = self._tenant_graph(tenant_id)
        sparql = f"""
        SELECT ?hop1 ?rel1 ?hop2 ?rel2
        WHERE {{
            GRAPH <{graph_uri}> {{
                <{entity_uri}> ?rel1 ?hop1 .
                OPTIONAL {{ ?hop1 ?rel2 ?hop2 . }}
            }}
        }}
        LIMIT 200
        """
        try:
            result = await self.query(sparql)
            bindings = result.get("results", {}).get("bindings", [])
            neighbors = []
            for b in bindings:
                entry = {
                    "hop1": b["hop1"]["value"],
                    "rel1": b["rel1"]["value"],
                }
                if "hop2" in b:
                    entry["hop2"] = b["hop2"]["value"]
                    entry["rel2"] = b["rel2"]["value"]
                neighbors.append(entry)
            return neighbors
        except Exception as e:
            logger.error("rdflib_neighbors_failed", entity=entity_uri, error=str(e))
            return []

    async def health_check(self) -> bool:
        """Always healthy — rdflib runs in-process."""
        return True

    # ------------------------------------------------------------------
    # Persistence — serialize to / load from Supabase
    # ------------------------------------------------------------------

    async def load_all_graphs(self) -> None:
        """Load all persisted tenant graphs from the database on startup."""
        try:
            from backend.core.database import async_session_factory
            from sqlalchemy import text

            async with async_session_factory() as db:
                result = await db.execute(
                    text("SELECT tenant_id, graph_uri, serialized_data, format FROM tenant_graphs")
                )
                rows = result.fetchall()

            for row in rows:
                await asyncio.to_thread(
                    self._load_graph_sync, row.graph_uri, row.serialized_data, row.format
                )
            if rows:
                logger.info("rdflib_graphs_loaded", count=len(rows))
        except Exception as e:
            # Table might not exist yet — that's fine on first run
            logger.warning("rdflib_load_graphs_skipped", error=str(e)[:100])

    def _load_graph_sync(self, graph_uri: str, data: str, fmt: str) -> None:
        with self._lock:
            graph = self._dataset.graph(URIRef(graph_uri))
            graph.parse(data=data, format=fmt)

    async def persist_dirty_graphs(self) -> None:
        """Flush all dirty graphs to the database."""
        if not self._dirty_graphs:
            return
        dirty = list(self._dirty_graphs)
        self._dirty_graphs.clear()

        try:
            from backend.core.database import async_session_factory
            from sqlalchemy import text

            async with async_session_factory() as db:
                for graph_uri in dirty:
                    g = self._dataset.graph(URIRef(graph_uri))
                    if len(g) == 0:
                        # Graph was deleted — remove from DB
                        await db.execute(
                            text("DELETE FROM tenant_graphs WHERE graph_uri = :uri"),
                            {"uri": graph_uri},
                        )
                        continue
                    nt_data = await asyncio.to_thread(g.serialize, format="nt")
                    # Extract tenant_id from graph_uri
                    tid = graph_uri.replace("urn:snowkap:tenant:", "")
                    await db.execute(
                        text("""
                            INSERT INTO tenant_graphs (tenant_id, graph_uri, serialized_data, format, triple_count, updated_at)
                            VALUES (:tid, :uri, :data, 'nt', :cnt, NOW())
                            ON CONFLICT (tenant_id) DO UPDATE SET
                                serialized_data = EXCLUDED.serialized_data,
                                triple_count = EXCLUDED.triple_count,
                                updated_at = NOW()
                        """),
                        {"tid": tid, "uri": graph_uri, "data": nt_data, "cnt": len(g)},
                    )
                await db.commit()
            logger.info("rdflib_graphs_persisted", count=len(dirty))
        except Exception as e:
            logger.warning("rdflib_persist_failed", error=str(e)[:100])
            # Re-add to dirty set for next flush attempt
            self._dirty_graphs.update(dirty)

    async def close(self) -> None:
        """Persist dirty graphs on shutdown."""
        await self.persist_dirty_graphs()


# Singleton
jena_client = JenaClient()
