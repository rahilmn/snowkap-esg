"""Apache Jena Fuseki HTTP client.

Per CLAUDE.md:
- Rule #5: NEVER expose Jena SPARQL endpoint directly — always proxy through FastAPI
- Each tenant gets a named graph: urn:snowkap:tenant:{tenant_id}
- Base ontology: sustainability.ttl (OWL2)
"""

import structlog
import httpx

from backend.core.config import settings

logger = structlog.get_logger()


class JenaClient:
    """HTTP client for Apache Jena Fuseki SPARQL endpoint."""

    def __init__(self) -> None:
        self.base_url = settings.JENA_FUSEKI_URL
        self.dataset = settings.JENA_DATASET
        self.sparql_url = f"{self.base_url}/{self.dataset}/sparql"
        self.update_url = f"{self.base_url}/{self.dataset}/update"
        self.data_url = f"{self.base_url}/{self.dataset}/data"

    def _tenant_graph(self, tenant_id: str) -> str:
        """Return the named graph URI for a tenant."""
        return f"urn:snowkap:tenant:{tenant_id}"

    async def query(self, sparql: str, tenant_id: str | None = None) -> dict:
        """Execute a SPARQL SELECT query, optionally scoped to a tenant graph."""
        params = {"query": sparql}
        if tenant_id:
            params["default-graph-uri"] = self._tenant_graph(tenant_id)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self.sparql_url,
                    params=params,
                    headers={"Accept": "application/sparql-results+json"},
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            logger.error("jena_query_failed", error=str(e), query_preview=sparql[:100])
            raise

    async def construct(self, sparql: str, tenant_id: str | None = None) -> str:
        """Execute a SPARQL CONSTRUCT query, returns Turtle RDF."""
        params = {"query": sparql}
        if tenant_id:
            params["default-graph-uri"] = self._tenant_graph(tenant_id)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self.sparql_url,
                    params=params,
                    headers={"Accept": "text/turtle"},
                )
                response.raise_for_status()
                return response.text
        except httpx.HTTPError as e:
            logger.error("jena_construct_failed", error=str(e))
            raise

    async def update(self, sparql_update: str, tenant_id: str | None = None) -> bool:
        """Execute a SPARQL UPDATE (INSERT/DELETE) on a tenant graph."""
        if tenant_id:
            graph_uri = self._tenant_graph(tenant_id)
            # Wrap in GRAPH clause if not already specified
            if "GRAPH" not in sparql_update.upper():
                sparql_update = sparql_update.replace(
                    "INSERT DATA {",
                    f"INSERT DATA {{ GRAPH <{graph_uri}> {{",
                ).rstrip("}") + "} }"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.update_url,
                    data={"update": sparql_update},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                logger.info("jena_update_success", tenant_id=tenant_id)
                return True
        except httpx.HTTPError as e:
            logger.error("jena_update_failed", error=str(e))
            return False

    async def upload_ttl(self, ttl_content: str, graph_uri: str | None = None) -> bool:
        """Upload Turtle (TTL) data to Jena, optionally into a named graph."""
        params = {}
        if graph_uri:
            params["graph"] = graph_uri

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.data_url,
                    content=ttl_content,
                    params=params,
                    headers={"Content-Type": "text/turtle"},
                )
                response.raise_for_status()
                logger.info("jena_ttl_uploaded", graph=graph_uri)
                return True
        except httpx.HTTPError as e:
            logger.error("jena_ttl_upload_failed", error=str(e))
            return False

    async def delete_graph(self, graph_uri: str) -> bool:
        """Delete an entire named graph."""
        sparql = f"DROP SILENT GRAPH <{graph_uri}>"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.update_url,
                    data={"update": sparql},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                logger.info("jena_graph_deleted", graph=graph_uri)
                return True
        except httpx.HTTPError as e:
            logger.error("jena_graph_delete_failed", error=str(e))
            return False

    async def graph_exists(self, tenant_id: str) -> bool:
        """Check if a tenant's named graph exists and has data."""
        graph_uri = self._tenant_graph(tenant_id)
        sparql = f"ASK {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    self.sparql_url,
                    params={"query": sparql},
                    headers={"Accept": "application/sparql-results+json"},
                )
                response.raise_for_status()
                return response.json().get("boolean", False)
        except httpx.HTTPError:
            return False

    async def count_triples(self, tenant_id: str) -> int:
        """Count triples in a tenant's named graph."""
        graph_uri = self._tenant_graph(tenant_id)
        sparql = f"SELECT (COUNT(*) AS ?count) WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
        try:
            result = await self.query(sparql)
            bindings = result.get("results", {}).get("bindings", [])
            if bindings:
                return int(bindings[0]["count"]["value"])
            return 0
        except Exception:
            return 0

    async def insert_triples(self, triples: list[tuple[str, str, str]], tenant_id: str) -> bool:
        """Insert a batch of (subject, predicate, object) triples into a tenant graph."""
        if not triples:
            return True

        graph_uri = self._tenant_graph(tenant_id)
        triple_lines = "\n".join(f"  {s} {p} {o} ." for s, p, o in triples)
        sparql = f"INSERT DATA {{ GRAPH <{graph_uri}> {{\n{triple_lines}\n}} }}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.update_url,
                    data={"update": sparql},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                logger.info("jena_triples_inserted", count=len(triples), tenant_id=tenant_id)
                return True
        except httpx.HTTPError as e:
            logger.error("jena_insert_failed", error=str(e), count=len(triples))
            return False

    async def find_neighbors(
        self, entity_uri: str, tenant_id: str, max_depth: int = 2,
    ) -> list[dict]:
        """Find all neighbors of an entity up to max_depth hops via property paths."""
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
            logger.error("jena_neighbors_failed", entity=entity_uri, error=str(e))
            return []

    async def health_check(self) -> bool:
        """Check if Jena Fuseki is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/$/ping")
                return response.status_code == 200
        except httpx.HTTPError:
            return False


# Singleton
jena_client = JenaClient()
