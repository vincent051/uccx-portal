"""Synthèse ElevenLabs → WAV PCM (préécoute) ; quota free via GET /v1/user."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.piper_tts import TtsError, uccx_lang_key

logger = logging.getLogger(__name__)

_API_BASE = "https://api.elevenlabs.io/v1"
_SAFE_VOICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_MAX_CHARS = 2500
_SHARED_IMPORT_LIMIT = 10

# Prefixe langue UCCX → codes / labels ElevenLabs approximatifs
_LANG_HINTS: dict[str, tuple[str, ...]] = {
    "fr": ("fr", "french", "français", "francais"),
    "en": ("en", "english", "american", "british"),
    "de": ("de", "german", "deutsch"),
    "lb": ("lb", "luxembourg", "luxembourgish", "luxembourgish"),
    "it": ("it", "italian", "italiano"),
    "es": ("es", "spanish", "español", "espanol"),
    "pt": ("pt", "portuguese", "português", "portugues"),
    "nl": ("nl", "dutch", "nederlands"),
}

_LANG_DISPLAY: dict[str, str] = {
    "fr": "Français",
    "de": "Deutsch",
    "en": "English",
    "lb": "Lëtzebuergesch",
    "it": "Italiano",
    "es": "Español",
    "pt": "Português",
    "nl": "Nederlands",
    "other": "Autre",
}

_LANG_SORT_ORDER: tuple[str, ...] = (
    "fr",
    "de",
    "en",
    "lb",
    "it",
    "es",
    "pt",
    "nl",
    "other",
)

# Prefixe UCCX → code language= pour GET /shared-voices
_SHARED_LANG: dict[str, str] = {
    "fr": "fr",
    "en": "en",
    "de": "de",
    "it": "it",
    "es": "es",
    "pt": "pt",
    "nl": "nl",
    "lb": "lb",
}


def api_key(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (getattr(settings, "elevenlabs_api_key", None) or "").strip()


def elevenlabs_ready(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not getattr(settings, "elevenlabs_enabled", True):
        return False
    return bool(api_key(settings))


def _headers(settings: Settings) -> dict[str, str]:
    key = api_key(settings)
    if not key:
        raise TtsError(
            "Clé ElevenLabs manquante (Admin → Paramètres / ELEVENLABS_API_KEY)",
            status_code=503,
        )
    return {"xi-api-key": key, "Accept": "application/json"}


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(timeout=90.0, headers=_headers(settings))


def _raise_http(resp: httpx.Response, what: str) -> None:
    if resp.status_code < 400:
        return
    detail = ""
    try:
        payload = resp.json()
        d = payload.get("detail")
        if isinstance(d, dict):
            detail = str(d.get("message") or d.get("status") or "")
            if d.get("status") == "missing_permissions":
                detail = (
                    f"{detail} — recréez une clé API ElevenLabs avec les droits "
                    "voices_read, voices_write, text_to_speech et user_read."
                )
        elif isinstance(d, str):
            detail = d
    except Exception:  # noqa: BLE001
        detail = (resp.text or "")[:200]
    if resp.status_code == 401:
        raise TtsError(
            detail or "Clé ElevenLabs invalide ou permissions insuffisantes",
            status_code=502,
        )
    if resp.status_code == 429:
        raise TtsError("Quota ElevenLabs dépassé", status_code=429)
    raise TtsError(
        f"ElevenLabs {what} HTTP {resp.status_code}: {detail or resp.text[:200]}",
        status_code=502,
    )


def _lang_prefix(lang: str | None) -> str | None:
    key = uccx_lang_key(lang)
    if key == "default":
        return None
    return key.split("_", 1)[0].lower()


def _voice_matches_lang(voice: dict[str, Any], prefix: str | None) -> bool:
    if not prefix:
        return True
    hints = _LANG_HINTS.get(prefix, (prefix,))
    labels = voice.get("labels") or {}
    blob_parts = [
        str(labels.get("language") or ""),
        str(labels.get("accent") or ""),
        str(labels.get("locale") or ""),
        str(voice.get("name") or ""),
    ]
    blob = " ".join(blob_parts).lower()
    return any(h in blob for h in hints)


def _match_lang_code(blob: str) -> str | None:
    text = (blob or "").strip().lower()
    if not text:
        return None
    # Code ISO court en tête (fr, en-US, …)
    head = re.split(r"[\s_/,-]+", text, maxsplit=1)[0]
    if head in _LANG_HINTS:
        return head
    for code, hints in _LANG_HINTS.items():
        if any(h in text for h in hints):
            return code
    return None


def _detect_voice_lang(voice: dict[str, Any]) -> str:
    """Code langue pour regroupement UI (Mes voix) ; défaut other."""
    labels = voice.get("labels") or {}
    for key in ("language", "locale", "accent"):
        code = _match_lang_code(str(labels.get(key) or ""))
        if code:
            return code
    for entry in voice.get("verified_languages") or []:
        if isinstance(entry, dict):
            blob = " ".join(
                str(entry.get(k) or "")
                for k in ("language", "locale", "accent", "preview_language")
            )
        else:
            blob = str(entry or "")
        code = _match_lang_code(blob)
        if code:
            return code
    # Dernier recours : nom de la voix
    return _match_lang_code(str(voice.get("name") or "")) or "other"


def list_voices_raw(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    if not elevenlabs_ready(settings):
        raise TtsError("ElevenLabs désactivé ou sans clé API", status_code=503)
    with _client(settings) as client:
        return _fetch_my_voices(client)


def _fetch_my_voices(client: httpx.Client) -> list[dict[str, Any]]:
    # show_legacy=true : inclut les voix premade default (hors legacy sinon).
    resp = client.get(f"{_API_BASE}/voices", params={"show_legacy": "true"})
    _raise_http(resp, "voices")
    data = resp.json()
    # Uniquement les voix default EL (premade) — pas de Voice Library / paid.
    return [
        v
        for v in (data.get("voices") or [])
        if str(v.get("category") or "").strip().lower() == "premade"
    ]


def _auth_error_code(resp: httpx.Response) -> str | None:
    try:
        detail = (resp.json() or {}).get("detail")
    except Exception:  # noqa: BLE001
        return None
    if isinstance(detail, dict):
        return str(detail.get("status") or detail.get("code") or "") or None
    return None


def _try_import_shared_voices(
    client: httpx.Client,
    prefix: str,
    existing_ids: set[str],
) -> tuple[int, str | None]:
    """Ajoute des voix Voice Library à « My Voices ». Retourne (nb, motif_blocage)."""
    iso = _SHARED_LANG.get(prefix)
    if not iso or iso == "en":
        return 0, None
    try:
        resp = client.get(
            f"{_API_BASE}/shared-voices",
            params={"page_size": _SHARED_IMPORT_LIMIT, "language": iso},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("shared-voices %s: %s", iso, exc)
        return 0, None
    if resp.status_code >= 400:
        logger.info("shared-voices %s HTTP %s", iso, resp.status_code)
        return 0, None
    shared = list((resp.json() or {}).get("voices") or [])
    added = 0
    block: str | None = None
    for v in shared:
        vid = str(v.get("voice_id") or "").strip()
        owner = str(v.get("public_owner_id") or "").strip()
        if not vid or not owner or vid in existing_ids:
            continue
        name = str(v.get("name") or f"{iso}-{vid[:8]}")[:80]
        try:
            add = client.post(
                f"{_API_BASE}/voices/add/{owner}/{vid}",
                json={"new_name": name},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("add shared voice %s: %s", vid, exc)
            continue
        if add.status_code < 400:
            added += 1
            existing_ids.add(vid)
            continue
        code = _auth_error_code(add) or ""
        body = (add.text or "").lower()
        if code == "missing_permissions" or "voices_write" in body:
            return added, "voices_write"
        if (
            "free" in body
            or "creator" in body
            or "paid" in body
            or "subscription" in body
            or code in {"payment_required", "forbidden"}
        ):
            return added, "paid_required"
        logger.info("add shared %s HTTP %s: %s", vid, add.status_code, add.text[:160])
        block = block or "add_failed"
    return added, block


def _format_voice_rows(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Libellés = nom seul ; groupe unique Multilingue (voix premade default)."""
    out: list[dict[str, Any]] = []
    for v in raw:
        if str(v.get("category") or "").strip().lower() != "premade":
            continue
        vid = str(v.get("voice_id") or "").strip()
        if not vid:
            continue
        name = str(v.get("name") or vid).strip()
        out.append(
            {
                "id": vid,
                "label": name,
                "default": False,
                "lang": "multi",
                "lang_label": "Multilingue",
                "lang_family": "multi",
                "lang_matched": True,
            }
        )
    out.sort(key=lambda row: str(row.get("label") or "").lower())
    if out:
        out[0]["default"] = True
    return out


def collect_voices_for_uccx_lang(
    lang: str | None,
    settings: Settings | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Voix default ElevenLabs (premade) uniquement — multilangues, pas de Library."""
    settings = settings or get_settings()
    if not elevenlabs_ready(settings):
        raise TtsError("ElevenLabs désactivé ou sans clé API", status_code=503)
    # lang ignoré : les premade sont utilisables via le modèle multilangue.
    _ = lang
    with _client(settings) as client:
        raw = _fetch_my_voices(client)
    rows = _format_voice_rows(raw)
    notice = None
    if not rows:
        notice = (
            "Aucune voix default ElevenLabs (premade). "
            "Vérifiez la clé API (voices_read) et show_legacy."
        )
    return rows, notice


def voices_for_uccx_lang(
    lang: str | None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Voix default ElevenLabs premade (multilingues)."""
    rows, _notice = collect_voices_for_uccx_lang(lang, settings)
    return rows


def _mp3_to_pcm_wav(mp3: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TtsError("ffmpeg requis pour convertir ElevenLabs → WAV", status_code=503)
    with tempfile.TemporaryDirectory(prefix="el-tts-") as tmp:
        src = Path(tmp) / "in.mp3"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(mp3)
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-ar",
                "22050",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(dst),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0 or not dst.is_file():
            err = (proc.stderr or proc.stdout or "")[:300]
            raise TtsError(f"Conversion ElevenLabs échouée: {err}", status_code=502)
        return dst.read_bytes()


def synthesize_pcm_wav(
    text: str,
    *,
    voice: str | None = None,
    lang: str | None = None,
    settings: Settings | None = None,
) -> bytes:
    settings = settings or get_settings()
    if not settings.tts_enabled:
        raise TtsError("TTS désactivé", status_code=503)
    if not elevenlabs_ready(settings):
        raise TtsError("ElevenLabs désactivé ou sans clé API", status_code=503)
    cleaned = (text or "").strip()
    if not cleaned:
        raise TtsError("Texte TTS vide")
    if len(cleaned) > _MAX_CHARS:
        raise TtsError(f"Texte trop long (max {_MAX_CHARS} caractères)")

    voice_id = (voice or "").strip()
    if not voice_id or not _SAFE_VOICE_RE.match(voice_id):
        voices = voices_for_uccx_lang(lang, settings)
        if not voices:
            raise TtsError("Aucune voix ElevenLabs disponible", status_code=503)
        voice_id = voices[0]["id"]

    model = (getattr(settings, "elevenlabs_model", None) or "eleven_multilingual_v2").strip()
    payload = {
        "text": cleaned,
        "model_id": model,
    }
    with _client(settings) as client:
        resp = client.post(
            f"{_API_BASE}/text-to-speech/{voice_id}",
            json=payload,
            headers={
                **_headers(settings),
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
        )
        _raise_http(resp, "TTS")
        mp3 = resp.content
    if not mp3:
        raise TtsError("Réponse audio ElevenLabs vide", status_code=502)
    return _mp3_to_pcm_wav(mp3)


def usage_payload(settings: Settings | None = None) -> dict[str, Any]:
    """Pourcentage d’usage caractères du cycle de facturation courant."""
    settings = settings or get_settings()
    if not elevenlabs_ready(settings):
        raise TtsError("ElevenLabs désactivé ou sans clé API", status_code=503)
    with _client(settings) as client:
        resp = client.get(f"{_API_BASE}/user")
        _raise_http(resp, "user")
        data = resp.json()
    sub = data.get("subscription") or {}
    used = int(sub.get("character_count") or 0)
    limit = int(sub.get("character_limit") or 0)
    pct = round(100.0 * used / limit, 1) if limit > 0 else 0.0
    remaining = max(0, limit - used)
    reset_unix = sub.get("next_character_count_reset_unix")
    reset_at = None
    if reset_unix:
        try:
            reset_at = datetime.fromtimestamp(int(reset_unix), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            reset_at = None
    return {
        "available": True,
        "percent": pct,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "reset_at": reset_at,
        "tier": sub.get("tier") or "",
        "status": sub.get("status") or "",
    }
