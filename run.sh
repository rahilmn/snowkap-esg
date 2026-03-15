#!/usr/bin/env bash
set -e

echo "=== SNOWKAP ESG Platform — Replit Startup ==="

# 1. Build frontend (skip if already built)
if [ ! -d "client/dist" ] || [ "client/src" -nt "client/dist/index.html" ]; then
  echo ">> Building frontend..."
  cd client
  npm install --legacy-peer-deps
  npm run build
  cd ..
  echo ">> Frontend built."
else
  echo ">> Frontend already built, skipping."
fi

# 2. Install Python deps (pip caches, so fast on reruns)
echo ">> Installing Python dependencies..."
pip install -q -r requirements.txt

# 3. Run database migrations
echo ">> Running database migrations..."
python -m alembic -c backend/migrations/alembic.ini upgrade head || echo ">> Migration warning (may already be up to date)"

# 4. Start FastAPI server
echo ">> Starting SNOWKAP ESG API on port 8000..."
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
