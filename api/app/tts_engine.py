"""Façade multi-moteurs TTS (Piper + Kokoro + Edge + ElevenLabs)."""

from __future__ import annotations

import logging
from typing import Any

from app import edge_tts
from app import elevenlabs_tts
from app import kokoro_tts
from app import piper_tts
from app.config import Settings, get_settings
from app.piper_tts import TtsError

logger = logging.getLogger(__name__)

ENGINE_PIPER = "piper"
ENGINE_KOKORO = "kokoro"
ENGINE_EDGE = "edge"
ENGINE_ELEVENLABS = "elevenlabs"
_ALL_ENGINES = (ENGINE_PIPER, ENGINE_KOKORO, ENGINE_EDGE, ENGINE_ELEVENLABS)


def list_engines(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    engines = [
        {"id": ENGINE_PIPER, "label": "Piper", "available": True},
    ]
    if settings.kokoro_enabled:
        engines.append(
            {
                "id": ENGINE_KOKORO,
                "label": "Kokoro",
                "available": True,
                "model_ready": kokoro_tts.kokoro_model_ready(settings),
            }
        )
    if getattr(settings, "edge_enabled", True):
        engines.append(
            {
                "id": ENGINE_EDGE,
                "label": "Edge TTS",
                "available": edge_tts.edge_ready(settings),
            }
        )
    if getattr(settings, "elevenlabs_enabled", True):
        engines.append(
            {
                "id": ENGINE_ELEVENLABS,
                "label": "ElevenLabs",
                "available": elevenlabs_tts.elevenlabs_ready(settings),
            }
        )
    return engines


def normalize_engine(engine: str | None) -> str:
    e = (engine or ENGINE_PIPER).strip().lower()
    if e not in _ALL_ENGINES:
        raise TtsError(f"Moteur TTS inconnu : {engine!r}")
    return e


def list_voices(
    engine: str | None,
    lang: str | None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    eng = normalize_engine(engine)
    if eng == ENGINE_KOKORO:
        if not settings.kokoro_enabled:
            raise TtsError("Kokoro désactivé", status_code=503)
        return kokoro_tts.voices_for_uccx_lang(lang, settings)
    if eng == ENGINE_EDGE:
        if not getattr(settings, "edge_enabled", True):
            raise TtsError("Edge TTS désactivé", status_code=503)
        return edge_tts.voices_for_uccx_lang(lang, settings)
    if eng == ENGINE_ELEVENLABS:
        if not getattr(settings, "elevenlabs_enabled", True):
            raise TtsError("ElevenLabs désactivé", status_code=503)
        return elevenlabs_tts.voices_for_uccx_lang(lang, settings)
    return piper_tts.voices_for_uccx_lang(lang, settings)


def catalog_voices(
    engine: str | None,
    lang: str | None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    eng = normalize_engine(engine)
    if eng in (ENGINE_KOKORO, ENGINE_EDGE, ENGINE_ELEVENLABS):
        return []
    return piper_tts.piper_catalog_for_lang(lang, settings)


def resolve_engine_for_lang(
    engine: str | None,
    lang: str | None,
    *,
    voice: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Kokoro sans voix pour la langue (ex. de_DE) → Piper, sauf voix explicite."""
    settings = settings or get_settings()
    eng = normalize_engine(engine)
    if eng == ENGINE_ELEVENLABS:
        if not elevenlabs_tts.elevenlabs_ready(settings):
            raise TtsError("ElevenLabs désactivé ou sans clé API", status_code=503)
        return eng
    if eng == ENGINE_EDGE:
        if not edge_tts.edge_ready(settings):
            raise TtsError("Edge TTS désactivé ou package manquant", status_code=503)
        return eng
    if eng != ENGINE_KOKORO:
        return eng
    if (voice or "").strip():
        return eng
    if list_voices(ENGINE_KOKORO, lang, settings):
        return eng
    logger.warning(
        "Kokoro: aucune voix pour lang=%r — bascule Piper (UCCX)",
        lang,
    )
    return ENGINE_PIPER


def synthesize_pcm(
    text: str,
    *,
    engine: str | None = None,
    voice: str | None = None,
    lang: str | None = None,
    settings: Settings | None = None,
) -> bytes:
    settings = settings or get_settings()
    if not settings.tts_enabled:
        raise TtsError("TTS désactivé (TTS_ENABLED=false)", status_code=503)
    eng = resolve_engine_for_lang(
        engine, lang, voice=voice, settings=settings
    )
    if eng == ENGINE_ELEVENLABS:
        return elevenlabs_tts.synthesize_pcm_wav(
            text, voice=voice, lang=lang, settings=settings
        )
    if eng == ENGINE_EDGE:
        return edge_tts.synthesize_pcm_wav(
            text, voice=voice, lang=lang, settings=settings
        )
    if eng == ENGINE_KOKORO:
        return kokoro_tts.synthesize_pcm_wav(
            text, voice=voice, lang=lang, settings=settings
        )
    return piper_tts.synthesize_pcm_wav(
        text, voice=voice, lang=lang, settings=settings
    )


def synthesize_ulaw(
    text: str,
    *,
    engine: str | None = None,
    voice: str | None = None,
    lang: str | None = None,
    settings: Settings | None = None,
) -> tuple[bytes, str]:
    """Synthèse → WAV µ-law 8 kHz mono. Retourne ``(wav, engine_utilisé)``."""
    from app.audio_convert import AudioConvertError, convert_to_ulaw_wav

    settings = settings or get_settings()
    eng = resolve_engine_for_lang(
        engine, lang, voice=voice, settings=settings
    )
    raw = synthesize_pcm(
        text, engine=eng, voice=voice, lang=lang, settings=settings
    )
    try:
        return convert_to_ulaw_wav(raw, f"{eng}.wav"), eng
    except AudioConvertError as exc:
        raise TtsError(f"Conversion µ-law échouée : {exc}", status_code=502) from exc


def warmup_engines(settings: Settings | None = None) -> None:
    """Précharge Kokoro pour éviter 10–20 s au 1er appel UCCX."""
    settings = settings or get_settings()
    if not settings.kokoro_enabled or not kokoro_tts.kokoro_model_ready(settings):
        return
    try:
        ids = kokoro_tts.list_kokoro_voice_ids(settings)
        logger.info("Warmup Kokoro: %s voix chargées", len(ids))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Warmup Kokoro échoué: %s", exc)


def download_voice(
    engine: str | None,
    voice_id: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    eng = normalize_engine(engine)
    if eng == ENGINE_KOKORO:
        raise TtsError(
            "Kokoro : toutes les voix sont locales (voices.bin). "
            "Installez le modèle via scripts/download-kokoro-model.sh",
            status_code=400,
        )
    if eng == ENGINE_EDGE:
        raise TtsError(
            "Edge TTS : les voix sont cloud (pas de téléchargement local).",
            status_code=400,
        )
    if eng == ENGINE_ELEVENLABS:
        raise TtsError(
            "ElevenLabs : les voix sont cloud (pas de téléchargement local).",
            status_code=400,
        )
    return piper_tts.download_piper_voice(voice_id, settings)
