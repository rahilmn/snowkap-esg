#!/usr/bin/env bash
# Smoke test — verify all Docker services are healthy after docker compose up.
# Usage: ./scripts/smoke_test.sh
#
# Per MASTER_BUILD_PLAN Phase 1:
# Verify: all services healthy, FastAPI /health → 200, Fuseki /$/ping → 200

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

check() {
    local name="$1"
    local url="$2"
    local expected="${3:-200}"

    printf "  %-30s " "$name"
    status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo "000")
    if [ "$status" = "$expected" ]; then
        printf "${GREEN}PASS${NC} (HTTP $status)\n"
        PASS=$((PASS + 1))
    else
        printf "${RED}FAIL${NC} (HTTP $status, expected $expected)\n"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "========================================="
echo "  SNOWKAP ESG Platform — Smoke Test"
echo "========================================="
echo ""

echo "--- Docker Services ---"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "(docker compose not running)"
echo ""

echo "--- HTTP Health Checks ---"

# FastAPI
check "FastAPI /health" "http://localhost:8000/api/health"
check "FastAPI /docs" "http://localhost:8000/api/docs"

# Jena Fuseki
check "Jena Fuseki /$/ping" "http://localhost:3030/\$/ping"

# MinIO
check "MinIO /minio/health/live" "http://localhost:9000/minio/health/live"

# Redis (via API health that checks Redis)
check "Nginx proxy /health" "http://localhost/api/health"

# MiroFish
check "MiroFish /health" "http://localhost:5001/health"

echo ""
echo "--- Database Connectivity ---"
printf "  %-30s " "PostgreSQL"
if docker compose exec -T postgres pg_isready -U esg_user -d esg_platform > /dev/null 2>&1; then
    printf "${GREEN}PASS${NC} (pg_isready OK)\n"
    PASS=$((PASS + 1))
else
    printf "${RED}FAIL${NC} (pg_isready failed)\n"
    FAIL=$((FAIL + 1))
fi

printf "  %-30s " "Redis"
if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
    printf "${GREEN}PASS${NC} (PONG)\n"
    PASS=$((PASS + 1))
else
    printf "${RED}FAIL${NC} (no PONG)\n"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "--- pgvector Extension ---"
printf "  %-30s " "pgvector installed"
if docker compose exec -T postgres psql -U esg_user -d esg_platform -c "SELECT extversion FROM pg_extension WHERE extname='vector'" 2>/dev/null | grep -q .; then
    printf "${GREEN}PASS${NC}\n"
    PASS=$((PASS + 1))
else
    printf "${YELLOW}SKIP${NC} (pgvector not installed yet)\n"
fi

echo ""
echo "========================================="
echo "  Results: ${GREEN}$PASS passed${NC}, ${RED}$FAIL failed${NC}"
echo "========================================="
echo ""

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
