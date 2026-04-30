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

# 1. Python deps. Different platforms install differently:
#    - Docker: handled at image-build time via the Dockerfile
#    - Replit: Nix-managed Python is PEP-668 "externally managed";
#      Replit's deploy infra pre-installs to .pythonlibs/ from
#      requirements.txt. Calling `pip install` directly fails with
#      `error: externally-managed-environment`.
#    - Local dev: a venv is expected to be activated already.
#
# Strategy: probe whether the core deps are already importable.
# If they are, skip install entirely (Docker / Replit / local-with-venv).
# Otherwise, fall back to `--user --break-system-packages` which is
# Replit's documented escape hatch for Nix-managed Pythons.
if ! python -c "import fastapi, uvicorn, openai, rdflib, jwt" 2>/dev/null; then
  echo "Python deps not loadable — installing with --user --break-system-packages..."
  python -m pip install --quiet --user --break-system-packages -r requirements.txt || {
    echo "  (fallback) installing without --break-system-packages..."
    python -m pip install --quiet --user -r requirements.txt
  }
else
  echo "Python deps already loadable; skipping install."
fi

# 2. Frontend build. Skip if dist/ exists AND is newer than the latest
#    src/ change — keeps boot time fast on Replit restarts. Wrap the
#    npm install/build in `|| true` so a transient npm error doesn't
#    block API boot — the OLD dist/ stays intact and serves the previous
#    UI version. The "Rebuild Frontend" admin script can retry later.
_build_frontend() {
  ( cd client && npm install --silent && npm run build ) && return 0
  echo "WARN: frontend build failed — keeping previous dist/ if any"
  return 1
}

if [ ! -d "client/dist" ] || [ ! -f "client/dist/index.html" ]; then
  echo "Building frontend (first boot)..."
  _build_frontend || true
elif [ -n "$(find client/src -newer client/dist -type f -print -quit 2>/dev/null)" ]; then
  echo "Frontend src changed since last build — rebuilding..."
  _build_frontend || true
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
