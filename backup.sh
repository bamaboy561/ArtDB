#!/bin/bash
set -euo pipefail

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
DATE="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

docker exec artdb_db pg_dump -U "${DB_USER}" "${DB_NAME}" | gzip > "$BACKUP_DIR/artdb_$DATE.sql.gz"

find "$BACKUP_DIR" -name "artdb_*.sql.gz" -mtime +7 -delete

echo "Backup created: $BACKUP_DIR/artdb_$DATE.sql.gz"
