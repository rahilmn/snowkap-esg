"""L1 #1 — Validation infrastructure (SHACL + owlrl + CQ runner).

Verifies the v2 plan's L1 #1 deliverable:
  - reasoner: clean ontology consistent + adversarial collision detected
  - SHACL:    clean primitives validate without violations + adversarial
              out-of-bounds criticality score caught
  - CQ runner: ≥12 CQs registered, zero errors, keyword-trigger CQ passes

Note: pytest must run with `-s` (capture=no) to avoid the Python 3.14 +
pytest 9.x I/O capture bug. See L0 commit 074c25c for context.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from engine.config import get_data_path
from engine.ontology.cq_runner import run_all
from engine.ontology.reasoner import run_reasoner, run_reasoner_on_graph
from engine.ontology.shacl_validator import validate, validate_graph


# ---------------------------------------------------------------------------
# Reasoner
# ---------------------------------------------------------------------------


def test_reasoner_returns_consistent_on_clean_ontology():
    """The shipping schema.ttl must be reasoner-consistent."""
    r = run_reasoner(get_data_path("ontology", "schema.ttl"))
    assert r.status in ("consistent", "skipped"), (
        f"expected consistent/skipped, got status={r.status} notes={r.notes!r}"
    )
    assert r.consistent, (
        f"clean schema.ttl is reported inconsistent — "
        f"unsatisfiable={r.unsatisfiable_classes[:3]} notes={r.notes!r}"
    )


def test_reasoner_blocks_company_regulation_collision():
    """Adversarial: a graph with conflicting types for one instance must be
    flagged inconsistent.

    Per L0 reviewer I5 — the gate isn't satisfied by 'reasoner runs'; it's
    satisfied by 'reasoner BLOCKS contradictions.' The test synthesizes
    the contradiction (instance with two disjoint types + the disjointness
    assertion) and asserts the reasoner catches it.
    """
    from rdflib import Graph, RDF, URIRef, OWL, Namespace
    SNOW = Namespace("http://snowkap.com/ontology/esg#")
    g = Graph()
    g.parse(get_data_path("ontology", "schema.ttl"), format="turtle")
    test_iri = URIRef(str(SNOW.test_collision_uri))
    g.add((test_iri, RDF.type, SNOW.Company))
    g.add((test_iri, RDF.type, SNOW.Regulation))
    # Heuristic relies on disjointness assertion in same graph (Base
    # Version reasoner test idiom).
    g.add((SNOW.Company, OWL.disjointWith, SNOW.Regulation))

    r = run_reasoner_on_graph(g)
    assert (not r.consistent) or len(r.unsatisfiable_classes) >= 1, (
        f"reasoner missed Company/Regulation collision — "
        f"status={r.status} consistent={r.consistent} notes={r.notes!r}"
    )


# ---------------------------------------------------------------------------
# SHACL
# ---------------------------------------------------------------------------


def test_shacl_validates_clean_primitives_no_violations():
    """First-pass shapes must NOT over-constrain existing data.

    Per L0 reviewer R5: SHACL must allow all current edges to validate.
    Validates the rich primitives_edges_p2p.ttl against the 5 starter
    shapes — expect zero violations because none of the shapes target
    properties that file uses.
    """
    sr = validate(
        get_data_path("ontology", "primitives_edges_p2p.ttl"),
        get_data_path("ontology", "shacl", "snowkap_core.shacl.ttl"),
    )
    if sr.status == "skipped":
        pytest.skip(f"pyshacl unavailable: {sr.notes}")
    assert sr.status == "ok", (
        f"first-pass SHACL over-constrained existing edges — "
        f"status={sr.status} violations={sr.violations[:3]}"
    )
    assert not sr.violations, (
        f"existing edges should validate cleanly: {sr.violations[:3]}"
    )


def test_shacl_blocks_out_of_bounds_criticality_score():
    """Adversarial: synthesize a triple `<x> snowkap:criticalityScore 1.5`
    and assert the SHACL shape catches it."""
    from rdflib import Graph, Literal, URIRef, Namespace
    SNOW = Namespace("http://snowkap.com/ontology/esg#")
    g = Graph()
    g.parse(get_data_path("ontology", "schema.ttl"), format="turtle")
    test_node = URIRef(str(SNOW.test_score_node))
    g.add((test_node, SNOW.criticalityScore, Literal(Decimal("1.5"))))

    sr = validate_graph(g, get_data_path("ontology", "shacl", "snowkap_core.shacl.ttl"))
    if sr.status == "skipped":
        pytest.skip(f"pyshacl unavailable: {sr.notes}")
    assert sr.status == "violations" and sr.violations, (
        f"SHACL did not catch criticalityScore=1.5 — "
        f"status={sr.status} violations={sr.violations}"
    )


# ---------------------------------------------------------------------------
# CQ runner
# ---------------------------------------------------------------------------


def test_cq_runner_finds_at_least_12_cqs():
    """The CQ corpus must register ≥12 named CQs across categories.

    Per v2 plan I2: ≥12 CQs covering all intelligence.py helper categories.
    """
    report = run_all()
    assert report.total >= 12, (
        f"CQ corpus too small: total={report.total} "
        f"(passing={report.passing}, errors={report.errors})"
    )


def test_cq_runner_zero_errors_on_live_ontology():
    """Every CQ in the corpus must execute without SPARQL syntax errors.

    A CQ may legitimately return zero rows (status='empty') if the
    ontology genuinely lacks data for that question — that's a
    'warning'. But 'error' means the query itself is malformed and
    must be fixed.
    """
    report = run_all()
    error_summaries = [
        f"{r.name}: {r.error}" for r in report.results if r.status == "error"
    ]
    assert report.errors == 0, (
        f"CQ runner has {report.errors} error(s):\n  - " + "\n  - ".join(error_summaries[:5])
    )


def test_keyword_trigger_cq_passes():
    """The L1 #9 → L1 #1 integration: the CQ that counts keyword triggers
    must find ≥24 (the migration count from L1 #9)."""
    report = run_all()
    matches = [r for r in report.results if r.name == "keyword_triggers_present"]
    assert matches, (
        f"CQ 'keyword_triggers_present' not found in report. "
        f"Available: {[r.name for r in report.results[:10]]}"
    )
    cq = matches[0]
    assert cq.status == "pass", f"keyword_triggers_present status={cq.status}"
    assert cq.row_count >= 24, (
        f"L1 #9 migrated 24 keyword→primitive triples but CQ found only "
        f"{cq.row_count} — possible regression in primitives_keywords.ttl"
    )
