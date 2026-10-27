#!/usr/bin/env bash
# Télécharge les modèles Piper FR / LB / EN / DE sous data/piper/ (idempotent).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${PIPER_MODEL_DIR:-$ROOT/data/piper}"
HF="https://huggingface.co/rhasspy/piper-voices/resolve/main"

# voice_stem|hf_subdir
MODELS=(
  "fr_FR-siwis-medium|fr/fr_FR/siwis/medium"
  "fr_FR-upmc-medium|fr/fr_FR/upmc/medium"
  "lb_LU-marylux-medium|lb/lb_LU/marylux/medium"
  "en_US-lessac-medium|en/en_US/lessac/medium"
  "en_US-amy-medium|en/en_US/amy/medium"
  "de_DE-thorsten-medium|de/de_DE/thorsten/medium"
  "de_DE-kerstin-low|de/de_DE/kerstin/low"
)

mkdir -p "$DEST"
cd "$DEST"

for entry in "${MODELS[@]}"; do
  voice="${entry%%|*}"
  sub="${entry#*|}"
  onnx="${voice}.onnx"
  json="${voice}.onnx.json"
  if [[ -f "$onnx" && -f "$json" ]]; then
    echo "OK déjà présent : $DEST/$onnx"
    continue
  fi
  echo "Téléchargement $voice → $DEST"
  curl -fL --progress-bar -o "$onnx" "$HF/$sub/$onnx"
  curl -fL --progress-bar -o "$json" "$HF/$sub/$json"
  echo "OK : $onnx ($(du -h "$onnx" | cut -f1))"
done

echo "Modèles dans $DEST :"
ls -lh ./*.onnx ./*.onnx.json 2>/dev/null || true
