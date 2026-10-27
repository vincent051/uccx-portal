#!/usr/bin/env bash
# Démarre Docker Engine natif dans WSL si besoin.
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker n'est pas installé. Voir README (Docker Engine natif WSL)." >&2
  exit 1
fi

if ! pgrep -x dockerd >/dev/null 2>&1; then
  echo "Démarrage de dockerd..."
  sudo service docker start
  for _ in $(seq 1 30); do
    if [[ -S /var/run/docker.sock ]]; then
      break
    fi
    sleep 0.5
  done
fi

# Session sans groupe docker (Cursor/SSH) → ACL utilisateur sur le socket
if ! docker info >/dev/null 2>&1; then
  if sudo docker info >/dev/null 2>&1; then
    echo "Correction ACL docker.sock pour $USER..."
    sudo setfacl -m "u:${USER}:rw" /var/run/docker.sock || true
  else
    echo "Docker ne répond pas." >&2
    exit 1
  fi
fi

if ! docker info >/dev/null 2>&1; then
  echo "Toujours pas d'accès docker. Ouvrez un nouveau terminal WSL ou: sudo docker ..." >&2
  exit 1
fi

echo "Docker OK"
