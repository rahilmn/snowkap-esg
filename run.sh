#!/usr/bin/env bash
# Snowkap ESG — Replit / Docker entrypoint (rewritten 2026-04-29 Track A).
#
# Boot order:
#   1. Install/refresh Python deps if needed.
#   2. Install/refresh Node deps + build frontend if dist/ is missing or stale.
#   3. Initialise SQLite index if needed (idempotent).
#   4. Hand off to uvicorn — which starts the API + (in-process) scheduler.
#
# This script is idempotent. It's safe to run on every deploy / restart.
# Replit's deployment shell + the Dockerfile both invoke it.
#
# Required env (set via Replit Secrets / Docker --env):
#   OPENAI_API_KEY         — required
#   RESEND_API_KEY         — required for share-by-email
#   NEWSAPI_AI_API_KEY     — required for live news ingestion
#   JWT_SECRET             — 32+ chars, required in production
#   SNOWKAP_API_KEY        — 32+ chars, required for the X-API-Key gate
#   SNOWKAP_INTERNAL_EMAILS — comma-separated allowlist (super-admin grant)
#   SNOWKAP_FROM_ADDRESS   — Snowkap ESG <newsletter@snowkap.co.in>
#   SNOWKAP_ENV=production
#   REQUIRE_SIGNED_JWT=1
#   SENTRY_DSN             — optional
#   SNOWKAP_INPROCESS_SCHEDULER=1  — default in production; runs ingest+promote loops
#
# Tunables (optional, sensible defaults in api/main.py):
#   SNOWKAP_INGEST_INTERVAL_MIN=60
#   SNOWKAP_PROMOTE_INTERVAL_MIN=30
#   SNOWKAP_MAX_PER_QUERY=10
#   SNOWKAP_PER_RUN_LIMIT=5

set -euo pipefail

cd "$(dirname "$0")"

echo "=== SNOWKAP ESG Intelligence Engine ==="
echo "Boot start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 1. Python deps. Replit reuses the venv across boots so this is fast on
#    subsequent runs. The Dockerfile multi-stage build also handles this,
#    but harmless to re-check.
if [ ! -f ".venv/.deps_installed" ]; then
  echo "Installing Python dependencies..."
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
  mkdir -p .venv
  touch .venv/.deps_installed
fi

# 2. Frontend build. Skip if dist/ exists AND is newer than the latest
#    src/ change — keeps boot time fast on Replit restarts.
if [ ! -d "client/dist" ]; then
  echo "Building frontend (first boot)..."
  ( cd client && npm install --silent && npm run build )
elif [ -n "$(find client/src -newer client/dist -type f -print -quit 2>/dev/null)" ]; then
  echo "Frontend src changed since last build — rebuilding..."
  ( cd client && npm install --silent && npm run build )
else
  echo "Frontend dist/ is up to date; skipping rebuild."
fi

# 3. SQLite index init (idempotent — does nothing if schema already present)
python -c "from engine.index.sqlite_index import ensure_schema; ensure_schema()"

# 4. Hand off to uvicorn. Single worker keeps the in-process scheduler
#    behaviour predictable. For higher concurrency, set SNOWKAP_WORKERS
#    AND SNOWKAP_INPROCESS_SCHEDULER=0 (run the scheduler as a separate
#    Replit scheduled task instead).
WORKERS="${SNOWKAP_WORKERS:-1}"
PORT="${PORT:-8000}"

echo "Starting API on 0.0.0.0:${PORT} with ${WORKERS} worker(s)..."
exec python -m uvicorn api.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WORKERS}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
