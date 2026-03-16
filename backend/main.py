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
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.core.config import settings
from backend.core.database import engine
from backend.core.socketio import sio_app
from backend.agent.router import router as agent_router
from backend.routers import admin, analysis, auth, campaigns, companies, media, news, ontology, predictions
from backend.routers import tenant_config

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
app.include_router(agent_router, prefix="/api/agent", tags=["agent"])

# Mount Socket.IO — per CLAUDE.md: Socket.IO + Redis pub/sub
app.mount("/ws", sio_app)


@app.get("/api/health")
async def health_check() -> dict:
    """Health check endpoint for Docker and monitoring."""
    return {"status": "healthy", "service": "esg-api", "version": settings.APP_VERSION}


# --- Serve frontend SPA from built Vite output (for Replit / production) ---
_client_dist = Path(__file__).resolve().parent.parent / "client" / "dist"
if _client_dist.is_dir():
    _assets_dir = _client_dist / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="static-assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def serve_spa(path: str) -> FileResponse:
        """Serve the React SPA — any non-API route returns index.html."""
        file_path = _client_dist / path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(_client_dist / "index.html"))
