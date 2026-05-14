"""Phase 24 — NormativePrinciple ontology + SPARQL query regression.

Verifies that:

  1. ``data/ontology/normative_principles.ttl`` parses cleanly.
  2. The graph loader pulls it in (added to the optional file list).
  3. ``query_normative_principles_for_event`` returns deterministic,
     polarity-correct, event-scoped results.
  4. Every CRITICAL/HIGH-materiality "do nothing" verdict has at least
     one cross-cutting materiality principle available — the rebuttal
     discipline relies on this.
"""

from __future__ import annotations

import pytest

from engine.ontology.graph import OntologyGraph, reset_graph
from engine.ontology.intelligence import (
    NormativePrinciple,
    query_normative_principles_for_event,
)


@pytest.fixture(autouse=True)
def _reset():
    """Ensure each test sees a fresh graph load."""
    reset_graph()
    yield
    reset_graph()


# ---------------------------------------------------------------------------
# 1. TTL parsing + graph integration
# ---------------------------------------------------------------------------


class TestNormativePrinciplesLoad:
    def test_ttl_file_parses(self):
        from pathlib import Path
        from rdflib import Graph
        ttl_path = Path("data/ontology/normative_principles.ttl")
        assert ttl_path.exists(), "normative_principles.ttl missing"
        g = Graph()
        g.parse(ttl_path, format="turtle")
        # At least 15 NormativePrinciple instances per the seed
        from rdflib.namespace import RDF
        from engine.ontology.graph import SNOWKAP
        instances = list(g.subjects(RDF.type, SNOWKAP.NormativePrinciple))
        assert len(instances) >= 15, f"expected ≥15 principles, got {len(instances)}"

    def test_graph_loader_includes_principles(self):
        og = OntologyGraph().load()
        # If the principles loaded, at least one principleId should be queryable
        rows = og.select_rows("""
            SELECT ?pid WHERE {
                ?p a snowkap:NormativePrinciple ;
                   snowkap:principleId ?pid .
            }
            LIMIT 5
        """)
        assert len(rows) >= 5, "graph loader did not include normative_principles.ttl"
        ids = {r["pid"] for r in rows}
        assert any(pid.startswith("NP-") for pid in ids)


# ---------------------------------------------------------------------------
# 2. Query — event-scoped selection
# ---------------------------------------------------------------------------


class TestQueryByEvent:
    def test_regulatory_penalty_returns_reg_principle(self):
        results = query_normative_principles_for_event(
            "event_regulatory_penalty",
            polarity="negative",
            limit=5,
        )
        assert len(results) >= 1
        assert all(isinstance(r, NormativePrinciple) for r in results)
        # NP-REG-001 should appear (it scopes to event_regulatory_penalty + negative)
        ids = {r.principle_id for r in results}
        assert "NP-REG-001" in ids

    def test_contract_win_returns_positive_principles(self):
        results = query_normative_principles_for_event(
            "event_contract_win",
            polarity="positive",
            limit=5,
        )
        assert len(results) >= 1
        ids = {r.principle_id for r in results}
        # NP-OPS-002 (positive contract-win) and NP-FIN-001 (source-tagging) both apply
        assert "NP-OPS-002" in ids or "NP-FIN-001" in ids

    def test_unknown_event_falls_back_to_cross_cutting(self):
        # Unknown event name → only cross-cutting principles (those without
        # an appliesToEvent restriction) should match.
        results = query_normative_principles_for_event(
            "event_does_not_exist",
            polarity="both",
            limit=10,
        )
        # NP-MAT-* and NP-FIN-002, NP-FIN-003 are cross-cutting
        ids = {r.principle_id for r in results}
        assert "NP-MAT-001" in ids or "NP-MAT-003" in ids or "NP-FIN-002" in ids


# ---------------------------------------------------------------------------
# 3. Polarity discipline
# ---------------------------------------------------------------------------


class TestPolarityDiscipline:
    def test_negative_only_principle_excluded_for_positive_event(self):
        # NP-REG-001 has appliesToPolarity "negative". A positive-polarity
        # query for the same event must NOT return it.
        results = query_normative_principles_for_event(
            "event_regulatory_penalty",
            polarity="positive",
            limit=10,
        )
        ids = {r.principle_id for r in results}
        assert "NP-REG-001" not in ids, (
            "NP-REG-001 is marked polarity=negative; should not surface "
            "on positive-polarity query"
        )

    def test_both_polarity_principle_returned_either_way(self):
        # NP-REG-002 has appliesToPolarity "both" — should return for both
        for polarity in ("positive", "negative"):
            results = query_normative_principles_for_event(
                "event_regulatory_consultation",
                polarity=polarity,
                limit=10,
            )
            ids = {r.principle_id for r in results}
            assert "NP-REG-002" in ids, (
                f"NP-REG-002 (polarity=both) missing from {polarity} query"
            )


# ---------------------------------------------------------------------------
# 4. "Do nothing" rebuttal discipline — NP-MAT-003 is the warrant
# ---------------------------------------------------------------------------


class TestDoNothingDiscipline:
    def test_do_nothing_warrant_always_available(self):
        """NP-MAT-003 must be reachable on any query because it's the
        rebuttal-discipline principle. Without it, a do-nothing verdict
        cannot be defended at audit (per CLAUDE.md rule 4)."""
        # Query a totally generic event — NP-MAT-003 should still surface
        # because it's a cross-cutting materiality principle.
        results = query_normative_principles_for_event(
            None,
            polarity="both",
            limit=20,
        )
        ids = {r.principle_id for r in results}
        assert "NP-MAT-003" in ids, (
            "NP-MAT-003 (do-nothing rebuttal discipline) must be available "
            "as a cross-cutting principle"
        )


# ---------------------------------------------------------------------------
# 5. Limit + ordering
# ---------------------------------------------------------------------------


class TestLimitAndOrdering:
    def test_limit_respected(self):
        results = query_normative_principles_for_event(
            "event_regulatory_penalty",
            polarity="negative",
            limit=2,
        )
        assert len(results) <= 2

    def test_event_specific_principles_come_first(self):
        # Event-specific principles (priority 1) should appear before
        # cross-cutting principles (priority 2) in the result list.
        results = query_normative_principles_for_event(
            "event_contract_win",
            polarity="positive",
            limit=10,
        )
        # Find the index of any event-specific (NP-OPS-002 or NP-FIN-001)
        # vs. any cross-cutting (NP-MAT-* or NP-FIN-002/003) principle.
        event_idx = next(
            (i for i, r in enumerate(results)
             if r.principle_id in {"NP-OPS-002", "NP-FIN-001"}),
            None,
        )
        cross_idx = next(
            (i for i, r in enumerate(results)
             if r.principle_id.startswith("NP-MAT-")
             or r.principle_id in {"NP-FIN-002", "NP-FIN-003"}),
            None,
        )
        if event_idx is not None and cross_idx is not None:
            assert event_idx < cross_idx, (
                "Event-specific principles must come before cross-cutting "
                "ones in the result list"
            )
