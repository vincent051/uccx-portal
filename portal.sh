#!/usr/bin/env bash
# UCCX Portal — install / update / status / doctor / offline bundle
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

COMPOSE=(docker compose)
API_IMAGE="uccx-portal-api:latest"
BASE_IMAGES=(postgres:16-alpine traefik:v3.6 httpd:2.4-alpine)

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "→ $*"; }

compose_files() {
  local files=(-f docker-compose.yml)
  if [[ -f docker-compose.post-assets.yml ]]; then
    files+=(-f docker-compose.post-assets.yml)
  fi
  echo "${files[@]}"
}

compose() {
  # shellcheck disable=SC2046
  "${COMPOSE[@]}" $(compose_files) "$@"
}

load_env() {
  if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
  fi
}

ensure_docker() {
  if [[ -x "$ROOT/scripts/ensure-docker.sh" ]]; then
    "$ROOT/scripts/ensure-docker.sh"
  elif ! docker info >/dev/null 2>&1; then
    die "Docker inaccessible. Démarrez le démon ou lancez scripts/ensure-docker.sh"
  fi
}

gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

ensure_env() {
  if [[ ! -f .env ]]; then
    [[ -f .env.example ]] || die ".env.example manquant"
    cp .env.example .env
    info "Créé .env depuis .env.example — éditez PORTAL_HOST, mots de passe, UCCX_*"
  fi
  load_env
  if [[ -z "${SECRET_KEY:-}" || "${SECRET_KEY}" == "change-me-to-a-long-random-string" ]]; then
    local sk
    sk="$(gen_secret)"
    if grep -q '^SECRET_KEY=' .env; then
      sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${sk}|" .env
    else
      echo "SECRET_KEY=${sk}" >> .env
    fi
    info "SECRET_KEY généré"
    load_env
  fi
  if [[ -z "${PORTAL_HOST:-}" || "${PORTAL_HOST}" == "portal.example.com" ]]; then
    echo "WARN: définissez PORTAL_HOST dans .env (DNS public ou hostname local)" >&2
  fi
  if [[ -z "${ADMIN_PASSWORD:-}" || "${ADMIN_PASSWORD}" == "change-me-admin" || "${ADMIN_PASSWORD}" == "admin" ]]; then
    echo "WARN: changez ADMIN_PASSWORD dans .env avant une mise en prod" >&2
  fi
}

ensure_dirs() {
  mkdir -p data/piper data/kokoro data/whisper letsencrypt dist
  if [[ ! -f letsencrypt/acme.json ]]; then
    touch letsencrypt/acme.json
  fi
  chmod 600 letsencrypt/acme.json 2>/dev/null || true
}

wait_health() {
  local url="${1:-http://127.0.0.1:${UCCX_HTTP_PORT:-8080}/api/public/health}"
  local i
  info "Attente health: $url"
  for i in $(seq 1 60); do
    if curl -fsS -k "$url" 2>/dev/null | grep -qi ok; then
      info "Health OK"
      return 0
    fi
    sleep 2
  done
  die "Healthcheck échoué après ~2 min ($url)"
}

cmd_install() {
  ensure_docker
  ensure_env
  ensure_dirs
  info "Build + démarrage stack"
  compose up -d --build
  load_env
  wait_health
  echo
  echo "Install terminé."
  echo "  HTTPS : https://${PORTAL_HOST:-<PORTAL_HOST>}/"
  echo "  Local : https://127.0.0.1/  (cert auto-signé)"
  echo "  Health: http://127.0.0.1:${UCCX_HTTP_PORT:-8080}/api/public/health"
  echo "  Doc   : docs/DEPLOY.md"
}

cmd_update() {
  ensure_docker
  ensure_env
  if [[ -d .git ]]; then
    info "git pull"
    git pull --ff-only || echo "WARN: git pull échoué — poursuite avec l’arbre local" >&2
  fi
  ensure_dirs
  info "Rebuild + recreate"
  compose up -d --build
  load_env
  wait_health
  info "Update terminé"
}

cmd_status() {
  ensure_docker
  load_env
  compose ps
  echo
  local url="http://127.0.0.1:${UCCX_HTTP_PORT:-8080}/api/public/health"
  if curl -fsS -k "$url" 2>/dev/null; then
    echo
  else
    echo "health: DOWN ($url)"
  fi
}

cmd_doctor() {
  local ok=0
  echo "=== doctor ==="
  if docker info >/dev/null 2>&1; then
    echo "OK  docker"
  else
    echo "FAIL docker"; ok=1
  fi
  if [[ -f .env ]]; then
    echo "OK  .env"
    load_env
  else
    echo "FAIL .env manquant (./portal.sh install)"; ok=1
  fi
  if [[ -n "${PORTAL_HOST:-}" && "${PORTAL_HOST}" != "portal.example.com" ]]; then
    echo "OK  PORTAL_HOST=${PORTAL_HOST}"
  else
    echo "WARN PORTAL_HOST non configuré"
  fi
  if [[ -n "${SECRET_KEY:-}" && "${SECRET_KEY}" != "change-me-to-a-long-random-string" ]]; then
    echo "OK  SECRET_KEY"
  else
    echo "WARN SECRET_KEY faible ou vide"
  fi
  if [[ -f letsencrypt/acme.json ]]; then
    echo "OK  letsencrypt/acme.json"
  else
    echo "WARN acme.json absent"
  fi
  if [[ -f docker-compose.post-assets.yml ]]; then
    echo "OK  post-assets override"
  else
    echo "INFO pas de docker-compose.post-assets.yml (UI Post CSS optionnelle)"
  fi
  if ls data/piper/*.onnx >/dev/null 2>&1; then
    echo "OK  modèles Piper"
  else
    echo "INFO Piper absent (./scripts/download-piper-model.sh)"
  fi
  if [[ -f data/kokoro/kokoro-v1.0.int8.onnx ]]; then
    echo "OK  modèle Kokoro"
  else
    echo "INFO Kokoro absent (./scripts/download-kokoro-model.sh)"
  fi
  if docker info >/dev/null 2>&1; then
    if compose ps --status running 2>/dev/null | grep -q api; then
      echo "OK  service api running"
    else
      echo "WARN api non running"
    fi
  fi
  local url="http://127.0.0.1:${UCCX_HTTP_PORT:-8080}/api/public/health"
  if curl -fsS -k "$url" 2>/dev/null | grep -qi ok; then
    echo "OK  health $url"
  else
    echo "WARN health KO ($url)"
  fi
  return "$ok"
}

cmd_logs() {
  ensure_docker
  compose logs -f --tail=100
}

resolve_api_image() {
  if docker image inspect "$API_IMAGE" >/dev/null 2>&1; then
    echo "$API_IMAGE"
    return
  fi
  # fallback project name from compose
  local alt
  alt="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'uccx-portal.*api' | head -1 || true)"
  if [[ -n "$alt" ]]; then
    echo "$alt"
  else
    echo "$API_IMAGE"
  fi
}

cmd_export_offline() {
  ensure_docker
  ensure_env
  ensure_dirs
  info "Téléchargement modèles manquants (si réseau)"
  if [[ -x scripts/download-piper-model.sh ]]; then
    scripts/download-piper-model.sh || echo "WARN piper download" >&2
  fi
  if [[ -x scripts/download-kokoro-model.sh ]]; then
    scripts/download-kokoro-model.sh || echo "WARN kokoro download" >&2
  fi
  info "Build image API"
  compose build api
  local api_img
  api_img="$(resolve_api_image)"
  local stamp
  stamp="$(date +%Y%m%d)"
  local work
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' RETURN
  mkdir -p "$work/bundle"
  info "docker save → images.tar.gz"
  docker save "$api_img" "${BASE_IMAGES[@]}" | gzip >"$work/bundle/images.tar.gz"
  info "Copie code + data modèles"
  tar -C "$ROOT" -cf - \
    docker-compose.yml \
    docker-compose.post-assets.yml.example \
    .env.example \
    portal.sh \
    scripts \
    api \
    docs \
    README.md \
    AGENTS.md \
    start.sh \
    2>/dev/null | tar -C "$work/bundle" -xf -
  mkdir -p "$work/bundle/data"
  for d in piper kokoro whisper; do
    if [[ -d "data/$d" ]]; then
      tar -C "$ROOT/data" -cf - "$d" | tar -C "$work/bundle/data" -xf -
    fi
  done
  mkdir -p "$work/bundle/letsencrypt" "$work/bundle/data"
  touch "$work/bundle/letsencrypt/acme.json"
  printf '%s\n' "$api_img" >"$work/bundle/API_IMAGE.txt"
  local out="dist/uccx-portal-offline-${stamp}.tar.gz"
  mkdir -p dist
  tar -C "$work" -czf "$out" bundle
  info "Bundle: $ROOT/$out ($(du -h "$out" | cut -f1))"
  echo "$out"
}

cmd_import_offline() {
  local archive="${1:-}"
  [[ -n "$archive" ]] || die "usage: ./portal.sh import-offline <bundle.tar.gz>"
  [[ -f "$archive" ]] || die "fichier introuvable: $archive"
  ensure_docker
  local work
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' RETURN
  info "Extraction $archive"
  tar -xzf "$archive" -C "$work"
  local src="$work/bundle"
  [[ -d "$src" ]] || die "archive invalide (pas de dossier bundle/)"
  # Merge into ROOT (do not overwrite existing .env)
  info "Copie fichiers vers $ROOT"
  cp -a "$src/docker-compose.yml" "$ROOT/" 2>/dev/null || true
  cp -a "$src/docker-compose.post-assets.yml.example" "$ROOT/" 2>/dev/null || true
  cp -a "$src/.env.example" "$ROOT/" 2>/dev/null || true
  cp -a "$src/portal.sh" "$ROOT/" 2>/dev/null || true
  chmod +x "$ROOT/portal.sh" 2>/dev/null || true
  if [[ -d "$src/scripts" ]]; then cp -a "$src/scripts/." "$ROOT/scripts/"; fi
  if [[ -d "$src/api" ]]; then
    mkdir -p "$ROOT/api"
    cp -a "$src/api/." "$ROOT/api/"
  fi
  if [[ -d "$src/docs" ]]; then
    mkdir -p "$ROOT/docs"
    cp -a "$src/docs/." "$ROOT/docs/"
  fi
  for d in piper kokoro whisper; do
    if [[ -d "$src/data/$d" ]]; then
      mkdir -p "$ROOT/data/$d"
      cp -a "$src/data/$d/." "$ROOT/data/$d/"
    fi
  done
  ensure_dirs
  ensure_env
  info "docker load"
  gunzip -c "$src/images.tar.gz" | docker load
  local api_img
  api_img="$(cat "$src/API_IMAGE.txt" 2>/dev/null || echo "$API_IMAGE")"
  if [[ "$api_img" != "$API_IMAGE" ]] && docker image inspect "$api_img" >/dev/null 2>&1; then
    docker tag "$api_img" "$API_IMAGE" || true
  fi
  info "Démarrage --no-build"
  compose up -d --no-build
  load_env
  wait_health
  info "Import offline terminé"
}

usage() {
  cat <<'EOF'
Usage: ./portal.sh <command> [args]

  install              Première install (env, dirs, compose up --build)
  update               git pull + compose up --build
  status               compose ps + health
  doctor               Vérifications Docker / .env / modèles / health
  logs                 Logs stack (follow)
  export-offline       Bundle images + code + modèles → dist/*.tar.gz
  import-offline FILE  Restaure un bundle et démarre --no-build

Voir docs/DEPLOY.md
EOF
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    install) cmd_install "$@" ;;
    update) cmd_update "$@" ;;
    status) cmd_status "$@" ;;
    doctor) cmd_doctor "$@" ;;
    logs) cmd_logs "$@" ;;
    export-offline) cmd_export_offline "$@" ;;
    import-offline) cmd_import_offline "$@" ;;
    -h|--help|help|"") usage ;;
    *) die "commande inconnue: $cmd (./portal.sh help)" ;;
  esac
}

main "$@"
