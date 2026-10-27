"""Synthèse Microsoft Edge TTS (edge-tts) → WAV PCM."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.piper_tts import TtsError, uccx_lang_key

logger = logging.getLogger(__name__)

_MAX_CHARS = 3000
_SAFE_VOICE_RE = re.compile(r"^[A-Za-z]{2,3}-[A-Za-z]{2,3}-[A-Za-z0-9]+$")
_VOICES_TTL_SEC = 3600.0
_voices_cache: tuple[float, list[dict[str, Any]]] | None = None

# Préfixe UCCX (fr / de / …) → préfixes Locale Edge (fr-FR, …)
_LOCALE_PREFIXES: dict[str, tuple[str, ...]] = {
    "fr": ("fr-",),
    "de": ("de-",),
    "en": ("en-",),
    "lb": ("lb-",),
    "it": ("it-",),
    "es": ("es-",),
    "pt": ("pt-",),
    "nl": ("nl-",),
}


def _edge_import():
    try:
        import edge_tts  # noqa: F401

        return edge_tts
    except ImportError as exc:
        raise TtsError(
            "Package edge-tts non installé (pip install edge-tts)",
            status_code=503,
        ) from exc


def edge_ready(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not getattr(settings, "edge_enabled", True):
        return False
    try:
        _edge_import()
        return True
    except TtsError:
        return False


def _run_async(coro):
    """Exécute une coroutine depuis un contexte sync (routes FastAPI)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Boucle déjà active : isoler dans un thread
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=120)


def _locale_prefixes_for_lang(lang: str | None) -> tuple[str, ...] | None:
    """None = toutes les locales connues du portail ; () = aucune."""
    key = uccx_lang_key(lang)
    if key == "default":
        return tuple(p for prefs in _LOCALE_PREFIXES.values() for p in prefs)
    family = key.split("_", 1)[0].lower()
    return _LOCALE_PREFIXES.get(family, (f"{family}-",))


async def _list_voices_raw() -> list[dict[str, Any]]:
    edge_tts = _edge_import()
    return list(await edge_tts.list_voices())


def _cached_raw_voices() -> list[dict[str, Any]]:
    global _voices_cache
    now = time.monotonic()
    if _voices_cache and (now - _voices_cache[0]) < _VOICES_TTL_SEC:
        return _voices_cache[1]
    try:
        raw = _run_async(_list_voices_raw())
    except TtsError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("edge-tts list_voices: %s", exc)
        raise TtsError(f"Liste voix Edge TTS échouée: {exc}", status_code=502) from exc
    _voices_cache = (now, raw)
    return raw


def voices_for_uccx_lang(
    lang: str | None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    if not edge_ready(settings):
        raise TtsError("Edge TTS désactivé ou package manquant", status_code=503)
    prefixes = _locale_prefixes_for_lang(lang)
    raw = _cached_raw_voices()
    out: list[dict[str, Any]] = []
    for v in raw:
        short = str(v.get("ShortName") or "").strip()
        if not short:
            continue
        locale = str(v.get("Locale") or "").strip().lower()
        if prefixes is not None and not any(
            locale.startswith(p.lower()) for p in prefixes
        ):
            continue
        friendly = str(v.get("FriendlyName") or short).strip()
        # FriendlyName souvent « Microsoft Denise Online … » — ShortName plus lisible
        label = short
        if friendly and friendly != short and "Microsoft" not in friendly:
            label = friendly
        family = locale.split("-", 1)[0] if locale else "all"
        out.append(
            {
                "id": short,
                "label": label,
                "default": False,
                "lang": family,
                "lang_family": family,
                "lang_matched": True,
            }
        )
    out.sort(key=lambda r: str(r.get("label") or "").lower())
    if out:
        out[0]["default"] = True
    return out


def _mp3_to_pcm_wav(mp3: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TtsError("ffmpeg requis pour convertir Edge TTS → WAV", status_code=503)
    with tempfile.TemporaryDirectory(prefix="edge-tts-") as tmp:
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
            raise TtsError(f"Conversion Edge TTS échouée: {err}", status_code=502)
        return dst.read_bytes()


async def _synthesize_mp3(text: str, voice: str) -> bytes:
    edge_tts = _edge_import()
    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            chunks.append(chunk["data"])
    return b"".join(chunks)


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
    if not edge_ready(settings):
        raise TtsError("Edge TTS désactivé ou package manquant", status_code=503)
    cleaned = (text or "").strip()
    if not cleaned:
        raise TtsError("Texte TTS vide")
    if len(cleaned) > _MAX_CHARS:
        raise TtsError(f"Texte trop long (max {_MAX_CHARS} caractères)")

    voice_id = (voice or "").strip()
    if not voice_id or not _SAFE_VOICE_RE.match(voice_id):
        voices = voices_for_uccx_lang(lang, settings)
        if not voices:
            raise TtsError(
                f"Aucune voix Edge TTS pour la langue {lang!r}",
                status_code=503,
            )
        voice_id = voices[0]["id"]

    try:
        mp3 = _run_async(_synthesize_mp3(cleaned, voice_id))
    except TtsError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("edge-tts synthesize: %s", exc)
        raise TtsError(f"Synthèse Edge TTS échouée: {exc}", status_code=502) from exc
    if not mp3:
        raise TtsError("Réponse audio Edge TTS vide", status_code=502)
    return _mp3_to_pcm_wav(mp3)
