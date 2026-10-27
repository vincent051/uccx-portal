#!/usr/bin/env bash
# Génère letsencrypt/htpasswd depuis .env (BASIC_AUTH_* ou ADMIN_*).
# Appelé par le service Compose auth-init ; utilisable aussi à la main.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/letsencrypt/htpasswd}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

USER_NAME="${BASIC_AUTH_USER:-${ADMIN_USER:-admin}}"
PASS="${BASIC_AUTH_PASSWORD:-${ADMIN_PASSWORD:-}}"

if [[ -z "$PASS" ]]; then
  echo "ERROR: définir BASIC_AUTH_PASSWORD ou ADMIN_PASSWORD dans .env" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
if command -v htpasswd >/dev/null 2>&1; then
  htpasswd -nbB "$USER_NAME" "$PASS" >"$OUT"
elif command -v openssl >/dev/null 2>&1; then
  # apr1 compatible Traefik basicAuth
  HASH="$(openssl passwd -apr1 "$PASS")"
  printf '%s:%s\n' "$USER_NAME" "$HASH" >"$OUT"
else
  echo "ERROR: htpasswd ou openssl requis" >&2
  exit 1
fi
chmod 600 "$OUT"
echo "htpasswd OK → $OUT (user=$USER_NAME)"
