#!/usr/bin/env bash
set -e

echo "=== SNOWKAP ESG Platform ==="
exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
