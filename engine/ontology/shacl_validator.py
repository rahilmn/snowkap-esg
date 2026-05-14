"""SHACL validator for the Snowkap ontology (L1 #1, ported from Base Version).

Wraps `pyshacl.validate()` and returns a structured ``SHACLResult`` with
violations parsed into plain dicts (focus_node / path / message / severity).

Defensive: skips gracefully if `pyshacl` is not installed; per-shape parse
failures don't kill the whole validation run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


SHACLStatus = Literal["ok", "violations", "skipped", "error"]


@dataclass
class SHACLResult:
    status: SHACLStatus
    violations: list[dict] = field(default_factory=list)
    notes: str = ""


def _try_import_pyshacl():
    try:
        import pyshacl  # noqa: WPS433
        return pyshacl
    except ImportError:
        return None


def _parse_violation_text(report_text: str) -> list[dict]:
    """Parse pyshacl's plain-text violation report into structured dicts.

    pyshacl returns a multi-section text report; each violation begins with
    ``Constraint Violation in ...``. We pull focus_node + path + message
    out of each section. This is a defensive parser — any unparseable
    section becomes a single-field ``{"message": <raw>}`` entry rather than
    being dropped.
    """
    violations: list[dict] = []
    for section in report_text.split("Constraint Violation in"):
        section = section.strip()
        if not section or section.startswith("Validation Report"):
            continue
        v: dict = {"raw": section[:300]}
        for line in section.splitlines():
            line = line.strip()
            if line.startswith("Focus Node:"):
                v["focus_node"] = line.split("Focus Node:", 1)[1].strip()
            elif line.startswith("Result Path:"):
                v["path"] = line.split("Result Path:", 1)[1].strip()
            elif line.startswith("Message:"):
                v["message"] = line.split("Message:", 1)[1].strip()
            elif line.startswith("Severity:"):
                v["severity"] = line.split("Severity:", 1)[1].strip()
        violations.append(v)
    return violations


def validate_graph(graph, shapes_ttl_path: Path) -> SHACLResult:
    """Validate an in-memory rdflib Graph against the SHACL shapes file."""
    pyshacl = _try_import_pyshacl()
    if pyshacl is None:
        return SHACLResult(status="skipped", notes="pyshacl not installed")
    if not Path(shapes_ttl_path).exists():
        return SHACLResult(
            status="error", notes=f"shapes file not found: {shapes_ttl_path}",
        )
    try:
        from rdflib import Graph
        shapes_g = Graph()
        shapes_g.parse(shapes_ttl_path, format="turtle")
    except Exception as exc:  # noqa: BLE001
        return SHACLResult(status="error", notes=f"shapes parse failed: {exc}")

    try:
        conforms, _results_graph, results_text = pyshacl.validate(
            data_graph=graph,
            shacl_graph=shapes_g,
            inference="none",  # rdfs/owl-rl inference is the reasoner's job
            advanced=True,     # enables sh:sparql constraints
            meta_shacl=False,
            debug=False,
        )
    except Exception as exc:  # noqa: BLE001
        return SHACLResult(status="error", notes=f"pyshacl crashed: {exc}")

    if conforms:
        return SHACLResult(status="ok", violations=[], notes="conforms")

    violations = _parse_violation_text(results_text or "")
    return SHACLResult(
        status="violations", violations=violations,
        notes=f"{len(violations)} violation(s) detected",
    )


def validate(data_ttl_path: Path, shapes_ttl_path: Path) -> SHACLResult:
    """Load TTL into a fresh Graph and validate against shapes."""
    if not Path(data_ttl_path).exists():
        return SHACLResult(
            status="error", notes=f"data file not found: {data_ttl_path}",
        )
    try:
        from rdflib import Graph
        g = Graph()
        g.parse(data_ttl_path, format="turtle")
    except Exception as exc:  # noqa: BLE001
        return SHACLResult(
            status="error", notes=f"data parse failed for {data_ttl_path}: {exc}",
        )
    return validate_graph(g, shapes_ttl_path)
