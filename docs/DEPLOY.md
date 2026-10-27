# Déploiement UCCX Portal

## Prérequis

- Docker Engine + plugin Compose v2
- Ports **80**, **443** (Traefik) et optionnellement **8080** (HTTP direct UCCX / TTS)
- DNS `PORTAL_HOST` → IP publique (Let's Encrypt) **ou** accès local via `https://127.0.0.1`

## Install en ligne

```bash
git clone https://github.com/<user>/uccx-portal.git
cd uccx-portal
cp .env.example .env
# Éditer au minimum :
#   PORTAL_HOST=portal.example.com
#   PUBLIC_BASE_URL=https://portal.example.com
#   ADMIN_PASSWORD=...
#   BASIC_AUTH_PASSWORD=...
#   ACME_EMAIL=admin@example.com
#   UCCX_HOST / UCCX_USER / UCCX_PASSWORD
./portal.sh install
./portal.sh doctor
```

`install` génère un `SECRET_KEY` si vide, crée `letsencrypt/acme.json`, build et démarre la stack, puis attend `/api/public/health`.

### Assets UI Post (optionnel)

Les CSS/fonts Post ne sont pas dans le dépôt. Si vous avez une copie de `uccx/public` :

```bash
cp docker-compose.post-assets.yml.example docker-compose.post-assets.yml
# dans .env :
# UCCX_PUBLIC_DIR=/path/to/uccx/public
./portal.sh update
```

`portal.sh` utilise automatiquement `docker-compose.post-assets.yml` s’il est présent.

### Modèles TTS / STT

```bash
./scripts/download-piper-model.sh
./scripts/download-kokoro-model.sh
# Whisper se télécharge au premier usage STT (ou via le bundle offline)
```

## Mise à jour

```bash
./portal.sh update
```

Équivalent : `git pull` + `docker compose up -d --build` + healthcheck.

## Offline (air-gap)

### Machine avec Internet

```bash
./portal.sh export-offline
# → dist/uccx-portal-offline-YYYYMMDD.tar.gz
```

Le bundle contient : images Docker (`api`, `postgres`, `traefik`, `httpd`), code, scripts, modèles `data/piper|kokoro|whisper`.

### Machine sans Internet

```bash
# Docker déjà installé (paquets offline)
tar xzf uccx-portal-offline-YYYYMMDD.tar.gz
cd bundle   # ou copier le contenu vers le répertoire cible
./portal.sh import-offline /chemin/uccx-portal-offline-YYYYMMDD.tar.gz
```

`import-offline` fait `docker load` puis `compose up -d --no-build` (aucun `apt` / `pip` / GitHub).

Désactiver les services cloud dans `.env` :

```bash
EDGE_ENABLED=false
ELEVENLABS_ENABLED=false
```

Let's Encrypt ne fonctionne pas hors ligne : utilisez `https://127.0.0.1` (cert auto-signé) ou un certificat d’entreprise.

## Certificats

| Mode | Comportement |
|------|----------------|
| Prod + DNS + ports 80/443 | Traefik ACME (`ACME_EMAIL`, stockage `letsencrypt/acme.json`) |
| Local / offline | Routes `Host(localhost\|127.0.0.1)` avec TLS auto-signé Traefik |

## Commandes utiles

| Commande | Rôle |
|----------|------|
| `./portal.sh status` | `compose ps` + health |
| `./portal.sh doctor` | Diagnostic |
| `./portal.sh logs` | Logs follow |
| `./scripts/ensure-docker.sh` | Démarre Docker / ACL socket (WSL) |

## Sécurité

- Ne pas committer `.env`, `letsencrypt/acme.json`, `letsencrypt/htpasswd`
- Changer `ADMIN_PASSWORD` et `BASIC_AUTH_PASSWORD` dès l’install
- Le header JWT applicatif est **`X-UCCX-Token`** (pas `Authorization: Bearer`, réservé au Basic Traefik)
