"""Phase 24 (W3) — CFO-credibility preflight gating.

Single decision point that runs after Stage 11 (perspective transform)
and answers one question: **is this insight safe to publish to a CFO
right now?**

Six independent gates, each pass/fail, each individually logged to
``data/audit/preflight_log.jsonl``:

  1. **financial_impact_quantified** — `decision_summary.financial_exposure`
     contains a ₹/Rs/INR figure with explicit `(from article)` or
     `(engine estimate)` source tag (per CLAUDE.md rule 7).
  2. **framework_mapped** — at least one ESG framework is cited with a
     section code (`BRSR:P5`, `GRI:303`, `ESRS:E1`, …).
  3. **no_stale_data** — source article published within
     ``freshness_window_days`` for the event type (read from ontology
     `:FreshnessWindow` triples; falls back to
     ``DEFAULT_FRESHNESS_DAYS = 14`` when no window is declared).
  4. **polarity_coherent** — Phase 12.4 narrative-coherence verifier
     emitted no warnings (event polarity ↔ insight polarity ↔ NLP
     sentiment all agree).
  5. **numeric_consistent** — Phase 12.5 cross-section ₹ drift below
     ``DRIFT_TOLERANCE_PCT`` (35 %) — every published ₹ figure agrees
     with the canonical exposure.
  6. **stakeholder_polarity_matched** — Phase 15 stakeholder map
     positions match the event polarity (positive event → positive
     stakeholder stances; never mixed).

If ANY gate fails, the article does NOT surface on the CFO perspective
dashboard. ESG Analyst surface continues to show all (so analysts can
see what the CFO is missing and why). Each gate emits one
``preflight_log.jsonl`` entry per article so operators can answer
"how many articles fell out of CFO surface yesterday and why?".

The evaluator is purely additive on top of pipeline outputs already
computed elsewhere (insight, frameworks, verifier report). Zero new
LLM tokens. ~2 ms latency (one SPARQL query for the freshness window).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_FRESHNESS_DAYS = 14
DRIFT_TOLERANCE_PCT = 0.35  # Phase 12.5 threshold

GateName = Literal[
    "financial_impact_quantified",
    "framework_mapped",
    "no_stale_data",
    "polarity_coherent",
    "numeric_consistent",
    "stakeholder_polarity_matched",
]

ALL_GATES: tuple[GateName, ...] = (
    "financial_impact_quantified",
    "framework_mapped",
    "no_stale_data",
    "polarity_coherent",
    "numeric_consistent",
    "stakeholder_polarity_matched",
)


@dataclass
class GateResult:
    gate: str
    passed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreflightReport:
    """Aggregate result of all 6 gates for one article."""
    passed: bool  # AND of all gates
    gate_results: list[GateResult] = field(default_factory=list)
    canonical_exposure_cr: float | None = None
    freshness_window_days: int | None = None

    def failed_gates(self) -> list[str]:
        return [r.gate for r in self.gate_results if not r.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "gates": {r.gate: r.passed for r in self.gate_results},
            "failures": [
                {"gate": r.gate, "reason": r.reason}
                for r in self.gate_results if not r.passed
            ],
            "canonical_exposure_cr": self.canonical_exposure_cr,
            "freshness_window_days": self.freshness_window_days,
        }


# ---------------------------------------------------------------------------
# Gate 1 — financial_impact_quantified
# ---------------------------------------------------------------------------

# Same regex shape as output_verifier — ensures we treat the source-tag
# discipline identically to the verifier path.
_RUPEE_FIGURE_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s*\d+(?:,\d+)*(?:\.\d+)?\s*(?:crore|Lakh|Lkh|Cr|L)?\b",
    re.IGNORECASE,
)
_SOURCE_TAG_RE = re.compile(
    r"\((?:from\s+article|engine\s+estimate)\)", re.IGNORECASE,
)


def gate_financial_impact_quantified(insight: dict[str, Any]) -> GateResult:
    decision = insight.get("decision_summary") or {}
    exposure = (decision.get("financial_exposure") or "").strip()
    if not exposure or exposure.upper() in {"N/A", "NONE"}:
        return GateResult(
            gate="financial_impact_quantified", passed=False,
            reason="decision_summary.financial_exposure missing or N/A",
        )
    if not _RUPEE_FIGURE_RE.search(exposure):
        return GateResult(
            gate="financial_impact_quantified", passed=False,
            reason="financial_exposure has no ₹ / Rs / INR figure",
        )
    if not _SOURCE_TAG_RE.search(exposure):
        return GateResult(
            gate="financial_impact_quantified", passed=False,
            reason="financial_exposure missing source tag (from article | engine estimate)",
        )
    return GateResult(gate="financial_impact_quantified", passed=True)


# ---------------------------------------------------------------------------
# Gate 2 — framework_mapped
# ---------------------------------------------------------------------------


def gate_framework_mapped(framework_codes: Iterable[str] | None) -> GateResult:
    """Pass if at least one framework citation looks like a section code
    (contains ``:`` or a digit) — bare framework names like 'BRSR' alone
    are too coarse to anchor a CFO action."""
    codes = [c for c in (framework_codes or []) if c]
    if not codes:
        return GateResult(
            gate="framework_mapped", passed=False,
            reason="no frameworks cited",
        )
    has_section_code = any(
        ":" in c or any(ch.isdigit() for ch in c) for c in codes
    )
    if not has_section_code:
        return GateResult(
            gate="framework_mapped", passed=False,
            reason=f"frameworks present ({len(codes)}) but no section codes "
                   f"(e.g. BRSR:P5, GRI:303); too coarse for CFO citation",
        )
    return GateResult(gate="framework_mapped", passed=True)


# ---------------------------------------------------------------------------
# Gate 3 — no_stale_data
# ---------------------------------------------------------------------------


def _parse_published_date(published_at: str | None) -> datetime | None:
    if not published_at:
        return None
    raw = published_at.strip()
    # Try ISO-8601 (most common); fall back to YYYY-MM-DD
    candidates = [raw, raw.replace("Z", "+00:00")]
    for cand in candidates:
        try:
            dt = datetime.fromisoformat(cand)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            continue
    try:
        # Bare date
        dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _query_freshness_window(event_id: str | None) -> int | None:
    """Return freshness window in days for an event type, or None if
    not declared in the ontology."""
    if not event_id:
        return None
    try:
        from rdflib import Literal as _Literal
        from engine.ontology.graph import get_graph
        g = get_graph()
        rows = g.select_rows(
            """
            SELECT ?days WHERE {
                ?w a snowkap:FreshnessWindow ;
                   snowkap:freshnessForEvent ?evt ;
                   snowkap:freshnessDays ?days .
                FILTER(STR(?evt) = ?event_id_param)
            }
            LIMIT 1
            """,
            init_bindings={"event_id_param": _Literal(event_id)},
        )
        if rows:
            return int(float(rows[0]["days"]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("freshness_window SPARQL failed: %s", exc)
    return None


def gate_no_stale_data(
    published_at: str | None,
    event_id: str | None,
    *,
    now: datetime | None = None,
) -> tuple[GateResult, int]:
    """Returns (result, window_days_used)."""
    window = _query_freshness_window(event_id) or DEFAULT_FRESHNESS_DAYS
    pub = _parse_published_date(published_at)
    if pub is None:
        return (
            GateResult(
                gate="no_stale_data", passed=False,
                reason="published_at missing or unparseable",
            ),
            window,
        )
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=window)
    if pub < cutoff:
        age_days = ((now or datetime.now(timezone.utc)) - pub).days
        return (
            GateResult(
                gate="no_stale_data", passed=False,
                reason=f"article is {age_days}d old; freshness window for "
                       f"{event_id or 'default'} is {window}d",
            ),
            window,
        )
    return (GateResult(gate="no_stale_data", passed=True), window)


# ---------------------------------------------------------------------------
# Gate 4 — polarity_coherent
# ---------------------------------------------------------------------------


def gate_polarity_coherent(
    insight: dict[str, Any],
    verifier_warnings: Iterable[str] | None = None,
) -> GateResult:
    """Pass if no coherence warning was raised by Phase 12.4 verifier
    AND the insight does not carry the ``low_confidence_classification``
    flag (Phase 13 S4)."""
    if insight.get("low_confidence_classification"):
        return GateResult(
            gate="polarity_coherent", passed=False,
            reason="low_confidence_classification flag set by verifier",
        )
    warnings = list(verifier_warnings or insight.get("warnings") or [])
    coherence_hits = [
        w for w in warnings
        if "coherence" in str(w).lower()
        or "narrative" in str(w).lower() and "mismatch" in str(w).lower()
    ]
    if coherence_hits:
        return GateResult(
            gate="polarity_coherent", passed=False,
            reason=f"coherence warning(s): {len(coherence_hits)}",
        )
    return GateResult(gate="polarity_coherent", passed=True)


# ---------------------------------------------------------------------------
# Gate 5 — numeric_consistent
# ---------------------------------------------------------------------------


def gate_numeric_consistent(
    insight: dict[str, Any],
) -> tuple[GateResult, float | None]:
    """Pass if cross-section ₹ drift is below DRIFT_TOLERANCE_PCT.

    Reuses the existing Phase 12.5 verifier helper so the CFO surface
    sees the same canonical-exposure threshold as the writer path.
    Returns (result, canonical_exposure_cr). The canonical figure is
    surfaced on the report so the UI / audit log can show it.
    """
    try:
        from engine.analysis.output_verifier import verify_cross_section_consistency
        canonical, drift_warnings = verify_cross_section_consistency(
            insight, tolerance_pct=DRIFT_TOLERANCE_PCT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("cross-section consistency check failed: %s", exc)
        return (
            GateResult(
                gate="numeric_consistent", passed=False,
                reason=f"verifier failure: {exc.__class__.__name__}",
            ),
            None,
        )
    if drift_warnings:
        return (
            GateResult(
                gate="numeric_consistent", passed=False,
                reason=f"₹ drift exceeds {DRIFT_TOLERANCE_PCT*100:.0f}%: "
                       f"{len(drift_warnings)} field(s) inconsistent",
            ),
            canonical,
        )
    return (GateResult(gate="numeric_consistent", passed=True), canonical)


# ---------------------------------------------------------------------------
# Gate 6 — stakeholder_polarity_matched
# ---------------------------------------------------------------------------

# Phrases in CEO stakeholder_map that flag wrong polarity (negative
# stance language when the event is positive, or vice-versa).
_NEGATIVE_STANCE_TOKENS = (
    "penalty", "fine", "scn", "show cause", "moratorium", "downgrade",
    "investigation", "violation", "breach",
)
_POSITIVE_STANCE_TOKENS = (
    "upgrade", "leader", "milestone", "expedited", "premium", "uplift",
    "leadership",
)


def gate_stakeholder_polarity_matched(
    perspectives: dict[str, Any] | None,
    event_polarity: str,
) -> GateResult:
    """Pass if every stakeholder_map entry's stance language matches the
    event polarity. Phase 15 polarity-aware SPARQL should already prevent
    leakage, but this gate is the final safety net at the CFO surface.

    Skipped (PASS) when the CEO perspective has no stakeholder_map (e.g.
    on a low-tier article where the dedicated generator wasn't run)."""
    if event_polarity not in {"positive", "negative"}:
        return GateResult(gate="stakeholder_polarity_matched", passed=True)

    ceo = (perspectives or {}).get("ceo") or {}
    stakeholders = ceo.get("stakeholder_map")
    if not isinstance(stakeholders, list) or not stakeholders:
        return GateResult(gate="stakeholder_polarity_matched", passed=True,
                          reason="no stakeholder_map present")

    forbidden = (
        _NEGATIVE_STANCE_TOKENS if event_polarity == "positive"
        else _POSITIVE_STANCE_TOKENS
    )
    leakage: list[str] = []
    for entry in stakeholders:
        if not isinstance(entry, dict):
            continue
        text = " ".join(
            str(v) for v in entry.values() if isinstance(v, str)
        ).lower()
        for token in forbidden:
            if token in text:
                leakage.append(
                    f"{entry.get('stakeholder', '?')}: '{token}' on a "
                    f"{event_polarity} event"
                )
                break
    if leakage:
        return GateResult(
            gate="stakeholder_polarity_matched", passed=False,
            reason="; ".join(leakage[:3]),
        )
    return GateResult(gate="stakeholder_polarity_matched", passed=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_preflight(
    insight: dict[str, Any] | None,
    *,
    perspectives: dict[str, Any] | None,
    framework_codes: Iterable[str] | None,
    published_at: str | None,
    event_id: str | None,
    event_polarity: str = "neutral",
    verifier_warnings: Iterable[str] | None = None,
    article_id: str | None = None,
    company_slug: str | None = None,
    now: datetime | None = None,
    log_to_audit: bool = True,
) -> PreflightReport:
    """Run all 6 gates on a finished insight.

    ``insight`` is the dict shape from :class:`engine.analysis.insight_generator.DeepInsight.to_dict`.

    When ``article_id`` and ``company_slug`` are passed, each gate result
    is mirrored to ``data/audit/preflight_log.jsonl`` via
    :func:`engine.audit.append_preflight`. Set ``log_to_audit=False`` to
    skip (useful for tests + dry-run scenarios).

    REJECTED articles (no insight produced) auto-fail all gates.
    """
    if not insight:
        # No deep insight → no CFO surface. Return all-fail.
        results = [
            GateResult(gate=g, passed=False, reason="insight not generated")
            for g in ALL_GATES
        ]
        report = PreflightReport(passed=False, gate_results=results)
        _maybe_log(report, article_id, company_slug, log_to_audit)
        return report

    results: list[GateResult] = []

    # Gate 1
    results.append(gate_financial_impact_quantified(insight))
    # Gate 2
    results.append(gate_framework_mapped(framework_codes))
    # Gate 3
    g3, window = gate_no_stale_data(published_at, event_id, now=now)
    results.append(g3)
    # Gate 4
    results.append(gate_polarity_coherent(insight, verifier_warnings))
    # Gate 5
    g5, canonical = gate_numeric_consistent(insight)
    results.append(g5)
    # Gate 6
    results.append(
        gate_stakeholder_polarity_matched(perspectives, event_polarity)
    )

    passed = all(r.passed for r in results)
    report = PreflightReport(
        passed=passed,
        gate_results=results,
        canonical_exposure_cr=canonical,
        freshness_window_days=window,
    )
    _maybe_log(report, article_id, company_slug, log_to_audit)
    return report


def _maybe_log(
    report: PreflightReport,
    article_id: str | None,
    company_slug: str | None,
    log_to_audit: bool,
) -> None:
    if not (log_to_audit and article_id and company_slug):
        return
    try:
        from engine import audit as _audit
        for r in report.gate_results:
            _audit.append_preflight(
                r.gate,  # type: ignore[arg-type]
                article_id=article_id,
                company_slug=company_slug,
                perspective="cfo",
                passed=r.passed,
                reason=r.reason or None,
                extra={"overall_passed": report.passed},
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("preflight audit append failed (non-fatal): %s", exc)
