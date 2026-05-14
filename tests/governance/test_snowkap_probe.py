"""Tests for ``/snowkap-probe`` ReAct gate (L0 of Base Version adoption plan).

Verifies the probe:
- returns clean ProbeResult dataclasses for arbitrary input
- finds known live signals (Lloyds Transparency divergence in staging)
- searches all 6 sources when present
- caps excerpts at 200 chars
- rejects SPARQL injection attempts without crashing
- uses parameterised SPARQL (init_bindings), NEVER f-string interpolation

This is the regression suite for the v2 plan's L0 verification gate.
"""

from __future__ import annotations

import ast
import inspect
import re

from engine.governance import probe as probe_module
from engine.governance.probe import probe


def _strip_docstrings(src: str) -> str:
    """Return source with module/class/function docstrings replaced by '\\n'.

    Uses the AST so we ONLY strip the first-statement-string-literal in each
    scope (the docstring) — assigned string constants like
    ``X = "SELECT ..."`` are preserved. This is the right level of precision
    for the SPARQL-keyword-presence and f-string-ban checks below.
    """
    tree = ast.parse(src)
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            # Lines are 1-indexed; end_lineno is inclusive.
            spans.append((first.lineno, first.end_lineno or first.lineno))
    if not spans:
        return src
    lines = src.splitlines(keepends=True)
    for start, end in spans:
        for i in range(start - 1, end):
            if 0 <= i < len(lines):
                # Replace with a blank line so byte-offsets still roughly
                # correspond to source.
                lines[i] = "\n"
    return "".join(lines)


EXPECTED_SOURCES = {
    "decision_log",
    "discovery_audit",
    "discovery_staging",
    "discovered_ttl",
    "tenant_painpoints",
    "live_sparql",
}


def test_probe_returns_clean_result_for_nonsense_query():
    """A query nothing can match must return an empty matches list (not crash)."""
    result = probe("xyzqwerty_does_not_exist_zzz123")
    assert result is not None
    assert hasattr(result, "matches")
    assert hasattr(result, "searched_sources")
    assert hasattr(result, "query")
    assert result.query == "xyzqwerty_does_not_exist_zzz123"
    assert isinstance(result.matches, list)
    assert len(result.matches) == 0


def test_probe_searches_all_6_sources_by_default():
    """When no ``sources`` arg is passed, the probe attempts all 6 sources.

    A source may legitimately produce zero matches (e.g. discovered.ttl is
    nearly empty in production), but ``searched_sources`` must record that
    every source was *attempted* so the operator knows what was covered.
    """
    result = probe("nonexistent_unique_query_zzz")
    assert set(result.searched_sources) == EXPECTED_SOURCES, (
        f"missing sources: {EXPECTED_SOURCES - set(result.searched_sources)}; "
        f"unexpected: {set(result.searched_sources) - EXPECTED_SOURCES}"
    )


def test_probe_respects_explicit_sources_subset():
    """Passing ``sources=[...]`` limits the search to that subset only."""
    result = probe("xyz", sources=["decision_log"])
    assert result.searched_sources == ["decision_log"]


# ---------------------------------------------------------------------------
# Gate 1: probe must find live signals in data/ontology/discovery_staging.json
# ---------------------------------------------------------------------------


def test_probe_finds_lloyds_transparency_divergence_in_staging():
    """The live staging file has a pending Transparency weight divergence for
    Lloyds Banking Group. A query mentioning either name must surface it.

    This is the v2 plan's L0 verification gate 1.
    """
    result = probe("Lloyds transparency")
    staging_matches = [m for m in result.matches if m.source == "discovery_staging"]
    assert staging_matches, (
        "probe did not find any staging matches for 'Lloyds transparency'; "
        f"all sources searched: {result.searched_sources}; "
        f"total matches across all sources: {len(result.matches)}"
    )
    # At least one must be HIGH (substring of 'transparency' or 'lloyds' is
    # present in the label/slug of multiple staging entries).
    assert any(m.confidence == "HIGH" for m in staging_matches), (
        "expected at least one HIGH-confidence match in staging; "
        f"got confidences: {[m.confidence for m in staging_matches]}"
    )


def test_probe_match_excerpt_capped_at_200_chars():
    """No match's excerpt may exceed 200 chars (UI/CLI line-wrap guarantee)."""
    result = probe("Lloyds transparency")
    for m in result.matches:
        assert len(m.excerpt) <= 200, (
            f"match excerpt exceeds cap: {len(m.excerpt)} chars from source "
            f"{m.source} (ref {m.line_or_record_ref})"
        )


# ---------------------------------------------------------------------------
# Gate 2: SPARQL injection must be rejected safely (no crash, no execution)
# ---------------------------------------------------------------------------


def test_probe_rejects_sparql_injection_safely():
    """A query containing SPARQL grammar tokens must NOT crash the probe and
    must NOT cause unintended SPARQL execution.

    The crafted payload includes a DROP GRAPH statement and a comment marker.
    If the probe were vulnerable (f-string interpolating into SPARQL), the
    parser would either reject malformed input loudly OR — worse — execute
    the injected statement against the live graph. Parameterised SPARQL via
    ``init_bindings`` treats the entire string as a literal, eliminating
    both failure modes.

    This is the v2 plan's L0 verification gate 2.
    """
    payload = 'test"; DROP GRAPH <urn:x>; #'
    result = probe(payload)
    # Must return a clean ProbeResult — no exception, no truncation.
    assert result is not None
    assert result.query == payload
    # All 6 sources must have been attempted (none crashed out mid-search).
    assert set(result.searched_sources) == EXPECTED_SOURCES


def test_probe_module_uses_init_bindings_not_fstring_for_sparql():
    """Regression: every SPARQL query inside probe.py MUST pass user input
    via rdflib ``init_bindings``, NEVER via f-string interpolation.

    This catches a future contributor accidentally replicating the vulnerable
    pattern at ``engine/ontology/discovery/promoter.py::_entity_exists_fuzzy``
    lines 118-132 (which L3 fixes separately).
    """
    src = inspect.getsource(probe_module)

    # AST-based docstring removal: strips the first-string-literal in each
    # module/class/function scope but PRESERVES assigned string constants
    # like ``X = "SELECT ..."`` which legitimately contain SPARQL.
    code_only = _strip_docstrings(src)
    code_only = re.sub(r"^\s*#.*$", "", code_only, flags=re.MULTILINE)

    # If the module contains ANY SPARQL grammar keyword, it must also have
    # an init_bindings= call site. Both conditions checked against code_only.
    sparql_kw_re = re.compile(
        r"\b(SELECT|ASK|CONSTRUCT|DESCRIBE|INSERT|DELETE|FILTER|WHERE|PREFIX)\b",
        re.IGNORECASE,
    )
    has_sparql = bool(sparql_kw_re.search(code_only))
    init_bindings_call = re.search(r"init_bindings\s*=", code_only)
    assert has_sparql, (
        "probe.py has no SPARQL grammar keywords in code (only in docstrings). "
        "L0 spec requires SPARQL search of discovered.ttl + tenant painpoints "
        "+ live ontology. Implement these sources before passing this test."
    )
    assert init_bindings_call, (
        "probe.py has SPARQL but no init_bindings= call site — VULNERABLE to "
        "injection via f-string interpolation. Use rdflib init_bindings."
    )

    # No f-string literal containing SPARQL grammar keywords.
    sparql_kw = r"(SELECT|ASK|CONSTRUCT|DESCRIBE|INSERT|DELETE|FILTER|WHERE|PREFIX)"
    fstr_sparql = re.compile(
        r'f"""[^"]*?' + sparql_kw + r'.*?"""',
        re.IGNORECASE | re.DOTALL,
    )
    fstr_sparql_single = re.compile(
        r'f"[^"]*?' + sparql_kw + r'[^"]*?"',
        re.IGNORECASE,
    )
    matches = fstr_sparql.findall(code_only) + fstr_sparql_single.findall(code_only)
    assert not matches, (
        "probe.py contains f-string SPARQL — VULNERABLE to injection. "
        f"Use init_bindings instead. Hits: {matches[:2]}"
    )
