# =============================================================================
# Snowkap ESG — Production Dockerfile (Phase 16)
#
# Single-image build that serves both the FastAPI backend AND the built
# React frontend (vite build → static files served by FastAPI). One
# container, one port — designed for a one-click managed-host deploy
# (Render, Railway, Fly.io, Replit Pro, Heroku, Cloud Run).
#
# Build:    docker build -t snowkap-esg .
# Run:      docker run -p 8000:8000 --env-file .env snowkap-esg
# Compose:  docker compose up
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: build the React frontend
# -----------------------------------------------------------------------------
FROM node:20-alpine AS frontend

WORKDIR /app/client

# Cache npm install layer
COPY client/package.json client/package-lock.json* ./
RUN npm ci --omit=dev || npm install --omit=dev

COPY client/ ./
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2: Python runtime + bundled frontend
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# System deps for rdflib + pdfplumber + Pillow + sqlite3 backup
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        sqlite3 \
        libsqlite3-dev \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/snowkap

# Cache pip install layer
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the engine + API + ontology + scripts
COPY api/        ./api/
COPY engine/     ./engine/
COPY config/     ./config/
COPY data/       ./data/
COPY scripts/    ./scripts/

# Copy the built frontend from the previous stage
COPY --from=frontend /app/client/dist /opt/snowkap/client/dist

# Pre-warm the ontology graph so the first request doesn't pay the load cost
RUN python -c "from engine.ontology.graph import OntologyGraph; OntologyGraph().load()" || true

# Non-root user for security
RUN useradd --system --user-group --create-home snowkap && \
    chown -R snowkap:snowkap /opt/snowkap
USER snowkap

EXPOSE 8000

# Healthcheck for the platform
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Boot:
#   - Phase 11A signed JWT verification (REQUIRE_SIGNED_JWT=1 in prod)
#   - Phase 11D structlog + Sentry init at startup
#   - Phase 13 S3 eager ontology load at startup (fail-fast on bad TTL)
#   - Phase 13 production env guard (fails-fast on missing secrets)
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--log-level", "info"]
