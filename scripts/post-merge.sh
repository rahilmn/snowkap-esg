#!/usr/bin/env bash
# Post-merge setup — runs automatically after a task agent's branch
# is merged into main. Mirrors the prep steps in run.sh (Python deps,
# frontend build, SQLite schema) but stops short of starting uvicorn
# (the workflow reconciliation step restarts the API workflow).
#
# Idempotent. Stdin is closed by the platform, so all package
# installs use non-interactive flags. Fails fast on real errors;
# tolerates a transient frontend-build failure (the existing dist/
# keeps serving the previous UI in that case).

set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== post-merge: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 1. Python deps. Probe-then-install — same logic run.sh uses on boot.
if ! python -c "import fastapi, uvicorn, openai, rdflib, jwt" 2>/dev/null; then
  echo "[post-merge] python deps missing — installing..."
  python -m pip install --quiet --user --break-system-packages -r requirements.txt || \
    python -m pip install --quiet --user -r requirements.txt
else
  echo "[post-merge] python deps already loadable; skipping."
fi

# 2. Frontend build. Rebuild only when src/ is newer than dist/ or
#    dist/ is missing. A transient npm failure leaves the previous
#    dist/ intact, so we don't fail the merge on it.
if [ ! -d "client/dist" ] || [ ! -f "client/dist/index.html" ]; then
  echo "[post-merge] building frontend (no dist/)..."
  ( cd client && npm install --silent --no-audit --no-fund && npm run build ) || \
    echo "[post-merge] WARN: frontend build failed — keeping previous dist/"
elif [ -n "$(find client/src -newer client/dist -type f -print -quit 2>/dev/null)" ]; then
  echo "[post-merge] frontend src changed — rebuilding..."
  ( cd client && npm install --silent --no-audit --no-fund && npm run build ) || \
    echo "[post-merge] WARN: frontend build failed — keeping previous dist/"
else
  echo "[post-merge] frontend dist/ up to date; skipping rebuild."
fi

# 3. SQLite index schema (idempotent).
python -c "from engine.index.sqlite_index import ensure_schema; ensure_schema()"

echo "[post-merge] done."
