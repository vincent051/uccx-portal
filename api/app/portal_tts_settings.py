"""Réglage runtime du moteur TTS pour l’API publique UCCX."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock

from app.config import Settings, get_settings
from app.piper_tts import TtsError
from app.tts_engine import (
    ENGINE_EDGE,
    ENGINE_ELEVENLABS,
    ENGINE_KOKORO,
    ENGINE_PIPER,
    normalize_engine,
)
from app import edge_tts
from app import elevenlabs_tts

logger = logging.getLogger(__name__)

_lock = Lock()
_SETTINGS_NAME = "portal_tts.json"


def _settings_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.data_dir).resolve() / _SETTINGS_NAME


def read_stored_engine(settings: Settings | None = None) -> str | None:
    """Retourne le moteur persisté, ou None si absent / invalide."""
    path = _settings_path(settings)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("portal_tts.json illisible: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("engine")
    if raw is None or not str(raw).strip():
        return None
    try:
        return normalize_engine(str(raw))
    except TtsError:
        logger.warning("portal_tts.json engine invalide: %r", raw)
        return None


def write_stored_engine(engine: str, settings: Settings | None = None) -> str:
    """Persiste le moteur ; retourne la valeur normalisée."""
    settings = settings or get_settings()
    eng = normalize_engine(engine)
    if eng == ENGINE_KOKORO and not settings.kokoro_enabled:
        raise TtsError("Kokoro désactivé (KOKORO_ENABLED=false)", status_code=503)
    if eng == ENGINE_EDGE and not edge_tts.edge_ready(settings):
        raise TtsError(
            "Edge TTS désactivé ou package manquant",
            status_code=503,
        )
    if eng == ENGINE_ELEVENLABS and not elevenlabs_tts.elevenlabs_ready(settings):
        raise TtsError(
            "ElevenLabs désactivé ou sans clé API",
            status_code=503,
        )
    path = _settings_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"engine": eng}, ensure_ascii=False, indent=2) + "\n"
    with _lock:
        path.write_text(payload, encoding="utf-8")
    return eng


def resolve_public_engine(
    request_engine: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Priorité : param REST > fichier portail > env TTS_PUBLIC_ENGINE."""
    settings = settings or get_settings()
    if request_engine and str(request_engine).strip():
        eng = normalize_engine(request_engine)
    else:
        stored = read_stored_engine(settings)
        if stored:
            eng = stored
        else:
            eng = normalize_engine(settings.tts_public_engine or ENGINE_PIPER)

    if eng == ENGINE_KOKORO and not settings.kokoro_enabled:
        raise TtsError("Kokoro désactivé (KOKORO_ENABLED=false)", status_code=503)
    if eng == ENGINE_EDGE and not edge_tts.edge_ready(settings):
        raise TtsError(
            "Edge TTS désactivé ou package manquant",
            status_code=503,
        )
    if eng == ENGINE_ELEVENLABS and not elevenlabs_tts.elevenlabs_ready(settings):
        raise TtsError(
            "ElevenLabs désactivé ou sans clé API",
            status_code=503,
        )
    return eng


def public_settings_payload(settings: Settings | None = None) -> dict:
    from app.tts_engine import list_engines

    settings = settings or get_settings()
    env_default = normalize_engine(settings.tts_public_engine or ENGINE_PIPER)
    stored = read_stored_engine(settings)
    return {
        "engine": stored or env_default,
        "env_default": env_default,
        "stored": stored is not None,
        "engines": list_engines(settings),
        "elevenlabs_ready": elevenlabs_tts.elevenlabs_ready(settings),
        "edge_ready": edge_tts.edge_ready(settings),
    }
