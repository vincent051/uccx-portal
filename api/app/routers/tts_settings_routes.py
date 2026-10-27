"""Réglages TTS API publique (JWT)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import User
from app.piper_tts import TtsError
from app.portal_tts_settings import public_settings_payload, write_stored_engine
from app.secure_settings import get_effective_settings
from app import elevenlabs_tts

router = APIRouter(prefix="/api/tts", tags=["tts"])


class PublicTtsSettingsBody(BaseModel):
    engine: str = Field(..., min_length=1, max_length=32)


@router.get("/public-settings")
def get_public_tts_settings(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return public_settings_payload(get_effective_settings(db))


@router.put("/public-settings")
def put_public_tts_settings(
    body: PublicTtsSettingsBody,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    settings = get_effective_settings(db)
    try:
        eng = write_stored_engine(body.engine, settings)
    except TtsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    payload = public_settings_payload(settings)
    payload["engine"] = eng
    return payload


@router.get("/elevenlabs/usage")
def get_elevenlabs_usage(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Quota caractères ElevenLabs (free / plan) pour l’UI portail."""
    settings = get_effective_settings(db)
    try:
        return elevenlabs_tts.usage_payload(settings)
    except TtsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
