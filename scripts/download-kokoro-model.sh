#!/usr/bin/env bash
# Télécharge Kokoro int8 + voices-v1.0.bin sous data/kokoro/ (idempotent).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${KOKORO_MODEL_DIR:-$ROOT/data/kokoro}"
BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

mkdir -p "$DEST"
cd "$DEST"

download() {
  local file="$1"
  local url="$2"
  if [[ -f "$file" && $(stat -c%s "$file" 2>/dev/null || echo 0) -gt 1000 ]]; then
    echo "OK déjà présent : $DEST/$file"
    return 0
  fi
  echo "Téléchargement $file → $DEST"
  curl -fL --progress-bar -o "${file}.partial" "$url"
  mv -f "${file}.partial" "$file"
  echo "OK : $file ($(du -h "$file" | cut -f1))"
}

download "kokoro-v1.0.int8.onnx" "$BASE/kokoro-v1.0.int8.onnx"
download "voices-v1.0.bin" "$BASE/voices-v1.0.bin"

echo "Modèles dans $DEST :"
ls -lh . 2>/dev/null || true
