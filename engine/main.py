"""CLI entry point for the Snowkap ESG Intelligence Engine.

Usage:
    python engine/main.py ingest --company <slug>
    python engine/main.py ingest --all
    python engine/main.py analyze --file <path> --company <slug>
    python engine/main.py analyze --prompt <path> --company <slug>
    python engine/main.py query --company <slug> --perspective <lens> --latest
    python engine/main.py stats

All commands honour the ontology-driven pipeline and write JSONB-compatible
insight files to ``data/outputs/{company-slug}/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow `python engine/main.py ...` to work without PYTHONPATH being set.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from engine.config import (  # noqa: E402
    Company,
    get_company,
    get_data_path,
    get_output_dir,
    load_companies,
)

logger = logging.getLogger("snowkap")


# ---------------------------------------------------------------------------
# Logging setup — structlog if available, else stdlib logging
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO") -> None:
    level_int = getattr(logging, level.upper(), logging.INFO)
    try:
        import structlog

        logging.basicConfig(
            format="%(message)s",
            level=level_int,
        )
        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level_int),
            cache_logger_on_first_use=True,
        )
    except ImportError:
        logging.basicConfig(
            level=level_int,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )


# ---------------------------------------------------------------------------
# Shared engine runner
# ---------------------------------------------------------------------------


@dataclass
class ArticleRunSummary:
    article_id: str
    title: str
    tier: str
    rejected: bool
    impact_score: float | None
    recommendations: int
    ontology_queries: int
    elapsed_seconds: float
    files_written: int


def _run_article(article: dict[str, Any], company: Company) -> ArticleRunSummary:
    """Run a single article through the full 12-stage pipeline."""
    # Lazy imports keep CLI startup fast for `--help`
    from engine.analysis.insight_generator import generate_deep_insight
    from engine.analysis.perspective_engine import transform_for_perspective
    from engine.analysis.pipeline import process_article
    from engine.analysis.recommendation_engine import generate_recommendations
    from engine.output.writer import write_insight

    started = time.perf_counter()
    result = process_article(article, company)

    insight = None
    perspectives: dict = {}
    recs = None
    files = 0

    if not result.rejected:
        insight = generate_deep_insight(result, company)
        if insight:
            for lens in ("esg-analyst", "cfo", "ceo"):
                perspectives[lens] = transform_for_perspective(insight, result, lens)
            recs = generate_recommendations(insight, result, company)
            written = write_insight(result, insight, perspectives, recs)
            # Count non-None files
            d = written.to_dict()
            files = sum(1 for k, v in d.items() if v and not isinstance(v, dict))
            files += len(d.get("perspectives") or {})

    return ArticleRunSummary(
        article_id=result.article_id,
        title=result.title[:100],
        tier=result.tier,
        rejected=result.rejected,
        impact_score=insight.impact_score if insight else None,
        recommendations=len(recs.recommendations) if recs and not recs.do_nothing else 0,
        ontology_queries=result.ontology_query_count,
        elapsed_seconds=round(time.perf_counter() - started, 2),
        files_written=files,
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> int:
    """Fetch news and run the full analysis pipeline."""
    from engine.ingestion.news_fetcher import fetch_for_company

    if args.all:
        companies = load_companies()
    else:
        if not args.company:
            print("error: --company <slug> or --all is required", file=sys.stderr)
            return 2
        companies = [get_company(args.company)]

    totals = {
        "companies": 0,
        "fetched": 0,
        "processed": 0,
        "home": 0,
        "secondary": 0,
        "rejected": 0,
        "files_written": 0,
        "total_elapsed_s": 0.0,
    }

    for company in companies:
        print(f"\n=== {company.name} ({company.slug}) ===")
        fresh = fetch_for_company(company, max_per_query=args.max)
        print(f"  fetched {len(fresh)} new articles")
        totals["companies"] += 1
        totals["fetched"] += len(fresh)

        for idx, article in enumerate(fresh, 1):
            article_dict = {
                "id": article.id,
                "title": article.title,
                "content": article.content,
                "summary": article.summary,
                "source": article.source,
                "url": article.url,
                "published_at": article.published_at,
                "metadata": article.metadata,
            }
            if args.limit and idx > args.limit:
                print(f"  [limit reached: {args.limit} articles]")
                break
            print(
                f"  [{idx:02d}/{min(len(fresh), args.limit or len(fresh))}] {article.title[:70]}"
            )
            try:
                summary = _run_article(article_dict, company)
            except Exception as exc:  # noqa: BLE001
                logger.exception("article pipeline failed: %s", article.id)
                print(f"      FAILED: {exc}")
                continue
            totals["processed"] += 1
            totals["total_elapsed_s"] += summary.elapsed_seconds
            totals["files_written"] += summary.files_written
            if summary.tier == "HOME":
                totals["home"] += 1
            elif summary.tier == "SECONDARY":
                totals["secondary"] += 1
            else:
                totals["rejected"] += 1
            print(
                f"      tier={summary.tier:9s} score={summary.impact_score or '-':>4} "
                f"recs={summary.recommendations} queries={summary.ontology_queries} "
                f"files={summary.files_written} time={summary.elapsed_seconds}s"
            )

    print("\n=== SUMMARY ===")
    for key, val in totals.items():
        print(f"  {key}: {val}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Run the pipeline on a local file or prompt for a specific company."""
    from engine.ingestion.file_parser import parse as parse_file
    from engine.ingestion.prompt_handler import normalize_file as parse_prompt_file
    from engine.ingestion.news_fetcher import _url_hash

    company = get_company(args.company)

    if args.file:
        parsed = parse_file(Path(args.file))
        article = {
            "id": _url_hash(args.file),
            "title": parsed.title,
            "content": parsed.content,
            "summary": parsed.content[:400],
            "source": parsed.filename,
            "url": f"file://{Path(args.file).resolve()}",
            "published_at": "",
            "metadata": parsed.metadata,
        }
    else:
        prompt = parse_prompt_file(Path(args.prompt))
        article = {
            "id": _url_hash(args.prompt),
            "title": prompt.title,
            "content": prompt.content,
            "summary": prompt.content[:400],
            "source": "user_prompt",
            "url": f"prompt://{Path(args.prompt).resolve()}",
            "published_at": "",
            "metadata": prompt.metadata,
        }

    print(f"Analyzing: {article['title']}")
    print(f"Company: {company.name}")

    summary = _run_article(article, company)
    print()
    print(f"tier={summary.tier}, score={summary.impact_score}, files={summary.files_written}")
    print(f"ontology_queries={summary.ontology_queries}, elapsed={summary.elapsed_seconds}s")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """Read insight files from data/outputs/ for a company."""
    company = get_company(args.company)
    lens = args.perspective

    if lens == "esg-analyst":
        folder = get_output_dir(company.slug) / "insights"
    else:
        folder = get_output_dir(company.slug) / "perspectives" / lens

    if not folder.exists():
        print(f"No insights found at {folder}", file=sys.stderr)
        return 1

    files = sorted(folder.glob("*.json"), reverse=True)
    if not files:
        print(f"No insights found for {company.slug}/{lens}", file=sys.stderr)
        return 1

    if args.latest:
        files = files[:1]
    else:
        files = files[: args.limit]

    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        print(f"\n=== {f.name} ===")
        if lens == "esg-analyst":
            insight = data.get("insight") or {}
            print(f"Headline: {insight.get('headline', '')}")
            print(f"Impact: {insight.get('impact_score', 'N/A')}")
            print(f"Materiality: {(insight.get('decision_summary') or {}).get('materiality', '')}")
        else:
            print(f"Headline: {data.get('headline', '')}")
            print(f"Do nothing: {data.get('do_nothing', False)}")
            print(f"Impact grid: {data.get('impact_grid', {})}")
            for bullet in data.get("what_matters", []):
                print(f"  - {bullet}")
            print(f"Action: {data.get('action', [])}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Print ontology + output statistics."""
    from engine.index.sqlite_index import stats as index_stats
    from engine.ontology.graph import get_graph

    graph = get_graph()
    stats = graph.stats()
    print("=== ONTOLOGY STATS ===")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    print("\n=== OUTPUT COUNTS (filesystem) ===")
    for company in load_companies():
        base = get_output_dir(company.slug)
        insight_count = len(list((base / "insights").glob("*.json")))
        print(f"  {company.slug}: {insight_count} insights")

    print("\n=== SQLITE INDEX STATS ===")
    try:
        idx = index_stats()
        print(f"  total: {idx['total']}")
        print(f"  by_tier: {idx['by_tier']}")
        print(f"  by_company: {idx['by_company']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (index not available: {exc})")
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    """Rebuild the SQLite index from all JSON files under data/outputs/."""
    from engine.index.reindex import reindex_all
    from engine.index.sqlite_index import stats as index_stats

    print("Reindexing SQLite from data/outputs/ ...")
    summary = reindex_all()
    for slug, count in summary.items():
        print(f"  {slug}: {count}")
    idx = index_stats()
    print(f"\nIndex total: {idx['total']}")
    print(f"By tier: {idx['by_tier']}")
    return 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snowkap-esg",
        description="Snowkap ESG Intelligence Engine — ontology-driven executive intelligence.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="Fetch news and run the analysis pipeline.")
    ingest.add_argument("--company", help="Company slug (e.g. adani-power).")
    ingest.add_argument("--all", action="store_true", help="Ingest for all 7 target companies.")
    ingest.add_argument("--max", type=int, default=None, help="Max articles per query.")
    ingest.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap total articles processed per company per run (controls LLM cost).",
    )

    analyze = subparsers.add_parser("analyze", help="Run the pipeline on a file or prompt.")
    analyze.add_argument("--company", required=True, help="Company slug.")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="Path to a PDF / Excel / image / text file.")
    source.add_argument("--prompt", help="Path to a text prompt file.")

    query = subparsers.add_parser("query", help="Read insights from data/outputs/.")
    query.add_argument("--company", required=True, help="Company slug.")
    query.add_argument(
        "--perspective",
        choices=["cfo", "ceo", "esg-analyst"],
        default="esg-analyst",
        help="Perspective lens to query.",
    )
    query.add_argument("--latest", action="store_true", help="Return only the most recent insight.")
    query.add_argument("--limit", type=int, default=5, help="Max insights to return.")

    subparsers.add_parser("stats", help="Print ontology + output statistics.")
    subparsers.add_parser("reindex", help="Rebuild SQLite index from JSON outputs.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(args.log_level)

    if args.command == "ingest":
        return cmd_ingest(args)
    if args.command == "analyze":
        return cmd_analyze(args)
    if args.command == "query":
        return cmd_query(args)
    if args.command == "stats":
        return cmd_stats(args)
    if args.command == "reindex":
        return cmd_reindex(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
