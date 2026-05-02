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
import time
import uuid
from pathlib import Path

# Make the project root importable when uvicorn starts us up
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api.routes import admin, admin_email, admin_onboard, admin_reanalyze, campaigns, companies, ingest, insights, legacy_adapter, share
from engine.config import load_settings  # noqa: F401  (eager-loads .env)
from engine.index.sqlite_index import ensure_schema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 11D: structlog JSON config
# ---------------------------------------------------------------------------


def _configure_structlog() -> None:
    """JSON logging bound with contextvars (request_id, tenant_id). Safe to
    call multiple times (structlog is idempotent)."""
    try:
        import structlog
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.add_log_level,
                structlog.processors.StackInfoRenderer(),
                structlog.dev.set_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            cache_logger_on_first_use=True,
        )
    except ImportError:
        logger.warning("structlog not installed; using stdlib logging fallback")


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5500",
        "http://localhost:3000",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5500",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,  # legacy client sends Authorization header
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)


@app.on_event("startup")
def _startup() -> None:
    ensure_schema()
    _configure_structlog()
    _check_production_env()  # raises RuntimeError in prod if secrets missing
    _init_sentry()
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

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            run_ingest_job,
            trigger="interval",
            minutes=ingest_min,
            args=[max_per_query, per_run_limit],
            next_run_time=None,  # wait for first interval — don't ingest at boot
            id="ingest_all_companies",
            replace_existing=True,
        )
        if promote_min > 0:
            scheduler.add_job(
                run_promote_job,
                trigger="interval",
                minutes=promote_min,
                next_run_time=None,
                id="discovery_promote",
                replace_existing=True,
            )
        scheduler.start()
        # Stash on app.state so we can shut it down cleanly + introspect via /metrics
        app.state.scheduler = scheduler
        logger.info(
            "in-process scheduler started (ingest_every=%dmin, promote_every=%dmin)",
            ingest_min,
            promote_min,
        )
    except Exception as exc:  # noqa: BLE001
        # Scheduler must NEVER block API startup. Log and continue.
        logger.exception("in-process scheduler failed to start: %s", exc)


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


# Phase 11D: request-timing middleware + request_id contextvar binding
@app.middleware("http")
async def _request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    try:
        import structlog
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
    except ImportError:
        pass
    start = time.perf_counter()
    try:
        response: Response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.exception("request failed after %.0fms path=%s", elapsed_ms, request.url.path)
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.0f}"
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
    return {"status": "ok", "service": "snowkap-esg-api", "version": "0.2.0"}


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

    lines = [
        "# HELP snowkap_articles_total Total analysed articles in the index by tier",
        "# TYPE snowkap_articles_total gauge",
        f'snowkap_articles_total{{tier="HOME"}} {by_tier.get("HOME", 0)}',
        f'snowkap_articles_total{{tier="SECONDARY"}} {by_tier.get("SECONDARY", 0)}',
        f'snowkap_articles_total{{tier="REJECTED"}} {by_tier.get("REJECTED", 0)}',
        f'snowkap_articles_total{{tier="ALL"}} {idx_stats.get("total", 0)}',
        "",
        "# HELP snowkap_campaigns Drip campaigns by status",
        "# TYPE snowkap_campaigns gauge",
        f'snowkap_campaigns{{status="active"}} {active_campaigns}',
        f'snowkap_campaigns{{status="paused"}} {paused_campaigns}',
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
    return Response(content="\n".join(lines), media_type="text/plain; version=0.0.4")


# Register fine-grained routers FIRST so their explicit routes win over
# any overlapping legacy_adapter fallbacks.
app.include_router(companies.router)
app.include_router(insights.router)
app.include_router(ingest.router)
app.include_router(share.router)
app.include_router(admin.router)  # Phase 10: /api/admin/tenants (super_admin only)
app.include_router(admin_onboard.router)  # Phase 11B: /api/admin/onboard (manage_drip_campaigns only)
app.include_router(admin_email.router)  # Phase 13 B7: /api/admin/email-config-status
app.include_router(admin_reanalyze.router)  # Phase 18: /api/admin/companies/{slug}/reanalyze
app.include_router(campaigns.router)  # Phase 10: /api/campaigns/* (manage_drip_campaigns only)

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
    @app.get("/{path:path}", include_in_schema=False)
    async def _spa_fallback(path: str):
        # /api/... routes are claimed by routers above; this handler only
        # fires for unmatched paths. Serve a static file if it exists,
        # otherwise hand back index.html (SPA pattern).
        candidate = _dist_path / path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_dist_path / "index.html"))

    logger.info("static frontend mounted from %s", _dist_path)
else:
    logger.info(
        "no built frontend at %s — running API-only (use Vite dev server on :5173)",
        _dist_path,
    )
