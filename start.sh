#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r api/requirements.txt
fi

set -a
# shellcheck disable=SC1091
[[ -f .env ]] && source .env
set +a

export DATABASE_URL="${DATABASE_URL:-sqlite:////home/deepadm/uccx-portal/data/portal.db}"
export XML_DIR="${XML_DIR:-$ROOT/data/xml}"
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:8080}"

mkdir -p "$XML_DIR" data
cd api
exec ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
