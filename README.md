# UCCX Portal

Portail web (Docker) pour piloter les paramètres des scripts UCCX : horaires, fériés, prompts TTS/STT, SkillsManager, SSO SAML, admin users.

- **Mode A** : UCCX lit un XML HTTP servi par ce portail
- **Failover** : sync Document Management UCCX à chaque save
- **RBAC** : accès par application / teams Skills
- **Traefik** : HTTPS Let's Encrypt + basic auth

## Démarrage rapide

```bash
git clone https://github.com/<user>/uccx-portal.git
cd uccx-portal
cp .env.example .env   # éditer PORTAL_HOST, mots de passe, UCCX_*
./portal.sh install
./portal.sh doctor
```

Mise à jour :

```bash
./portal.sh update
```

Déploiement détaillé (online, offline, Post assets, certificats) : **[docs/DEPLOY.md](docs/DEPLOY.md)**.

| Commande | Rôle |
|----------|------|
| `./portal.sh install` | Première install |
| `./portal.sh update` | Pull + rebuild |
| `./portal.sh status` / `doctor` / `logs` | Ops |
| `./portal.sh export-offline` | Bundle air-gap |
| `./portal.sh import-offline FILE` | Restore air-gap |

## Docker / WSL

```bash
./scripts/ensure-docker.sh   # démon + ACL docker.sock
```

### Accès local

| URL | Auth |
|-----|------|
| https://127.0.0.1/api/public/health | aucune → `ok` |
| https://127.0.0.1/ | basic auth puis login portail |
| https://`$PORTAL_HOST`/ | idem + Let's Encrypt si DNS/80/443 OK |

### Assets UI Post (optionnel)

```bash
cp docker-compose.post-assets.yml.example docker-compose.post-assets.yml
# .env → UCCX_PUBLIC_DIR=/path/to/uccx/public
./portal.sh update
```

### Mots de passe

Éditer `.env` (`ADMIN_PASSWORD`, `BASIC_AUTH_PASSWORD`) puis :

```bash
docker compose down && ./portal.sh update
```

`auth-init` régénère `letsencrypt/htpasswd` à chaque `compose up`.

## Dev sans Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt
mkdir -p data/xml
export DATABASE_URL=sqlite:////$(pwd)/data/portal.db
export XML_DIR=$(pwd)/data/xml
export PUBLIC_BASE_URL=http://127.0.0.1:8080
./start.sh
```

## Documentation

| Doc | Contenu |
|-----|---------|
| [docs/DEPLOY.md](docs/DEPLOY.md) | Install / update / offline |
| [docs/SCRIPT_FAILOVER.md](docs/SCRIPT_FAILOVER.md) | Config UCCX |
| [docs/SAML_SSO.md](docs/SAML_SSO.md) | SSO |
| [docs/TTS_PIPER_UCCX.md](docs/TTS_PIPER_UCCX.md) | TTS public |
| [AGENTS.md](AGENTS.md) | Architecture pour agents |
