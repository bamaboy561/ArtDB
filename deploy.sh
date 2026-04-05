#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "Create .env from .env.example before running deploy.sh" >&2
  exit 1
fi

set -a
source .env
set +a

ACTION="${1:-update}"

case "$ACTION" in
  up)
    echo "Starting ArtDB services..."
    docker compose up -d --build
    echo "ArtDB services are up."
    ;;
  update|deploy)
    echo "Pulling latest changes from Git..."
    git pull origin main

    echo "Rebuilding and restarting services..."
    docker compose up -d --build

    echo "Cleaning up unused Docker images..."
    docker image prune -f

    echo "ArtDB successfully deployed."
    ;;
  ssl)
    if [[ -z "${DOMAIN_NAME:-}" || -z "${LETSENCRYPT_EMAIL:-}" ]]; then
      echo "Fill DOMAIN_NAME and LETSENCRYPT_EMAIL in .env before requesting SSL." >&2
      exit 1
    fi

    echo "Starting nginx and certbot..."
    docker compose up -d nginx certbot

    echo "Requesting Let's Encrypt certificate for ${DOMAIN_NAME}..."
    docker compose run --rm --entrypoint certbot certbot certonly \
      --webroot \
      -w /var/www/certbot \
      -d "$DOMAIN_NAME" \
      --email "$LETSENCRYPT_EMAIL" \
      --agree-tos \
      --no-eff-email

    echo "Restarting nginx with SSL configuration..."
    docker compose restart nginx
    echo "SSL is configured."
    ;;
  logs)
    docker compose logs -f "${2:-}"
    ;;
  *)
    echo "Usage: ./deploy.sh [up|update|deploy|ssl|logs]" >&2
    exit 1
    ;;
esac
