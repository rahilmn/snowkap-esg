"""SNOWKAP ESG Platform — FastAPI Application Entry Point.

Per CLAUDE.md: FastAPI + Pydantic v2 (Python 3.12), async-first.
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from backend.core.config import settings
from backend.core.database import engine
from backend.core.socketio import sio_app
from backend.agent.router import router as agent_router  # noqa: force-reload-v2
from backend.routers import admin, analysis, auth, campaigns, companies, media, news, ontology, predictions
from backend.routers import preferences, tenant_config, ftux

logger = structlog.get_logger()

# --- Sentry integration (Phase 9) ---
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            release=f"snowkap-esg@{settings.APP_VERSION}",
            traces_sample_rate=0.1 if settings.ENVIRONMENT == "production" else 1.0,
            profiles_sample_rate=0.1,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                SqlalchemyIntegration(),
            ],
        )
        logger.info("sentry_initialized", environment=settings.ENVIRONMENT)
    except ImportError:
        logger.warning("sentry_sdk_not_installed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle."""
    logger.info("snowkap_starting", version=settings.APP_VERSION, environment=settings.ENVIRONMENT)
    yield
    # Cleanup persistent connections
    from backend.ontology.jena_client import jena_client
    await jena_client.close()
    await engine.dispose()
    logger.info("snowkap_shutdown")


app = FastAPI(
    title="SNOWKAP ESG Platform",
    description="ESG Intelligence Platform with Smart Ontology & Causal Chain Reasoning",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# QA: Rate limiting — 60 requests/minute per IP for general endpoints
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if settings.ENVIRONMENT == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        if os.environ.get("ENVIRONMENT") != "deployed":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# CORS — explicit methods and headers (no wildcards)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization", "Content-Type", "Accept", "Origin",
        "X-Requested-With", "X-Tenant-Id",
    ],
    expose_headers=["X-Request-Id"],
)

# --- Routers per CLAUDE.md: modular FastAPI routers ---
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(companies.router, prefix="/api/companies", tags=["companies"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
app.include_router(news.router, prefix="/api/news", tags=["news"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["predictions"])
app.include_router(ontology.router, prefix="/api/ontology", tags=["ontology"])
app.include_router(campaigns.router, prefix="/api/campaigns", tags=["campaigns"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(tenant_config.router, prefix="/api/tenant-config", tags=["tenant-config"])
app.include_router(media.router, prefix="/api/media", tags=["media"])
app.include_router(preferences.router, prefix="/api/preferences", tags=["preferences"])
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])
app.include_router(ftux.router, prefix="/api/ftux", tags=["ftux"])

# Mount Socket.IO — per CLAUDE.md: Socket.IO + Redis pub/sub
app.mount("/ws", sio_app)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all: log full traceback server-side, return safe message to client."""
    import traceback
    tb = traceback.format_exc()
    logger.error("unhandled_exception", path=str(request.url.path), error=str(exc), traceback=tb[:1000])
    from fastapi.responses import JSONResponse
    # NEVER expose traceback to client in production
    detail = str(exc)[:200] if settings.DEBUG else "Internal server error"
    return JSONResponse(
        status_code=500,
        content={"detail": detail},
    )


@app.get("/api/health")
async def health_check() -> dict:
    """Health check endpoint for Docker and monitoring."""
    return {"status": "healthy", "service": "esg-api", "version": settings.APP_VERSION}


# --- Serve frontend SPA from built Vite output (for Replit / production) ---
#
# IMPORTANT: The SPA catch-all is mounted via app.mount() rather than
# @app.api_route() so it has LOWER priority than API router routes.
# Using api_route("/{path:path}") would swallow requests like GET /api/companies
# (without trailing slash) before FastAPI's redirect_slashes logic can redirect
# them to /api/companies/ where the router endpoint lives.  Mounted apps are
# checked only AFTER all explicit routes, so API routes always win.
_client_dist = Path(__file__).resolve().parent.parent / "client" / "dist"
if _client_dist.is_dir():
    _assets_dir = _client_dist / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="static-assets")

    # SPA fallback — read index.html from disk on each request so rebuilds take effect immediately
    _index_html_path = _client_dist / "index.html"

    @app.middleware("http")
    async def spa_fallback(request: Request, call_next):
        """SPA fallback: serve index.html for non-API, non-static paths that 404."""
        response = await call_next(request)
        path = request.url.path
        # Only serve SPA fallback for non-API paths that returned 404 or 405
        if (
            response.status_code in (404, 405)
            and not path.startswith(("/api/", "/api", "/ws/", "/ws"))
            and "." not in path.rsplit("/", 1)[-1]
        ):
            return Response(content=_index_html_path.read_bytes(), media_type="text/html")
        return response

    # Serve static files from dist/ (JS, CSS, fonts, images)
    app.mount("/", StaticFiles(directory=str(_client_dist)), name="spa-static")
# reload Tue Mar 31 14:30:07 IST 2026
