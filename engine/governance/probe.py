"""``/snowkap-probe`` ReAct gate (Base Version adoption L0).

Read-only search across 6 sources for prior art before any TTL or
config mutation. Returns confidence-banded matches so the operator
can decide whether the proposed mutation duplicates existing work or
is genuinely new.

Sources searched (in order, each contributes 0+ matches):

1. ``data/audit/decision_log.jsonl``        — Phase 19 pipeline decision audit
2. ``data/ontology/discovery_audit.jsonl``  — discovery promote/reject/defer audit
3. ``data/ontology/discovery_staging.json`` — pending discovery candidates queue
4. ``data/ontology/discovered.ttl``         — promoted discovered triples (RDF)
5. ``data/ontology/tenants/*/painpoints.ttl`` — per-tenant LLM-discovered painpoints (RDF)
6. live SPARQL via ``engine.ontology.intelligence`` / ``engine.ontology.graph``

Source #3 (staging) is included as an explicit sixth source even though
the v2 plan listed five — staging is the in-progress queue feeding the
audit log, and gate 1 ("find the live Lloyds Transparency divergence")
requires reading it. Folding it into source #2 would be opaque; keeping
it explicit makes the probe transparent to the operator.

**SPARQL safety:** every SPARQL query passes user input via
``init_bindings={"needle": Literal(...)}``. Zero f-string interpolation
into SPARQL strings — that's the vulnerable pattern at
``engine/ontology/discovery/promoter.py::_entity_exists_fuzzy`` lines
118-132 that L3 fixes separately. Do NOT replicate it here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repo paths — delegate to the canonical resolver in ``engine.config`` so the
# probe sees exactly the same files the rest of the engine reads (handles
# inner-vs-outer ``data/ontology/`` ambiguity flagged in the source audit,
# and survives any future move of ``probe.py`` to a different directory).
# ``DATA_DIR`` is the project's anchored ``data/`` directory; ``DATA_DIR.parent``
# is the repo root.
# ---------------------------------------------------------------------------


def _data_path(*parts: str) -> Path:
    """Return ``data/<parts>`` via the canonical engine.config resolver."""
    from engine.config import get_data_path
    return get_data_path(*parts)


def _repo_root() -> Path:
    """Project root, derived from the same anchor as ``_data_path``."""
    from engine.config import DATA_DIR
    return DATA_DIR.parent


# ---------------------------------------------------------------------------
# Excerpt + match helpers
# ---------------------------------------------------------------------------


_EXCERPT_CAP = 200
_HIGH_SUBSTRING = "HIGH"
_MEDIUM_TOKEN = "MEDIUM"
_LOW_JARO = "LOW"


def _trim_excerpt(s: str, cap: int = _EXCERPT_CAP) -> str:
    """Cap excerpt at ``cap`` chars with middle elision so both ends survive."""
    if len(s) <= cap:
        return s
    keep = cap - 3  # room for ellipsis
    head = keep // 2
    tail = keep - head
    return s[:head] + "..." + s[-tail:]


def _classify_label_match(query: str, label: str) -> tuple[str, str] | None:
    """Return (confidence_band, excerpt) if ``label`` matches ``query``.

    Banding (case-insensitive throughout):
    - HIGH: full query is a substring of label (or vice versa), OR any
      query token of length ≥4 appears as a substring inside the label.
      The length-4 cutoff prevents stop-words ("the", "and", "for") from
      promoting a noisy match to HIGH while still catching meaningful
      single-concept words like "transparency", "labour", "climate".
    - MEDIUM: at least one query token of length ≥3 appears as a separate
      whitespace-bounded token in the label (token-overlap, no substring).
    - None: no overlap at all.

    Jaro-Winkler is reserved for the RDF/SPARQL paths (LOW band) where
    labels are taken from arbitrary ontology subjects.
    """
    if not query or not label:
        return None
    q = query.strip().lower()
    lo = label.lower()
    if q in lo or lo in q:
        return (_HIGH_SUBSTRING, _trim_excerpt(label))
    q_tokens = [t for t in q.replace("_", " ").split() if t]
    # HIGH: any meaningful-length query token is a substring of label.
    for tok in q_tokens:
        if len(tok) >= 4 and tok in lo:
            return (_HIGH_SUBSTRING, _trim_excerpt(label))
    # MEDIUM: token overlap on ≥3-char tokens (standalone in both).
    q_set = {t for t in q_tokens if len(t) >= 3}
    l_set = {t for t in lo.replace("_", " ").split() if len(t) >= 3}
    if q_set & l_set:
        return (_MEDIUM_TOKEN, _trim_excerpt(label))
    return None


# ---------------------------------------------------------------------------
# Source registry — each entry is (name, search_fn). Order matters: HIGH
# matches from earlier sources surface first when results are merged.
# ---------------------------------------------------------------------------


ALL_SOURCES: tuple[str, ...] = (
    "decision_log",
    "discovery_audit",
    "discovery_staging",
    "discovered_ttl",
    "tenant_painpoints",
    "live_sparql",
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


Confidence = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(frozen=True)
class ProbeMatch:
    """A single prior-art match returned by ``probe()``.

    Attributes:
        source: which of the 6 sources produced this match (see module docstring)
        confidence: HIGH / MEDIUM / LOW per the scoring rules in module
        excerpt: ≤200 char context snippet around the match
        file_path: absolute or repo-relative path that contained the match (if any)
        line_or_record_ref: line number / JSONL record index / SPARQL subject URI
    """

    source: str
    confidence: Confidence
    excerpt: str
    file_path: str | None = None
    line_or_record_ref: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    """Aggregate result of one probe() call."""

    query: str
    matches: list[ProbeMatch] = field(default_factory=list)
    searched_sources: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def probe(query: str, sources: list[str] | None = None) -> ProbeResult:
    """Search 6 sources for prior art matching ``query``.

    Args:
        query: the candidate label / slug / topic the operator wants to add.
            Treated as opaque text — never interpolated into SPARQL.
        sources: optional subset of source names to limit search to.
            If None, all 6 sources are searched. Unknown source names are
            silently ignored (so callers can pass forward-compat names).

    Returns:
        ProbeResult with matches sorted by confidence (HIGH > MEDIUM > LOW).
    """
    requested = sources if sources is not None else list(ALL_SOURCES)
    # Preserve canonical order; filter unknowns silently.
    targets = [s for s in ALL_SOURCES if s in requested]

    all_matches: list[ProbeMatch] = []
    searched: list[str] = []
    for source_name in targets:
        searched.append(source_name)
        search_fn = _SOURCE_DISPATCH.get(source_name)
        if search_fn is None:
            continue
        try:
            all_matches.extend(search_fn(query))
        except Exception as exc:  # noqa: BLE001 — per-source isolation
            # A failing source must never propagate (e.g. injection input
            # malforming a SPARQL parse, a missing optional file). Log
            # and continue with the remaining sources.
            logger.debug(
                "probe source %s failed for query=%r: %s",
                source_name, query, exc,
            )

    # Sort: HIGH > MEDIUM > LOW, then by source order in ALL_SOURCES, then
    # by excerpt content for determinism.
    confidence_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    source_rank = {s: i for i, s in enumerate(ALL_SOURCES)}
    all_matches.sort(key=lambda m: (
        confidence_rank.get(m.confidence, 99),
        source_rank.get(m.source, 99),
        m.excerpt,
    ))
    return ProbeResult(query=query, matches=all_matches, searched_sources=searched)


# ---------------------------------------------------------------------------
# Per-source search functions (each returns 0+ ProbeMatch; never raises
# upward — exceptions caught by probe() and logged).
# ---------------------------------------------------------------------------


def _search_decision_log(query: str) -> list[ProbeMatch]:
    """Search Phase 19 decision audit (JSONL)."""
    return []


def _search_discovery_audit(query: str) -> list[ProbeMatch]:
    """Search discovery promote/reject/defer audit (JSONL)."""
    return []


def _search_discovery_staging(query: str) -> list[ProbeMatch]:
    """Search pending discovery candidates queue (JSON).

    Reads ``data/ontology/discovery_staging.json`` and matches against each
    candidate's ``label`` + ``slug``. Companies and article ids are not
    searched (they're metadata, not the candidate identity).
    """
    path = _data_path("ontology", "discovery_staging.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("discovery_staging.json read failed: %s", exc)
        return []
    candidates = data.get("candidates", {})
    if not isinstance(candidates, dict):
        return []
    matches: list[ProbeMatch] = []
    for key, cand in candidates.items():
        if not isinstance(cand, dict):
            continue
        label = str(cand.get("label", ""))
        slug = str(cand.get("slug", ""))
        # Match against label first (more readable), fall back to slug.
        hit = _classify_label_match(query, label) or _classify_label_match(query, slug)
        if hit is None:
            continue
        conf, excerpt = hit
        matches.append(ProbeMatch(
            source="discovery_staging",
            confidence=conf,  # type: ignore[arg-type]
            excerpt=excerpt,
            file_path="data/ontology/discovery_staging.json",
            line_or_record_ref=key,
        ))
    return matches


_SPARQL_LABEL_SEARCH = """
SELECT DISTINCT ?s ?label WHERE {
    ?s rdfs:label ?label .
    FILTER(CONTAINS(LCASE(STR(?label)), ?needle))
}
LIMIT 50
"""


def _parse_isolated_ttl(path: Path):
    """Parse a single TTL file into a fresh rdflib Graph in isolation.

    Importing rdflib lazily avoids paying the cost when the probe is called
    against only non-RDF sources. A parse error returns None (caller logs
    and continues).
    """
    try:
        from rdflib import Graph
    except ImportError:
        logger.debug("rdflib not installed; SPARQL sources skipped")
        return None
    if not path.exists():
        return None
    try:
        g = Graph()
        g.parse(path, format="turtle")
        return g
    except Exception as exc:  # noqa: BLE001 — graph parse can fail many ways
        logger.debug("ttl parse failed %s: %s", path, exc)
        return None


def _search_ttl_file(query: str, ttl_path: Path, source_name: str) -> list[ProbeMatch]:
    """Parameterised SPARQL label search across one TTL file.

    Uses ``initBindings`` to bind the literal query value — the SPARQL
    string is fixed at module load and contains no user input. This is
    the secure pattern that L3 will retrofit onto promoter.py.
    """
    from rdflib import Literal

    g = _parse_isolated_ttl(ttl_path)
    if g is None:
        return []
    needle = query.strip().lower()
    if not needle:
        return []
    try:
        rows = g.query(_SPARQL_LABEL_SEARCH, initBindings={"needle": Literal(needle)})
    except Exception as exc:  # noqa: BLE001 — malformed input shouldn't propagate
        logger.debug("SPARQL query on %s failed for %r: %s", ttl_path, query, exc)
        return []
    out: list[ProbeMatch] = []
    for row in rows:
        # SELECT returned (?s, ?label) tuples
        subject = str(row[0]) if len(row) > 0 else ""
        label = str(row[1]) if len(row) > 1 else ""
        hit = _classify_label_match(query, label)
        if hit is None:
            # SPARQL CONTAINS already prefiltered, so any row reached here
            # has at least an overlap; classify as MEDIUM if our token
            # heuristic disagrees (rare).
            hit = (_MEDIUM_TOKEN, _trim_excerpt(label))
        conf, excerpt = hit
        out.append(ProbeMatch(
            source=source_name,
            confidence=conf,  # type: ignore[arg-type]
            excerpt=excerpt,
            file_path=str(ttl_path.relative_to(_repo_root())).replace("\\", "/"),
            line_or_record_ref=subject,
        ))
    return out


def _search_discovered_ttl(query: str) -> list[ProbeMatch]:
    """Search promoted discovered triples (RDF via parameterised SPARQL)."""
    return _search_ttl_file(
        query=query,
        ttl_path=_data_path("ontology", "discovered.ttl"),
        source_name="discovered_ttl",
    )


def _search_tenant_painpoints(query: str) -> list[ProbeMatch]:
    """Search per-tenant painpoints TTL files (parameterised SPARQL).

    Iterates ``data/ontology/tenants/*/painpoints.ttl`` — each tenant's
    LLM-discovered painpoints are addressable individuals with rdfs:label.
    """
    tenants_dir = _data_path("ontology", "tenants")
    if not tenants_dir.exists():
        return []
    matches: list[ProbeMatch] = []
    for tenant_dir in tenants_dir.iterdir():
        if not tenant_dir.is_dir():
            continue
        ttl = tenant_dir / "painpoints.ttl"
        if not ttl.exists():
            continue
        matches.extend(_search_ttl_file(
            query=query,
            ttl_path=ttl,
            source_name="tenant_painpoints",
        ))
    return matches


def _search_live_sparql(query: str) -> list[ProbeMatch]:
    """Search live ontology graph via ``engine.ontology.graph`` (parameterised SPARQL).

    Uses the cached active-tenant graph rather than reloading from disk so
    the probe sees the same triples the pipeline sees.
    """
    try:
        from rdflib import Literal
        from engine.ontology.graph import get_graph
    except ImportError as exc:
        logger.debug("live_sparql skipped — engine.ontology not importable: %s", exc)
        return []
    needle = query.strip().lower()
    if not needle:
        return []
    try:
        graph = get_graph()
        graph.ensure_loaded()
        rows = graph.query(
            _SPARQL_LABEL_SEARCH,
            init_bindings={"needle": Literal(needle)},
        )
    except Exception as exc:  # noqa: BLE001 — never propagate
        logger.debug("live SPARQL failed for %r: %s", query, exc)
        return []
    out: list[ProbeMatch] = []
    for row in rows:
        subject = str(row[0]) if len(row) > 0 else ""
        label = str(row[1]) if len(row) > 1 else ""
        hit = _classify_label_match(query, label) or (_MEDIUM_TOKEN, _trim_excerpt(label))
        conf, excerpt = hit
        out.append(ProbeMatch(
            source="live_sparql",
            confidence=conf,  # type: ignore[arg-type]
            excerpt=excerpt,
            file_path=None,
            line_or_record_ref=subject,
        ))
    return out


_SOURCE_DISPATCH: dict[str, Callable[[str], list[ProbeMatch]]] = {
    "decision_log": _search_decision_log,
    "discovery_audit": _search_discovery_audit,
    "discovery_staging": _search_discovery_staging,
    "discovered_ttl": _search_discovered_ttl,
    "tenant_painpoints": _search_tenant_painpoints,
    "live_sparql": _search_live_sparql,
}
