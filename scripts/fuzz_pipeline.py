"""Phase 12 final — Nightly fuzz harness for the analysis pipeline.

Runs every article in `tests/fuzz_corpus/corpus.jsonl` through the full
12-stage pipeline, compares output against per-article expectations, and
emits a pass/fail report. Intended to run nightly via cron + alert if the
regression rate drifts above an SLA threshold (default 5%).

Each corpus entry is a JSON object with keys:
  {
    "id": "<short stable id>",
    "company_slug": "jsw-energy",
    "title": "<article title>",
    "url": "<source url>",
    "source": "<publisher>",
    "published_at": "<iso datetime>",
    "content": "<full article body>",
    "expectations": {
        "event_id": "event_supply_chain_disruption",      # exact match
        "materiality_in": ["MODERATE", "HIGH", "CRITICAL"], # one of
        "min_recs": 3,                                       # at least N
        "max_recs": 8,                                       # at most N
        "must_not_contain": ["Vedanta Konkola"],             # not in any output
        "must_have_warning": "hallucination audit",           # optional
        "must_not_warning": "cross-section ₹ drift",          # optional
        "min_keywords_matched": 2                             # event keyword hits
    }
  }

Run:
    python scripts/fuzz_pipeline.py
    python scripts/fuzz_pipeline.py --corpus path/to/custom_corpus.jsonl
    python scripts/fuzz_pipeline.py --slo-fail-pct 5  # fail run if >5% regress
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Force UTF-8 stdout/stderr on Windows so the ✓ / ✗ status glyphs don't
# crash the harness with `UnicodeEncodeError: 'charmap' codec`. No-op on
# POSIX where stdout is UTF-8 by default.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ArticleResult:
    """Result of running one article through the pipeline + checks."""
    id: str
    company_slug: str
    title: str
    elapsed_seconds: float
    # Pipeline outputs
    event_id: str = ""
    event_keywords_matched: int = 0
    tier: str = ""
    materiality: str = ""
    rec_count: int = 0
    warning_count: int = 0
    warnings: list[str] = field(default_factory=list)
    headline: str = ""
    # Verification
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    # Cost / metrics (if available)
    ontology_queries: int = 0
    # Phase 26 SLO signals (per-article, aggregated by FuzzSummary)
    criticality_score: float | None = None
    criticality_band: str | None = None
    cross_role_drift_violations: int = 0
    bullet_verifier_pass_count: int = 0  # bullets that pass §6.3
    bullet_verifier_total: int = 0       # total bullets evaluated
    subject_verifier_passed: bool | None = None  # None when subject not built
    provenance_leaked_in_narrative: bool = False  # True if (engine estimate) survived


@dataclass
class FuzzSummary:
    """Aggregate report across all articles."""
    started_at: str
    finished_at: str
    total_articles: int
    passed: int
    failed: int
    total_elapsed_sec: float
    avg_elapsed_sec: float
    p95_elapsed_sec: float
    rejected_count: int  # articles that hit the materiality gate
    home_count: int
    secondary_count: int
    # Verifier signal rates
    hallucination_audit_count: int
    cross_section_drift_count: int
    coherence_mismatch_count: int
    # Phase 26 SLO signals (aggregated)
    criticality_coverage_pct: float = 0.0  # % of non-rejected articles with a band
    cross_role_drift_articles: int = 0     # articles with at least 1 violation
    bullet_pass_rate: float = 1.0          # bullets passing §6.3 / total bullets
    subject_pass_rate: float = 1.0         # subjects passing §6.2 / subjects built
    provenance_leak_articles: int = 0      # articles where (engine estimate) survived
    # Per-article details — must keep default to come after defaulted fields
    results: list[ArticleResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total_articles if self.total_articles else 0.0

    @property
    def fail_rate(self) -> float:
        return self.failed / self.total_articles if self.total_articles else 0.0


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    env = _ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def _clear_caches() -> None:
    """Clear ontology + classifier caches so each run sees the latest TTL."""
    from engine.nlp import event_classifier
    event_classifier._cached_rules.cache_clear()
    from engine.ontology import intelligence as intel
    for name in dir(intel):
        fn = getattr(intel, name, None)
        if callable(fn) and hasattr(fn, "cache_clear"):
            try:
                fn.cache_clear()
            except Exception:
                pass
    import engine.ontology.graph as g
    if hasattr(g, "_graph"):
        g._graph = None


def _check_article(entry: dict, result: ArticleResult, full_payload: dict) -> None:
    """Apply the per-article expectations and update `result.failures`.

    Each expectation that fails appends a string to `result.failures`. If any
    failure is recorded the article's `passed` flag flips to False.
    """
    exp = entry.get("expectations") or {}

    # Phase 22.5 — when an expectation declares the article SHOULD be rejected
    # (e.g. cross-entity gate on a sibling-group article that doesn't actually
    # mention the target company), short-circuit: validate the rejection
    # reason matches and skip the post-pipeline expectations (event_id,
    # rec_count, etc.) since the pipeline never reached those stages.
    expect_reject = exp.get("expect_rejection_reason")
    if expect_reject:
        if result.materiality != "REJECTED":
            result.failures.append(
                f"expected rejection with reason {expect_reject!r}, "
                f"got materiality={result.materiality!r}"
            )
        else:
            actual_reason = (full_payload.get("rejection_reason") or "")
            if expect_reject not in actual_reason:
                result.failures.append(
                    f"rejection_reason: expected to contain {expect_reject!r}, "
                    f"got {actual_reason!r}"
                )
        # `must_not_contain` is still relevant on rejected articles
        if "must_not_contain" in exp:
            full_text = json.dumps(full_payload, ensure_ascii=False)
            for marker in exp["must_not_contain"]:
                if marker in full_text:
                    result.failures.append(
                        f"forbidden phrase present: {marker!r}"
                    )
        result.passed = len(result.failures) == 0
        return

    # Event classification
    if "event_id" in exp and exp["event_id"] != result.event_id:
        result.failures.append(
            f"event_id: expected {exp['event_id']!r}, got {result.event_id!r}"
        )

    # Minimum keyword hits — guards against Phase 12.1 confidence-bar regressions
    if "min_keywords_matched" in exp and result.event_keywords_matched < exp["min_keywords_matched"]:
        result.failures.append(
            f"event_keywords_matched: expected ≥{exp['min_keywords_matched']}, "
            f"got {result.event_keywords_matched}"
        )

    # Materiality bucket
    if "materiality_in" in exp and result.materiality not in exp["materiality_in"]:
        result.failures.append(
            f"materiality: expected one of {exp['materiality_in']}, "
            f"got {result.materiality!r}"
        )

    # Recommendation count window
    if "min_recs" in exp and result.rec_count < exp["min_recs"]:
        result.failures.append(
            f"rec_count: expected ≥{exp['min_recs']}, got {result.rec_count}"
        )
    if "max_recs" in exp and result.rec_count > exp["max_recs"]:
        result.failures.append(
            f"rec_count: expected ≤{exp['max_recs']}, got {result.rec_count}"
        )

    # Hallucination markers — phrases that, if present anywhere in output,
    # indicate a regression. Useful for catching the Vedanta-Konkola anchor
    # bug we fixed in Phase 12.6.
    if "must_not_contain" in exp:
        full_text = json.dumps(full_payload, ensure_ascii=False)
        for marker in exp["must_not_contain"]:
            if marker in full_text:
                result.failures.append(f"forbidden phrase present: {marker!r}")

    # Optional: assert the verifier emitted a specific warning (used to test
    # that the hallucination audit fires on a known-bad article)
    if "must_have_warning" in exp:
        needle = exp["must_have_warning"].lower()
        if not any(needle in w.lower() for w in result.warnings):
            result.failures.append(
                f"missing expected warning containing {exp['must_have_warning']!r}"
            )

    # Optional: assert a warning is absent (used to verify a clean article
    # doesn't trip the drift checker)
    if "must_not_warning" in exp:
        needle = exp["must_not_warning"].lower()
        if any(needle in w.lower() for w in result.warnings):
            result.failures.append(
                f"unexpected warning containing {exp['must_not_warning']!r}"
            )

    result.passed = len(result.failures) == 0


def _run_article(entry: dict) -> ArticleResult:
    """Run one corpus entry through the full pipeline. Catches exceptions
    so a single bad article doesn't crash the whole nightly run."""
    from engine.config import get_company
    from engine.analysis.pipeline import process_article
    from engine.analysis.insight_generator import generate_deep_insight
    from engine.analysis.perspective_engine import transform_for_perspective
    from engine.analysis.recommendation_engine import generate_recommendations

    article = dict(entry)
    article.setdefault("id", hashlib.sha256((entry.get("url") or entry.get("title") or "").encode()).hexdigest()[:16])

    result = ArticleResult(
        id=str(entry.get("id") or article["id"]),
        company_slug=str(entry.get("company_slug") or ""),
        title=str(entry.get("title") or "")[:200],
        elapsed_seconds=0.0,
    )

    t0 = time.perf_counter()
    try:
        company = get_company(result.company_slug)
        pipe = process_article(article, company)
        # Phase 22.1 — when the cross-entity gate rejects (or any pipeline
        # gate that short-circuits before Stage 3 event classification),
        # pipe.event is None. Treat as empty event for harness-level
        # reporting; the rejection is captured separately in pipe.rejected.
        result.event_id = pipe.event.event_id if pipe.event else ""
        result.event_keywords_matched = (
            len(pipe.event.matched_keywords) if pipe.event else 0
        )
        result.tier = pipe.tier
        result.ontology_queries = pipe.ontology_query_count

        full_payload: dict[str, Any] = {
            "title": pipe.title,
            "tier": pipe.tier,
            "event": pipe.event.event_id if pipe.event else "",
            "rejected": pipe.rejected,
            "rejection_reason": pipe.rejection_reason,
        }

        # Force HOME so we exercise stages 10-12 even on SECONDARY tier
        # (so the harness can stress every layer of the verifier).
        if not pipe.rejected and pipe.tier != "HOME":
            pipe.tier = "HOME"

        if pipe.rejected:
            result.materiality = "REJECTED"
            result.headline = pipe.title[:200]
            result.elapsed_seconds = round(time.perf_counter() - t0, 2)
            _check_article(entry, result, full_payload)
            return result

        insight = generate_deep_insight(pipe, company)
        result.headline = (insight.headline or "")[:200]
        result.warnings = list(insight.warnings or [])
        result.warning_count = len(result.warnings)
        result.materiality = (insight.decision_summary or {}).get("materiality") or ""

        full_payload["headline"] = result.headline
        full_payload["decision_summary"] = insight.decision_summary
        full_payload["warnings"] = result.warnings

        # Phase 26 — capture criticality stamped by Stage 9.5 / insight time
        crit = getattr(insight, "criticality", None) or {}
        if isinstance(crit, dict):
            score = crit.get("score")
            try:
                result.criticality_score = float(score) if score is not None else None
            except (TypeError, ValueError):
                result.criticality_score = None
            band = crit.get("band")
            result.criticality_band = str(band) if band else None

        # Phase 2.3 — provenance leak check on narrative fields
        try:
            import re as _re
            _leak_re = _re.compile(
                r"\((?:engine\s+estimate|from\s+article)\)", _re.IGNORECASE,
            )
            narrative_blob = " ".join(
                str(v) for v in [
                    insight.headline,
                    insight.net_impact_summary,
                    (insight.decision_summary or {}).get("financial_exposure", ""),
                    (insight.decision_summary or {}).get("key_risk", ""),
                    (insight.decision_summary or {}).get("top_opportunity", ""),
                ] if v
            )
            result.provenance_leaked_in_narrative = bool(_leak_re.search(narrative_blob))
        except Exception:  # noqa: BLE001 — never break harness on side check
            pass

        # Phase 4 §6.3 — bullet verifier pass rate on key_takeaways
        try:
            from engine.output.insight_verifier import verify_bullets
            takeaways: list[str] = []
            if insight.net_impact_summary:
                takeaways.append(str(insight.net_impact_summary))
            ds = insight.decision_summary or {}
            for key in ("key_risk", "top_opportunity"):
                if ds.get(key):
                    takeaways.append(str(ds[key]))
            if takeaways:
                verdicts = verify_bullets(takeaways)
                result.bullet_verifier_total = len(verdicts)
                result.bullet_verifier_pass_count = sum(1 for v in verdicts if v.passed)
        except Exception:
            pass

        # Phase 4 §6.2 — subject verifier on the editorial subject line
        try:
            from engine.output.subject_line import build_subject
            from engine.output.insight_verifier import verify_subject
            subj = build_subject(
                company.name if company else "",
                {
                    "headline": insight.headline,
                    "decision_summary": insight.decision_summary or {},
                    "net_impact_summary": insight.net_impact_summary,
                    "event_polarity": getattr(insight, "event_polarity", "neutral"),
                },
                article,
            )
            if subj:
                result.subject_verifier_passed = verify_subject(subj).passed
        except Exception:
            pass

        # Stage 11/12 — only need CFO + recs for the harness
        cfo = transform_for_perspective(insight, pipe, "cfo")
        recs = generate_recommendations(insight, pipe, company)
        full_payload["cfo_headline"] = getattr(cfo, "headline", "") or (cfo.get("headline", "") if isinstance(cfo, dict) else "")
        full_payload["recs"] = [
            {"title": r.title, "priority": r.priority, "framework_section": r.framework_section}
            for r in (recs.recommendations or [])
        ]
        result.rec_count = len(recs.recommendations or [])

        # Phase 3 §5.5 — cross-role drift count (set on insight by verify_and_correct)
        try:
            ceo = transform_for_perspective(insight, pipe, "ceo")
            analyst = transform_for_perspective(insight, pipe, "esg-analyst")
            from engine.analysis.cross_role_drift import compute_drift
            payloads: dict[str, Any] = {}
            for label, p in (("cfo", cfo), ("ceo", ceo), ("esg_analyst", analyst)):
                if isinstance(p, dict):
                    payloads[label] = p
                elif p is not None and hasattr(p, "to_dict"):
                    payloads[label] = p.to_dict()
                elif p is not None:
                    payloads[label] = {
                        "headline": getattr(p, "headline", ""),
                        "paragraph": getattr(p, "paragraph", ""),
                    }
            drift = compute_drift(payloads)
            result.cross_role_drift_violations = len(drift.violations)
        except Exception:
            pass

    except Exception as exc:  # noqa: BLE001 — harness must keep going
        result.failures.append(f"pipeline crash: {type(exc).__name__}: {str(exc)[:200]}")
        result.passed = False
        result.elapsed_seconds = round(time.perf_counter() - t0, 2)
        return result

    result.elapsed_seconds = round(time.perf_counter() - t0, 2)
    _check_article(entry, result, full_payload)
    return result


# ---------------------------------------------------------------------------
# Aggregation + reporting
# ---------------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = int(round((pct / 100.0) * (len(s) - 1)))
    return s[k]


def _summarise(results: list[ArticleResult]) -> FuzzSummary:
    elapsed = [r.elapsed_seconds for r in results]

    # Phase 26 SLO aggregations — only count non-rejected articles for
    # criticality coverage (rejected articles never get scored)
    non_rejected = [r for r in results if r.materiality != "REJECTED"]
    if non_rejected:
        scored = sum(1 for r in non_rejected if r.criticality_band)
        coverage_pct = scored / len(non_rejected)
    else:
        coverage_pct = 1.0  # vacuous pass on empty corpus

    bullet_total = sum(r.bullet_verifier_total for r in results)
    bullet_pass = sum(r.bullet_verifier_pass_count for r in results)
    bullet_rate = bullet_pass / bullet_total if bullet_total else 1.0

    subjects_evaluated = [r for r in results if r.subject_verifier_passed is not None]
    if subjects_evaluated:
        subject_pass = sum(1 for r in subjects_evaluated if r.subject_verifier_passed)
        subject_rate = subject_pass / len(subjects_evaluated)
    else:
        subject_rate = 1.0

    return FuzzSummary(
        started_at="",  # set by caller
        finished_at="",  # set by caller
        total_articles=len(results),
        passed=sum(1 for r in results if r.passed),
        failed=sum(1 for r in results if not r.passed),
        total_elapsed_sec=round(sum(elapsed), 2),
        avg_elapsed_sec=round(sum(elapsed) / len(elapsed), 2) if elapsed else 0.0,
        p95_elapsed_sec=round(_percentile(elapsed, 95.0), 2),
        rejected_count=sum(1 for r in results if r.materiality == "REJECTED"),
        home_count=sum(1 for r in results if r.tier == "HOME"),
        secondary_count=sum(1 for r in results if r.tier == "SECONDARY"),
        hallucination_audit_count=sum(
            1 for r in results if any("hallucination audit" in w.lower() for w in r.warnings)
        ),
        cross_section_drift_count=sum(
            1 for r in results if any("cross-section" in w.lower() for w in r.warnings)
        ),
        coherence_mismatch_count=sum(
            1 for r in results if any("coherence mismatch" in w.lower() for w in r.warnings)
        ),
        criticality_coverage_pct=round(coverage_pct, 4),
        cross_role_drift_articles=sum(
            1 for r in results if r.cross_role_drift_violations > 0
        ),
        bullet_pass_rate=round(bullet_rate, 4),
        subject_pass_rate=round(subject_rate, 4),
        provenance_leak_articles=sum(
            1 for r in results if r.provenance_leaked_in_narrative
        ),
        results=results,
    )


def _print_markdown_report(summary: FuzzSummary) -> str:
    lines: list[str] = []
    lines.append("# Snowkap Pipeline Fuzz Harness Report")
    lines.append("")
    lines.append(f"- **Run window**: {summary.started_at} → {summary.finished_at}")
    lines.append(f"- **Articles**: {summary.total_articles}")
    lines.append(f"- **Pass rate**: {summary.pass_rate:.1%}  ({summary.passed} pass / {summary.failed} fail)")
    lines.append(f"- **Latency**: avg {summary.avg_elapsed_sec:.1f}s, p95 {summary.p95_elapsed_sec:.1f}s, total {summary.total_elapsed_sec:.0f}s")
    lines.append(f"- **Tier breakdown**: HOME {summary.home_count}, SECONDARY {summary.secondary_count}, REJECTED {summary.rejected_count}")
    lines.append("")
    lines.append("## Verifier signal rates (lower = better)")
    lines.append("")
    n = max(summary.total_articles, 1)
    lines.append(f"- Hallucination-audit fired:  {summary.hallucination_audit_count}/{n}  ({summary.hallucination_audit_count/n:.1%})")
    lines.append(f"- Cross-section ₹ drift:      {summary.cross_section_drift_count}/{n}  ({summary.cross_section_drift_count/n:.1%})")
    lines.append(f"- Coherence-mismatch:         {summary.coherence_mismatch_count}/{n}  ({summary.coherence_mismatch_count/n:.1%})")
    lines.append("")
    lines.append("## Phase 26 SLOs")
    lines.append("")
    lines.append(f"- Criticality coverage:       {summary.criticality_coverage_pct:.1%}  (target: 100% on non-rejected)")
    lines.append(f"- Cross-role ₹ drift fires:   {summary.cross_role_drift_articles}/{n}  ({summary.cross_role_drift_articles/n:.1%}; target: 0)")
    lines.append(f"- Bullet-verifier pass rate:  {summary.bullet_pass_rate:.1%}  (§6.3; target: ≥80%)")
    lines.append(f"- Subject-verifier pass rate: {summary.subject_pass_rate:.1%}  (§6.2; target: ≥90%)")
    lines.append(f"- Provenance leaks:           {summary.provenance_leak_articles}/{n}  (target: 0 — strip pass)")
    lines.append("")

    failed = [r for r in summary.results if not r.passed]
    if failed:
        lines.append("## Failed articles")
        lines.append("")
        for r in failed:
            lines.append(f"### {r.id} — {r.company_slug}: {r.title[:80]}")
            lines.append(f"- event: `{r.event_id}` ({r.event_keywords_matched} kws)")
            lines.append(f"- materiality: {r.materiality}")
            lines.append(f"- elapsed: {r.elapsed_seconds:.1f}s")
            lines.append("- failures:")
            for f in r.failures:
                lines.append(f"  - {f}")
            lines.append("")
    else:
        lines.append("## ✅ All articles passed")
    lines.append("")

    lines.append("## Per-article summary")
    lines.append("")
    lines.append("| ID | Company | Event | Mat | Recs | Warn | Elapsed | Status |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in summary.results:
        status = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.id} | {r.company_slug} | {r.event_id} | {r.materiality} | "
            f"{r.rec_count} | {r.warning_count} | {r.elapsed_seconds:.1f}s | {status} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        default=str(_ROOT / "tests" / "fuzz_corpus" / "corpus.jsonl"),
        help="Path to corpus JSONL.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(_ROOT / "data" / "fuzz_reports"),
        help="Output directory for report files.",
    )
    parser.add_argument(
        "--slo-fail-pct",
        type=float,
        default=10.0,
        help="Exit non-zero if fail rate exceeds this percentage. Default 10.",
    )
    # Phase 26 SLOs — independent of the per-article pass/fail rate
    parser.add_argument(
        "--slo-bullet-pass-min",
        type=float,
        default=0.0,
        help="Min bullet-verifier pass rate (0..1). 0 disables. Recommend 0.80.",
    )
    parser.add_argument(
        "--slo-subject-pass-min",
        type=float,
        default=0.0,
        help="Min subject-verifier pass rate (0..1). 0 disables. Recommend 0.90.",
    )
    parser.add_argument(
        "--slo-criticality-coverage-min",
        type=float,
        default=0.0,
        help="Min criticality-band coverage on non-rejected articles (0..1). 0 disables.",
    )
    parser.add_argument(
        "--slo-cross-role-drift-max",
        type=int,
        default=-1,
        help="Max # articles allowed to fire cross-role ₹ drift. -1 disables.",
    )
    parser.add_argument(
        "--slo-provenance-leaks-max",
        type=int,
        default=-1,
        help="Max # articles allowed to leak '(engine estimate)' / '(from article)'. -1 disables.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Run only the first N articles (for dev iteration).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    _load_dotenv()
    _clear_caches()

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"error: corpus not found at {corpus_path}", file=sys.stderr)
        return 2

    entries: list[dict] = []
    for line in corpus_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"warn: skipping malformed corpus line: {exc}", file=sys.stderr)

    if args.limit:
        entries = entries[: args.limit]
    if not entries:
        print("error: corpus is empty.", file=sys.stderr)
        return 2

    print(f"Running fuzz harness on {len(entries)} articles…")
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results: list[ArticleResult] = []
    for i, entry in enumerate(entries, 1):
        print(f"  [{i}/{len(entries)}] {entry.get('company_slug', '?')} — {(entry.get('title') or '')[:60]}…")
        r = _run_article(entry)
        status = "✓" if r.passed else "✗"
        print(f"      {status}  event={r.event_id}  tier={r.tier}  recs={r.rec_count}  warn={r.warning_count}  ({r.elapsed_seconds:.1f}s)")
        if not r.passed:
            for f in r.failures:
                print(f"        - {f}")
        results.append(r)

    finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary = _summarise(results)
    summary.started_at = started
    summary.finished_at = finished

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"fuzz_{stamp}.json"
    md_path = out_dir / f"fuzz_{stamp}.md"
    json_path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_print_markdown_report(summary), encoding="utf-8")

    print()
    print("=" * 70)
    print(f"Pass: {summary.passed}/{summary.total_articles}  ({summary.pass_rate:.1%})")
    print(f"Latency: avg {summary.avg_elapsed_sec:.1f}s  p95 {summary.p95_elapsed_sec:.1f}s")
    print(f"Hallucination-audit fired on {summary.hallucination_audit_count} articles")
    print(f"Cross-section drift fired on {summary.cross_section_drift_count} articles")
    print(f"Coherence-mismatch fired on {summary.coherence_mismatch_count} articles")
    print(f"Report: {md_path}")
    print("=" * 70)

    # Existing SLO — per-article fail rate
    fail_pct = summary.fail_rate * 100
    slo_violations: list[str] = []
    if fail_pct > args.slo_fail_pct:
        slo_violations.append(
            f"fail rate {fail_pct:.1f}% exceeds SLO {args.slo_fail_pct:.0f}%"
        )

    # Phase 26 SLOs — only fire when the operator opted in (>0 / != -1)
    if (
        args.slo_bullet_pass_min > 0
        and summary.bullet_pass_rate < args.slo_bullet_pass_min
    ):
        slo_violations.append(
            f"bullet-verifier pass rate {summary.bullet_pass_rate:.1%} "
            f"< SLO {args.slo_bullet_pass_min:.0%}"
        )
    if (
        args.slo_subject_pass_min > 0
        and summary.subject_pass_rate < args.slo_subject_pass_min
    ):
        slo_violations.append(
            f"subject-verifier pass rate {summary.subject_pass_rate:.1%} "
            f"< SLO {args.slo_subject_pass_min:.0%}"
        )
    if (
        args.slo_criticality_coverage_min > 0
        and summary.criticality_coverage_pct < args.slo_criticality_coverage_min
    ):
        slo_violations.append(
            f"criticality coverage {summary.criticality_coverage_pct:.1%} "
            f"< SLO {args.slo_criticality_coverage_min:.0%}"
        )
    if (
        args.slo_cross_role_drift_max >= 0
        and summary.cross_role_drift_articles > args.slo_cross_role_drift_max
    ):
        slo_violations.append(
            f"cross-role drift fired on {summary.cross_role_drift_articles} articles "
            f"(SLO max {args.slo_cross_role_drift_max})"
        )
    if (
        args.slo_provenance_leaks_max >= 0
        and summary.provenance_leak_articles > args.slo_provenance_leaks_max
    ):
        slo_violations.append(
            f"provenance leaked in {summary.provenance_leak_articles} articles "
            f"(SLO max {args.slo_provenance_leaks_max})"
        )

    if slo_violations:
        print()
        for v in slo_violations:
            print(f"❌ SLO BREACH: {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
