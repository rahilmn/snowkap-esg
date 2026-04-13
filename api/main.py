"""FastAPI application for the Snowkap ESG Intelligence Engine.

Run with::

    uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

Authentication (optional — only enforced if ``SNOWKAP_API_KEY`` is set):
    Either ``X-API-Key: <your-key>`` or ``Authorization: Bearer <token>``
    (the legacy UI uses the Bearer header; the adapter mints the token).

OpenAPI docs: http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Make the project root importable when uvicorn starts us up
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import companies, ingest, insights, legacy_adapter
from engine.config import load_settings  # noqa: F401  (eager-loads .env)
from engine.index.sqlite_index import ensure_schema

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Snowkap ESG Intelligence Engine",
    description="Ontology-driven ESG intelligence for 7 target companies. Serves both the new minimal routes (/api/companies, /api/insights, /api/feed) and the legacy-UI adapter (/api/auth, /api/news, /api/agent, /api/preferences, /api/ontology).",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:4173",
        "http://127.0.0.1:5173",
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
    key_set = bool(os.environ.get("SNOWKAP_API_KEY", "").strip())
    logger.info("api startup: auth=%s", "enabled" if key_set else "disabled (dev mode)")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "snowkap-esg-api", "version": "0.2.0"}


# Register fine-grained routers FIRST so their explicit routes win over
# any overlapping legacy_adapter fallbacks.
app.include_router(companies.router)
app.include_router(insights.router)
app.include_router(ingest.router)

# Legacy adapter LAST — exposes /api/auth, /api/news, /api/agent, etc.
app.include_router(legacy_adapter.router)
