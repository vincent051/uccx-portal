# Piper TTS → UCCX (Document → Prompt, multi-langue)

Endpoint : `POST /api/public/tts` (moteurs **Piper** ou **Kokoro** → WAV µ-law)

**URL pour UCCX (HTTP obligatoire — pas de HTTPS côté UCCX)** :

```text
http://<IP-Windows-sur-le-LAN>:8080/api/public/tts
```

Exemple si le PC Windows est `172.27.200.10` :  
`http://172.27.200.10:8080/api/public/tts`

Le portail reste en HTTPS pour les humains (`https://collab-edge.i-hub.com`).  
Prérequis Windows/WSL : `./scripts/windows-expose-ports.ps1` (admin) pour le **portproxy 8080**.

Réponse : **corps binaire** `Content-Type: audio/wav` (G.711 µ-law, 8 kHz, mono).

**Entrée UCCX** : `application/x-www-form-urlencoded` (champs `text`, `lang` ; optionnels `voice`, `engine`) via **Create URL Document**.  
**Tests hors UCCX** : JSON `{"text","lang","voice","engine"}` accepté aussi.

**Moteur** (priorité) : param `engine` de la requête → réglage portail (UI « Moteur TTS API UCCX », fichier `data/portal_tts.json`) → env `TTS_PUBLIC_ENGINE` (défaut `piper`).  
Kokoro n’a pas de voix allemandes : pour `de_DE`, utiliser Piper (l’UI de préécoute bascule automatiquement).

## Prérequis serveur

```bash
cd ~/uccx-portal
./scripts/download-piper-model.sh   # FR + EN
docker compose build api && docker compose up -d api
```

| Variable | Rôle |
|----------|------|
| `TTS_ENABLED` | active/désactive |
| `TTS_PUBLIC_ENGINE` | moteur défaut API publique (`piper` \| `kokoro`) |
| `PIPER_MODEL` | modèle par défaut (si pas de `lang`) |
| `PIPER_LANG_MAP` | CSV `lang:model` additionnel |
| `TTS_ALLOWED_CIDRS` | subnets autorisés (défaut `172.27.200.0/24` + loopback) |
| `TTS_TRUSTED_PROXY_CIDRS` | peers dont on lit `X-Forwarded-For` (Traefik) |

### Langues / modèles

| `lang` UCCX | Modèle Piper (défaut) | Autres voix (script download) |
|-------------|----------------------|-------------------------------|
| `fr_FR` / `fr` | `fr_FR-siwis-medium` | `fr_FR-upmc-medium` |
| `fr_LU` / `lb_LU` / `lb` | `lb_LU-marylux-medium` (Lëtzebuergesch) | + voix `fr_*` dans l’UI pour `fr_LU` |
| `en_US` / `en` / `en_GB` | `en_US-lessac-medium` | `en_US-amy-medium` |
| `de_DE` / `de` | `de_DE-thorsten-medium` | `de_DE-kerstin-low` |
| `default` | toutes les voix du moteur | — |

Paramètre form optionnel `voice` (ex. `fr_FR-siwis-medium`) : prioritaire sur `lang`.

**UI portail** (JWT, pas d’IP allowlist) :

| Endpoint | Rôle |
|----------|------|
| `GET/PUT /api/tts/public-settings` | Moteur défaut API UCCX (sélecteur section Prompts) |
| `GET …/tts/voices?lang=&engine=` | Voix installées (Piper ou Kokoro) ; Kokoro filtré par langue, `default` = toutes |
| `POST …/prompts/tts` | Synthèse PCM `{text,lang,voice?,engine}` |
| `POST …/prompts` | Upload ; form optionnel `transcript` + `source=tts` (texte input TTS) |
| `GET …/prompts/transcript` | Texte stocké ou Whisper lazy (`source` = `tts` \| `stt`) |
| `POST …/prompt-languages` | Créer une langue UCCX |

Kokoro : toutes les voix sont dans `voices-v1.0.bin` (install ops via `./scripts/download-kokoro-model.sh`). Pas de téléchargement de voix dans l’UI.

**Survol case prompt** : bulle avec en-tête **Texte TTS** (input stocké) ou **Transcription STT** (Whisper). Les anciens fichiers UCCX sans texte en base sont transcrits au premier survol.

## Sécurité IP

Seules les IP de `TTS_ALLOWED_CIDRS` sont acceptées (403 sinon).  
Derrière Traefik, l’IP UCCX est prise dans `X-Forwarded-For` si le peer est un proxy de confiance (`TTS_TRUSTED_PROXY_CIDRS`).

**HTTP direct sur `:8080` (UCCX)** : après portproxy Windows → Docker, l’API voit souvent la passerelle **172.18.0.0/16** ou le réseau WSL **172.23.0.0/16**, pas l’IP UCCX `172.27.200.x`. Il faut les inclure dans `TTS_ALLOWED_CIDRS` et restreindre l’accès au port **8080** au pare-feu Windows (subnet UCCX uniquement).

## Script UCCX — Create URL Document

Variables : `s_texte` (String), `s_langue` (String, ex. depuis Get Contact Info Language), `doc_audio` (Document), `p_tts` (Prompt).

### 1. Create URL Document

- **URL** : `http://<IP-Windows>:8080/api/public/tts` (pas `https://`)
- **Method** : POST
- **Parameters** (grille UCCX — envoyés en form-urlencoded) :

| Parameter Name | Value |
|----------------|--------|
| `text` | `s_texte` |
| `lang` | `s_langue` (ex. `fr_FR`, `en_US`) |
| `engine` | *(optionnel)* `piper` ou `kokoro` — sinon défaut portail / `TTS_PUBLIC_ENGINE` |
| `voice` | *(optionnel)* id voix (ex. `de_DE-thorsten-medium`) |

Pas de body JSON à construire dans l’éditeur.

### 2. Cache Document

Placer **Cache Document** juste après pour déclencher l’envoi effectif du POST et stocker la réponse dans `doc_audio`.

**Timeout UCCX** : Kokoro sur CPU met souvent **5–15 s** (texte ~150 car.). Si le script « plante » / timeout alors que l’API finit en 200, augmenter le timeout HTTP du step (Create URL Document / Cache Document) à **≥ 20 s**, ou utiliser `engine=piper` (plus rapide). L’allemand (`de_DE`) n’a pas de voix Kokoro stock : l’API bascule automatiquement sur Piper.

### 3. Set + Play Prompt

```
p_tts = (Prompt) doc_audio
```

Puis **Play Prompt** sur `p_tts`.

## Tests

```bash
# Form (comme UCCX Create URL Document)
docker compose exec -T api curl -sS -X POST http://127.0.0.1:8080/api/public/tts \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'text=Bonjour' \
  --data-urlencode 'lang=fr_FR' -o /tmp/tts-form.wav

# JSON (rétrocompat)
docker compose exec -T api curl -sS -X POST http://127.0.0.1:8080/api/public/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello","lang":"en_US"}' -o /tmp/tts-json.wav

docker compose exec -T api ffprobe -v error -show_entries stream=codec_name,sample_rate,channels -of default=nw=1 /tmp/tts-form.wav
```
