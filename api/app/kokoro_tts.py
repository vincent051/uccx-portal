"""Synthèse Kokoro (kokoro-onnx) → WAV PCM pour préécoute portail."""

from __future__ import annotations

import io
import logging
import re
import wave
from pathlib import Path
from threading import Lock
from typing import Any

from app.config import Settings, get_settings
from app.piper_tts import TtsError, uccx_lang_key

logger = logging.getLogger(__name__)

_SAFE_VOICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_kokoro_lock = Lock()
_kokoro_instance: Any = None
_kokoro_paths: tuple[str, str] | None = None

# Préfixe ISO UCCX → préfixes voix Kokoro (1ère lettre du voice id).
# Pas d’allemand (de / de_DE) dans Kokoro v1 — l’UI bascule sur Piper.
_ISO_TO_KOKORO_PREFIXES: dict[str, tuple[str, ...]] = {
    "en": ("af", "am", "bf", "bm"),
    "en_us": ("af", "am"),
    "en_gb": ("bf", "bm"),
    "fr": ("ff",),
    "fr_fr": ("ff",),
    "fr_lu": ("ff",),
    "lb": ("ff",),
    "lb_lu": ("ff",),
    "es": ("ef", "em"),
    "es_es": ("ef", "em"),
    "es_mx": ("ef", "em"),
    "it": ("if", "im"),
    "it_it": ("if", "im"),
    "pt": ("pf", "pm"),
    "pt_br": ("pf", "pm"),
    "hi": ("hf", "hm"),
    "ja": ("jf", "jm"),
    "zh": ("zf", "zm"),
    "zh_cn": ("zf", "zm"),
}

# lang_code misaki / kokoro-onnx
_PREFIX_LANG_CODE: dict[str, str] = {
    "a": "en-us",
    "b": "en-gb",
    "e": "es",
    "f": "fr-fr",
    "h": "hi",
    "i": "it",
    "j": "ja",
    "p": "pt-br",
    "z": "zh",
}

# Préfixe voix Kokoro → locale style UCCX (labels UI type Piper)
_PREFIX_TO_UCCX_LOCALE: dict[str, str] = {
    "af": "en_US",
    "am": "en_US",
    "bf": "en_GB",
    "bm": "en_GB",
    "ff": "fr_FR",
    "ef": "es_ES",
    "em": "es_ES",
    "if": "it_IT",
    "im": "it_IT",
    "pf": "pt_BR",
    "pm": "pt_BR",
    "hf": "hi_IN",
    "hm": "hi_IN",
    "jf": "ja_JP",
    "jm": "ja_JP",
    "zf": "zh_CN",
    "zm": "zh_CN",
}


def kokoro_model_ready(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return Path(settings.kokoro_model).is_file() and Path(settings.kokoro_voices).is_file()


def _get_kokoro(settings: Settings):
    global _kokoro_instance, _kokoro_paths
    if not settings.kokoro_enabled:
        raise TtsError("Kokoro désactivé (KOKORO_ENABLED=false)", status_code=503)
    model = str(Path(settings.kokoro_model))
    voices = str(Path(settings.kokoro_voices))
    if not Path(model).is_file() or not Path(voices).is_file():
        raise TtsError(
            "Modèle Kokoro manquant — exécutez scripts/download-kokoro-model.sh",
            status_code=503,
        )
    with _kokoro_lock:
        if _kokoro_instance is not None and _kokoro_paths == (model, voices):
            return _kokoro_instance
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise TtsError(
                "Paquet kokoro-onnx non installé (rebuild image API)",
                status_code=503,
            ) from exc
        logger.info("Chargement Kokoro model=%s voices=%s", model, voices)
        _kokoro_instance = Kokoro(model, voices)
        _kokoro_paths = (model, voices)
        return _kokoro_instance


def list_kokoro_voice_ids(settings: Settings | None = None) -> list[str]:
    """IDs de voix exposés par le fichier voices.bin."""
    settings = settings or get_settings()
    if not kokoro_model_ready(settings):
        return []
    try:
        k = _get_kokoro(settings)
        # kokoro-onnx : get_voices() ou attribut voices
        if hasattr(k, "get_voices"):
            ids = list(k.get_voices())
        elif hasattr(k, "voices"):
            raw = k.voices
            ids = list(raw.keys()) if isinstance(raw, dict) else list(raw)
        else:
            ids = []
        return sorted(str(x) for x in ids if _SAFE_VOICE_RE.match(str(x)))
    except TtsError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("list kokoro voices: %s", exc)
        return []


def _voice_label(vid: str) -> str:
    """Label UI style Piper : jessica (en_US, af)."""
    if "_" in vid:
        prefix, name = vid.split("_", 1)
        locale = _PREFIX_TO_UCCX_LOCALE.get(prefix.lower(), prefix)
        return f"{name} ({locale}, {prefix})"
    return vid


def _prefixes_for_lang(lang: str | None) -> tuple[str, ...] | None:
    key = uccx_lang_key(lang)
    if key == "default":
        return None
    if key in _ISO_TO_KOKORO_PREFIXES:
        return _ISO_TO_KOKORO_PREFIXES[key]
    prefix = key.split("_", 1)[0]
    if prefix in _ISO_TO_KOKORO_PREFIXES:
        return _ISO_TO_KOKORO_PREFIXES[prefix]
    return ()


def voices_for_uccx_lang(
    lang: str | None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    if not settings.kokoro_enabled:
        return []
    all_ids = list_kokoro_voice_ids(settings)
    if not all_ids:
        return []

    prefixes = _prefixes_for_lang(lang)
    if prefixes is None:
        # default : toutes les voix locales
        filtered_ids = list(all_ids)
    elif not prefixes:
        # Langue UCCX sans mapping Kokoro → aucune voix
        filtered_ids = []
    else:
        filtered_ids = [
            v for v in all_ids if any(v.startswith(f"{p}_") for p in prefixes)
        ]

    default_id = filtered_ids[0] if filtered_ids else None

    out: list[dict[str, Any]] = []
    for vid in filtered_ids:
        fam = vid.split("_", 1)[0] if "_" in vid else (vid[:1] if vid else "")
        out.append(
            {
                "id": vid,
                "label": _voice_label(vid),
                "lang_family": fam or "kokoro",
                "default": vid == default_id,
            }
        )
    return out


def _lang_code_for_voice(voice: str) -> str:
    prefix = (voice or "a")[0].lower()
    return _PREFIX_LANG_CODE.get(prefix, "en-us")


def synthesize_pcm_wav(
    text: str,
    *,
    voice: str | None = None,
    lang: str | None = None,
    settings: Settings | None = None,
) -> bytes:
    """Génère un WAV PCM 16-bit depuis Kokoro."""
    settings = settings or get_settings()
    cleaned = (text or "").strip()
    if not cleaned:
        raise TtsError("Texte vide")
    max_chars = max(1, int(settings.piper_max_chars or 500))
    if len(cleaned) > max_chars:
        raise TtsError(f"Texte trop long (max {max_chars} caractères)")

    chosen = (voice or "").strip() or None
    if not chosen:
        voices = voices_for_uccx_lang(lang, settings)
        chosen = next((v["id"] for v in voices if v.get("default")), None)
        if not chosen and voices:
            chosen = voices[0]["id"]
    if not chosen or not _SAFE_VOICE_RE.match(chosen):
        raise TtsError("Voix Kokoro invalide ou manquante")

    k = _get_kokoro(settings)
    lang_code = _lang_code_for_voice(chosen)
    try:
        samples, sample_rate = k.create(cleaned, voice=chosen, speed=1.0, lang=lang_code)
    except TypeError:
        # API sans lang=
        try:
            samples, sample_rate = k.create(cleaned, voice=chosen, speed=1.0)
        except Exception as exc:  # noqa: BLE001
            raise TtsError(f"Échec synthèse Kokoro : {exc}", status_code=502) from exc
    except Exception as exc:  # noqa: BLE001
        raise TtsError(f"Échec synthèse Kokoro : {exc}", status_code=502) from exc

    import numpy as np

    arr = np.asarray(samples)
    if arr.dtype != np.int16:
        # float -1..1 → int16
        clipped = np.clip(arr.astype(np.float64), -1.0, 1.0)
        arr = (clipped * 32767.0).astype(np.int16)
    if arr.ndim > 1:
        arr = arr.reshape(-1)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate) or 24000)
        wf.writeframes(arr.tobytes())
    return buf.getvalue()


def download_kokoro_model(settings: Settings | None = None) -> dict[str, Any]:
    """Télécharge modèle int8 + voices.bin si absents."""
    import urllib.request

    settings = settings or get_settings()
    model_path = Path(settings.kokoro_model)
    voices_path = Path(settings.kokoro_voices)
    model_path.parent.mkdir(parents=True, exist_ok=True)

    if model_path.is_file() and voices_path.is_file():
        return {"already": True, "model": str(model_path), "voices": str(voices_path)}

    # Releases thewh1teagle/kokoro-onnx
    base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
    targets = {
        model_path: f"{base}/kokoro-v1.0.int8.onnx",
        voices_path: f"{base}/voices-v1.0.bin",
    }
    # Si le path modèle n'est pas int8, tenter le nom demandé
    if "int8" not in model_path.name:
        targets[model_path] = f"{base}/{model_path.name}"

    temps: list[Path] = []
    try:
        for final, url in targets.items():
            if final.is_file() and final.stat().st_size > 1000:
                continue
            tmp = final.with_suffix(final.suffix + ".partial")
            temps.append(tmp)
            logger.info("download kokoro %s", url)
            urllib.request.urlretrieve(url, tmp)  # noqa: S310
            if not tmp.is_file() or tmp.stat().st_size < 1000:
                raise TtsError(f"Téléchargement Kokoro invalide : {final.name}", status_code=502)
            tmp.replace(final)
    except TtsError:
        for t in temps:
            t.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        for t in temps:
            t.unlink(missing_ok=True)
        raise TtsError(f"Échec téléchargement Kokoro : {exc}", status_code=502) from exc

    # Invalider le singleton
    global _kokoro_instance, _kokoro_paths
    with _kokoro_lock:
        _kokoro_instance = None
        _kokoro_paths = None

    return {"already": False, "model": str(model_path), "voices": str(voices_path)}
