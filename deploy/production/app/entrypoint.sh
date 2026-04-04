#!/bin/sh
set -eu

RETRIES="${DB_INIT_RETRIES:-30}"
COUNT=0

until python scripts/init_db.py; do
  COUNT=$((COUNT + 1))
  if [ "$COUNT" -ge "$RETRIES" ]; then
    echo "Database initialization failed after ${RETRIES} attempts." >&2
    exit 1
  fi
  sleep 2
done

exec streamlit run app.py \
  --server.address=0.0.0.0 \
  --server.port=8501 \
  --server.headless=true

