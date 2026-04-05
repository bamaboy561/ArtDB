#!/bin/sh
set -eu

DOMAIN_NAME="${DOMAIN_NAME:?DOMAIN_NAME is required}"
CERT_PATH="/etc/letsencrypt/live/${DOMAIN_NAME}/fullchain.pem"
KEY_PATH="/etc/letsencrypt/live/${DOMAIN_NAME}/privkey.pem"

if [ -f "$CERT_PATH" ] && [ -f "$KEY_PATH" ]; then
  TEMPLATE="/etc/nginx/custom-templates/default.ssl.conf"
else
  TEMPLATE="/etc/nginx/custom-templates/default.conf"
fi

sed "s/__DOMAIN_NAME__/${DOMAIN_NAME}/g" "$TEMPLATE" > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'

