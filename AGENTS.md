# UCCX Portal — instructions agent

Portail Docker (FastAPI + Traefik + Postgres) pour éditer les apps UCCX en mode AppAdmin.

Configurer `UCCX_HOST` / credentials dans `.env`. Workspace typique : clone du dépôt ; démarrer via `./portal.sh` ou `docker compose`.

## Stack

- `docker-compose.yml` : `traefik` (v3.6), `api`, `db` (Postgres)
- API : `api/app/` (FastAPI), montée en volume → pas besoin de rebuild pour le Python/static
- UI : `api/app/static/` (`index.html`, `app.js`, `portal.css`)
- CSS Post : optionnel via `docker-compose.post-assets.yml` + `UCCX_PUBLIC_DIR`
- Secrets / UCCX : `.env` (ne pas committer ; ne pas coller les mots de passe dans le chat)
- HTTPS : `https://$PORTAL_HOST` (Let's Encrypt) et `https://127.0.0.1` (local)
- Auth UI Traefik : basic auth ; API JWT via header **`X-UCCX-Token`** (jamais `Authorization: Bearer` — réservé au Basic Traefik)

## Démarrage

```bash
./scripts/ensure-docker.sh
./portal.sh install   # ou : docker compose up -d --build
# rebuild image seulement si Dockerfile / deps changent :
docker compose build api && docker compose up -d api
```

## Architecture métier

- **Pas de création d’apps** depuis le portail (import auto UCCX au `GET /api/apps`)
- Éditeur = valeurs AppAdmin + calendrier UCCX (`GET/PUT …/uccx`, `…/calendar/{id}`) + prompts + triggers en lecture seule
- Horaires / fériés = calendriers `CCCalendar` poussés vers UCCX (pas de fichier XML public)

### Endpoints clés

| Méthode | Path | Rôle |
|---------|------|------|
| GET | `/api/apps` | Liste + sync auto depuis UCCX |
| GET/PUT | `/api/apps/{name}/uccx` | Détail app (params, triggers, calendars) / push valeurs |
| PUT | `/api/apps/{name}/calendar/{calId}` | Push calendrier (référencé par un param CCCalendar) |
| POST | `/api/apps/{name}/prompts` | Upload → µ-law 8 kHz + **0,5 s silence** ; form optionnel `transcript`+`source=tts\|stt` |
| POST | `/api/apps/{name}/prompts/stt` | STT à la sélection |
| GET | `/api/apps/{name}/prompt-status` | Présence + transcripts |
| GET | `/api/apps/{name}/prompts/transcript` | Texte stocké ; `?force=1` retranscrit si STT actif |
| PATCH | `/api/apps/{name}/prompts/stt-settings` | Active/désactive STT par case |
| GET | `/api/apps/{name}/prompt-audio` | WAV UCCX ; `?download=1` → attachment |
| GET | `/api/apps/{name}/tts/voices` | Voix (`?lang=` + `?engine=`) |
| POST | `/api/apps/{name}/prompts/tts` | Synthèse → WAV PCM + silence |
| POST | `/api/apps/{name}/prompt-languages` | Créer dossier langue UCCX |
| GET/PUT | `/api/tts/public-settings` | Moteur défaut API publique |
| POST | `/api/public/tts` | TTS UCCX : form `text`/`lang`/`voice?`/`engine?` ; WAV µ-law |
| GET | `/api/auth/saml/login` · `POST …/acs` · `GET …/metadata` | SSO SAML |
| GET/PUT | `/api/skills/*` | SkillsManager |
| GET/PUT/PATCH | `/api/admin/users*` · `/settings` | Admin users + settings chiffrés |
| POST | `/api/admin/users/{id}/impersonate` | RunAs (JWT `imp`) |

### Modules API

- `uccx_apps.py` — list/get/PUT application, triggers, encode/decode String
- `uccx_calendar.py` — GET/PUT calendrier (`FULLTIME` / `FIXEDHOURS` / `FLEXIBLEHOURS`)
- `audio_convert.py` — sox/ffmpeg → WAV µ-law 8 kHz mono + silence
- `tts_engine.py` — façade Piper / Kokoro / Edge / ElevenLabs
- `portal_tts_settings.py` — moteur défaut (`data/portal_tts.json`)
- `piper_tts.py` / `kokoro_tts.py` / `edge_tts.py` / `elevenlabs_tts.py`
- `whisper_stt.py` — faster-whisper
- `prompt_transcripts.py` — textes TTS/STT
- `uccx_sync.py` — upload / download prompts, langues
- `uccx_skills.py` / `skill_scheduler.py` — SkillsManager
- `saml_auth.py` — SAML 2.0 SP
- `secure_settings.py` — settings chiffrés Fernet
- `user_access.py` — flags apps/skills + teams

### Types scriptParams UCCX

- `com.cisco.cccalendar.CCCalendar` → section calendrier (valeur = calId)
- `com.cisco.prompt.Playable` → prompts (dropzone / browse / STT-TTS)
- `java.lang.String` → stocké avec guillemets dans le XML application UCCX
- Autres → table params génériques

Toujours `GET …/application/{name}?allScriptParams=true` avant PUT (remplacement complet).

## UI / CSS

- Design system Post : `/static/css/post-material.min.css` + `uc.css` (via volumes optionnels)
- Layout : `/static/portal.css`
- Cache-bust : `app.js?v=…` / `portal.css?v=…` après changement front
- Dossiers `static/css|fonts|img` = points de montage vides si Post assets montés

## Conventions

- Français pour messages UI / commits / réponses utilisateur
- Ne pas recréer d’apps portail ni seed `DemoApp`
- Ne pas committer `.env`, `letsencrypt/acme.json`, secrets
- Rebuild image API si deps changent (`api/Dockerfile` / `requirements.txt`)
- TTS modèles : `./scripts/download-piper-model.sh` + `./scripts/download-kokoro-model.sh` ; doc `docs/TTS_PIPER_UCCX.md`
- STT : Whisper sous `$DATA_DIR/whisper` au premier usage
- UCCX sans HTTPS : HTTP `http://<IP-LAN>:8080/api/public/…` (`UCCX_HTTP_PORT`)
- WSL : éviter `networkingMode=mirrored` ; ports Windows via `scripts/windows-expose-ports.ps1`

## Tests manuels utiles

1. Login → liste apps UCCX
2. App avec calendrier → édition + enregistrement visible côté UCCX
3. Modifier param générique → PUT visible au GET UCCX
4. Upload MP3/WAV sur un Playable → µ-law + chemin `SP[…]`
5. STT / TTS sur panneau prompt ; préécoute ; download
6. Ajouter une langue UCCX → nouvelle colonne
7. Ctrl+F5 si ancien JS en cache

## CLI agent

```bash
agent --workspace <chemin-du-clone>
```

Ce fichier `AGENTS.md` est chargé automatiquement.
