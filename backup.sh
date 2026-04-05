#!/bin/bash
set -euo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "Create .env from .env.example before running backup.sh" >&2
  exit 1
fi

set -a
source .env
set +a

BACKUP_DIR="${BACKUP_DIR:-/opt/artdb/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
DATE="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found in PATH" >&2
  exit 1
fi

docker exec -e PGPASSWORD="${DB_PASSWORD:-}" artdb_db \
  pg_dump -h 127.0.0.1 -U "${DB_USER}" "${DB_NAME}" | gzip > "$BACKUP_DIR/artdb_$DATE.sql.gz"

find "$BACKUP_DIR" -name "artdb_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete

echo "Backup created: $BACKUP_DIR/artdb_$DATE.sql.gz"
