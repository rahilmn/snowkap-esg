"""Optional periodic ingestion via APScheduler.

Run with::

    python engine/scheduler.py

Defaults to hourly ingestion for all 7 target companies. Override with
``--interval-minutes`` or ``--cron-expr``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.main import cmd_ingest, setup_logging  # noqa: E402

logger = logging.getLogger(__name__)


class _IngestArgs:
    """Plain namespace matching what cmd_ingest expects."""

    def __init__(self, max_per_query: int | None, limit: int | None) -> None:
        self.all = True
        self.company = None
        self.max = max_per_query
        self.limit = limit


def run_ingest_job(max_per_query: int | None, limit: int | None) -> None:
    logger.info("scheduler: starting scheduled ingestion")
    cmd_ingest(_IngestArgs(max_per_query=max_per_query, limit=limit))
    logger.info("scheduler: finished scheduled ingestion")


def run_overnight_batch_job(
    *,
    fetch_per_tenant: int = 20,
    select_per_tenant: int = 3,
    workers: int = 4,
    tenant_slugs: list[str] | None = None,
) -> dict[str, int]:
    """Phase 25 W7 — overnight batch ingestion for the customer roster.

    Run nightly at 1am. For each customer tenant:
      1. Fetch ``fetch_per_tenant`` articles via existing news_fetcher
      2. Score all fetched via ``article_selector.select_top_n_for_pipeline``
      3. Run full pipeline (Stages 1-12) on the top ``select_per_tenant``
      4. Write outputs + index + per-night audit log

    Tenants are processed in parallel via ``ThreadPoolExecutor`` so the
    25-customer SLA (3 articles each, ready by 8am) hits its window.

    ``tenant_slugs=None`` defaults to all customer tenants discovered via
    ``engine.ontology.tenant_resolver.list_tenants()`` minus the ``_global``
    default + the original 7 target companies (those use the existing
    hourly ``run_ingest_job``). Pass an explicit list for testing.

    Returns counts dict: ``{tenants, fetched, selected, processed, errors}``.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timezone
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger.info(
        "scheduler: overnight batch starting (workers=%d, fetch_per_tenant=%d, "
        "select_per_tenant=%d)",
        workers, fetch_per_tenant, select_per_tenant,
    )

    # Resolve the customer tenants to process
    if tenant_slugs is None:
        tenant_slugs = _discover_batch_tenant_slugs()

    counts = {
        "tenants_attempted": len(tenant_slugs),
        "tenants_succeeded": 0,
        "articles_fetched": 0,
        "articles_selected": 0,
        "articles_passed_preflight": 0,
        "errors": 0,
    }
    error_log: list[dict] = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _process_one_tenant_overnight,
                slug=slug,
                fetch_per_tenant=fetch_per_tenant,
                select_per_tenant=select_per_tenant,
            ): slug
            for slug in tenant_slugs
        }
        for fut in as_completed(futures):
            slug = futures[fut]
            try:
                result = fut.result()
                counts["articles_fetched"] += result.get("fetched", 0)
                counts["articles_selected"] += result.get("selected", 0)
                counts["articles_passed_preflight"] += result.get("passed", 0)
                if result.get("ok"):
                    counts["tenants_succeeded"] += 1
                else:
                    counts["errors"] += 1
                    error_log.append({
                        "tenant_slug": slug,
                        "error_class": result.get("error_class", "unknown"),
                        "message": result.get("error_message", "")[:200],
                    })
            except Exception as exc:  # noqa: BLE001 — never let one tenant kill the batch
                logger.exception("scheduler: tenant %s overnight failed: %s", slug, exc)
                counts["errors"] += 1
                error_log.append({
                    "tenant_slug": slug,
                    "error_class": exc.__class__.__name__,
                    "message": str(exc)[:200],
                })

    completed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Write audit log entry
    try:
        from engine import audit as _audit
        _audit.append_overnight_run(
            started_at=started_at,
            completed_at=completed_at,
            tenants_attempted=counts["tenants_attempted"],
            tenants_succeeded=counts["tenants_succeeded"],
            articles_fetched=counts["articles_fetched"],
            articles_selected=counts["articles_selected"],
            articles_passed_preflight=counts["articles_passed_preflight"],
            errors=error_log if error_log else None,
            extra={
                "workers": workers,
                "fetch_per_tenant": fetch_per_tenant,
                "select_per_tenant": select_per_tenant,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler: overnight audit log append failed: %s", exc)

    logger.info("scheduler: overnight batch complete: %s", counts)
    return counts


def _discover_batch_tenant_slugs() -> list[str]:
    """Return the list of CUSTOMER tenant slugs to process tonight.

    Excludes:
      * ``_global`` (the default Layer 3 placeholder)
      * The original 7 target companies (they have their own hourly
        ``run_ingest_job`` and tenant-aware processing isn't needed
        because they all share the ``_global`` weights)

    Returns whatever's in ``data/ontology/tenants/<slug>/`` minus the
    excluded set, sorted alphabetically for deterministic ordering.
    """
    try:
        from engine.ontology.tenant_resolver import DEFAULT_TENANT, list_tenants
        all_tenants = list_tenants()
    except Exception:
        return []
    excluded = {DEFAULT_TENANT}
    # The 7 original target companies are loaded from companies.json, not
    # the tenants directory — but defensive against future renaming
    excluded.update({
        "icici-bank", "yes-bank", "idfc-first-bank", "waaree-energies",
        "singularity-amc", "adani-power", "jsw-energy",
    })
    return [t for t in all_tenants if t not in excluded]


def _process_one_tenant_overnight(
    *,
    slug: str,
    fetch_per_tenant: int,
    select_per_tenant: int,
) -> dict:
    """Process one tenant: fetch → select → ingest → return counts.

    Each ThreadPoolExecutor task calls this. Errors caught + returned as
    a structured dict so the batch run never crashes on one bad tenant.
    """
    from engine.ontology.tenant_resolver import active_tenant
    try:
        with active_tenant(slug):
            from engine.config import load_companies
            from engine.ingestion.news_fetcher import fetch_for_company
            from engine.analysis.article_selector import select_top_n_for_pipeline
            from engine.main import _run_article

            # Find the matching Company object by slug
            company = next(
                (c for c in load_companies() if c.slug == slug),
                None,
            )
            if company is None:
                return {
                    "ok": False, "error_class": "company_not_found",
                    "error_message": f"slug {slug} not in companies.json",
                    "fetched": 0, "selected": 0, "passed": 0,
                }

            articles = fetch_for_company(company, max_per_query=fetch_per_tenant)
            fetched_n = len(articles)
            if fetched_n == 0:
                return {
                    "ok": True, "fetched": 0, "selected": 0, "passed": 0,
                }

            # Pre-pipeline article selection — the cost lever
            selected_articles = select_top_n_for_pipeline(
                articles,
                n=select_per_tenant,
                company_slug=slug,
                primary_industry=getattr(company, "industry", None),
            )
            selected_n = len(selected_articles)

            # Run the full pipeline on each selected article
            passed_n = 0
            for art in selected_articles:
                try:
                    art_dict = _article_to_dict(art)
                    result = _run_article(art_dict, company)
                    if result and getattr(result, "tier", "") == "HOME":
                        passed_n += 1
                except Exception as exc:
                    logger.warning(
                        "_process_one_tenant_overnight: pipeline failed for "
                        "tenant=%s article=%r: %s",
                        slug, getattr(art, "id", "?"), exc,
                    )

            return {
                "ok": True,
                "fetched": fetched_n,
                "selected": selected_n,
                "passed": passed_n,
            }
    except Exception as exc:
        return {
            "ok": False,
            "error_class": exc.__class__.__name__,
            "error_message": str(exc)[:300],
            "fetched": 0, "selected": 0, "passed": 0,
        }


def _article_to_dict(article) -> dict:
    """Convert IngestedArticle dataclass to the dict shape _run_article expects."""
    from dataclasses import asdict
    try:
        return asdict(article)
    except TypeError:
        # Already a dict
        return dict(article) if hasattr(article, "__iter__") else {"id": str(article)}


def run_morning_digest_job() -> None:
    """Phase 25 W10 — 7:50am morning digest send to sales@snowkap.co.in.

    Composes the digest from last 24h CRITICAL/HIGH articles + the
    latest overnight batch summary, sends via Resend. Idempotent (safe
    to re-run for the same window — Resend dedups on subject + body
    hash for transactional sends).

    Feature-flagged: ``SNOWKAP_MORNING_DIGEST_ENABLED=0`` skips the run
    without removing the job from the scheduler.
    """
    try:
        from engine.output.digest_email import send_morning_digest
        result = send_morning_digest()
        logger.info("scheduler: morning digest -> %s", result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("scheduler: morning digest failed: %s", exc)


def run_promote_job() -> None:
    """Phase 19 — periodic discovery promoter.

    Drains the candidate buffer (`data/ontology/discovery_staging.json`),
    applies confidence + frequency thresholds, and inserts qualifying
    candidates into `data/ontology/discovered.ttl`. Auto-promotes for
    entity / event / framework categories; theme / edge / weight /
    stakeholder candidates remain pending until a human approves them
    via the discovery review endpoint.

    Pre-fix: the design called for this to run every 30 min but it was
    never wired up — the only time it ran was when an admin manually
    POSTed `/api/discovery/promote`. As a result `discovered.ttl` had
    one promotion (April 15) and 17 candidates accumulated in the buffer
    for 12 days. Now it runs as part of the scheduler loop.
    """
    try:
        from engine.ontology.discovery.promoter import batch_promote
        result = batch_promote()
        logger.info("scheduler: discovery promoter ran -> %s", result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("scheduler: discovery promoter failed: %s", exc)


def run_full_text_retry_job(
    *, max_files: int | None = None, per_call_timeout: float = 12.0,
) -> dict[str, int]:
    """Phase 36 — Periodic retry of headline-only article body extraction.

    Walks every raw-input file under ``data/inputs/news/*/*.json``,
    identifies headline-only articles (``len(content) < 300``), and
    re-runs ``extract_full_text`` for any whose last cached attempt is
    older than the failure-TTL (6h per `_CACHE_FAILURE_TTL_SECONDS`).

    On successful body backfill:
      1. Mutates the raw input file in place (writes `content`, `summary`,
         `metadata.publisher_url`, `metadata.full_text_source`).
      2. Stamps `meta.body_grounded_pending: True` on the corresponding
         insight at `data/outputs/{slug}/insights/{id}.json` so the next
         on-demand view force-re-enriches against the new body.

    Returns a summary dict so the metrics endpoint + scheduler_state
    table can record what happened. Returned dict shape:
        {
            slugs_scanned: int,
            files_checked: int,
            bodies_added: int,
            paywalled: int,
            network_failed: int,
            insights_marked_pending: int,
            elapsed_seconds: float,
        }

    Per-cron-run hard budget: 5 minutes wall-clock. After that the job
    exits early so a slow publisher can't hang the scheduler thread.
    Subsequent fires pick up where this one left off (oldest-failure-first
    ordering).
    """
    import json as _json
    import time as _time
    from collections import Counter
    from pathlib import Path as _Path
    from engine.config import get_data_path
    from engine.ingestion.full_text_extractor import extract_full_text
    from engine.models.scheduler_state import record_run

    started = _time.perf_counter()
    cron_deadline = started + 300.0  # 5 min hard cap
    stats: Counter = Counter()

    inputs_root = _Path(get_data_path("inputs", "news"))
    if not inputs_root.exists():
        logger.warning("run_full_text_retry_job: no inputs root at %s", inputs_root)
        result = {**stats, "elapsed_seconds": 0.0, "skipped_no_inputs_dir": True}
        record_run("full_text_retry", result=result, status="ok")
        return dict(result)

    outputs_root = _Path(get_data_path("outputs"))
    slug_dirs = sorted(p for p in inputs_root.iterdir() if p.is_dir())
    stats["slugs_scanned"] = len(slug_dirs)

    insights_to_mark_pending: list[_Path] = []

    for slug_dir in slug_dirs:
        if _time.perf_counter() > cron_deadline:
            logger.info(
                "run_full_text_retry_job: 5min budget hit at %s — "
                "deferring remaining slugs", slug_dir.name,
            )
            break
        slug = slug_dir.name
        for f in sorted(slug_dir.glob("*.json")):
            if _time.perf_counter() > cron_deadline:
                break
            stats["files_checked"] += 1
            try:
                raw = _json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            content = (raw.get("content") or "").strip()
            title = (raw.get("title") or "").strip()
            url = raw.get("url") or ""
            # Skip articles that are already body-grounded
            if len(content) >= 300 and content != title and len(content) > len(title) + 50:
                continue
            if not url:
                continue
            # Honor the cache TTL — failed entries < 6h old are skipped.
            # use_cache=True consults the cache + auto-skips fresh failures.
            try:
                result_ft = extract_full_text(url, timeout=per_call_timeout)
            except Exception as exc:  # noqa: BLE001
                logger.debug("retry extract failed for %s: %s", f.name, exc)
                stats["network_failed"] += 1
                continue
            if result_ft is None or not result_ft.body:
                stats["paywalled"] += 1
                continue

            # Body captured — mutate input file
            raw["content"] = result_ft.body
            raw["summary"] = result_ft.body[:500]
            meta = raw.get("metadata") or {}
            meta["full_text_source"] = "publisher_scrape_retry_cron"
            meta["full_text_char_count"] = result_ft.char_count
            meta["publisher_url"] = result_ft.publisher_url
            raw["metadata"] = meta
            try:
                f.write_text(_json.dumps(raw, indent=2), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                logger.warning("retry cron write failed for %s: %s", f.name, exc)
                continue
            stats["bodies_added"] += 1

            # Find + mark the matching insight as needing re-enrichment.
            # Insight files follow the pattern data/outputs/{slug}/insights/*{id}*.json
            article_id = raw.get("id") or f.stem.split("_", 1)[-1]
            insights_dir = outputs_root / slug / "insights"
            if insights_dir.exists():
                for ins_path in insights_dir.glob(f"*{article_id}*.json"):
                    insights_to_mark_pending.append(ins_path)

    # Mark insights pending — separate pass to keep file I/O isolated
    for ins_path in insights_to_mark_pending:
        try:
            d = _json.loads(ins_path.read_text(encoding="utf-8"))
            d.setdefault("meta", {})["body_grounded_pending"] = True
            ins_path.write_text(_json.dumps(d, indent=2), encoding="utf-8")
            stats["insights_marked_pending"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("retry cron pending-flag failed for %s: %s", ins_path.name, exc)

    elapsed = _time.perf_counter() - started
    result = {
        "slugs_scanned": stats["slugs_scanned"],
        "files_checked": stats["files_checked"],
        "bodies_added": stats["bodies_added"],
        "paywalled": stats["paywalled"],
        "network_failed": stats["network_failed"],
        "insights_marked_pending": stats["insights_marked_pending"],
        "elapsed_seconds": round(elapsed, 1),
    }
    logger.info("run_full_text_retry_job: %s", result)
    try:
        record_run("full_text_retry", result=result, status="ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("retry cron: scheduler_state.record_run failed: %s", exc)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scheduled ingestion for the Snowkap ESG engine"
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=60,
        help="Run interval in minutes (default: 60)",
    )
    parser.add_argument(
        "--max-per-query",
        type=int,
        default=10,
        help="Max articles per news query",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max articles processed per company per run (controls LLM cost)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run the ingest job once and exit (useful for cron)",
    )
    parser.add_argument(
        "--promote-interval-minutes",
        type=int,
        default=30,
        help=(
            "Phase 19 — discovery promoter interval (default: 30 min). "
            "Set to 0 to disable the promoter (ingest-only mode)."
        ),
    )
    parser.add_argument(
        "--promote-once",
        action="store_true",
        help="Run only the discovery promoter once and exit (no ingest).",
    )
    parser.add_argument(
        "--overnight-batch",
        action="store_true",
        help=(
            "Phase 25 W7 — run the customer-tenant overnight batch ONCE and "
            "exit. Fetches `--fetch-per-tenant` articles for every batch "
            "tenant in data/ontology/tenants/ (excluding _global + the "
            "original 7), selects top `--select-per-tenant` via "
            "engine.analysis.article_selector, runs full pipeline on each "
            "in parallel using `--workers` threads."
        ),
    )
    parser.add_argument(
        "--fetch-per-tenant", type=int, default=20,
        help="W7 — articles to fetch per tenant before selection (default: 20)",
    )
    parser.add_argument(
        "--select-per-tenant", type=int, default=3,
        help="W7 — articles to run full pipeline on per tenant (default: 3)",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="W7 — parallel ThreadPoolExecutor workers (default: 4)",
    )
    parser.add_argument(
        "--morning-digest-once",
        action="store_true",
        help=(
            "Phase 25 W10 — compose + send the morning digest ONCE and "
            "exit. Reads last 24h HOME-tier CRITICAL/HIGH articles, "
            "groups by customer, sends via Resend to "
            "$SNOWKAP_DIGEST_RECIPIENT (default sales@snowkap.co.in)."
        ),
    )
    args = parser.parse_args(argv)
    setup_logging("INFO")

    if args.promote_once:
        run_promote_job()
        return 0

    if args.overnight_batch:
        run_overnight_batch_job(
            fetch_per_tenant=args.fetch_per_tenant,
            select_per_tenant=args.select_per_tenant,
            workers=args.workers,
        )
        return 0

    if getattr(args, "morning_digest_once", False):
        run_morning_digest_job()
        return 0

    if args.once:
        run_ingest_job(args.max_per_query, args.limit)
        # Phase 19 — also drain the discovery buffer at the end of a one-shot
        # run so cron-based deployments don't need a separate cron entry.
        run_promote_job()
        return 0

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        logger.error(
            "APScheduler not installed. Install with: pip install apscheduler"
        )
        return 1

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_ingest_job,
        trigger="interval",
        minutes=args.interval_minutes,
        args=[args.max_per_query, args.limit],
        next_run_time=None,  # wait for first interval
    )
    # Phase 19 — discovery promoter on its own cadence (default 30 min).
    # Decoupled from ingestion because promotion is cheap (<1s) and we
    # want to drain the buffer even during quiet ingest periods.
    if args.promote_interval_minutes > 0:
        scheduler.add_job(
            run_promote_job,
            trigger="interval",
            minutes=args.promote_interval_minutes,
            next_run_time=None,
        )
    # Phase 25 W7 — overnight customer batch at 1am UTC (~6:30am IST).
    # Cron-trigger so it runs at a consistent wall-clock time regardless
    # of process restart. Skipped entirely when no customer tenants
    # are present (e.g. during local dev without batch onboarding done).
    try:
        from apscheduler.triggers.cron import CronTrigger
        overnight_hour = int(os.environ.get("SNOWKAP_OVERNIGHT_BATCH_UTC_HOUR", "1"))
        scheduler.add_job(
            run_overnight_batch_job,
            trigger=CronTrigger(hour=overnight_hour, minute=0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler: overnight cron wiring failed: %s", exc)
    # Phase 25 W10 — morning digest at 2:20am UTC (~7:50am IST).
    # Sends 5-10 minutes after the overnight batch finishes so the
    # digest reflects the night's analysis.
    try:
        from apscheduler.triggers.cron import CronTrigger
        digest_hour = int(os.environ.get("SNOWKAP_MORNING_DIGEST_UTC_HOUR", "2"))
        digest_minute = int(os.environ.get("SNOWKAP_MORNING_DIGEST_UTC_MINUTE", "20"))
        scheduler.add_job(
            run_morning_digest_job,
            trigger=CronTrigger(hour=digest_hour, minute=digest_minute),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler: morning digest cron wiring failed: %s", exc)
    logger.info(
        "scheduler: started, ingest_interval=%s min, promote_interval=%s min, "
        "max_per_query=%s, limit=%s, overnight_batch=cron 01:00 UTC, "
        "morning_digest=cron 02:20 UTC",
        args.interval_minutes,
        args.promote_interval_minutes,
        args.max_per_query,
        args.limit,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler: shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
