#!/bin/bash
# SNOWKAP ESG Platform — Automated Backup Script
# Per MASTER_BUILD_PLAN Phase 9: Daily 2am backups
# Usage: ./scripts/backup.sh [backup_dir]
#
# Add to crontab: 0 2 * * * /path/to/snowkap-esg/scripts/backup.sh /backups

set -euo pipefail

BACKUP_DIR="${1:-/backups/snowkap}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="${BACKUP_DIR}/${TIMESTAMP}"

mkdir -p "${BACKUP_PATH}"

echo "[${TIMESTAMP}] Starting SNOWKAP ESG backup..."

# 1. PostgreSQL dump
echo "  Backing up PostgreSQL..."
docker compose exec -T postgres pg_dump \
  -U esg_user \
  -d esg_platform \
  --format=custom \
  --compress=9 \
  > "${BACKUP_PATH}/postgres.dump"

echo "  PostgreSQL: $(du -h "${BACKUP_PATH}/postgres.dump" | cut -f1)"

# 2. Jena knowledge graph export
echo "  Backing up Jena graphs..."
curl -s "http://localhost:3030/esg/data" \
  -H "Accept: application/n-quads" \
  > "${BACKUP_PATH}/jena_graphs.nq" 2>/dev/null || echo "  Warning: Jena export failed (may not be running)"

if [ -f "${BACKUP_PATH}/jena_graphs.nq" ]; then
  xz -9 "${BACKUP_PATH}/jena_graphs.nq"
  echo "  Jena: $(du -h "${BACKUP_PATH}/jena_graphs.nq.xz" 2>/dev/null | cut -f1)"
fi

# 3. Redis RDB snapshot
echo "  Backing up Redis..."
docker compose exec -T redis redis-cli BGSAVE > /dev/null 2>&1
sleep 2
docker compose cp redis:/data/dump.rdb "${BACKUP_PATH}/redis.rdb" 2>/dev/null || echo "  Warning: Redis backup failed"

# 4. Compress the whole backup
echo "  Compressing..."
cd "${BACKUP_DIR}"
tar -cf "${TIMESTAMP}.tar" "${TIMESTAMP}/"
xz -9 "${TIMESTAMP}.tar"
rm -rf "${TIMESTAMP}/"

FINAL="${BACKUP_DIR}/${TIMESTAMP}.tar.xz"
echo "  Final backup: ${FINAL} ($(du -h "${FINAL}" | cut -f1))"

# 5. Cleanup old backups (keep last 14 days)
echo "  Cleaning up old backups..."
find "${BACKUP_DIR}" -name "*.tar.xz" -mtime +14 -delete

echo "[$(date +%Y%m%d_%H%M%S)] Backup complete: ${FINAL}"
