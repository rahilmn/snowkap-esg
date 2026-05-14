"""Phase 3 §5.5 — cross-role ₹ drift verifier.

The plan: take the three role payloads (CFO, CEO, ESG Analyst), extract
every ₹ figure from each, and compute drift across roles for the same
underlying metric. If drift > 5%, flag the offending role.

Existing ``perspective_dedup.py`` checks N-GRAM overlap (prose paraphrase
detection). This module checks NUMERIC drift — the case where each role
quotes a different ₹ figure for the SAME underlying claim.

Concrete failure mode this catches: CFO says "₹500 Cr exposure", CEO
says "₹450 Cr at risk", Analyst's confidence_bounds says "₹560 Cr". A
CXO comparing the three views immediately notices the figures don't
agree and trust collapses.

Usage:

    drift = compute_drift(payloads)
    if drift.has_violations:
        # send the offending role back through generation OR auto-clamp
        # to the canonical (largest) figure
        ...

Detection is purely advisory — no mutation. The caller decides whether
to regenerate or auto-clamp via the cross-section consistency
normaliser already in output_verifier.py.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_DRIFT_THRESHOLD = 0.05  # 5%

# Same regex as cross_section consistency check (output_verifier.py).
_RUPEE_FIGURE_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s?([\d,]+(?:\.\d+)?)\s?(?:Cr|crore)\b",
    re.IGNORECASE,
)


@dataclass
class RoleFigures:
    """Every ₹ Cr figure extracted from one role's payload, with field paths."""
    role: str
    figures: list[float] = field(default_factory=list)
    by_field: dict[str, list[float]] = field(default_factory=dict)

    @property
    def max_cr(self) -> float | None:
        return max(self.figures) if self.figures else None


@dataclass
class DriftViolation:
    """One pair (role_a, role_b) whose canonical figures differ > threshold."""
    role_a: str
    role_b: str
    figure_a: float
    figure_b: float
    drift_pct: float


@dataclass
class DriftReport:
    by_role: dict[str, RoleFigures]
    violations: list[DriftViolation]
    canonical_cr: float | None  # max across roles — recommended target

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


def _extract_strings(node: Any, prefix: str, out: dict[str, str]) -> None:
    """Walk a payload and collect every leaf string keyed by its path."""
    if isinstance(node, str):
        if node:
            out[prefix or "(root)"] = node
        return
    if isinstance(node, dict):
        for key, val in node.items():
            _extract_strings(val, f"{prefix}.{key}" if prefix else str(key), out)
        return
    if isinstance(node, list):
        for i, item in enumerate(node):
            _extract_strings(item, f"{prefix}[{i}]" if prefix else f"[{i}]", out)


def extract_role_figures(role: str, payload: Any) -> RoleFigures:
    """Pull every ₹X Cr / ₹X crore figure out of a role payload."""
    rf = RoleFigures(role=role)
    if payload is None:
        return rf
    strings: dict[str, str] = {}
    _extract_strings(payload, "", strings)
    for path, text in strings.items():
        for m in _RUPEE_FIGURE_RE.finditer(text):
            try:
                v = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            rf.figures.append(v)
            rf.by_field.setdefault(path, []).append(v)
    return rf


def compute_drift(
    payloads: dict[str, Any],
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> DriftReport:
    """Compute pairwise ₹ drift across role payloads.

    Args:
        payloads: e.g. {"cfo": {...}, "ceo": {...}, "esg_analyst": {...}}
        threshold: max allowable drift between any two roles' max figures
                   before a violation fires (0.05 = 5%).

    Returns DriftReport. ``canonical_cr`` is the global max — when used
    to auto-clamp, it preserves intent (largest figure usually = the
    canonical event exposure as decided by output_verifier.py).
    """
    by_role: dict[str, RoleFigures] = {}
    for role, payload in payloads.items():
        by_role[role] = extract_role_figures(role, payload)

    # Roles with at least one ₹ figure
    active = [(r, rf) for r, rf in by_role.items() if rf.max_cr is not None]
    canonical = max((rf.max_cr for _, rf in active), default=None)

    violations: list[DriftViolation] = []
    for i, (role_a, rf_a) in enumerate(active):
        for role_b, rf_b in active[i + 1:]:
            a, b = rf_a.max_cr, rf_b.max_cr
            if a is None or b is None or max(a, b) == 0:
                continue
            drift = abs(a - b) / max(a, b)
            if drift > threshold:
                violations.append(
                    DriftViolation(
                        role_a=role_a,
                        role_b=role_b,
                        figure_a=a,
                        figure_b=b,
                        drift_pct=round(drift, 4),
                    ),
                )

    return DriftReport(by_role=by_role, violations=violations, canonical_cr=canonical)


def serialise_report(report: DriftReport) -> dict[str, Any]:
    """JSON-friendly dict for logging to cross_role_drift.jsonl."""
    return {
        "canonical_cr": report.canonical_cr,
        "by_role": {
            role: {
                "max_cr": rf.max_cr,
                "figure_count": len(rf.figures),
                "fields_with_figures": sorted(rf.by_field.keys()),
            }
            for role, rf in report.by_role.items()
        },
        "violations": [
            {
                "role_a": v.role_a,
                "role_b": v.role_b,
                "figure_a": v.figure_a,
                "figure_b": v.figure_b,
                "drift_pct": v.drift_pct,
            }
            for v in report.violations
        ],
        "has_violations": report.has_violations,
    }
