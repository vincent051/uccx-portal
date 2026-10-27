# UCCX Portal

Portail web Docker pour administrer les applications Cisco UCCX : paramètres AppAdmin, calendriers, prompts (TTS/STT), SkillsManager, SSO SAML et gestion des utilisateurs.

## Fonctionnalités

- **Applications** — import depuis UCCX, édition des script params, calendriers `CCCalendar`, prompts Playable
- **Prompts** — upload µ-law, TTS (Piper / Kokoro / Edge / ElevenLabs), STT Whisper
- **SkillsManager** — compétences agents, templates, planifications
- **Admin** — utilisateurs (droits, mot de passe local, RunAs), settings chiffrés, SSO
- **Traefik** — HTTPS (Let's Encrypt), basic auth site ; API JWT via `X-UCCX-Token`

Les horaires et fériés se gèrent via les **calendriers UCCX natifs** (API Admin), pas via un fichier XML servi par le portail.

## Démarrage rapide

```bash
git clone https://github.com/vincent051/uccx-portal.git
cd uccx-portal
cp .env.example .env   # éditer PORTAL_HOST, mots de passe, UCCX_*
./portal.sh install
./portal.sh doctor
```

```bash
./portal.sh update          # mise à jour
./portal.sh export-offline  # bundle air-gap
./portal.sh import-offline FILE
```

Guide complet : **[docs/DEPLOY.md](docs/DEPLOY.md)**.

| Commande | Rôle |
|----------|------|
| `./portal.sh install` | Première install |
| `./portal.sh update` | Pull + rebuild |
| `./portal.sh status` / `doctor` / `logs` | Ops |
| `./portal.sh export-offline` / `import-offline` | Déploiement hors ligne |

## Auth

| Couche | Mécanisme |
|--------|-----------|
| Traefik (UI `/`) | Basic auth (`BASIC_AUTH_*` ou `ADMIN_*`) |
| API applicative | JWT dans le header **`X-UCCX-Token`** (pas `Authorization: Bearer`) |
| Opérateurs | SSO SAML si configuré ([docs/SAML_SSO.md](docs/SAML_SSO.md)) |
| Admin break-glass | Login local (formulaire replié si SSO actif) |

## Accès

| URL | Auth |
|-----|------|
| `https://$PORTAL_HOST/` | Basic + login portail |
| `https://127.0.0.1/` | Idem (cert auto-signé) |
| `https://…/api/public/health` | Aucune → `ok` |
| `http://<IP-LAN>:8080/api/public/tts` | Allowlist IP — TTS pour scripts UCCX |

Assets UI Post (optionnel) :

```bash
cp docker-compose.post-assets.yml.example docker-compose.post-assets.yml
# .env → UCCX_PUBLIC_DIR=/path/to/uccx/public
./portal.sh update
```

## Dev sans Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt
mkdir -p data
export DATABASE_URL=sqlite:////$(pwd)/data/portal.db
export DATA_DIR=$(pwd)/data
export PUBLIC_BASE_URL=http://127.0.0.1:8080
./start.sh
```

## Documentation

| Doc | Contenu |
|-----|---------|
| [docs/DEPLOY.md](docs/DEPLOY.md) | Install, update, offline, certificats |
| [docs/SAML_SSO.md](docs/SAML_SSO.md) | SSO opérateurs |
| [docs/TTS_PIPER_UCCX.md](docs/TTS_PIPER_UCCX.md) | TTS public pour scripts UCCX |
| [AGENTS.md](AGENTS.md) | Architecture API (agents / contributeurs) |
