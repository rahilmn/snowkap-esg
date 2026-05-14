"""Competency-Question (CQ) runner for the Snowkap ontology (L1 #1).

Loads SPARQL CQ files from ``data/ontology/competency_questions/`` (or a
caller-supplied dir), splits multi-CQ files on the ``# === CQ: <name> ===``
marker, runs each named CQ against the loaded ontology graph, and returns
a structured ``CQReport`` summarizing pass / empty / error.

Pass = query executes AND returns ≥1 row. Empty = executes but 0 rows.
Error = SPARQL syntax error, name conflict, etc.

Per-CQ try/except so one bad query doesn't kill the rest of the run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


CQStatus = Literal["pass", "empty", "error"]


@dataclass(frozen=True)
class CQResult:
    name: str
    status: CQStatus
    row_count: int
    error: str | None = None


@dataclass
class CQReport:
    total: int = 0
    passing: int = 0
    warnings: int = 0
    errors: int = 0
    results: list[CQResult] = field(default_factory=list)


_CQ_MARKER = re.compile(r"^\s*#\s*===\s*CQ:\s*([\w_\-]+)\s*===\s*$", re.MULTILINE)


def _split_file(text: str) -> list[tuple[str, str]]:
    """Split a multi-CQ .rq file on ``# === CQ: <name> ===`` markers.

    Returns a list of (name, sparql_text) tuples. The SPARQL text for each
    named CQ INCLUDES the prefix declarations from the file header so the
    query can stand alone when handed to rdflib.
    """
    matches = list(_CQ_MARKER.finditer(text))
    if not matches:
        return []
    # Header = everything before the first marker (typically the PREFIX
    # declarations). Each CQ's body = from end-of-marker up to the next
    # marker (or EOF).
    header = text[: matches[0].start()].strip()
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if (i + 1) < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if header and body:
            out.append((name, header + "\n\n" + body))
        elif body:
            out.append((name, body))
    return out


def _default_cq_dir() -> Path:
    from engine.config import get_data_path
    return get_data_path("ontology", "competency_questions")


def run_all(graph=None, cq_dir: Path | None = None) -> CQReport:
    """Run every CQ found in ``cq_dir`` against the loaded ontology."""
    if cq_dir is None:
        cq_dir = _default_cq_dir()
    if not Path(cq_dir).exists():
        return CQReport()  # empty report — nothing to run

    if graph is None:
        try:
            from engine.ontology.graph import get_graph
            g = get_graph()
            g.ensure_loaded()
        except Exception as exc:  # noqa: BLE001
            return CQReport()
    else:
        g = graph

    report = CQReport()
    rq_files = sorted(Path(cq_dir).glob("*.rq"))
    for rq_path in rq_files:
        try:
            text = rq_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            report.results.append(CQResult(
                name=f"<read-error:{rq_path.name}>",
                status="error", row_count=0, error=str(exc),
            ))
            report.total += 1
            report.errors += 1
            continue

        for name, sparql in _split_file(text):
            report.total += 1
            try:
                rows = list(g.select_rows(sparql)) if hasattr(g, "select_rows") \
                    else list(g.query(sparql))
            except Exception as exc:  # noqa: BLE001
                report.results.append(CQResult(
                    name=name, status="error", row_count=0, error=str(exc),
                ))
                report.errors += 1
                continue
            row_count = len(rows)
            status: CQStatus = "pass" if row_count >= 1 else "empty"
            report.results.append(CQResult(
                name=name, status=status, row_count=row_count, error=None,
            ))
            if status == "pass":
                report.passing += 1
            else:
                report.warnings += 1
    return report
