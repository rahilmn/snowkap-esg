#!/usr/bin/env bash
# Phase 11A — Hourly SQLite backup for snowkap.db.
#
# Uses SQLite's online `.backup` command (safe for a live, WAL-mode DB —
# no need to stop the API or cron runner). Keeps 14 days of hourly
# snapshots, then prunes.
#
# Install (cron, Linux):
#   0 * * * * cd /path/to/snowkap-esg && bash scripts/backup_db.sh >> logs/backup.log 2>&1
#
# Install (Windows Task Scheduler): create a Basic Task → daily → repeat
# every 1 hour → action: `bash` with argument `scripts/backup_db.sh` in the
# repo working directory.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${REPO_ROOT}/data/snowkap.db"
BACKUP_DIR="${REPO_ROOT}/data/backups"
RETENTION_DAYS="${SNOWKAP_BACKUP_RETENTION_DAYS:-14}"

mkdir -p "${BACKUP_DIR}"

if [ ! -f "${DB_PATH}" ]; then
    echo "$(date -Iseconds) [backup] db missing at ${DB_PATH} — skipping" >&2
    exit 0
fi

STAMP="$(date -u +%Y%m%d%H)"  # UTC hour for monotonic ordering
DEST="${BACKUP_DIR}/snowkap.${STAMP}.db"

# SQLite online .backup via Python — cross-platform, safe on a live WAL-mode
# DB, and avoids the Windows/git-bash path-translation issue that bites the
# sqlite3 CLI binary when paths are POSIX-style.
python -c "
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close(); dst.close()
" "${DB_PATH}" "${DEST}"

echo "$(date -Iseconds) [backup] wrote ${DEST} ($(du -h "${DEST}" | cut -f1))"

# Prune old snapshots (>RETENTION_DAYS days)
find "${BACKUP_DIR}" -name 'snowkap.*.db' -mtime +"${RETENTION_DAYS}" -print -delete || true

exit 0
