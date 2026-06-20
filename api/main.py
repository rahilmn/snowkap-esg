"""FastAPI application for the Snowkap ESG Intelligence Engine.

Run with::

    uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

Authentication (optional — only enforced if ``SNOWKAP_API_KEY`` is set):
    Either ``X-API-Key: <your-key>`` or ``Authorization: Bearer <token>``
    (the legacy UI uses the Bearer header; the adapter mints the token).

OpenAPI docs: http://localhost:8000/docs

Phase 11D observability:
  - `/health`  — liveness check
  - `/metrics` — Prometheus-format metrics (article counts, OpenAI spend
    last 24h, campaign + send counts)
  - structlog JSON logging bound with `request_id` via middleware
  - Sentry auto-init when `SENTRY_DSN` env is set (PII scrubbed)
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path

# Make the project root importable when uvicorn starts us up
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api.routes import admin, admin_body_coverage, admin_email, admin_onboard, admin_reanalyze, batch_onboard, campaigns, companies, discovery, ingest, insights, legacy_adapter, profile, session, share
from engine.config import load_settings  # noqa: F401  (eager-loads .env)
from engine.index.sqlite_index import ensure_schema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 11D: structlog JSON config
# ---------------------------------------------------------------------------


def _configure_structlog() -> None:
    """Unified JSON logging bound with contextvars (request_id, tenant_id).

    C#1 — the ~150 engine modules log via stdlib ``logging.getLogger()``, NOT
    ``structlog.get_logger()``. The old config only formatted structlog
    loggers, so engine logs were plain-text to stderr and the request_id /
    tenant_id contextvars never reached them — request correlation did not
    work end-to-end. We install a ``ProcessorFormatter`` on the stdlib ROOT
    handler with a ``foreign_pre_chain`` that merges the same contextvars, so
    BOTH structlog and the 150 stdlib call-sites render as one JSON stream
    with correlation ids. Degrades gracefully (keeps stdlib) if structlog or
    its stdlib bridge is unavailable. Idempotent."""
    try:
        import structlog
        from structlog.stdlib import ProcessorFormatter
    except ImportError:
        logger.warning("structlog not installed; using stdlib logging fallback")
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    # Shared by BOTH structlog-native records and foreign (stdlib) records.
    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        timestamper,
    ]
    try:
        structlog.configure(
            processors=pre_chain + [ProcessorFormatter.wrap_for_formatter],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        formatter = ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[
                ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,  # readable traceback string
                structlog.processors.JSONRenderer(),
            ],
        )
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler._snowkap_bridge = True  # type: ignore[attr-defined]
        root = logging.getLogger()
        # Idempotent: drop a previously-installed bridge handler, keep ours single.
        root.handlers = [
            h for h in root.handlers if not getattr(h, "_snowkap_bridge", False)
        ]
        root.handlers = [handler]
        root.setLevel(logging.INFO)
    except Exception:  # noqa: BLE001 — never let logging setup crash boot
        logger.warning("structlog stdlib bridge setup failed; stdlib logging stays", exc_info=True)


# Substrings that strongly indicate a .env placeholder that was never
# overwritten with a real secret. Intentionally conservative — a real token
# starting with "sk-proj-" must not match here even if it contains "example"
# somewhere in the middle, so we anchor on the start / whole-value.
_PLACEHOLDER_MARKERS = ("your_", "changeme", "placeholder", "replace_me", "todo_", "example_")


def _looks_like_placeholder(value: str) -> bool:
    v = value.strip().lower()
    if not v:
        return True
    # Anchor on start to avoid matching real keys that happen to contain the
    # substring (e.g. a Resend key containing "example" by coincidence).
    if any(v.startswith(marker) for marker in _PLACEHOLDER_MARKERS):
        return True
    # Common "your_<thing>_here" pattern.
    if v.startswith("<") and v.endswith(">"):
        return True
    return False


def _check_production_env() -> None:
    """Fail-fast env audit. Runs in production mode only.

    Triggered when `SNOWKAP_ENV=production` (or `ENV=production`). Refuses to
    boot the API if a required secret is empty or obviously a placeholder.
    Dev/CI stays permissive so `make dev` keeps working without Resend.
    """
    env = (os.environ.get("SNOWKAP_ENV") or os.environ.get("ENV") or "").strip().lower()
    if env != "production":
        return

    required: dict[str, str] = {
        "OPENAI_API_KEY": "OpenAI (pipeline stages 1-2, 10, 12)",
        "RESEND_API_KEY": "Resend (outbound share + drip email)",
        "SNOWKAP_FROM_ADDRESS": "verified sender on the Resend domain",
        "JWT_SECRET": "signed JWT verification (api/auth_context.py)",
        "SNOWKAP_API_KEY": "legacy X-API-Key middleware",
    }
    missing: list[str] = []
    for var, purpose in required.items():
        val = os.environ.get(var, "")
        if not val.strip() or _looks_like_placeholder(val):
            missing.append(f"  - {var}  ({purpose})")

    # JWT_SECRET length guardrail — HS256 wants ≥32 bytes of entropy.
    jwt_secret = os.environ.get("JWT_SECRET", "")
    if jwt_secret and len(jwt_secret) < 32:
        missing.append("  - JWT_SECRET is set but shorter than 32 chars (weak HS256)")

    # Phase 47.N — STRICTLY enforce Supabase Postgres in production.
    # Pre-fix this only validated SUPABASE_DATABASE_URL *if* the user had
    # already set SNOWKAP_DB_BACKEND=postgres. If they forgot to set the
    # backend (default = sqlite), it silently fell back to SQLite — which
    # is what bit you on Replit: writes went to /home/runner/workspace/data/snowkap.db
    # (a local file that doesn't persist across deploys).
    #
    # Now: in production, BOTH must be set:
    #   SNOWKAP_DB_BACKEND=postgres
    #   SUPABASE_DATABASE_URL=postgresql://...
    # Otherwise the API refuses to boot.
    db_backend = (os.environ.get("SNOWKAP_DB_BACKEND") or "").strip().lower()
    sup_url = os.environ.get("SUPABASE_DATABASE_URL", "")
    if db_backend != "postgres":
        missing.append(
            f"  - SNOWKAP_DB_BACKEND must be set to 'postgres' in production "
            f"(got: {db_backend!r}). SQLite is NOT a production database — "
            f"every deploy loses data."
        )
    if not sup_url.strip() or _looks_like_placeholder(sup_url):
        missing.append(
            "  - SUPABASE_DATABASE_URL  (required in production — set in Replit Secrets)"
        )
    elif not sup_url.startswith("postgresql://"):
        missing.append(
            "  - SUPABASE_DATABASE_URL must start with 'postgresql://' "
            f"(got: {sup_url[:30]}...)"
        )
    # Pool-timeout sanity. Default Supabase pgbouncer is 60s which kills
    # long ingests; we recommend 300000 (5 min). Warn but don't fail.
    timeout = os.environ.get("SNOWKAP_PG_STATEMENT_TIMEOUT_MS", "")
    if timeout and not timeout.isdigit():
        missing.append(
            f"  - SNOWKAP_PG_STATEMENT_TIMEOUT_MS must be an integer (got: {timeout!r})"
        )

    if missing:
        msg = (
            "SNOWKAP_ENV=production but required secrets are missing or "
            "placeholder values:\n"
            + "\n".join(missing)
            + "\n\nSet them in the deploy environment and restart. "
            "(Hint: use `/health` from the LB to verify boot.)"
        )
        logger.error(msg)
        raise RuntimeError(msg)

    # Sentry is optional, but silent-off in production means errors + cron
    # failures (engine/scheduler.py _capture) never page. Warn loudly rather
    # than fail — the app runs fine without it.
    if not os.environ.get("SENTRY_DSN", "").strip():
        logger.warning(
            "production: SENTRY_DSN is unset — error + cron-failure reporting "
            "is OFF. Set it in Railway to page on failures."
        )

    logger.info("production env audit: all required secrets present")


def _init_sentry() -> None:
    """Enable Sentry only when DSN is set. Strips PII (emails) from events."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk

        def _scrub(event: dict, _hint: dict) -> dict:
            # Drop recipient emails from breadcrumbs + request context
            req = event.get("request") or {}
            if "data" in req and isinstance(req["data"], dict):
                req["data"].pop("recipient_email", None)
                req["data"].pop("email", None)
            return event

        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("SENTRY_ENV", "production"),
            traces_sample_rate=0.05,
            send_default_pii=False,
            before_send=_scrub,
        )
        logger.info("sentry: initialised")
    except ImportError:
        logger.warning("sentry_sdk not installed; skipping")

app = FastAPI(
    title="Snowkap ESG Intelligence Engine",
    description="Ontology-driven ESG intelligence for 7 target companies. Serves both the new minimal routes (/api/companies, /api/insights, /api/feed) and the legacy-UI adapter (/api/auth, /api/news, /api/agent, /api/preferences, /api/ontology).",
    version="0.2.0",
)

_cors_origins = [
    "http://localhost:5173",
    "http://localhost:5500",
    "http://localhost:3000",
    "http://localhost:4173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5500",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:4173",
]

# Phase 48.F — Railway hosting. Add the public domain(s) dynamically.
# `SNOWKAP_CORS_ORIGINS` is a comma-separated allowlist (e.g.
# "https://app.snowkap.com,https://snowkap.up.railway.app"); Railway also
# injects `RAILWAY_PUBLIC_DOMAIN` for the service's generated URL. Replit
# env vars (REPLIT_DOMAINS / REPLIT_DEV_DOMAIN) are no longer read.
for _origin in os.environ.get("SNOWKAP_CORS_ORIGINS", "").split(","):
    _origin = _origin.strip()
    if _origin:
        _cors_origins.append(_origin if _origin.startswith("http") else f"https://{_origin}")

_railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
if _railway_domain:
    _cors_origins.append(f"https://{_railway_domain}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)


# Phase 45.G — global unhandled-exception handler. Without this, FastAPI
# returns a bare "Internal Server Error" with no class/message/traceback,
# making 500s impossible to diagnose from the client side. Now: full
# traceback to the logs (visible in Replit), and a structured JSON body
# with the exception class + first line of the message so curl / the
# validation script can see WHAT broke.
from fastapi import Request
from fastapi.responses import JSONResponse
import traceback as _global_tb


@app.exception_handler(Exception)
async def _global_500_logger(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error_class": type(exc).__name__,
            "error_message": str(exc)[:300],
            "path": request.url.path,
            "trace_head": _global_tb.format_exc().splitlines()[-6:],
        },
    )


@app.on_event("startup")
def _startup() -> None:
    ensure_schema()
    # Phase 49.3 — ensure the newsletter_subscribers table exists at boot.
    # Login auto-subscribe (legacy_adapter._mint_login_response) + the weekly
    # Morning-Brew cron both depend on it; it was never created on the live
    # Supabase DB, so auto-subscribe was silently failing. Idempotent
    # CREATE TABLE IF NOT EXISTS — safe on every boot.
    try:
        from engine.models import newsletter_subscribers
        newsletter_subscribers.ensure_schema()
    except Exception as exc:  # noqa: BLE001
        logger.warning("newsletter_subscribers.ensure_schema failed: %s", exc)
    _configure_structlog()
    _check_production_env()  # raises RuntimeError in prod if secrets missing

    # Phase 48.0 — Postgres is mandatory in EVERY environment (not just
    # production). Snowkap runs strictly on Supabase. The only escape hatch
    # is SNOWKAP_ALLOW_SQLITE=1, reserved for the local test suite. Without
    # it, a non-postgres backend (or a missing SUPABASE_DATABASE_URL) crashes
    # the boot loudly rather than serving a phantom empty SQLite dashboard.
    try:
        from engine.db.connection import get_backend, is_postgres
        backend = get_backend()
        allow_sqlite = os.environ.get("SNOWKAP_ALLOW_SQLITE", "").strip() == "1"
        if not is_postgres() and not allow_sqlite:
            raise RuntimeError(
                f"DB backend is '{backend}' but Snowkap requires Supabase Postgres. "
                "Set SNOWKAP_DB_BACKEND=postgres + SUPABASE_DATABASE_URL=... "
                "(SNOWKAP_ALLOW_SQLITE=1 is for local tests only)."
            )
        logger.warning(
            "API startup: DB backend = %s (is_postgres=%s)",
            backend, is_postgres(),
        )
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not check DB backend at startup: %s", exc)

    _init_sentry()

    # Phase 51 — log the resolved reasoning model so a silent Opus→gpt-4.1
    # fallback (missing OPENROUTER_API_KEY) is visible at boot instead of being
    # discovered later via gpt-4.1-stamped insights.
    try:
        from engine.llm.health import report_routing
        report_routing()
    except Exception as exc:  # noqa: BLE001 — never block boot on the routing log
        logger.warning("LLM routing report failed: %s", exc)

    key_set = bool(os.environ.get("SNOWKAP_API_KEY", "").strip())
    logger.info("api startup: auth=%s", "enabled" if key_set else "disabled (dev mode)")

    # Phase 13 S3 — eager-load the ontology graph at boot. If the TTL files
    # are corrupt or missing, fail fast at startup instead of letting the
    # FIRST user request hit a 500 mid-demo. We log + suppress in dev mode
    # so a missing graph during local development doesn't block the API
    # from starting (only the ontology-driven endpoints will degrade).
    try:
        from api.routes.legacy_adapter import eager_load_ontology
        graph = eager_load_ontology()
        triple_count = len(graph.graph) if hasattr(graph, "graph") else 0
        logger.info("ontology eager-loaded: %d triples", triple_count)
    except Exception as exc:  # noqa: BLE001 — surface clearly, don't swallow
        env = (os.environ.get("SNOWKAP_ENV") or "").strip().lower()
        if env == "production":
            raise RuntimeError(
                f"ontology load failed at boot — check data/ontology/*.ttl "
                f"file integrity. Underlying error: {exc}"
            ) from exc
        logger.warning(
            "ontology load failed at boot (dev mode — continuing degraded): %s", exc
        )

    # Track A2 — in-process continuous scheduler (Replit always-on deploy).
    #
    # Runs the same jobs `engine/scheduler.py` exposes (60-min ingest +
    # 30-min discovery promoter), but as APScheduler BackgroundScheduler
    # threads inside the API process — so when Replit restarts the
    # deployment, the scheduler restarts with it. Avoids the ops complexity
    # of running a second always-on process for the scheduler.
    #
    # Gated by SNOWKAP_INPROCESS_SCHEDULER env var (default: enabled in
    # production, disabled elsewhere so local development doesn't burn
    # OpenAI dollars on every uvicorn --reload).
    _start_inprocess_scheduler()

    # Phase 25 — auto-bootstrap detector. Runs INDEPENDENTLY of the
    # in-process scheduler (a dev who turns SNOWKAP_INPROCESS_SCHEDULER=0
    # might still want auto-onboarding to fire, and vice-versa). The
    # detector itself is opt-in via SNOWKAP_PHASE25_AUTO_BOOTSTRAP=1
    # and idempotent — silent no-op once tenants exist.
    try:
        _maybe_run_auto_bootstrap()
    except Exception as exc:  # noqa: BLE001 — auto-bootstrap is additive
        logger.warning("phase 25 auto-bootstrap failed (non-fatal): %s", exc)

    # Phase 52 — build the on-disk wiki from the DB. Prod persists insights to
    # Postgres and runs on Railway's ephemeral filesystem, so wiki_root is empty
    # after every deploy → /api/wiki/* returned wiki_root_missing. This rebuilds
    # it from article_pool ⋈ company_article_view in a non-fatal daemon thread
    # (never blocks boot, never crashes the app). Gated by
    # SNOWKAP_WIKI_BUILD_ON_STARTUP (default on).
    try:
        from engine.wiki.startup_build import maybe_build_wiki_on_startup
        maybe_build_wiki_on_startup()
    except Exception as exc:  # noqa: BLE001 — wiki is non-critical
        logger.warning("wiki startup build wiring failed (non-fatal): %s", exc)


def _start_inprocess_scheduler() -> None:
    """Boot the BackgroundScheduler if SNOWKAP_INPROCESS_SCHEDULER is on.

    Fail-soft: if APScheduler isn't installed, or the env flag is off, we
    log and return. Never raise — the API must be runnable without this.
    """
    flag = (os.environ.get("SNOWKAP_INPROCESS_SCHEDULER") or "").strip().lower()
    env = (os.environ.get("SNOWKAP_ENV") or "").strip().lower()
    # Default-on in production, default-off elsewhere
    enabled = flag in {"1", "true", "yes", "on"} or (
        flag == "" and env == "production"
    )
    if not enabled:
        logger.info("in-process scheduler disabled (set SNOWKAP_INPROCESS_SCHEDULER=1 to enable)")
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning(
            "in-process scheduler requested but apscheduler is not installed; skipping"
        )
        return

    try:
        from engine.scheduler import run_ingest_job, run_promote_job

        # Tunables surfaced as env vars so ops can dial frequency without
        # a deploy. Defaults match the Phase 19 design (60 min / 30 min).
        ingest_min = int(os.environ.get("SNOWKAP_INGEST_INTERVAL_MIN", "60"))
        promote_min = int(os.environ.get("SNOWKAP_PROMOTE_INTERVAL_MIN", "30"))
        max_per_query = int(os.environ.get("SNOWKAP_MAX_PER_QUERY", "10"))
        per_run_limit = int(os.environ.get("SNOWKAP_PER_RUN_LIMIT", "5"))

        # Fire ingest 30s after boot so the 7 baseline companies have fresh
        # news as soon as the API is up — without it a freshly-wiped or
        # restarted instance sits with an empty article_index for a full
        # 60-min cycle, producing the "No ESG-relevant news" empty state
        # the user reported on 2026-05-19.
        from datetime import datetime, timedelta, timezone
        boot_offset = datetime.now(timezone.utc) + timedelta(seconds=30)
        # Phase 51.C — idempotency defaults for every job: coalesce collapses
        # missed runs into one, max_instances=1 prevents an overlapping second
        # run if one overruns its interval, misfire_grace_time lets a run that
        # was missed during a restart still fire (the weekly job's own
        # already-ran-this-week guard prevents a duplicate newsletter).
        scheduler = BackgroundScheduler(
            daemon=True,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
        )
        scheduler.add_job(
            run_ingest_job,
            trigger="interval",
            minutes=ingest_min,
            args=[max_per_query, per_run_limit],
            next_run_time=boot_offset,
            id="ingest_all_companies",
            replace_existing=True,
        )
        if promote_min > 0:
            scheduler.add_job(
                run_promote_job,
                trigger="interval",
                minutes=promote_min,
                next_run_time=boot_offset + timedelta(seconds=15),
                id="discovery_promote",
                replace_existing=True,
            )

        # Phase 25 W7 + W10 — overnight customer batch + morning digest
        # cron triggers, mirrored from the standalone scheduler so the
        # in-process Replit deploy gets the same nightly cycle.
        try:
            from apscheduler.triggers.cron import CronTrigger
            from engine.scheduler import (
                run_overnight_batch_job,
                run_morning_digest_job,
            )
            overnight_hour = int(os.environ.get("SNOWKAP_OVERNIGHT_BATCH_UTC_HOUR", "1"))
            scheduler.add_job(
                run_overnight_batch_job,
                trigger=CronTrigger(hour=overnight_hour, minute=0),
                id="phase25_overnight_batch",
                replace_existing=True,
            )
            digest_hour = int(os.environ.get("SNOWKAP_MORNING_DIGEST_UTC_HOUR", "2"))
            digest_minute = int(os.environ.get("SNOWKAP_MORNING_DIGEST_UTC_MINUTE", "20"))
            scheduler.add_job(
                run_morning_digest_job,
                trigger=CronTrigger(hour=digest_hour, minute=digest_minute),
                id="phase25_morning_digest",
                replace_existing=True,
            )
            logger.info(
                "phase 25 cron jobs wired: overnight_batch @ %02d:00 UTC, "
                "morning_digest @ %02d:%02d UTC",
                overnight_hour, digest_hour, digest_minute,
            )
        except Exception as exc:  # noqa: BLE001 — Phase 25 cron is additive
            logger.warning("phase 25 cron wiring failed (non-fatal): %s", exc)

        # Phase 36 — periodic full-text retry. Walks every raw-input file
        # under data/inputs/news/*/*.json, retries headline-only articles
        # whose cached extraction is older than 6h (the failure-TTL set in
        # full_text_extractor._cache_get). On successful body backfill,
        # mutates the input file in place + marks the corresponding insight
        # as `body_grounded_pending=True` so the next on-demand view
        # re-enriches against the new body. Default cadence: every 6 hours.
        # Override with SNOWKAP_FULL_TEXT_RETRY_HOURS. Set to 0 to disable.
        try:
            from engine.scheduler import run_full_text_retry_job
            retry_hours = int(os.environ.get("SNOWKAP_FULL_TEXT_RETRY_HOURS", "6"))
            if retry_hours > 0:
                scheduler.add_job(
                    run_full_text_retry_job,
                    trigger="interval",
                    hours=retry_hours,
                    # Fire first run 5 minutes after boot so it doesn't
                    # collide with the boot-time ingest job above (which
                    # is itself doing inline body backfill).
                    next_run_time=boot_offset + timedelta(minutes=5),
                    id="full_text_retry",
                    replace_existing=True,
                )
                logger.info(
                    "phase 36 cron wired: full_text_retry every %dh", retry_hours,
                )
        except Exception as exc:  # noqa: BLE001 — Phase 36 cron is additive
            logger.warning("phase 36 retry cron wiring failed (non-fatal): %s", exc)

        # Phase 48.I — weekly Sunday deck refresh + newsletter. For every
        # active company: fetch fresh NewsAPI.ai ESG news → tier-gated deck
        # (3 critical + 7 light, Opus approval) → send the weekly Morning-
        # Brew newsletter to active subscribers. Cadence override via
        # SNOWKAP_WEEKLY_REFRESH_CRON="<day>:<hour>" (default sun:6 UTC).
        try:
            from apscheduler.triggers.cron import CronTrigger as _CronTrigger
            from engine.scheduler import run_weekly_deck_refresh_job
            _wk = (os.environ.get("SNOWKAP_WEEKLY_REFRESH_CRON", "sun:6") or "sun:6").strip()
            _wk_day, _, _wk_hour = _wk.partition(":")
            scheduler.add_job(
                run_weekly_deck_refresh_job,
                trigger=_CronTrigger(
                    day_of_week=(_wk_day or "sun"),
                    hour=int(_wk_hour or "6"),
                    minute=0,
                ),
                id="phase48_weekly_refresh",
                replace_existing=True,
            )
            logger.info(
                "phase 48 cron wired: weekly_deck_refresh @ %s:%s UTC",
                _wk_day or "sun", _wk_hour or "6",
            )
        except Exception as exc:  # noqa: BLE001 — additive
            logger.warning("phase 48 weekly cron wiring failed (non-fatal): %s", exc)

        scheduler.start()
        # Stash on app.state so we can shut it down cleanly + introspect via /metrics
        app.state.scheduler = scheduler
        logger.info(
            "in-process scheduler started (ingest_every=%dmin, promote_every=%dmin)",
            ingest_min,
            promote_min,
        )
        # NOTE: phase 25 auto-bootstrap fires from _startup() directly,
        # NOT from here — see the call after _start_inprocess_scheduler()
        # in _startup. Decoupled so a dev with the scheduler off can
        # still opt into auto-onboarding (and vice-versa).
    except Exception as exc:  # noqa: BLE001
        # Scheduler must NEVER block API startup. Log and continue.
        logger.exception("in-process scheduler failed to start: %s", exc)


def _maybe_run_auto_bootstrap() -> None:
    """Phase 25 auto-bootstrap detector.

    Triggers ``scripts/phase25_bootstrap.py`` in a background thread iff
    ALL of these hold:

      1. ``SNOWKAP_PHASE25_AUTO_BOOTSTRAP`` is set to ``1``/``true``/
         ``yes`` (default OFF — explicit opt-in to avoid surprise).
      2. A HubSpot CSV exists at ``$SNOWKAP_BOOTSTRAP_CSV`` (or, if
         unset, ``../hubspot-crm-exports-all-deals-2026-05-01.csv``).
      3. No Phase 25 tenants exist yet — i.e.
         ``data/ontology/tenants/`` only contains ``_global``. Once any
         customer tenant is onboarded the auto-bootstrap stays silent
         on subsequent boots so the API doesn't redundantly ingest on
         every restart.

    The actual bootstrap runs in a daemon thread so the API request
    handler is never blocked. Output goes to the standard logger.
    """
    flag = (os.environ.get("SNOWKAP_PHASE25_AUTO_BOOTSTRAP") or "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        logger.debug("phase 25 auto-bootstrap not enabled "
                     "(set SNOWKAP_PHASE25_AUTO_BOOTSTRAP=1 to opt in)")
        return

    # Locate the CSV — env var first, then the repo-root default
    csv_env = (os.environ.get("SNOWKAP_BOOTSTRAP_CSV") or "").strip()
    if csv_env:
        csv_path = Path(csv_env)
    else:
        # ../hubspot-crm-exports-all-deals-2026-05-01.csv relative to repo root
        repo_root = Path(__file__).resolve().parent.parent
        csv_path = repo_root.parent / "hubspot-crm-exports-all-deals-2026-05-01.csv"
    if not csv_path.exists():
        logger.info(
            "phase 25 auto-bootstrap: CSV not found at %s — skipping "
            "(set SNOWKAP_BOOTSTRAP_CSV to override)", csv_path,
        )
        return

    # Idempotency check — skip if any customer tenant already exists
    try:
        from engine.ontology.tenant_resolver import (
            DEFAULT_TENANT, list_tenants,
        )
        existing = [t for t in list_tenants() if t != DEFAULT_TENANT]
        # Also exclude the original 7 target companies (they don't have
        # tenant dirs but they're in companies.json)
        if existing:
            logger.info(
                "phase 25 auto-bootstrap: %d tenant(s) already onboarded — skipping",
                len(existing),
            )
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("phase 25 auto-bootstrap idempotency check failed: %s", exc)
        return

    # Fire bootstrap in a background daemon thread
    import threading
    def _bg() -> None:
        try:
            from scripts.phase25_bootstrap import main as bootstrap_main
            logger.info("phase 25 auto-bootstrap: starting (csv=%s)", csv_path)
            rc = bootstrap_main([
                "--csv", str(csv_path),
                "--commit",
                "--log-level", "INFO",
            ])
            logger.info("phase 25 auto-bootstrap: completed exit_code=%d", rc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("phase 25 auto-bootstrap thread crashed: %s", exc)
    t = threading.Thread(target=_bg, daemon=True, name="phase25-bootstrap")
    t.start()
    logger.info("phase 25 auto-bootstrap: dispatched to background thread")


@app.on_event("shutdown")
def _shutdown() -> None:
    """Gracefully stop the BackgroundScheduler if it was started."""
    sched = getattr(app.state, "scheduler", None)
    if sched is None:
        return
    try:
        sched.shutdown(wait=False)
        logger.info("in-process scheduler shut down")
    except Exception as exc:  # noqa: BLE001
        logger.warning("scheduler shutdown raised: %s", exc)


# C#4 — in-process HTTP request metrics (no prometheus_client dependency).
# A latency histogram + a request/error counter, keyed by route TEMPLATE
# (e.g. "/api/forum/threads/{thread_id}") so article-id paths don't blow up
# cardinality. Lets /metrics expose p50/p95/p99 + 5xx rate, which the
# middleware's elapsed_ms otherwise computed and threw away.
_HTTP_BUCKETS_S = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_http_lock = threading.Lock()
_http_stats: dict[str, dict] = {}  # route -> {count, sum_s, buckets{le:n}, status{class:n}}


def _record_http(route: str, status: int, elapsed_s: float) -> None:
    cls = f"{status // 100}xx"
    with _http_lock:
        st = _http_stats.get(route)
        if st is None:
            st = {"count": 0, "sum_s": 0.0,
                  "buckets": {b: 0 for b in _HTTP_BUCKETS_S}, "status": {}}
            _http_stats[route] = st
        st["count"] += 1
        st["sum_s"] += elapsed_s
        for b in _HTTP_BUCKETS_S:  # cumulative: a 0.03s req increments every le >= 0.03
            if elapsed_s <= b:
                st["buckets"][b] += 1
        st["status"][cls] = st["status"].get(cls, 0) + 1


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or "other"


# Phase 11D + Phase 24 W5: request-timing middleware + request_id contextvar
# binding + per-request active tenant binding.
@app.middleware("http")
async def _request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    # Phase 24 W5 — bind the active tenant from the X-Tenant-Id header for
    # the duration of this request. Defaults to ``_global``. Every engine
    # SPARQL call inside the request handler will then route to the
    # correct tenant graph without any signature plumbing.
    from engine.ontology.tenant_resolver import (
        DEFAULT_TENANT,
        _ACTIVE_TENANT,
    )
    tenant_id = (request.headers.get("X-Tenant-Id") or DEFAULT_TENANT).strip()
    if not tenant_id:
        tenant_id = DEFAULT_TENANT
    tenant_token = _ACTIVE_TENANT.set(tenant_id)
    try:
        import structlog
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            tenant_id=tenant_id,
        )
    except ImportError:
        pass
    start = time.perf_counter()
    try:
        response: Response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.exception("request failed after %.0fms path=%s", elapsed_ms, request.url.path)
        _record_http(_route_template(request), 500, elapsed_ms / 1000.0)
        raise
    finally:
        # Always reset the tenant ContextVar — leaking across requests
        # would silently route a /icici-bank request to /acme_capital's
        # graph if the next worker reuses the asyncio task.
        _ACTIVE_TENANT.reset(tenant_token)
    elapsed_ms = (time.perf_counter() - start) * 1000
    _record_http(_route_template(request), response.status_code, elapsed_ms / 1000.0)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.0f}"
    response.headers["X-Tenant-Id"] = tenant_id
    if elapsed_ms > 500:
        logger.warning("slow request path=%s status=%d elapsed_ms=%.0f",
                       request.url.path, response.status_code, elapsed_ms)
    try:
        import structlog
        structlog.contextvars.clear_contextvars()
    except ImportError:
        pass
    return response


@app.get("/health")
def health() -> dict:
    # Liveness only — intentionally cheap and dependency-free so a transient
    # DB blip never trips Railway's healthcheck (healthcheckPath=/health,
    # restartPolicyType=ON_FAILURE) into a restart loop. DB reachability is
    # reported separately by /health/ready, polled by monitors / load balancers.
    return {"status": "ok", "service": "snowkap-esg-api", "version": "0.2.0"}


@app.get("/health/ready")
def health_ready(response: Response) -> dict:
    """Readiness probe — verifies the process can actually reach the database.

    Deliberately separate from /health (liveness). Monitors / load balancers
    poll this to know whether the app can serve DB-backed traffic; 503 on DB
    loss → alert + drain, NOT a container restart. Reuses
    ``engine.db.connection.connect()`` so it honours the same backend selection
    and connect timeout as the rest of the app.
    """
    db_ok = False
    detail = "ok"
    try:
        from engine.db.connection import connect

        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        detail = type(exc).__name__
        logger.warning("readiness DB ping failed", exc_info=True)
    if not db_ok:
        response.status_code = 503
    return {
        "status": "ready" if db_ok else "degraded",
        "service": "snowkap-esg-api",
        "db": "ok" if db_ok else "error",
        "detail": detail,
    }


@app.get("/api/health/routing")
def health_routing() -> dict:
    """LLM routing health — is reasoning_heavy actually on Opus, or silently
    on the gpt-4.1 fallback (missing/invalid OPENROUTER_API_KEY)?

    Lives under /api/* (not root like /health) so it is reachable through the
    public reverse proxy. No secrets — only the resolved model name + provider
    + an opus_active boolean + the chat model. Lets ops curl
    ``/api/health/routing`` to confirm Opus instead of digging through boot logs.
    """
    out: dict[str, object] = {"opus_active": False, "reasoning_heavy_model": "unknown"}
    try:
        from engine.llm.health import routing_report
        from engine.llm.routing import resolve_model
        rep = routing_report()
        out.update(rep)
        out["chat_model"] = resolve_model("chat")
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__
    return out


@app.get("/metrics", response_class=Response)
def metrics() -> Response:
    """Prometheus-format metrics. Surfaces live counts for ops dashboards.

    No auth gate — this is standard Prometheus pattern (scrape from inside
    the trusted network). In hostile environments, put a reverse-proxy ACL
    on `/metrics`.
    """
    from engine.index import sqlite_index
    from engine.models import campaign_store, llm_calls

    try:
        idx_stats = sqlite_index.stats()
    except Exception:
        idx_stats = {"total": 0, "by_tier": {}}

    try:
        active_campaigns = len(campaign_store.list_campaigns(status="active"))
        paused_campaigns = len(campaign_store.list_campaigns(status="paused"))
    except Exception:
        active_campaigns = 0
        paused_campaigns = 0

    try:
        llm_spend = llm_calls.spend_last_24h_usd()
        llm_count = llm_calls.count_last_24h()
    except Exception:
        llm_spend = 0.0
        llm_count = 0

    by_tier = idx_stats.get("by_tier") or {}

    # Phase 1 — criticality band distribution. Catches rollout regressions
    # (healthy prod: UNSCORED → 0 a week after Phase 1.7 backfill ran).
    try:
        by_band = sqlite_index.count_by_criticality_band()
    except Exception:
        by_band = {}

    # Phase 4 — outbound touch + CTA cadence health
    try:
        from engine.models.outbound_touches import (
            first_touch_ratio as _ft_ratio,
            total_count as _touch_total,
        )
        touches_total = _touch_total()
        cta_ratio = _ft_ratio()
    except Exception:
        touches_total = 0
        cta_ratio = {"first_touch": 0, "subsequent_touch": 0}

    # Phase 6 — persona adoption
    try:
        from engine.persona.persona_store import (
            count_by_role as _persona_by_role,
            total_count as _persona_total,
        )
        personas_total = _persona_total()
        personas_by_role = _persona_by_role()
    except Exception:
        personas_total = 0
        personas_by_role = {}

    # Phase 5 — NewsAPI.ai router budget remaining (in-process state)
    try:
        from engine.ingestion.news_router import get_router as _get_router
        budget = _get_router().budget.to_dict()
    except Exception:
        budget = {"remaining": 0, "burst_remaining": 0, "spent_this_month": 0}

    lines = [
        "# HELP snowkap_articles_total Total analysed articles in the index by tier",
        "# TYPE snowkap_articles_total gauge",
        f'snowkap_articles_total{{tier="HOME"}} {by_tier.get("HOME", 0)}',
        f'snowkap_articles_total{{tier="SECONDARY"}} {by_tier.get("SECONDARY", 0)}',
        f'snowkap_articles_total{{tier="REJECTED"}} {by_tier.get("REJECTED", 0)}',
        f'snowkap_articles_total{{tier="ALL"}} {idx_stats.get("total", 0)}',
        "",
        "# HELP snowkap_articles_by_criticality_band Article count per criticality band (Phase 1)",
        "# TYPE snowkap_articles_by_criticality_band gauge",
        f'snowkap_articles_by_criticality_band{{band="CRITICAL"}} {by_band.get("CRITICAL", 0)}',
        f'snowkap_articles_by_criticality_band{{band="HIGH"}} {by_band.get("HIGH", 0)}',
        f'snowkap_articles_by_criticality_band{{band="MEDIUM"}} {by_band.get("MEDIUM", 0)}',
        f'snowkap_articles_by_criticality_band{{band="LOW"}} {by_band.get("LOW", 0)}',
        f'snowkap_articles_by_criticality_band{{band="UNSCORED"}} {by_band.get("UNSCORED", 0)}',
        "",
        "# HELP snowkap_campaigns Drip campaigns by status",
        "# TYPE snowkap_campaigns gauge",
        f'snowkap_campaigns{{status="active"}} {active_campaigns}',
        f'snowkap_campaigns{{status="paused"}} {paused_campaigns}',
        "",
        "# HELP snowkap_outbound_touches_total Total outbound share emails sent across all (recipient, company) pairs",
        "# TYPE snowkap_outbound_touches_total gauge",
        f"snowkap_outbound_touches_total {touches_total}",
        "",
        "# HELP snowkap_cta_cadence Pair count by touch bucket (first-touch vs subsequent)",
        "# TYPE snowkap_cta_cadence gauge",
        f'snowkap_cta_cadence{{bucket="first_touch"}} {cta_ratio.get("first_touch", 0)}',
        f'snowkap_cta_cadence{{bucket="subsequent_touch"}} {cta_ratio.get("subsequent_touch", 0)}',
        "",
        "# HELP snowkap_personas_total Personas onboarded (Phase 6 MCQ adoption)",
        "# TYPE snowkap_personas_total gauge",
        f"snowkap_personas_total {personas_total}",
        "",
        "# HELP snowkap_personas_by_role Persona count by role bucket",
        "# TYPE snowkap_personas_by_role gauge",
        f'snowkap_personas_by_role{{role="cfo"}} {personas_by_role.get("cfo", 0)}',
        f'snowkap_personas_by_role{{role="ceo"}} {personas_by_role.get("ceo", 0)}',
        f'snowkap_personas_by_role{{role="analyst"}} {personas_by_role.get("analyst", 0)}',
        f'snowkap_personas_by_role{{role="other"}} {personas_by_role.get("other", 0)}',
        "",
        "# HELP snowkap_newsapi_budget Tier-1 (NewsAPI.ai) token budget state (Phase 5)",
        "# TYPE snowkap_newsapi_budget gauge",
        f'snowkap_newsapi_budget{{pool="remaining"}} {int(budget.get("remaining", 0))}',
        f'snowkap_newsapi_budget{{pool="burst_remaining"}} {int(budget.get("burst_remaining", 0))}',
        f'snowkap_newsapi_budget{{pool="spent_this_month"}} {int(budget.get("spent_this_month", 0))}',
        "",
        "# HELP snowkap_openai_cost_usd_24h Estimated OpenAI spend (USD) in the last 24h",
        "# TYPE snowkap_openai_cost_usd_24h gauge",
        f"snowkap_openai_cost_usd_24h {llm_spend:.4f}",
        "",
        "# HELP snowkap_openai_calls_24h Count of LLM calls in the last 24h",
        "# TYPE snowkap_openai_calls_24h gauge",
        f"snowkap_openai_calls_24h {llm_count}",
        "",
    ]

    # Phase 36 — body-capture coverage metrics. Pulls the cached coverage
    # snapshot the /api/admin/body-coverage endpoint also reads (60s
    # in-memory TTL so the FS walk doesn't run per scrape).
    try:
        from api.routes.admin_body_coverage import get_body_coverage
        coverage = get_body_coverage()
        lines.append("# HELP snowkap_articles_with_body_total Articles with full body content (>=300 chars) per tenant (Phase 36)")
        lines.append("# TYPE snowkap_articles_with_body_total gauge")
        for s in coverage["slugs"]:
            lines.append(
                f'snowkap_articles_with_body_total{{slug="{s["slug"]}"}} {s["with_body"]}'
            )
        lines.append("")
        lines.append("# HELP snowkap_articles_headline_only_total Articles stuck at headline-only per tenant (Phase 36)")
        lines.append("# TYPE snowkap_articles_headline_only_total gauge")
        for s in coverage["slugs"]:
            lines.append(
                f'snowkap_articles_headline_only_total{{slug="{s["slug"]}"}} {s["headline_only"]}'
            )
        lines.append("")
        lines.append("# HELP snowkap_articles_body_coverage_pct Body coverage % per tenant (Phase 36)")
        lines.append("# TYPE snowkap_articles_body_coverage_pct gauge")
        for s in coverage["slugs"]:
            lines.append(
                f'snowkap_articles_body_coverage_pct{{slug="{s["slug"]}"}} {s["body_coverage_pct"]}'
            )
        lines.append("")
        # Last-retry-cron timestamp + result (gauge — unix epoch seconds)
        last_ret = coverage.get("last_retry_at") or 0
        last_res = coverage.get("last_retry_result") or {}
        lines.append("# HELP snowkap_full_text_retry_last_run_seconds UNIX timestamp of last successful retry-cron fire (Phase 36)")
        lines.append("# TYPE snowkap_full_text_retry_last_run_seconds gauge")
        lines.append(f"snowkap_full_text_retry_last_run_seconds {last_ret}")
        lines.append("")
        lines.append("# HELP snowkap_full_text_retry_last_bodies_added Bodies captured on the last retry-cron fire (Phase 36)")
        lines.append("# TYPE snowkap_full_text_retry_last_bodies_added gauge")
        lines.append(f"snowkap_full_text_retry_last_bodies_added {int(last_res.get('bodies_added', 0) or 0)}")
        lines.append("")
        lines.append("# HELP snowkap_full_text_retry_last_paywalled Paywalled-skipped count on the last retry-cron fire (Phase 36)")
        lines.append("# TYPE snowkap_full_text_retry_last_paywalled gauge")
        lines.append(f"snowkap_full_text_retry_last_paywalled {int(last_res.get('paywalled', 0) or 0)}")
        lines.append("")
        # Cache-status counters (from article_full_text aggregate).
        # Uses engine.db.connect dispatcher so it reads from Supabase
        # Postgres in production (SNOWKAP_DB_BACKEND=postgres), SQLite
        # in dev. Same backend the cache writes to via _cache_put().
        try:
            from engine.db import connect as _db_connect
            with _db_connect() as _conn:
                _rows = _conn.execute(
                    "SELECT status, COUNT(*) FROM article_full_text GROUP BY status"
                ).fetchall()
            by_status = {r[0]: r[1] for r in _rows}
            lines.append("# HELP snowkap_full_text_extraction_attempts_total Cached extraction outcomes (Phase 36)")
            lines.append("# TYPE snowkap_full_text_extraction_attempts_total gauge")
            for status in ("ok", "failed", "paywall", "too_short"):
                lines.append(
                    f'snowkap_full_text_extraction_attempts_total{{status="{status}"}} {by_status.get(status, 0)}'
                )
            lines.append("")
        except Exception as exc:  # noqa: BLE001 — cache stats are additive
            logger.debug("metrics: article_full_text aggregate failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 — body-coverage metrics are additive
        logger.debug("metrics: body-coverage block failed: %s", exc)

    # Phase 51.C — scheduler job health. Emits each cron job's last-run epoch +
    # an ok/error gauge so a MISSED run ("now - last_run_seconds > threshold",
    # e.g. >8d for weekly_deck_refresh) or a FAILING job ("last_status == 0")
    # can be alerted on. Source: engine.models.scheduler_state.list_all_runs.
    try:
        from engine.models.scheduler_state import list_all_runs
        runs = list_all_runs()
        if runs:
            lines.append("# HELP snowkap_scheduler_last_run_seconds UNIX timestamp of each cron job's last run (Phase 51)")
            lines.append("# TYPE snowkap_scheduler_last_run_seconds gauge")
            for r in runs:
                lines.append(
                    f'snowkap_scheduler_last_run_seconds{{job="{r.get("job_id", "")}"}} '
                    f'{float(r.get("last_run_at") or 0):.0f}'
                )
            lines.append("")
            lines.append("# HELP snowkap_scheduler_last_status 1 when the job's last run was ok, 0 otherwise (Phase 51)")
            lines.append("# TYPE snowkap_scheduler_last_status gauge")
            for r in runs:
                lines.append(
                    f'snowkap_scheduler_last_status{{job="{r.get("job_id", "")}"}} '
                    f'{1 if r.get("last_status") == "ok" else 0}'
                )
            lines.append("")
    except Exception as exc:  # noqa: BLE001 — scheduler metrics are additive
        logger.debug("metrics: scheduler_state block failed: %s", exc)

    # C#4 — HTTP request latency histogram + status counter, route-templated.
    try:
        with _http_lock:
            snapshot = {
                r: {"count": s["count"], "sum_s": s["sum_s"],
                    "buckets": dict(s["buckets"]), "status": dict(s["status"])}
                for r, s in _http_stats.items()
            }

        def _lbl(v: str) -> str:
            return v.replace("\\", "\\\\").replace('"', '\\"')

        if snapshot:
            lines.append("# HELP snowkap_http_request_duration_seconds Request latency by route template (C#4)")
            lines.append("# TYPE snowkap_http_request_duration_seconds histogram")
            for route, s in snapshot.items():
                rl = _lbl(route)
                for b in _HTTP_BUCKETS_S:
                    lines.append(
                        f'snowkap_http_request_duration_seconds_bucket{{route="{rl}",le="{b}"}} {s["buckets"][b]}'
                    )
                lines.append(
                    f'snowkap_http_request_duration_seconds_bucket{{route="{rl}",le="+Inf"}} {s["count"]}'
                )
                lines.append(f'snowkap_http_request_duration_seconds_sum{{route="{rl}"}} {s["sum_s"]:.4f}')
                lines.append(f'snowkap_http_request_duration_seconds_count{{route="{rl}"}} {s["count"]}')
            lines.append("")
            lines.append("# HELP snowkap_http_requests_total Requests by route template + status class (C#4)")
            lines.append("# TYPE snowkap_http_requests_total counter")
            for route, s in snapshot.items():
                rl = _lbl(route)
                for cls, n in s["status"].items():
                    lines.append(f'snowkap_http_requests_total{{route="{rl}",status="{cls}"}} {n}')
            lines.append("")
    except Exception as exc:  # noqa: BLE001 — request metrics are additive
        logger.debug("metrics: http request block failed: %s", exc)

    # C#6 — LLM routing health gauge. reasoning_heavy silently falls back from
    # Opus to gpt-4.1 when OPENROUTER_API_KEY is unset/out-of-credit; it only
    # logs once at boot, so without a metric ops can't alert on the recurring
    # downgrade. Alert: snowkap_llm_opus_active == 0.
    try:
        from engine.llm.health import routing_report
        rep = routing_report()
        model = str(rep.get("reasoning_heavy_model") or "unknown")
        provider = str(rep.get("provider") or "unknown")
        active = 1 if rep.get("opus_active") else 0
        lines.append("# HELP snowkap_llm_opus_active 1 when reasoning_heavy resolves to Opus, 0 on the gpt-4.1 fallback (Phase 51)")
        lines.append("# TYPE snowkap_llm_opus_active gauge")
        lines.append(f'snowkap_llm_opus_active{{model="{model}",provider="{provider}"}} {active}')
        lines.append("")
    except Exception as exc:  # noqa: BLE001 — routing metrics are additive
        logger.debug("metrics: llm routing block failed: %s", exc)

    return Response(content="\n".join(lines), media_type="text/plain; version=0.0.4")


# Register fine-grained routers FIRST so their explicit routes win over
# any overlapping legacy_adapter fallbacks.
app.include_router(companies.router)
app.include_router(insights.router)
app.include_router(ingest.router)
app.include_router(share.router)
# Phase 31 — live-fetch hybrid news endpoint
from api.routes import live_news as _live_news  # noqa: E402
app.include_router(_live_news.router)
app.include_router(admin.router)  # Phase 10: /api/admin/tenants (super_admin only)
app.include_router(admin_onboard.router)  # Phase 11B: /api/admin/onboard (manage_drip_campaigns only)
app.include_router(batch_onboard.router)  # Phase 25 W6: /api/admin/onboard/batch (manage_drip_campaigns only)
app.include_router(admin_email.router)  # Phase 13 B7: /api/admin/email-config-status
app.include_router(admin_body_coverage.router)  # Phase 36: /api/admin/body-coverage
app.include_router(admin_reanalyze.router)  # Phase 18: /api/admin/companies/{slug}/reanalyze
app.include_router(discovery.router)  # Phase 24 (W2): /api/admin/discovery/* (manage_drip_campaigns only)
app.include_router(session.router)  # Phase 24 (W4): /api/session/* (analyst session state)
app.include_router(profile.router)  # W2: /api/me/onboard (self-service onboarding for any signed-in user)
# Phase 45 — POST /api/onboard/v2 — single synchronous endpoint replacing the
# worker-queue + SSE + alias-bridge complexity of /api/me/onboard. Postgres-only.
from api.routes import onboard_v2 as _onboard_v2  # noqa: E402
app.include_router(_onboard_v2.router)
# Phase 46.E — POST /api/onboard/v3 — single clean synchronous endpoint
# with full Stage 1-12 + lede on every article (no tier gate, no eager
# pass, no SSE). Replaces v2 + admin_onboard for new onboards. v2 stays
# registered for back-compat with any client still hitting it.
from api.routes import onboard_v3 as _onboard_v3  # noqa: E402
app.include_router(_onboard_v3.router)
# Phase 48.K — weekly newsletter (unsubscribe + send-me).
from api.routes import newsletter as _newsletter  # noqa: E402
app.include_router(_newsletter.router)
app.include_router(campaigns.router)  # Phase 10: /api/campaigns/* (manage_drip_campaigns only)

# Phase 28 — SSE onboarding progress stream (companion to profile.me_onboard).
# GET /api/me/onboard/{slug}/stream
from api.routes import onboard_stream as _onboard_stream  # noqa: E402
app.include_router(_onboard_stream.router)

# Phase 28 / Feature 2 — Methodology + role-explainer endpoint for the
# info-icon drawer. GET /api/insights/{article_id}/methodology
from api.routes import methodology as _methodology  # noqa: E402
app.include_router(_methodology.router)

# Phase 34.4 — Email-myself the technical report endpoint.
# POST /api/articles/{id}/email-self  (any authenticated user; recipient = JWT sub)
from api.routes import article_email_self as _article_email_self  # noqa: E402
app.include_router(_article_email_self.router)

# Phase 34.5 — Article comments (Reddit-style, non-anonymous, 1-level reply depth).
# GET    /api/articles/{id}/comments    (list)
# POST   /api/articles/{id}/comments    (create)
# DELETE /api/comments/{id}             (author-only soft-delete)
# POST   /api/comments/{id}/vote        (cast/change/retract vote)
from api.routes import article_comments as _article_comments  # noqa: E402
app.include_router(_article_comments.router)

# Phase 34.7 — Personal Wiki (server-side bookmarks + notes).
# GET    /api/me/bookmarks                  (list, optional ?section filter)
# POST   /api/me/bookmarks                  (add/update)
# DELETE /api/me/bookmarks/{article_id}     (remove)
# PATCH  /api/me/bookmarks/{article_id}     (update note or section)
# POST   /api/me/bookmarks/bulk             (idempotent bulk migration)
from api.routes import user_bookmarks as _user_bookmarks  # noqa: E402
app.include_router(_user_bookmarks.router)

# Phase 34.6 — Forum (user-generated threads + replies, tag-filtered).
# GET    /api/forum/threads                         (list, optional ?tag filter)
# POST   /api/forum/threads                         (create)
# GET    /api/forum/threads/{thread_id}             (read with replies)
# DELETE /api/forum/threads/{thread_id}             (author-only soft-delete)
# POST   /api/forum/threads/{thread_id}/replies     (reply)
# DELETE /api/forum/replies/{reply_id}              (author-only soft-delete)
from api.routes import forum as _forum  # noqa: E402
app.include_router(_forum.router)

# POW-4 — Power of Now deck + article endpoints.
# GET /api/now/feed?company={slug}&limit={n}     (industry-shared deck, top-3 CRITICAL)
# GET /api/now/article/{id}                       (shared + personalised analysis)
# See: docs/POWER_OF_NOW_ARCHITECTURE.md §5.1, §4.4.
from api.routes import now as _now  # noqa: E402
app.include_router(_now.router)

# Power of Now adoption — Phase C: 9 net-new routers wiring the Phase B
# subsystems (advisor, autoresearcher, beliefs, chat, conversations,
# intelligence, mcp_admin, memory, wiki). Imports kept late to keep the
# rest of the file unchanged.

# Base Version Adoption L7 — typed beliefs surface
from api.routes import beliefs as _beliefs  # noqa: E402
app.include_router(_beliefs.router)  # GET /api/companies/{slug}/beliefs[/{name}]

# Base Version Adoption L6 — advisor queue surface
from api.routes import advisor as _advisor  # noqa: E402
app.include_router(_advisor.router)  # GET /api/advisor/queue, POST /api/advisor/resolve

# Wiki search + page surface (B7)
from api.routes import wiki as _wiki  # noqa: E402
app.include_router(_wiki.router)  # GET /api/wiki/{search,related,page}

# Intelligence aggregate endpoint (cross-subsystem)
from api.routes import intelligence as _intelligence  # noqa: E402
app.include_router(_intelligence.router)  # GET /api/intelligence/{slug}/{competitors,forecast}

# Autoresearcher experiments + leaderboard + run dispatch (B6)
from api.routes import autoresearcher as _autoresearcher  # noqa: E402
app.include_router(_autoresearcher.router)  # GET experiments/leaderboard, POST run

# Phase C chat + memory + MCP admin (B2, B3, B8)
from api.routes import chat as _chat  # noqa: E402
from api.routes import conversations as _conversations  # noqa: E402
from api.routes import memory as _memory  # noqa: E402
from api.routes import mcp_admin as _mcp_admin  # noqa: E402
app.include_router(_chat.router)  # POST /api/chat (SSE)
app.include_router(_conversations.router)  # GET/POST/PATCH/DELETE /api/conversations/*
app.include_router(_memory.router)  # GET/POST/DELETE /api/memory/*
app.include_router(_mcp_admin.router)  # GET /api/mcp/{manifest,tools,resources}, POST /api/mcp/invoke

# Legacy adapter LAST — exposes /api/auth, /api/news, /api/agent, etc.
app.include_router(legacy_adapter.router)


# -----------------------------------------------------------------------------
# Phase 16 — Static frontend mount (single-image production deploy).
#
# When `client/dist/` exists (i.e. the React app has been built), serve it
# at the root path. The Dockerfile builds the frontend in stage 1 and
# copies `dist/` into the runtime image, so a single container handles
# both `/api/...` and `/`. Local dev still uses Vite on :5173 → /api proxy
# to :8000; the dist mount is a no-op when the directory doesn't exist.
# -----------------------------------------------------------------------------
import os as _os
from pathlib import Path as _Path

_dist_path = _Path(__file__).resolve().parent.parent / "client" / "dist"
if _dist_path.exists() and (_dist_path / "index.html").exists():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    # Mount /assets (Vite's hashed JS/CSS bundle output)
    if (_dist_path / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(_dist_path / "assets")), name="assets")

    # SPA fallback — any non-API path serves index.html so React Router
    # can handle client-side routes like /home, /settings/onboard, etc.
    # `index.html` MUST be served with no-cache headers because it
    # references content-hashed JS/CSS bundles whose names change on
    # every build. Without no-cache, browsers cling to an old
    # index.html (which references a stale bundle) for the lifetime of
    # their cache TTL — that's how a user can keep seeing pre-fix
    # behaviour days after the fix has shipped. The hashed assets under
    # /assets/* are immutable and safe to cache forever (Vite emits a
    # new filename on every change).
    _NO_CACHE_HTML = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa_fallback(path: str):
        # /api/... routes are claimed by routers above; this handler only
        # fires for unmatched paths. Serve a static file if it exists,
        # otherwise hand back index.html (SPA pattern).
        candidate = _dist_path / path
        if candidate.is_file():
            # Hashed bundle filenames (e.g. assets/index-Dv1n-e65.js) are
            # immutable; leave their Cache-Control alone. Everything else
            # served from dist (favicon, manifest, root index.html on a
            # direct hit) gets the no-cache headers.
            if "/" not in path and path.endswith((".html", ".json", ".webmanifest")):
                return FileResponse(str(candidate), headers=_NO_CACHE_HTML)
            return FileResponse(str(candidate))
        # SPA route — always no-cache.
        return FileResponse(str(_dist_path / "index.html"), headers=_NO_CACHE_HTML)

    logger.info("static frontend mounted from %s", _dist_path)
else:
    logger.info(
        "no built frontend at %s — running API-only (use Vite dev server on :5173)",
        _dist_path,
    )
