"""Apache Jena Fuseki HTTP client.

Per CLAUDE.md:
- Rule #5: NEVER expose Jena SPARQL endpoint directly — always proxy through FastAPI
- Each tenant gets a named graph: urn:snowkap:tenant:{tenant_id}
- Base ontology: sustainability.ttl (OWL2)

Stage 2.1: Persistent AsyncClient with connection pool (max 20).
Retry 2x on transient errors (timeout, 503). Raise JenaQueryError consistently.
"""

import asyncio

import httpx
import structlog

from backend.core.config import settings

logger = structlog.get_logger()


class JenaQueryError(Exception):
    """Raised when a Jena SPARQL query fails after retries."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# Transient HTTP status codes worth retrying
_TRANSIENT_STATUS = {503, 502, 504, 429}

# Transient exception types worth retrying
_TRANSIENT_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError, httpx.PoolTimeout)


class JenaClient:
    """HTTP client for Apache Jena Fuseki SPARQL endpoint.

    Uses a persistent connection pool instead of creating a new client per request.
    Retries transient failures up to 2 times with exponential backoff.
    """

    def __init__(self) -> None:
        self.base_url = settings.JENA_FUSEKI_URL
        self.dataset = settings.JENA_DATASET
        self.sparql_url = f"{self.base_url}/{self.dataset}/sparql"
        self.update_url = f"{self.base_url}/{self.dataset}/update"
        self.data_url = f"{self.base_url}/{self.dataset}/data"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the persistent async HTTP client with connection pooling."""
        if self._client is None or self._client.is_closed:
            # Add basic auth for Jena write operations if configured
            auth = None
            if settings.JENA_ADMIN_USER and settings.JENA_ADMIN_PASSWORD:
                auth = httpx.BasicAuth(settings.JENA_ADMIN_USER, settings.JENA_ADMIN_PASSWORD)

            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=2.0),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
                auth=auth,
            )
        return self._client

    async def close(self) -> None:
        """Close the persistent client. Call on application shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = 2,
        backoff_base: float = 1.0,
        **kwargs,
    ) -> httpx.Response:
        """Execute an HTTP request with retry on transient failures.

        Retries up to max_retries times with exponential backoff.
        Raises JenaQueryError on final failure.
        """
        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                if method == "GET":
                    response = await client.get(url, **kwargs)
                else:
                    response = await client.post(url, **kwargs)

                if response.status_code in _TRANSIENT_STATUS and attempt < max_retries:
                    wait = backoff_base * (2 ** attempt)
                    logger.warning(
                        "jena_transient_retry",
                        status=response.status_code,
                        attempt=attempt + 1,
                        wait_s=wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                response.raise_for_status()
                return response

            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                # Server not running — fail immediately, don't waste time retrying
                raise JenaQueryError(
                    f"Jena not reachable at {self.base_url}: {e}",
                ) from e

            except _TRANSIENT_EXCEPTIONS as e:
                last_error = e
                if attempt < max_retries:
                    wait = backoff_base * (2 ** attempt)
                    logger.warning(
                        "jena_transient_retry",
                        error=type(e).__name__,
                        attempt=attempt + 1,
                        wait_s=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                break

            except httpx.HTTPStatusError as e:
                # Non-transient HTTP error — don't retry
                raise JenaQueryError(
                    f"Jena HTTP error: {e.response.status_code} — {e.response.text[:200]}",
                    status_code=e.response.status_code,
                ) from e

        raise JenaQueryError(
            f"Jena request failed after {max_retries + 1} attempts: {last_error}",
        )

    def _tenant_graph(self, tenant_id: str) -> str:
        """Return the named graph URI for a tenant."""
        return f"urn:snowkap:tenant:{tenant_id}"

    async def query(self, sparql: str, tenant_id: str | None = None) -> dict:
        """Execute a SPARQL SELECT query, optionally scoped to a tenant graph."""
        params = {"query": sparql}
        if tenant_id:
            params["default-graph-uri"] = self._tenant_graph(tenant_id)

        try:
            response = await self._request_with_retry(
                "GET",
                self.sparql_url,
                params=params,
                headers={"Accept": "application/sparql-results+json"},
            )
            return response.json()
        except JenaQueryError:
            raise
        except Exception as e:
            logger.error("jena_query_failed", error=str(e), query_preview=sparql[:100])
            raise JenaQueryError(f"Query failed: {e}") from e

    async def construct(self, sparql: str, tenant_id: str | None = None) -> str:
        """Execute a SPARQL CONSTRUCT query, returns Turtle RDF."""
        params = {"query": sparql}
        if tenant_id:
            params["default-graph-uri"] = self._tenant_graph(tenant_id)

        try:
            response = await self._request_with_retry(
                "GET",
                self.sparql_url,
                params=params,
                headers={"Accept": "text/turtle"},
            )
            return response.text
        except JenaQueryError:
            raise
        except Exception as e:
            logger.error("jena_construct_failed", error=str(e))
            raise JenaQueryError(f"Construct failed: {e}") from e

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
            await self._request_with_retry(
                "POST",
                self.update_url,
                data={"update": sparql_update},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            logger.info("jena_update_success", tenant_id=tenant_id)
            return True
        except JenaQueryError as e:
            logger.error("jena_update_failed", error=str(e))
            raise

    async def upload_ttl(self, ttl_content: str, graph_uri: str | None = None) -> bool:
        """Upload Turtle (TTL) data to Jena, optionally into a named graph."""
        params = {}
        if graph_uri:
            params["graph"] = graph_uri

        try:
            await self._request_with_retry(
                "POST",
                self.data_url,
                content=ttl_content,
                params=params,
                headers={"Content-Type": "text/turtle"},
            )
            logger.info("jena_ttl_uploaded", graph=graph_uri)
            return True
        except JenaQueryError as e:
            logger.error("jena_ttl_upload_failed", error=str(e))
            raise

    async def delete_graph(self, graph_uri: str) -> bool:
        """Delete an entire named graph."""
        sparql = f"DROP SILENT GRAPH <{graph_uri}>"
        try:
            await self._request_with_retry(
                "POST",
                self.update_url,
                data={"update": sparql},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            logger.info("jena_graph_deleted", graph=graph_uri)
            return True
        except JenaQueryError as e:
            logger.error("jena_graph_delete_failed", error=str(e))
            raise

    async def graph_exists(self, tenant_id: str) -> bool:
        """Check if a tenant's named graph exists and has data."""
        graph_uri = self._tenant_graph(tenant_id)
        sparql = f"ASK {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
        try:
            response = await self._request_with_retry(
                "GET",
                self.sparql_url,
                params={"query": sparql},
                headers={"Accept": "application/sparql-results+json"},
            )
            return response.json().get("boolean", False)
        except (JenaQueryError, Exception):
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
        sparql = (
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            "PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
            f"INSERT DATA {{ GRAPH <{graph_uri}> {{\n{triple_lines}\n}} }}"
        )

        try:
            await self._request_with_retry(
                "POST",
                self.update_url,
                data={"update": sparql},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            logger.info("jena_triples_inserted", count=len(triples), tenant_id=tenant_id)
            return True
        except JenaQueryError as e:
            logger.error("jena_insert_failed", error=str(e), count=len(triples))
            raise

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
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/$/ping", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False


# Singleton
jena_client = JenaClient()
