"""Transcription locale faster-whisper pour prompts UCCX."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

WHISPER_SAMPLE_RATE = 16000

_model_lock = threading.Lock()
_stt_run_lock = threading.Lock()  # une transcription à la fois (CPU + pool DB)
_model = None
_model_key: tuple | None = None


class SttError(Exception):
    """Erreur métier STT."""

    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def whisper_lang_hint(uccx_lang: str | None) -> str | None:
    """Mappe fr_FR / en_US / … vers un code Whisper (fr, en, …)."""
    raw = (uccx_lang or "").strip()
    if not raw or raw.lower() == "default":
        return None
    base = raw.replace("-", "_").split("_")[0].lower()
    if 2 <= len(base) <= 3 and base.isalpha():
        return base
    return None


def _get_model(settings: Settings):
    global _model, _model_key
    key = (
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
        settings.whisper_model_dir,
    )
    with _model_lock:
        if _model is not None and _model_key == key:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise SttError(
                "faster-whisper non installé (rebuild image API)",
                status_code=503,
            ) from exc
        Path(settings.whisper_model_dir).mkdir(parents=True, exist_ok=True)
        logger.info(
            "Chargement Whisper model=%s device=%s compute=%s dir=%s",
            settings.whisper_model,
            settings.whisper_device,
            settings.whisper_compute_type,
            settings.whisper_model_dir,
        )
        _model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            download_root=settings.whisper_model_dir,
        )
        _model_key = key
        return _model


def _wav_bytes_to_float32(audio: bytes, sample_rate: int = WHISPER_SAMPLE_RATE) -> np.ndarray:
    """Décode via ffmpeg (µ-law / PCM) → float32 mono — évite le bug PyAV/faster-whisper."""
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        Path(path).write_bytes(audio)
        proc = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                path,
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-f",
                "f32le",
                "-",
            ],
            check=False,
            capture_output=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise SttError(f"ffmpeg decode échoué: {err or proc.returncode}")
        if not proc.stdout:
            raise SttError("ffmpeg decode vide")
        return np.frombuffer(proc.stdout, dtype=np.float32).copy()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def transcribe_wav_bytes(
    audio: bytes,
    *,
    lang_hint: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Transcrit un WAV (ou audio ffmpeg-compatible) → texte."""
    settings = settings or get_settings()
    if not settings.stt_enabled:
        raise SttError("STT désactivé (STT_ENABLED=false)", status_code=503)
    if not audio:
        raise SttError("Audio vide", status_code=400)

    whisper_lang = whisper_lang_hint(lang_hint)
    # Sérialiser : plusieurs survols ne doivent pas monopoliser CPU + connexions DB
    with _stt_run_lock:
        model = _get_model(settings)
        try:
            samples = _wav_bytes_to_float32(audio)
            segments, _info = model.transcribe(
                samples,
                language=whisper_lang,
                vad_filter=True,
                beam_size=1,
            )
            parts = [seg.text.strip() for seg in segments if seg.text and seg.text.strip()]
            text = " ".join(parts).strip()
            if not text:
                raise SttError("Transcription vide", status_code=422)
            return text
        except SttError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Whisper échec")
            raise SttError(f"Transcription échouée : {exc}") from exc
