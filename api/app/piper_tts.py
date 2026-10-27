"""Synthèse Piper → WAV (PCM pour préécoute portail, µ-law pour UCCX)."""

from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import subprocess
import tempfile
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Any

from app.audio_convert import AudioConvertError, convert_to_ulaw_wav
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

PIPER_TIMEOUT_SEC = 30
_SAFE_VOICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_SAFE_LANG_RE = re.compile(r"^[A-Za-z]{2,3}([_-][A-Za-z0-9]+)?$")

# Alias langue UCCX → stem modèle Piper (sans .onnx)
_DEFAULT_LANG_MODELS: dict[str, str] = {
    "fr": "fr_FR-siwis-medium",
    "fr_fr": "fr_FR-siwis-medium",
    "fr-fr": "fr_FR-siwis-medium",
    "fr_lu": "lb_LU-marylux-medium",
    "fr-lu": "lb_LU-marylux-medium",
    "lb": "lb_LU-marylux-medium",
    "lb_lu": "lb_LU-marylux-medium",
    "lb-lu": "lb_LU-marylux-medium",
    "en": "en_US-lessac-medium",
    "en_us": "en_US-lessac-medium",
    "en-us": "en_US-lessac-medium",
    "en_gb": "en_US-lessac-medium",
    "en-gb": "en_US-lessac-medium",
    "de": "de_DE-thorsten-medium",
    "de_de": "de_DE-thorsten-medium",
    "de-de": "de_DE-thorsten-medium",
}

# Familles de stems Piper proposées par code langue cellule UCCX
_UCCX_LANG_FAMILIES: dict[str, tuple[str, ...]] = {
    "fr_fr": ("fr",),
    "fr_lu": ("lb", "fr"),
    "lb": ("lb", "fr"),
    "lb_lu": ("lb", "fr"),
    "en_us": ("en",),
    "en_gb": ("en",),
    "de_de": ("de",),
}

_MIN_VOICES = 2
_PIPER_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
_DOWNLOAD_TIMEOUT_SEC = 600
_CATALOG_PATH = Path(__file__).with_name("tts_piper_catalog.json")

_rate_lock = Lock()
_rate_hits: dict[str, deque[float]] = defaultdict(deque)


class TtsError(Exception):
    """Erreur métier TTS (message exposable au client)."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def parse_cidrs(raw: str) -> list[ipaddress._BaseNetwork]:
    nets: list[ipaddress._BaseNetwork] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("CIDR TTS invalide ignoré: %s", part)
    return nets


def client_ip_from_request(
    peer_ip: str | None,
    x_forwarded_for: str | None,
    *,
    trusted_proxy_cidrs: list[ipaddress._BaseNetwork],
) -> str:
    """IP client réelle : X-Forwarded-For seulement si le peer est un proxy de confiance (Traefik)."""
    peer = (peer_ip or "").strip() or "unknown"
    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer

    from_trusted_proxy = any(peer_addr in net for net in trusted_proxy_cidrs)
    if from_trusted_proxy and x_forwarded_for:
        # Premier hop = client original
        first = x_forwarded_for.split(",")[0].strip()
        if first:
            try:
                ipaddress.ip_address(first)
                return first
            except ValueError:
                pass
    return peer


def check_ip_allowed(client_ip: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    allowed = parse_cidrs(settings.tts_allowed_cidrs)
    if not allowed:
        raise TtsError(
            "TTS_ALLOWED_CIDRS non configuré — accès refusé",
            status_code=503,
        )
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError as exc:
        raise TtsError("Adresse IP cliente invalide", status_code=403) from exc
    if any(addr in net for net in allowed):
        return
    raise TtsError(
        "Accès TTS refusé (IP hors subnet UCCX autorisé)",
        status_code=403,
    )


def check_rate_limit(client_ip: str, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    limit = max(0, int(settings.tts_rate_limit_per_min or 0))
    if limit <= 0:
        return
    now = time.monotonic()
    window = 60.0
    key = (client_ip or "unknown").strip() or "unknown"
    with _rate_lock:
        q = _rate_hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise TtsError(
                f"Trop de requêtes TTS (max {limit}/min). Réessayez plus tard.",
                status_code=429,
            )
        q.append(now)


def _lang_model_map(settings: Settings) -> dict[str, str]:
    """Fusionne la carte par défaut et PIPER_LANG_MAP (lang:model,...)."""
    out = dict(_DEFAULT_LANG_MODELS)
    raw = (settings.piper_lang_map or "").strip()
    if not raw:
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        lang, model = part.split(":", 1)
        lang_k = lang.strip().lower().replace("-", "_")
        model_v = model.strip()
        if lang_k and model_v and _SAFE_VOICE_RE.match(model_v.replace(".onnx", "")):
            out[lang_k] = model_v.removesuffix(".onnx")
    return out


def normalize_lang(lang: str | None) -> str | None:
    if not lang:
        return None
    cleaned = lang.strip().replace("-", "_")
    if not cleaned:
        return None
    if not _SAFE_LANG_RE.match(cleaned):
        raise TtsError(f"Code langue invalide : {lang!r}")
    return cleaned.lower()


def uccx_lang_key(lang: str | None) -> str:
    """Clé langue cellule : 'default' ou code normalisé (fr_lu, en_us, …)."""
    if not lang or not str(lang).strip():
        return "default"
    cleaned = str(lang).strip().replace("-", "_")
    if cleaned.lower() == "default":
        return "default"
    return normalize_lang(cleaned) or "default"


def families_for_uccx_lang(lang: str | None) -> tuple[str, ...] | None:
    """Familles Piper pour une langue UCCX ; None = toutes."""
    key = uccx_lang_key(lang)
    if key == "default":
        return None
    if key in _UCCX_LANG_FAMILIES:
        return _UCCX_LANG_FAMILIES[key]
    prefix = key.split("_", 1)[0]
    return (prefix,) if prefix else None


def voice_for_lang(lang: str | None, settings: Settings) -> str | None:
    """Retourne le stem modèle pour une langue UCCX (fr_FR, en_US, …)."""
    key = uccx_lang_key(lang)
    if key == "default":
        return None
    mapping = _lang_model_map(settings)
    if key in mapping:
        return mapping[key]
    # Fallback préfixe langue (fr_XX → fr)
    prefix = key.split("_", 1)[0]
    if prefix in mapping:
        return mapping[prefix]
    # Langue inconnue : première voix installée de la famille, sinon None
    fam = prefix
    for v in list_installed_voices(settings):
        if v["lang_family"] == fam:
            return v["id"]
    return None


def models_dir(settings: Settings) -> Path:
    return Path(settings.piper_model).parent


def _voice_family(stem: str) -> str:
    """Préfixe langue du stem Piper (fr_FR-siwis-medium → fr)."""
    base = stem.removesuffix(".onnx")
    locale = base.split("-", 1)[0]
    return locale.split("_", 1)[0].lower()


def _voice_label(stem: str) -> str:
    """Label UI : marylux (lb_LU, medium)."""
    base = stem.removesuffix(".onnx")
    parts = base.split("-")
    if len(parts) >= 3:
        locale, name, quality = parts[0], parts[1], parts[-1]
        return f"{name} ({locale}, {quality})"
    if len(parts) == 2:
        return f"{parts[1]} ({parts[0]})"
    return base


def list_installed_voices(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Liste les modèles .onnx (+ .json) présents sous data/piper."""
    settings = settings or get_settings()
    directory = models_dir(settings)
    if not directory.is_dir():
        return []
    voices: list[dict[str, Any]] = []
    for onnx in sorted(directory.glob("*.onnx")):
        if not Path(str(onnx) + ".json").is_file():
            continue
        stem = onnx.stem
        if not _SAFE_VOICE_RE.match(stem):
            continue
        voices.append(
            {
                "id": stem,
                "label": _voice_label(stem),
                "lang_family": _voice_family(stem),
            }
        )
    return voices


def _ensure_min_voices(
    filtered: list[dict[str, Any]],
    all_voices: list[dict[str, Any]],
    *,
    min_count: int = _MIN_VOICES,
) -> list[dict[str, Any]]:
    """Élargit avec d'autres familles si moins de min_count voix."""
    if len(filtered) >= min_count or not all_voices:
        return filtered
    have = {v["id"] for v in filtered}
    out = list(filtered)
    for v in all_voices:
        if v["id"] in have:
            continue
        enriched = {**v, "label": f"{v['label']} ({v['lang_family']})"}
        out.append(enriched)
        have.add(v["id"])
        if len(out) >= min_count:
            break
    return out


def voices_for_uccx_lang(
    lang: str | None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Voix installées filtrées pour une langue cellule UCCX (+ flag default)."""
    settings = settings or get_settings()
    all_voices = list_installed_voices(settings)
    key = uccx_lang_key(lang)
    families = families_for_uccx_lang(lang)

    if families is None:
        filtered = list(all_voices)
    else:
        fam_set = set(families)
        filtered = [v for v in all_voices if v["lang_family"] in fam_set]
        if not filtered:
            # Nouvelle langue sans modèles : toutes les voix installées
            filtered = list(all_voices)
        else:
            filtered = _ensure_min_voices(filtered, all_voices)

    default_id: str | None = None
    if key != "default":
        default_id = voice_for_lang(key, settings)
    if not default_id and filtered:
        default_id = filtered[0]["id"]

    if families:
        fam_order = {f: i for i, f in enumerate(families)}
        filtered.sort(
            key=lambda v: (fam_order.get(v["lang_family"], 99), v["id"])
        )
    else:
        filtered.sort(key=lambda v: v["id"])

    out: list[dict[str, Any]] = []
    for v in filtered:
        out.append(
            {
                **v,
                "default": bool(default_id and v["id"] == default_id),
            }
        )
    if out and not any(v["default"] for v in out):
        out[0]["default"] = True
    return out


def load_piper_catalog() -> list[dict[str, Any]]:
    """Catalogue voix Piper téléchargeables."""
    import json

    if not _CATALOG_PATH.is_file():
        return []
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("catalogue Piper illisible: %s", exc)
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        vid = str(row.get("id") or "").strip()
        sub = str(row.get("hf_subdir") or "").strip()
        if not vid or not sub or not _SAFE_VOICE_RE.match(vid):
            continue
        if ".." in sub or sub.startswith("/"):
            continue
        out.append(
            {
                "id": vid,
                "label": str(row.get("label") or _voice_label(vid)),
                "lang_family": str(row.get("lang_family") or _voice_family(vid)).lower(),
                "hf_subdir": sub,
            }
        )
    return out


def piper_catalog_for_lang(lang: str | None, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Catalogue filtré + flag installed."""
    settings = settings or get_settings()
    installed = {v["id"] for v in list_installed_voices(settings)}
    families = families_for_uccx_lang(lang)
    catalog = load_piper_catalog()
    rows: list[dict[str, Any]] = []
    for row in catalog:
        if families is not None and row["lang_family"] not in families:
            # Pour nouvelles langues / default élargi : si familles strictes vides d'install,
            # on garde le filtre familles ; pour default (None) tout passe.
            continue
        rows.append(
            {
                "id": row["id"],
                "label": row["label"],
                "lang_family": row["lang_family"],
                "installed": row["id"] in installed,
                "hf_subdir": row["hf_subdir"],
            }
        )
    # Si filtre trop strict et catalogue vide pour la langue, montrer tout le catalogue
    if families is not None and not rows:
        for row in catalog:
            rows.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "lang_family": row["lang_family"],
                    "installed": row["id"] in installed,
                    "hf_subdir": row["hf_subdir"],
                }
            )
    return rows


def download_piper_voice(voice_id: str, settings: Settings | None = None) -> dict[str, Any]:
    """Télécharge .onnx + .json depuis HuggingFace vers data/piper."""
    import urllib.request

    settings = settings or get_settings()
    vid = (voice_id or "").strip().removesuffix(".onnx")
    if not _SAFE_VOICE_RE.match(vid):
        raise TtsError("Identifiant de voix invalide")

    entry = next((c for c in load_piper_catalog() if c["id"] == vid), None)
    if not entry:
        raise TtsError(f"Voix absente du catalogue : {vid}", status_code=404)

    dest_dir = models_dir(settings)
    dest_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = dest_dir / f"{vid}.onnx"
    json_path = dest_dir / f"{vid}.onnx.json"
    if onnx_path.is_file() and json_path.is_file():
        return {"id": vid, "already": True, "path": str(onnx_path)}

    sub = entry["hf_subdir"]
    urls = {
        onnx_path: f"{_PIPER_HF_BASE}/{sub}/{vid}.onnx",
        json_path: f"{_PIPER_HF_BASE}/{sub}/{vid}.onnx.json",
    }
    temps: list[Path] = []
    try:
        for final, url in urls.items():
            tmp = final.with_suffix(final.suffix + ".partial")
            temps.append(tmp)
            logger.info("download piper %s → %s", url, final)
            urllib.request.urlretrieve(url, tmp)  # noqa: S310 — URL catalogue contrôlée
            if not tmp.is_file() or tmp.stat().st_size < 100:
                raise TtsError(f"Téléchargement invalide : {final.name}", status_code=502)
            tmp.replace(final)
    except TtsError:
        for t in temps:
            t.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001
        for t in temps:
            t.unlink(missing_ok=True)
        raise TtsError(f"Échec téléchargement Piper : {exc}", status_code=502) from exc

    return {"id": vid, "already": False, "path": str(onnx_path)}


def _resolve_model(
    settings: Settings,
    *,
    voice: str | None = None,
    lang: str | None = None,
) -> Path:
    """Résout le .onnx : voice explicite > lang mappé > PIPER_MODEL."""
    models_directory = models_dir(settings)
    chosen = (voice or "").strip() or None
    if not chosen and lang and uccx_lang_key(lang) != "default":
        chosen = voice_for_lang(lang, settings)
    if not chosen and lang and uccx_lang_key(lang) == "default":
        # Première voix installée
        installed = list_installed_voices(settings)
        if installed:
            chosen = installed[0]["id"]
    if chosen:
        name = chosen.strip()
        if not _SAFE_VOICE_RE.match(name.removesuffix(".onnx")):
            raise TtsError("Nom de voix invalide")
        if not name.endswith(".onnx"):
            name = f"{name}.onnx"
        if "/" in name or "\\" in name or ".." in name:
            raise TtsError("Nom de voix invalide")
        model = (models_directory / name).resolve()
        try:
            model.relative_to(models_directory.resolve())
        except ValueError as exc:
            raise TtsError("Nom de voix invalide") from exc
    else:
        model = Path(settings.piper_model)

    if not model.is_file():
        raise TtsError(
            f"Modèle Piper introuvable : {model}. "
            "Exécutez scripts/download-piper-model.sh",
            status_code=503,
        )
    config = Path(str(model) + ".json")
    if not config.is_file():
        raise TtsError(
            f"Config modèle manquante : {config.name}",
            status_code=503,
        )
    return model


def _run_piper(
    text: str,
    *,
    voice: str | None = None,
    lang: str | None = None,
    settings: Settings | None = None,
) -> bytes:
    """Exécute Piper et retourne le WAV PCM brut."""
    settings = settings or get_settings()
    if not settings.tts_enabled:
        raise TtsError("TTS désactivé (TTS_ENABLED=false)", status_code=503)

    cleaned = (text or "").strip()
    if not cleaned:
        raise TtsError("Texte vide")
    max_chars = max(1, int(settings.piper_max_chars or 500))
    if len(cleaned) > max_chars:
        raise TtsError(f"Texte trop long (max {max_chars} caractères)")

    piper_bin = Path(settings.piper_bin)
    if not piper_bin.is_file():
        found = shutil.which(str(settings.piper_bin))
        if not found:
            raise TtsError(
                f"Binaire Piper introuvable : {settings.piper_bin}",
                status_code=503,
            )
        piper_bin = Path(found)

    model = _resolve_model(settings, voice=voice, lang=lang)
    logger.info("TTS model=%s lang=%s chars=%s", model.name, lang or "-", len(cleaned))

    with tempfile.TemporaryDirectory(prefix="piper-tts-") as tmp:
        tmp_path = Path(tmp)
        out_wav = tmp_path / "piper.wav"
        cmd = [
            str(piper_bin),
            "--model",
            str(model),
            "--output_file",
            str(out_wav),
        ]
        try:
            proc = subprocess.run(
                cmd,
                input=cleaned.encode("utf-8"),
                capture_output=True,
                timeout=PIPER_TIMEOUT_SEC,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TtsError("Synthèse Piper trop longue", status_code=502) from exc
        except FileNotFoundError as exc:
            raise TtsError(
                f"Binaire Piper introuvable : {piper_bin}",
                status_code=503,
            ) from exc

        if proc.returncode != 0 or not out_wav.is_file():
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
            logger.warning("piper failed rc=%s: %s", proc.returncode, err[:400])
            raise TtsError(
                f"Échec synthèse Piper : {(err.strip() or f'code {proc.returncode}')[:200]}",
                status_code=502,
            )

        raw = out_wav.read_bytes()
        if not raw:
            raise TtsError("Piper a produit un fichier vide", status_code=502)
        return raw


def synthesize_pcm_wav(
    text: str,
    *,
    voice: str | None = None,
    lang: str | None = None,
    settings: Settings | None = None,
) -> bytes:
    """Génère un WAV PCM (sortie native Piper) pour préécoute navigateur."""
    return _run_piper(text, voice=voice, lang=lang, settings=settings)


def synthesize_ulaw_wav(
    text: str,
    *,
    voice: str | None = None,
    lang: str | None = None,
    settings: Settings | None = None,
) -> bytes:
    """Génère un WAV µ-law 8 kHz mono à partir du texte."""
    raw = _run_piper(text, voice=voice, lang=lang, settings=settings)
    try:
        return convert_to_ulaw_wav(raw, "piper.wav")
    except AudioConvertError as exc:
        raise TtsError(f"Conversion µ-law échouée : {exc}", status_code=502) from exc
