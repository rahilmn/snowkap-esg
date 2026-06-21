"""Phase 32 — SASB materiality loader.

Provides ``query_sasb_materiality(sasb_sector, topic) → (weight, kind)``
where ``kind`` ∈ {"direct", "asset_based"}. Reads from
``data/ontology/sasb_materiality.ttl`` (~15 sectors today, full 76-sector
SASB taxonomy as TTL expansion is a follow-up).

The loader is **separate from the main ontology graph** so:
  * Adding a sector doesn't require re-loading the 8,000-triple primary graph
  * SASB lookups can be cached independently
  * The loader fails-soft to neutral 0.5 when the sector isn't mapped
    (DECISION 3.2 — `warning="sasb_unmapped"` returned alongside)

Used by:
  * ``engine.ontology.intelligence.query_materiality_weight`` (Phase 3
    extension — sasb_sector kwarg)
  * ``engine.analysis.unified_analysis.build_unified_analysis`` (sasb
    warning flag on the why_it_matters bullet)
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_TTL_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "ontology" / "sasb_materiality.ttl"


def _label_to_uri(label: str) -> str:
    """Map a human-readable label (e.g. "Commercial Banks") to its
    snowkap:sasb_* URI.

    The TTL uses snake_case suffixes; we lowercase + strip + replace
    spaces / & / dashes accordingly. Returns "" when no match heuristic
    matches.
    """
    if not label:
        return ""
    s = label.strip().lower()
    s = (s.replace("&", "and")
         .replace("—", " ").replace("–", " ").replace("-", " ")
         .replace("/", " "))
    parts = [p for p in s.split() if p]
    if not parts:
        return ""
    return "sasb_" + "_".join(parts)


@lru_cache(maxsize=1)
def _load_graph():
    """Load the SASB TTL once per process.

    Returns ``None`` when rdflib isn't installed or the file is missing —
    callers fall through to the neutral-weight path.
    """
    try:
        from rdflib import Graph  # type: ignore
    except ImportError:
        logger.warning("sasb_loader: rdflib unavailable; SASB queries disabled.")
        return None
    if not _TTL_PATH.exists():
        logger.warning("sasb_loader: TTL not found at %s", _TTL_PATH)
        return None
    g = Graph()
    try:
        g.parse(str(_TTL_PATH), format="turtle")
    except Exception as exc:  # noqa: BLE001
        logger.warning("sasb_loader: TTL parse failed (%s)", exc)
        return None
    return g


# ---------------------------------------------------------------------------
# Public query
# ---------------------------------------------------------------------------


def query_sasb_materiality(
    sasb_sector: str | None, topic: str | None,
) -> tuple[Optional[float], Optional[str]]:
    """Return ``(weight, kind)`` for a given SASB sector × topic.

    Args:
        sasb_sector: free-form label (e.g. "Commercial Banks") OR snake_case
            URI suffix (e.g. "sasb_commercial_banks"). The loader normalises
            free-form labels into URI suffixes.
        topic: snake_case topic id (e.g. "scope_3_financed",
            "scope_1_emissions"). Tolerant of legacy formats — strips
            "topic_" prefix if present.

    Returns:
        ``(weight, kind)`` when a match exists; ``(None, None)`` otherwise.
        Callers degrade to the neutral 0.5 fallback + `warning="sasb_unmapped"`
        when None.
    """
    if not sasb_sector or not topic:
        return None, None
    g = _load_graph()
    if g is None:
        return None, None

    # Normalise the sector to its rdfs:label (the most stable identifier).
    # URI-style "sasb_*" inputs are mapped back to label-style via the
    # TTL's rdfs:label lookup so callers can pass either form.
    sector_label = sasb_sector.strip()
    sector_uri_suffix = sasb_sector if sasb_sector.startswith("sasb_") else None

    # Normalise the topic
    topic_id = topic.lower().strip()
    if topic_id.startswith("topic_"):
        topic_id = topic_id[len("topic_"):]
    topic_id = topic_id.replace(" ", "_").replace("-", "_")

    # Match by rdfs:label (primary) OR URI suffix (secondary).
    sparql = """
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX snowkap: <http://snowkap.com/ontology/esg#>
    SELECT ?weight ?kind WHERE {
        ?sector a snowkap:SASBSector .
        ?sector ?edge_predicate ?link .
        ?link snowkap:topic ?topic .
        ?link snowkap:materialityWeight ?weight .
        ?link snowkap:materialityKind ?kind .
        OPTIONAL { ?sector rdfs:label ?label }
        FILTER(
            (BOUND(?label) && LCASE(STR(?label)) = LCASE(?label_in))
            || (STRENDS(STR(?sector), ?uri_suffix))
        )
        FILTER(STRENDS(STR(?topic), ?topic_id))
    }
    """
    try:
        from rdflib import Literal  # type: ignore
        results = g.query(
            sparql,
            initBindings={
                "label_in": Literal(sector_label),
                "uri_suffix": Literal(sector_uri_suffix or "__NEVER_MATCH__"),
                "topic_id": Literal(topic_id),
            },
        )
        for row in results:
            weight = float(row.weight)
            kind = str(row.kind)
            return weight, kind
    except Exception as exc:  # noqa: BLE001
        # Phase 53 (A4) — log loudly with traceback. A silent 0.5 default here
        # hides a prod pyparsing/rdflib SPARQL-parser break (the
        # "Param.postParse2() missing tokenList" error) — pyparsing is pinned in
        # requirements.txt so the deployed image cannot regress the parser.
        logger.warning(
            "sasb_loader: SPARQL failed for %s / %s (%s)",
            sasb_sector, topic, exc, exc_info=True,
        )
    return None, None


@lru_cache(maxsize=64)
def query_material_topics_for_sector(
    sasb_sector: str | None,
) -> tuple[tuple[str, float, str], ...]:
    """Phase 53 (A2) — the per-industry MATERIAL ESG TOPIC SET for a SASB sector.

    Returns ``((topic_suffix, weight, kind), ...)`` ordered by weight DESC, where
    ``topic_suffix`` is the snake_case id of ``snowkap:topic_<suffix>`` minus the
    ``topic_`` prefix (e.g. ``"climate"``, ``"data_privacy"``) and ``kind`` ∈
    {"direct","asset_based"}. Empty tuple when the sector isn't mapped.

    This is the building block the industry/thematic news lane needs: the top
    topics become the ESG search terms for the company's INDUSTRY, and the weight
    scores sector/thematic news as material to the company — both keyed by SASB
    SECTOR, so it works for any onboarded company without per-company seeding.
    """
    if not sasb_sector:
        return ()
    g = _load_graph()
    if g is None:
        return ()
    sector_uri_suffix = sasb_sector if sasb_sector.startswith("sasb_") else "__NEVER_MATCH__"
    sparql = """
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX snowkap: <http://snowkap.com/ontology/esg#>
    SELECT ?topic ?weight ?kind WHERE {
        ?sector a snowkap:SASBSector .
        ?sector ?edge ?link .
        ?link snowkap:topic ?topic .
        ?link snowkap:materialityWeight ?weight .
        ?link snowkap:materialityKind ?kind .
        OPTIONAL { ?sector rdfs:label ?label }
        FILTER(
            (BOUND(?label) && LCASE(STR(?label)) = LCASE(?label_in))
            || (STRENDS(STR(?sector), ?uri_suffix))
        )
    }
    ORDER BY DESC(?weight)
    """
    best: dict[str, tuple[str, float, str]] = {}
    try:
        from rdflib import Literal  # type: ignore
        for row in g.query(
            sparql,
            initBindings={
                "label_in": Literal(sasb_sector.strip()),
                "uri_suffix": Literal(sector_uri_suffix),
            },
        ):
            uri = str(row.topic)
            suffix = uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
            if suffix.startswith("topic_"):
                suffix = suffix[len("topic_"):]
            w = float(row.weight)
            kind = str(row.kind)
            # keep the highest-weight occurrence per topic (a topic can be both
            # direct + asset_based across the same sector — take the stronger).
            if suffix not in best or w > best[suffix][1]:
                best[suffix] = (suffix, w, kind)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sasb_loader: material-topics SPARQL failed for %s (%s)",
            sasb_sector, exc, exc_info=True,
        )
        return ()
    return tuple(sorted(best.values(), key=lambda x: -x[1]))


def is_sector_mapped(sasb_sector: str | None) -> bool:
    """Cheap check: does the TTL contain this sector at all?

    Used by ``unified_analysis.build_why_it_matters`` to set
    ``warning="sasb_unmapped"`` when a company's industry doesn't map.
    """
    if not sasb_sector:
        return False
    g = _load_graph()
    if g is None:
        return False
    sector_uri_suffix = sasb_sector if sasb_sector.startswith("sasb_") else "__NEVER_MATCH__"
    sparql = """
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX snowkap: <http://snowkap.com/ontology/esg#>
    ASK {
        ?s a snowkap:SASBSector .
        OPTIONAL { ?s rdfs:label ?label }
        FILTER(
            (BOUND(?label) && LCASE(STR(?label)) = LCASE(?label_in))
            || (STRENDS(STR(?s), ?uri_suffix))
        )
    }
    """
    try:
        from rdflib import Literal  # type: ignore
        return bool(g.query(
            sparql,
            initBindings={
                "label_in": Literal(sasb_sector.strip()),
                "uri_suffix": Literal(sector_uri_suffix),
            },
        ).askAnswer)
    except Exception:  # noqa: BLE001
        return False


__all__ = ["query_sasb_materiality", "query_material_topics_for_sector", "is_sector_mapped"]
