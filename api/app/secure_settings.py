"""Paramètres runtime chiffrés (Fernet) — override .env sans le réécrire en clair."""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import PortalSecret

logger = logging.getLogger(__name__)

# Clés éditables depuis l’UI admin (whitelist)
EDITABLE_KEYS: dict[str, dict[str, Any]] = {
    "public_base_url": {"secret": False, "label": "URL publique", "group": "portal"},
    "uccx_host": {"secret": False, "label": "UCCX host", "group": "uccx"},
    "uccx_user": {"secret": False, "label": "UCCX user", "group": "uccx"},
    "uccx_password": {"secret": True, "label": "UCCX password", "group": "uccx"},
    "uccx_verify_ssl": {"secret": False, "label": "UCCX verify SSL", "group": "uccx"},
    "uccx_sync_enabled": {"secret": False, "label": "Sync apps UCCX", "group": "uccx"},
    "uccx_document_folder": {"secret": False, "label": "Document folder", "group": "uccx"},
    "tts_enabled": {"secret": False, "label": "TTS enabled", "group": "tts"},
    "tts_public_engine": {"secret": False, "label": "TTS engine public", "group": "tts"},
    "tts_allowed_cidrs": {"secret": False, "label": "TTS allowed CIDRs", "group": "tts"},
    "tts_trusted_proxy_cidrs": {
        "secret": False,
        "label": "TTS trusted proxies",
        "group": "tts",
    },
    "elevenlabs_enabled": {
        "secret": False,
        "label": "ElevenLabs enabled",
        "group": "tts",
    },
    "elevenlabs_api_key": {
        "secret": True,
        "label": "ElevenLabs API key",
        "group": "tts",
    },
    "elevenlabs_model": {
        "secret": False,
        "label": "ElevenLabs model",
        "group": "tts",
    },
    "edge_enabled": {
        "secret": False,
        "label": "Edge TTS enabled",
        "group": "tts",
    },
    "stt_enabled": {"secret": False, "label": "STT enabled", "group": "stt"},
    "saml_enabled": {"secret": False, "label": "SAML SSO enabled", "group": "saml"},
    "saml_idp_metadata_url": {
        "secret": False,
        "label": "IdP metadata URL",
        "group": "saml",
    },
    "saml_idp_metadata_xml": {
        "secret": False,
        "label": "IdP metadata XML",
        "group": "saml",
    },
    "saml_sp_entity_id": {"secret": False, "label": "SP Entity ID", "group": "saml"},
    "saml_userid_attr": {
        "secret": False,
        "label": "Attribute userid",
        "group": "saml",
    },
    "saml_want_assertions_signed": {
        "secret": False,
        "label": "Require signed assertions",
        "group": "saml",
    },
    "skill_scheduler_interval_sec": {
        "secret": False,
        "label": "Skill scheduler interval (s)",
        "group": "skills",
    },
}

_MASK = "********"


def _fernet(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    raw = (getattr(settings, "settings_fernet_key", None) or "").strip()
    if not raw:
        raw = settings.secret_key
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_value(plain: str, settings: Settings | None = None) -> str:
    return _fernet(settings).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_value(token: str, settings: Settings | None = None) -> str:
    try:
        return _fernet(settings).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValueError("Impossible de déchiffrer le secret") from exc


def _coerce(key: str, raw: str) -> Any:
    """Convertit la valeur stockée vers le type Settings."""
    settings = get_settings()
    if not hasattr(settings, key):
        # Champs SAML / extras stockés seulement en DB
        if key in (
            "saml_enabled",
            "saml_want_assertions_signed",
            "uccx_verify_ssl",
            "uccx_sync_enabled",
            "tts_enabled",
            "stt_enabled",
            "elevenlabs_enabled",
            "edge_enabled",
        ):
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        if key == "skill_scheduler_interval_sec":
            try:
                return int(raw)
            except ValueError:
                return 60
        return raw
    current = getattr(settings, key)
    if isinstance(current, bool):
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    return raw


def load_overrides(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    out: dict[str, Any] = {}
    for row in db.query(PortalSecret).all():
        if row.key not in EDITABLE_KEYS:
            continue
        try:
            plain = decrypt_value(row.value_enc, settings)
        except ValueError:
            logger.warning("Secret illisible: %s", row.key)
            continue
        out[row.key] = _coerce(row.key, plain)
    return out


def apply_overrides_to_settings(db: Session) -> Settings:
    """Recharge Settings + applique overrides DB ; invalide le cache lru."""
    get_settings.cache_clear()
    base = get_settings()
    overrides = load_overrides(db, base)
    if not overrides:
        return base
    data = base.model_dump()
    for k, v in overrides.items():
        if k in data:
            data[k] = v
    # Attache extras SAML sur l’instance
    merged = Settings(**{k: v for k, v in data.items() if k in Settings.model_fields})
    for k, v in overrides.items():
        if k not in Settings.model_fields:
            object.__setattr__(merged, k, v)
    return merged


def get_effective_settings(db: Session | None = None) -> Settings:
    if db is None:
        return get_settings()
    return apply_overrides_to_settings(db)


def list_settings_for_admin(db: Session) -> list[dict[str, Any]]:
    settings = get_settings()
    overrides = load_overrides(db, settings)
    rows = []
    for key, meta in EDITABLE_KEYS.items():
        if key in overrides:
            raw_val = overrides[key]
            source = "db"
        elif hasattr(settings, key):
            raw_val = getattr(settings, key)
            source = "env"
        else:
            raw_val = ""
            source = "default"
        display = raw_val
        if meta["secret"] and raw_val not in (None, "", False):
            display = _MASK
        elif isinstance(raw_val, bool):
            display = "true" if raw_val else "false"
        else:
            display = "" if raw_val is None else str(raw_val)
        rows.append(
            {
                "key": key,
                "label": meta["label"],
                "group": meta["group"],
                "secret": meta["secret"],
                "value": display,
                "source": source,
            }
        )
    return rows


def save_settings(db: Session, updates: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_settings()
    for key, value in updates.items():
        if key not in EDITABLE_KEYS:
            continue
        meta = EDITABLE_KEYS[key]
        if meta["secret"] and str(value).strip() in ("", _MASK):
            continue  # ne pas écraser avec le masque
        plain = value if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else str(value)
        row = db.query(PortalSecret).filter(PortalSecret.key == key).first()
        enc = encrypt_value(plain, settings)
        if row:
            row.value_enc = enc
            row.is_secret = bool(meta["secret"])
        else:
            db.add(
                PortalSecret(
                    key=key,
                    value_enc=enc,
                    is_secret=bool(meta["secret"]),
                )
            )
    db.commit()
    get_settings.cache_clear()
    return list_settings_for_admin(db)


def get_setting_value(db: Session, key: str, default: Any = None) -> Any:
    overrides = load_overrides(db)
    if key in overrides:
        return overrides[key]
    settings = get_settings()
    if hasattr(settings, key):
        return getattr(settings, key)
    return default
