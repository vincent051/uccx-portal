# UCCX Portal — instructions agent

Portail Docker (FastAPI + Traefik + Postgres) pour éditer les apps UCCX en mode AppAdmin.
Workspace : `/home/deepadm/uccx-portal`. Host UCCX : `172.27.200.7` (adminapi).

## Stack

- `docker-compose.yml` : `traefik` (v3.6), `api`, `db` (Postgres)
- API : `api/app/` (FastAPI), montée en volume → pas besoin de rebuild pour le Python/static
- UI : `api/app/static/` (`index.html`, `app.js`, `portal.css`)
- CSS Post : volumes depuis `/home/deepadm/uccx/public/{css,fonts,img}` → `/static/`
- Secrets / UCCX : `.env` (ne pas committer ; ne pas coller les mots de passe dans le chat)
- HTTPS : `https://collab-edge.i-hub.com` (LE) et `https://127.0.0.1` (local)
- Auth UI Traefik : basic auth ; API JWT via header **`X-UCCX-Token`** (jamais `Authorization: Bearer` — réservé au Basic Traefik)

## Démarrage

```bash
cd ~/uccx-portal
./scripts/ensure-docker.sh
docker compose up -d
# rebuild seulement si Dockerfile change (sox/ffmpeg) :
docker compose build api && docker compose up -d api
```

## Architecture métier (état actuel)

- **Pas de création d’apps** depuis le portail (import auto UCCX au `GET /api/apps`)
- Éditeur = valeurs AppAdmin uniquement + calendrier UCCX + prompts + triggers RO
- Ancien éditeur XML (WeekHours/Holidays/flags) : API `/api/apps/{name}/config` encore là pour DOC failover ; l’UI principale utilise `/uccx`

### Endpoints clés

| Méthode | Path | Rôle |
|---------|------|------|
| GET | `/api/apps` | Liste + sync auto depuis UCCX |
| GET/PUT | `/api/apps/{name}/uccx` | Détail app (params, triggers, calendars) / push valeurs |
| PUT | `/api/apps/{name}/calendar/{calId}` | Push calendrier (doit être référencé par un param CCCalendar) |
| POST | `/api/apps/{name}/prompts` | Upload → µ-law 8 kHz + **0,5 s silence** ; form optionnel `transcript`+`source=tts\|stt` |
| POST | `/api/apps/{name}/prompts/stt` | STT à la sélection : multipart `file` et/ou `relpath`+`source_lang` + `lang` |
| GET | `/api/apps/{name}/prompt-status` | Présence + `transcripts` `{param:{lang:{text,source,stt_enabled}}}` |
| GET | `/api/apps/{name}/prompts/transcript` | Texte stocké ; `?force=1` retranscrit si STT actif |
| PATCH | `/api/apps/{name}/prompts/stt-settings` | Active/désactive STT par case `{param_name,lang,stt_enabled}` |
| GET | `/api/apps/{name}/prompt-audio` | WAV UCCX ; `?download=1` → attachment |
| GET | `/api/apps/{name}/tts/voices` | Voix installées (`?lang=` + `?engine=piper\|kokoro`) |
| POST | `/api/apps/{name}/prompts/tts` | Synthèse → WAV PCM + **0,5 s silence** ; préécoute auto côté UI |
| POST | `/api/apps/{name}/prompt-languages` | Créer dossier langue UCCX (`{lang}`) |
| GET/PUT | `/api/tts/public-settings` | Moteur défaut API publique (`engine` → `data/portal_tts.json`) |
| POST | `/api/public/tts` | TTS UCCX : form `text`/`lang`/`voice?`/`engine?` ; WAV µ-law ; `TTS_ALLOWED_CIDRS` |
| GET | `/api/auth/saml/login` · `POST …/acs` · `GET …/metadata` | SSO SAML (voir `docs/SAML_SSO.md`) |
| GET/PUT | `/api/skills/*` | SkillsManager : agents, templates, schedules |
| GET/PUT/PATCH | `/api/admin/users*` · `/settings` · sync UCCX | Admin users + settings chiffrés |

### Modules API

- `uccx_apps.py` — list/get/PUT application, triggers, encode/decode String (`"…"`)
- `uccx_calendar.py` — GET/PUT calendrier (`FULLTIME` / `FIXEDHOURS` / `FLEXIBLEHOURS`, `businessDays`, `holidays`)
- `audio_convert.py` — sox/ffmpeg → WAV µ-law 8 kHz mono + `prepend_silence_ulaw(0.5)`
- `tts_engine.py` — façade Piper / Kokoro (voix + PCM + µ-law public)
- `portal_tts_settings.py` — moteur défaut API publique (`data/portal_tts.json` + `TTS_PUBLIC_ENGINE`)
- `piper_tts.py` — Piper (PCM portail / µ-law) ; `default` = toutes les voix ; DE = thorsten/kerstin
- `kokoro_tts.py` — Kokoro-onnx ; pas d’allemand (UI bascule Piper) ; `default` = toutes
- `whisper_stt.py` — faster-whisper STT (force client après upload / panneau texte)
- `prompt_transcripts.py` — `PromptTranscript` (`source` = `tts` \| `stt`, `stt_enabled`)
- `uccx_sync.py` — Document Management + prompt upload + `create_prompt_language`
- `uccx_skills.py` — Admin API skill/team/resource (SkillsManager)
- `skill_scheduler.py` — job apply/restore templates
- `saml_auth.py` — SAML 2.0 SP (SSO opérateurs)
- `secure_settings.py` — paramètres admin chiffrés Fernet (`portal_secrets`)
- `user_access.py` — flags apps/skills + teams skills

### Types scriptParams UCCX

- `com.cisco.cccalendar.CCCalendar` → section calendrier (valeur = calId)
- `com.cisco.prompt.Playable` → prompts ; dropzone / browse / panneau **STT/TTS** (texte partagé, toggle STT, Copier, Générer+préécoute) ; ↓ download ; STT dès la sélection
- `java.lang.String` → stocké avec guillemets dans le XML UCCX
- Autres → table params génériques

Toujours `GET …/application/{name}?allScriptParams=true` avant PUT (remplacement complet).

## UI / CSS

- Design system Post : `/static/css/post-material.min.css` + `uc.css`
- Layout seul : `/static/portal.css`
- Classes : `panel panel-default`, `form-control`, `btn btn-primary` / `btn-default`, `table table-striped`
- Cache-bust : `app.js?v=…` / `portal.css?v=…` après changement front
- Dossiers `static/css|fonts|img` = points de montage vides (contenu via volumes Docker). Ne pas y mettre de symlinks (StaticFiles + symlinks = 404 sauf `follow_symlink=True`)

## Conventions

- Français pour messages UI / commits / réponses utilisateur
- Ne pas recréer d’apps portail ni seed `DemoApp`
- Ne pas committer `.env`, `letsencrypt/acme.json`, secrets
- Rebuild image API si deps changent (`sox`, `ffmpeg`, `espeak-ng`, piper, `kokoro-onnx`, `faster-whisper` dans `api/Dockerfile` / requirements)
- TTS : `./scripts/download-piper-model.sh` + `./scripts/download-kokoro-model.sh` (voix via scripts ops, pas de download UI) ; doc `docs/TTS_PIPER_UCCX.md`
- STT : modèle Whisper téléchargé sous `/data/whisper` au premier usage (`WHISPER_MODEL=base`)
- UCCX sans HTTPS : API en **HTTP** sur `http://<IP-Windows>:8080/api/public/…` (port `UCCX_HTTP_PORT`, script `windows-expose-ports.ps1`)
- WSL : ne pas activer `networkingMode=mirrored` (casse SSH/portproxy). Ports Windows : `scripts/windows-expose-ports.ps1`

## Tests manuels utiles

1. Login portail → liste apps UCCX (`CustomerService_Cal`, `promptRecording`, …)
2. `CustomerService_Cal` → calendrier éditable + triggers RO
3. Modifier param générique → PUT visible au GET UCCX
4. Upload MP3/WAV sur un Playable → converti µ-law + chemin `SP[…]` mis à jour
5. Drop/browse → STT immédiat (si STT auto) remplit le panneau **STT/TTS** ; Générer → préécoute auto (+ 0,5 s silence)
6. Enregistrer pousse UCCX + texte déjà connu (tts/stt) ; ↓ télécharge le WAV
7. Ajouter une langue UCCX (ex. `it_IT`) → nouvelle colonne
8. Soft refresh : Ctrl+F5 si ancien JS en cache

## CLI agent

```bash
cd ~/uccx-portal
agent --workspace /home/deepadm/uccx-portal
```

Ce fichier `AGENTS.md` est chargé automatiquement. Pas de flag dédié.
