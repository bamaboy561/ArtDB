#!/bin/sh
set -eu

DOMAIN_NAME="${DOMAIN_NAME:?DOMAIN_NAME is required}"
CERT_PATH="/etc/letsencrypt/live/${DOMAIN_NAME}/fullchain.pem"
KEY_PATH="/etc/letsencrypt/live/${DOMAIN_NAME}/privkey.pem"
BASIC_AUTH_INCLUDE="/etc/nginx/conf.d/basic-auth.inc"
BASIC_AUTH_FILE="/etc/nginx/.htpasswd"

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if is_true "${BASIC_AUTH_ENABLED:-false}"; then
  BASIC_AUTH_USERNAME="${BASIC_AUTH_USERNAME:-}"
  BASIC_AUTH_PASSWORD="${BASIC_AUTH_PASSWORD:-}"
  BASIC_AUTH_PASSWORD_HASH="${BASIC_AUTH_PASSWORD_HASH:-}"
  BASIC_AUTH_REALM="${BASIC_AUTH_REALM:-ArtDB Protected Area}"

  if [ -z "$BASIC_AUTH_USERNAME" ]; then
    echo "BASIC_AUTH_USERNAME is required when BASIC_AUTH_ENABLED=true" >&2
    exit 1
  fi

  if [ -n "$BASIC_AUTH_PASSWORD_HASH" ]; then
    printf '%s:%s\n' "$BASIC_AUTH_USERNAME" "$BASIC_AUTH_PASSWORD_HASH" > "$BASIC_AUTH_FILE"
  elif [ -n "$BASIC_AUTH_PASSWORD" ]; then
    printf '%s\n' "$BASIC_AUTH_PASSWORD" | htpasswd -ciB "$BASIC_AUTH_FILE" "$BASIC_AUTH_USERNAME" >/dev/null
  else
    echo "Set BASIC_AUTH_PASSWORD or BASIC_AUTH_PASSWORD_HASH when BASIC_AUTH_ENABLED=true" >&2
    exit 1
  fi

  chmod 600 "$BASIC_AUTH_FILE"
  cat > "$BASIC_AUTH_INCLUDE" <<EOF
auth_basic "${BASIC_AUTH_REALM}";
auth_basic_user_file ${BASIC_AUTH_FILE};
EOF
else
  : > "$BASIC_AUTH_INCLUDE"
fi

if [ -f "$CERT_PATH" ] && [ -f "$KEY_PATH" ]; then
  TEMPLATE="/etc/nginx/custom-templates/default.ssl.conf"
else
  TEMPLATE="/etc/nginx/custom-templates/default.conf"
fi

sed "s/__DOMAIN_NAME__/${DOMAIN_NAME}/g" "$TEMPLATE" > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'

