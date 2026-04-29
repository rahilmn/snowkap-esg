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
    # Per-article details
    results: list[ArticleResult]

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
        result.event_id = pipe.event.event_id
        result.event_keywords_matched = len(pipe.event.matched_keywords)
        result.tier = pipe.tier
        result.ontology_queries = pipe.ontology_query_count

        full_payload: dict[str, Any] = {
            "title": pipe.title,
            "tier": pipe.tier,
            "event": pipe.event.event_id,
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

        # Stage 11/12 — only need CFO + recs for the harness
        cfo = transform_for_perspective(insight, pipe, "cfo")
        recs = generate_recommendations(insight, pipe, company)
        full_payload["cfo_headline"] = getattr(cfo, "headline", "") or (cfo.get("headline", "") if isinstance(cfo, dict) else "")
        full_payload["recs"] = [
            {"title": r.title, "priority": r.priority, "framework_section": r.framework_section}
            for r in (recs.recommendations or [])
        ]
        result.rec_count = len(recs.recommendations or [])

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

    fail_pct = summary.fail_rate * 100
    if fail_pct > args.slo_fail_pct:
        print(
            f"❌ FAIL: fail rate {fail_pct:.1f}% exceeds SLO {args.slo_fail_pct:.0f}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
