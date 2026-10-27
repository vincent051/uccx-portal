"""CRUD transcripts prompts (TTS input ou Whisper STT)."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models import PromptTranscript

logger = logging.getLogger(__name__)


def ensure_prompt_transcript_schema(engine: Engine) -> None:
    """Ajoute stt_enabled si la table existe déjà sans la colonne."""
    with engine.begin() as conn:
        dialect = engine.dialect.name
        if dialect == "sqlite":
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(prompt_transcripts)")).fetchall()
            }
            if cols and "stt_enabled" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE prompt_transcripts "
                        "ADD COLUMN stt_enabled BOOLEAN NOT NULL DEFAULT 1"
                    )
                )
                logger.info("prompt_transcripts: colonne stt_enabled ajoutée (sqlite)")
        elif dialect == "postgresql":
            conn.execute(
                text(
                    "ALTER TABLE prompt_transcripts "
                    "ADD COLUMN IF NOT EXISTS stt_enabled BOOLEAN NOT NULL DEFAULT TRUE"
                )
            )

SOURCE_TTS = "tts"
SOURCE_STT = "stt"
VALID_SOURCES = frozenset({SOURCE_TTS, SOURCE_STT})


def normalize_source(source: str | None) -> str | None:
    s = (source or "").strip().lower()
    if s in VALID_SOURCES:
        return s
    return None


def get_transcript(
    db: Session,
    *,
    app_name: str,
    param_name: str,
    lang: str,
) -> PromptTranscript | None:
    return (
        db.query(PromptTranscript)
        .filter_by(app_name=app_name, param_name=param_name, lang=lang)
        .one_or_none()
    )


def is_stt_enabled(
    db: Session,
    *,
    app_name: str,
    param_name: str,
    lang: str,
) -> bool:
    row = get_transcript(db, app_name=app_name, param_name=param_name, lang=lang)
    if row is None:
        return True
    return bool(getattr(row, "stt_enabled", True))


def upsert_transcript(
    db: Session,
    *,
    app_name: str,
    param_name: str,
    lang: str,
    relpath: str,
    text: str,
    source: str,
    stt_enabled: bool | None = None,
) -> PromptTranscript:
    src = normalize_source(source) or SOURCE_STT
    cleaned = (text or "").strip()
    row = get_transcript(db, app_name=app_name, param_name=param_name, lang=lang)
    if row is None:
        row = PromptTranscript(
            app_name=app_name,
            param_name=param_name,
            lang=lang,
            relpath=(relpath or "").strip(),
            text=cleaned,
            source=src,
            stt_enabled=True if stt_enabled is None else bool(stt_enabled),
        )
        db.add(row)
    else:
        row.relpath = (relpath or "").strip()
        row.text = cleaned
        row.source = src
        if stt_enabled is not None:
            row.stt_enabled = bool(stt_enabled)
        row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def clear_transcript_text(
    db: Session,
    *,
    app_name: str,
    param_name: str,
    lang: str,
    relpath: str = "",
) -> PromptTranscript:
    """Efface le texte après remplacement audio ; conserve stt_enabled."""
    row = get_transcript(db, app_name=app_name, param_name=param_name, lang=lang)
    if row is None:
        row = PromptTranscript(
            app_name=app_name,
            param_name=param_name,
            lang=lang,
            relpath=(relpath or "").strip(),
            text="",
            source=SOURCE_STT,
            stt_enabled=True,
        )
        db.add(row)
    else:
        row.text = ""
        row.source = SOURCE_STT
        if relpath:
            row.relpath = relpath.strip()
        row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def set_stt_enabled(
    db: Session,
    *,
    app_name: str,
    param_name: str,
    lang: str,
    enabled: bool,
    relpath: str = "",
) -> PromptTranscript:
    row = get_transcript(db, app_name=app_name, param_name=param_name, lang=lang)
    if row is None:
        row = PromptTranscript(
            app_name=app_name,
            param_name=param_name,
            lang=lang,
            relpath=(relpath or "").strip(),
            text="",
            source=SOURCE_STT,
            stt_enabled=bool(enabled),
        )
        db.add(row)
    else:
        row.stt_enabled = bool(enabled)
        if relpath:
            row.relpath = relpath.strip()
        row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def delete_transcript(
    db: Session,
    *,
    app_name: str,
    param_name: str,
    lang: str,
) -> None:
    row = get_transcript(db, app_name=app_name, param_name=param_name, lang=lang)
    if row is not None:
        db.delete(row)
        db.commit()


def transcripts_for_app(db: Session, app_name: str) -> dict[str, dict[str, dict]]:
    """{ param_name: { lang: { text, source, stt_enabled } } }."""
    out: dict[str, dict[str, dict]] = {}
    rows = db.query(PromptTranscript).filter_by(app_name=app_name).all()
    for row in rows:
        by_lang = out.setdefault(row.param_name, {})
        by_lang[row.lang] = {
            "text": row.text or "",
            "source": row.source,
            "stt_enabled": bool(getattr(row, "stt_enabled", True)),
        }
    return out
