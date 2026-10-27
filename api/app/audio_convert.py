"""Conversion audio vers WAV µ-law 8 kHz mono (format Cisco G.711)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".ogg",
    ".oga",
    ".flac",
    ".aiff",
    ".aif",
    ".wma",
    ".webm",
}


class AudioConvertError(Exception):
    pass


def _run(cmd: list[str]) -> None:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AudioConvertError(f"Outil manquant: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioConvertError("Conversion audio trop longue") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:400]
        raise AudioConvertError(f"{cmd[0]} a échoué: {err or 'code ' + str(proc.returncode)}")


def convert_to_ulaw_wav(data: bytes, original_filename: str = "audio.bin") -> bytes:
    """Convertit n'importe quel audio supporté en WAV µ-law 8 kHz mono."""
    if not data:
        raise AudioConvertError("Fichier audio vide")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AudioConvertError(f"Fichier trop volumineux (max {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo)")

    suffix = Path(original_filename).suffix.lower() or ".bin"
    with tempfile.TemporaryDirectory(prefix="uccx-audio-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / f"input{suffix}"
        dst = tmp_path / "output.wav"
        src.write_bytes(data)

        sox = shutil.which("sox")
        ffmpeg = shutil.which("ffmpeg")

        if sox:
            try:
                _run(
                    [
                        sox,
                        str(src),
                        "-r",
                        "8000",
                        "-c",
                        "1",
                        "-e",
                        "u-law",
                        str(dst),
                    ]
                )
                return dst.read_bytes()
            except AudioConvertError as sox_err:
                logger.warning("sox échoué (%s), tentative ffmpeg", sox_err)
                if not ffmpeg:
                    raise

        if not ffmpeg:
            raise AudioConvertError("sox/ffmpeg non disponibles dans le conteneur")

        # ffmpeg → WAV PCM 8 kHz mono puis sox µ-law si possible, sinon pcm_mulaw
        mid = tmp_path / "mid.wav"
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-ar",
                "8000",
                "-ac",
                "1",
                "-c:a",
                "pcm_mulaw",
                str(mid),
            ]
        )
        # Renommer en .wav standard
        mid.replace(dst)
        return dst.read_bytes()


def prepend_silence_wav(wav_bytes: bytes, seconds: float = 0.5) -> bytes:
    """Ajoute du silence au début d'un WAV en préservant le format (PCM TTS, etc.)."""
    if not wav_bytes:
        raise AudioConvertError("Audio vide")
    if seconds <= 0:
        return wav_bytes

    sox = shutil.which("sox")
    ffmpeg = shutil.which("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="uccx-silence-pcm-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "in.wav"
        dst = tmp_path / "out.wav"
        src.write_bytes(wav_bytes)

        if sox:
            try:
                _run([sox, str(src), str(dst), "pad", str(seconds), "0"])
                return dst.read_bytes()
            except AudioConvertError as sox_err:
                logger.warning("sox pad silence PCM échoué (%s), tentative ffmpeg", sox_err)
                if not ffmpeg:
                    raise

        if not ffmpeg:
            raise AudioConvertError("sox/ffmpeg non disponibles pour le silence")

        delay_ms = int(round(seconds * 1000))
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-af",
                f"adelay={delay_ms}|{delay_ms}",
                str(dst),
            ]
        )
        return dst.read_bytes()


def prepend_silence_ulaw(wav_bytes: bytes, seconds: float = 0.5) -> bytes:
    """Ajoute du silence µ-law 8 kHz mono au début d'un WAV (prompts UCCX)."""
    if not wav_bytes:
        raise AudioConvertError("Audio vide")
    if seconds <= 0:
        return wav_bytes

    sox = shutil.which("sox")
    ffmpeg = shutil.which("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="uccx-silence-") as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "in.wav"
        dst = tmp_path / "out.wav"
        src.write_bytes(wav_bytes)

        if sox:
            try:
                # pad début : silence puis audio d'origine, sortie µ-law 8 kHz mono
                _run(
                    [
                        sox,
                        str(src),
                        "-r",
                        "8000",
                        "-c",
                        "1",
                        "-e",
                        "u-law",
                        str(dst),
                        "pad",
                        str(seconds),
                        "0",
                    ]
                )
                return dst.read_bytes()
            except AudioConvertError as sox_err:
                logger.warning("sox pad silence échoué (%s), tentative ffmpeg", sox_err)
                if not ffmpeg:
                    raise

        if not ffmpeg:
            raise AudioConvertError("sox/ffmpeg non disponibles pour le silence")

        # adelay en ms sur stream µ-law → ré-encode pcm_mulaw
        delay_ms = int(round(seconds * 1000))
        _run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-af",
                f"adelay={delay_ms}|{delay_ms}",
                "-ar",
                "8000",
                "-ac",
                "1",
                "-c:a",
                "pcm_mulaw",
                str(dst),
            ]
        )
        return dst.read_bytes()


def concat_pcm_wavs(wav_list: list[bytes], gap_ms: int = 400) -> bytes:
    """Concatène des WAV PCM (TTS) avec un silence entre chaque segment.

    Rééchantillonne tous les segments (mono) pour accepter Piper + Kokoro
    aux rates différents.
    """
    if not wav_list:
        raise AudioConvertError("Aucun segment audio")
    if len(wav_list) == 1:
        return wav_list[0]

    gap_s = max(0, int(gap_ms)) / 1000.0
    sox = shutil.which("sox")
    ffmpeg = shutil.which("ffmpeg")
    if not sox and not ffmpeg:
        raise AudioConvertError("sox/ffmpeg non disponibles pour la concaténation")

    with tempfile.TemporaryDirectory(prefix="uccx-concat-pcm-") as tmp:
        tmp_path = Path(tmp)
        srcs: list[Path] = []
        for i, data in enumerate(wav_list):
            if not data:
                raise AudioConvertError(f"Segment audio {i + 1} vide")
            p = tmp_path / f"seg{i}.wav"
            p.write_bytes(data)
            srcs.append(p)

        dst = tmp_path / "out.wav"
        target_rate = "22050"
        soxi = shutil.which("soxi")
        if soxi:
            try:
                proc = subprocess.run(
                    [soxi, "-r", str(srcs[0])],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if proc.returncode == 0 and (proc.stdout or "").strip().isdigit():
                    target_rate = proc.stdout.strip()
            except Exception:  # noqa: BLE001
                pass

        if sox:
            try:
                padded: list[Path] = []
                for i, src in enumerate(srcs):
                    norm = tmp_path / f"n{i}.wav"
                    _run([sox, str(src), "-r", target_rate, "-c", "1", str(norm)])
                    if gap_s > 0 and i < len(srcs) - 1:
                        with_gap = tmp_path / f"g{i}.wav"
                        _run([sox, str(norm), str(with_gap), "pad", "0", str(gap_s)])
                        padded.append(with_gap)
                    else:
                        padded.append(norm)
                _run([sox, *[str(p) for p in padded], str(dst)])
                return dst.read_bytes()
            except AudioConvertError as sox_err:
                logger.warning("sox concat échoué (%s), tentative ffmpeg", sox_err)
                if not ffmpeg:
                    raise

        parts: list[Path] = []
        for i, src in enumerate(srcs):
            norm = tmp_path / f"fn{i}.wav"
            _run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(src),
                    "-ar",
                    target_rate,
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(norm),
                ]
            )
            parts.append(norm)
            if gap_s > 0 and i < len(srcs) - 1:
                sil = tmp_path / f"sil{i}.wav"
                _run(
                    [
                        ffmpeg,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"anullsrc=r={target_rate}:cl=mono",
                        "-t",
                        str(gap_s),
                        "-c:a",
                        "pcm_s16le",
                        str(sil),
                    ]
                )
                parts.append(sil)

        n = len(parts)
        inputs: list[str] = []
        for p in parts:
            inputs.extend(["-i", str(p)])
        filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
        _run(
            [
                ffmpeg,
                "-y",
                *inputs,
                "-filter_complex",
                filt,
                "-map",
                "[out]",
                str(dst),
            ]
        )
        return dst.read_bytes()


def wav_filename_for_upload(original_filename: str, param_name: str = "") -> str:
    """Nom WAV court (UCCX est sensible aux noms longs)."""
    base = Path(original_filename).stem or "prompt"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)[:24]
    prefix = "".join(c if c.isalnum() or c in "-_" else "_" for c in (param_name or ""))[:16]
    name = f"{prefix}_{safe}" if prefix else safe
    # Garde-fou longueur totale hors extension
    name = name[:40].rstrip("_")
    return f"{name}.wav"
