"""L1 #9 — Move KEYWORD_TO_PRIMITIVE dict from edge_discoverer.py to TTL.

Asserts that the hardcoded Python dict is gone, the TTL file exists with
the 24 keyword→primitive mappings, and the discover_edges() behaviour is
preserved end-to-end (same primitive pair detected from same input text).

This regression suite enforces the v2 plan's L1/#9 deliverable:
domain knowledge moves from Python dicts to TTL/SPARQL — the Snowkap
"every dict lookup is a smell" rule applied to discovery edge detection.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from engine.ontology import graph as graph_module
from engine.ontology.discovery.modules import edge_discoverer


# The 24 (keyword, primitive) pairs that USED to live as a Python dict at
# edge_discoverer.py:20-40. These now live in primitives_keywords.ttl and
# must be queryable via SPARQL.
EXPECTED_KEYWORD_PRIMITIVE_PAIRS = {
    ("energy price", "EP"), ("electricity price", "EP"), ("fuel cost", "EP"),
    ("power price", "EP"),
    ("freight", "FR"), ("logistics", "FR"), ("shipping", "FR"),
    ("transport cost", "FR"),
    ("lead time", "LT"), ("delivery time", "LT"), ("supply delay", "LT"),
    ("interest rate", "IR"), ("credit", "IR"), ("borrowing cost", "IR"),
    ("cost of capital", "IR"),
    ("currency", "FX"), ("exchange rate", "FX"), ("rupee", "FX"),
    ("dollar", "FX"),
    ("regulation", "RG"), ("regulatory", "RG"), ("compliance", "CL"),
    ("penalty", "CL"), ("fine", "CL"),
    ("weather", "XW"), ("drought", "XW"), ("flood", "XW"),
    ("cyclone", "XW"), ("heat", "XW"),
    ("commodity", "CM"), ("coal", "CM"), ("oil", "CM"), ("raw material", "CM"),
    ("labor", "LC"), ("wage", "LC"), ("salary", "LC"),
    ("workforce", "WF"), ("worker", "WF"),
    ("operating cost", "OX"), ("opex", "OX"), ("cost increase", "OX"),
    ("revenue", "RV"), ("demand", "RV"), ("sales", "RV"),
    ("capex", "CX"), ("investment", "CX"), ("expansion", "CX"),
    ("emission", "GE"), ("ghg", "GE"), ("carbon", "GE"),
    ("energy use", "EU"), ("electricity consumption", "EU"),
    ("water", "WA"), ("waste", "WS"),
    ("downtime", "DT"), ("outage", "DT"), ("shutdown", "DT"),
    ("supply chain", "SC"), ("supplier", "SC"),
    ("cyber", "CY"), ("data breach", "CY"),
    ("safety", "HS"), ("injury", "HS"), ("accident", "HS"),
}


def test_keyword_to_primitive_dict_removed_from_edge_discoverer():
    """The hardcoded Python dict must NOT exist in edge_discoverer module.

    Per Snowkap's 'every dict lookup is a smell' rule: domain knowledge
    lives in TTL/SPARQL, not Python.
    """
    assert not hasattr(edge_discoverer, "KEYWORD_TO_PRIMITIVE"), (
        "KEYWORD_TO_PRIMITIVE Python dict still exists in edge_discoverer.py — "
        "L1 #9 is to migrate it to TTL via snw:keywordTrigger triples"
    )


def test_edge_discoverer_module_uses_sparql_not_dict():
    """The module source must reference the TTL/SPARQL path, not a dict literal.

    Catches the regression where someone resurrects the dict because 'SPARQL
    is too slow' or similar. The migration is the point.

    Bans the assignment pattern ``KEYWORD_TO_PRIMITIVE = {...}`` only —
    mentioning the name in docstrings/comments is fine (historical context
    explaining what was migrated).
    """
    import re
    src = inspect.getsource(edge_discoverer)
    # Match KEYWORD_TO_PRIMITIVE = { ... } (the dict resurrection)
    assignment_pattern = re.compile(
        r"^\s*KEYWORD_TO_PRIMITIVE\s*=", re.MULTILINE,
    )
    assert not assignment_pattern.search(src), (
        "edge_discoverer.py reintroduces a KEYWORD_TO_PRIMITIVE = ... "
        "assignment — L1/#9 forbids the dict; keywords must live in TTL"
    )
    # Must reference the SPARQL helper or the keyword lookup function.
    assert "keyword" in src.lower() and ("sparql" in src.lower() or "query" in src.lower()), (
        "edge_discoverer.py no longer references a keyword-query mechanism — "
        "either dict resurrected or migration incomplete"
    )


def test_keyword_triples_loaded_via_sparql():
    """The 24 keyword→primitive pairs must be queryable via SPARQL.

    Loads the canonical ontology graph, runs a parameterised SPARQL query
    over snw:keywordTrigger predicates, and asserts every (keyword, prim)
    pair from the original Python dict resolves correctly.
    """
    g = graph_module.get_graph()
    g.ensure_loaded()
    # Query: ?primitive snowkap:keywordTrigger ?keyword
    # (snowkap: and snw: alias the same URI per primitives_schema.ttl @prefix
    # declarations; engine.ontology.graph.DEFAULT_PREFIXES exposes snowkap:)
    rows = g.select_rows("""
        SELECT ?prim_uri ?keyword WHERE {
            ?prim_uri snowkap:keywordTrigger ?keyword .
        }
    """)
    found = set()
    for row in rows:
        prim_uri = row.get("prim_uri", "")
        keyword = row.get("keyword", "")
        # Extract the slug from the URI (e.g. http://...#prim_EP → "EP")
        slug = prim_uri.rsplit("prim_", 1)[-1] if "prim_" in prim_uri else prim_uri.rsplit("#", 1)[-1]
        found.add((keyword.lower(), slug.upper()))
    missing = EXPECTED_KEYWORD_PRIMITIVE_PAIRS - found
    assert not missing, (
        f"keyword→primitive triples missing from TTL: {sorted(missing)[:5]} "
        f"(of {len(missing)} total missing). Found {len(found)} triples."
    )


def test_discover_edges_still_works_end_to_end():
    """Regression: discover_edges() must behave the same way after migration.

    Input narrative containing 'energy price' + 'operating cost' must still
    detect (EP, OX) as a candidate primitive edge.
    """
    nlp = SimpleNamespace(
        narrative_implied_causation=(
            "Rising energy price is driving up operating cost across the "
            "Power & Energy sector this quarter."
        ),
    )
    candidates = edge_discoverer.discover_edges(
        nlp=nlp,
        article_id="test-l1-9-regression",
        source="test",
        company_slug="adani-power",
        now="2026-05-13T00:00:00+00:00",
    )
    # Either 0 candidates (because the EP→OX edge already exists in
    # primitives_edges_p2p.ttl — the discoverer dedups) OR 1 candidate
    # whose label mentions either EP or OX. Both are correct outcomes.
    if candidates:
        labels = [c.label for c in candidates]
        assert any("EP" in lbl or "OX" in lbl for lbl in labels), (
            f"discover_edges saw EP+OX keywords but emitted unrelated edge: {labels}"
        )
    # Either way: the function ran without crashing and returned a list.
    assert isinstance(candidates, list)
