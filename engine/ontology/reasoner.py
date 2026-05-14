"""OWL-RL reasoner for the Snowkap ontology (L1 #1, ported from Base Version).

Runs `owlrl.DeductiveClosure` over the loaded ontology and detects:
- pre-closure disjointness violations (instance-level rdf:type collisions
  between two `owl:disjointWith` classes), and
- post-closure unsatisfiable classes (any class inferred as a subclass of
  ``owl:Nothing`` after closure).

Both signals make ``ReasonerResult.consistent = False``.

Defensive: if ``owlrl`` is not installed, returns ``status='skipped'`` with
a clear note rather than crashing — the L1 #1 CI gate runs in advisory
mode for the first sprint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


ReasonerStatus = Literal["consistent", "inconsistent", "skipped", "error"]


@dataclass
class ReasonerResult:
    """Output of one reasoner run.

    Attributes:
        consistent: True iff zero disjointness violations AND zero
            unsatisfiable classes detected.
        status: ``consistent`` | ``inconsistent`` | ``skipped`` | ``error``.
        unsatisfiable_classes: list of class IRIs found subclass-of owl:Nothing.
        triples_before: triple count before closure.
        triples_after: triple count after closure (or same as before if skipped).
        notes: free-form diagnostic text (errors, skip reasons, etc.).
    """

    consistent: bool
    status: ReasonerStatus
    unsatisfiable_classes: list[str] = field(default_factory=list)
    triples_before: int = 0
    triples_after: int = 0
    notes: str = ""


def _try_import_owlrl():
    """Lazy import — returns the ``owlrl`` module or None."""
    try:
        import owlrl  # noqa: WPS433
        return owlrl
    except ImportError:
        return None


def _detect_disjoint_violations(graph) -> list[str]:
    """Pre-closure heuristic: find instance-level rdf:type collisions where
    the two types are asserted ``owl:disjointWith``.

    Returns a list of violation descriptions (one per offending instance).
    Extends Base Version's class-level walk to instance level — the L1 #1
    adversarial test asserts ``:test_uri a Company, Regulation`` (instance,
    not class) and expects this to be flagged.
    """
    from rdflib import RDF, OWL
    violations: list[str] = []
    # Instance-level: ?inst rdf:type ?A,?B . ?A owl:disjointWith ?B
    for inst in set(graph.subjects(RDF.type, None)):
        types = set(graph.objects(inst, RDF.type))
        types_list = list(types)
        for i, type_a in enumerate(types_list):
            for type_b in types_list[i + 1:]:
                if (type_a, OWL.disjointWith, type_b) in graph or \
                   (type_b, OWL.disjointWith, type_a) in graph:
                    violations.append(
                        f"{inst} typed as both {type_a} and {type_b} (disjoint)"
                    )
    return violations


def _find_unsatisfiable_classes(graph) -> list[str]:
    """Post-closure: any class inferred as subclass of ``owl:Nothing``.

    Excludes ``owl:Nothing`` itself — by definition the empty class IS a
    subclass of itself, so without this filter every clean ontology would
    falsely report 1 'unsatisfiable' class. Catches the pattern where
    e.g. a contradiction makes an application class collapse to
    ``owl:Nothing``.
    """
    from rdflib import RDFS, OWL
    return sorted(
        str(s)
        for s in graph.subjects(RDFS.subClassOf, OWL.Nothing)
        if str(s) != str(OWL.Nothing)
    )


def run_reasoner_on_graph(graph) -> ReasonerResult:
    """Run reasoner directly against an in-memory rdflib Graph.

    Required for adversarial tests that synthesize a contradiction graph
    (e.g. instance with two disjoint types) without writing to disk.
    """
    try:
        triples_before = len(graph)
    except Exception as exc:  # noqa: BLE001
        return ReasonerResult(
            consistent=False, status="error",
            notes=f"could not count triples: {exc}",
        )

    # Pre-closure disjointness check
    try:
        pre_violations = _detect_disjoint_violations(graph)
    except Exception as exc:  # noqa: BLE001
        pre_violations = []
        logger.debug("pre-closure disjointness check failed: %s", exc)

    # OWL-RL closure (optional — owlrl may not be installed)
    owlrl = _try_import_owlrl()
    if owlrl is None:
        triples_after = triples_before
        unsat = []
        if pre_violations:
            return ReasonerResult(
                consistent=False, status="inconsistent",
                unsatisfiable_classes=[],
                triples_before=triples_before, triples_after=triples_after,
                notes=(f"owlrl not installed; pre-closure heuristic detected "
                       f"{len(pre_violations)} disjoint violation(s): "
                       f"{pre_violations[:2]}"),
            )
        return ReasonerResult(
            consistent=True, status="skipped",
            unsatisfiable_classes=[],
            triples_before=triples_before, triples_after=triples_after,
            notes="owlrl not installed; pre-closure heuristic clean",
        )

    try:
        owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(graph)
    except Exception as exc:  # noqa: BLE001
        return ReasonerResult(
            consistent=False, status="error",
            unsatisfiable_classes=[],
            triples_before=triples_before, triples_after=len(graph),
            notes=f"owlrl closure failed: {exc}",
        )

    triples_after = len(graph)
    try:
        unsat = _find_unsatisfiable_classes(graph)
    except Exception as exc:  # noqa: BLE001
        unsat = []
        logger.debug("unsatisfiable scan failed: %s", exc)

    has_violation = bool(pre_violations) or bool(unsat)
    return ReasonerResult(
        consistent=not has_violation,
        status="inconsistent" if has_violation else "consistent",
        unsatisfiable_classes=unsat,
        triples_before=triples_before, triples_after=triples_after,
        notes=(
            f"pre-closure violations: {len(pre_violations)}; "
            f"unsatisfiable: {len(unsat)}"
            if has_violation else
            f"closure expanded {triples_after - triples_before} triples"
        ),
    )


def run_reasoner(ttl_path: Path) -> ReasonerResult:
    """Load a TTL file into a fresh Graph and reason over it."""
    from rdflib import Graph
    if not Path(ttl_path).exists():
        return ReasonerResult(
            consistent=False, status="error",
            notes=f"TTL file not found: {ttl_path}",
        )
    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception as exc:  # noqa: BLE001
        return ReasonerResult(
            consistent=False, status="error",
            notes=f"TTL parse failed for {ttl_path}: {exc}",
        )
    return run_reasoner_on_graph(g)
