import logging
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audio_convert import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    AudioConvertError,
    concat_pcm_wavs,
    convert_to_ulaw_wav,
    prepend_silence_ulaw,
    prepend_silence_wav,
    wav_filename_for_upload,
)
from app.auth import get_accessible_app, get_current_user
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import AppConfig, Application, User
from app.piper_tts import TtsError
from app.secure_settings import get_effective_settings
from app.prompt_transcripts import (
    SOURCE_STT,
    SOURCE_TTS,
    clear_transcript_text,
    delete_transcript,
    get_transcript,
    is_stt_enabled,
    normalize_source,
    set_stt_enabled,
    transcripts_for_app,
    upsert_transcript,
)
from app.schemas import AppConfigOut, AppConfigPayload, ApplicationOut
from app.seed import load_payload
from app.tts_engine import (
    list_engines,
    list_voices,
    synthesize_pcm,
)
from app.whisper_stt import SttError, transcribe_wav_bytes
from app.uccx_apps import (
    CALENDAR_TYPE,
    PLAYABLE_TYPE,
    application_to_dict,
    get_application,
    update_application_values,
)
from app.uccx_calendar import calendars_for_params, update_calendar
from app.uccx_sync import (
    canonical_prompt_relpath,
    copy_prompt_to_lang,
    create_prompt_language,
    delete_prompt,
    download_prompt,
    list_prompt_files,
    list_prompt_files_all_langs,
    list_prompt_languages,
    parse_playable_path,
    prompt_exists_in_any_lang,
    prompt_file_exists,
    relpath_to_playable,
    resolve_upload_targets,
    sync_document,
    upload_prompt_to_lang,
)
from app.xml_gen import write_config_xml

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/apps", tags=["apps"])


class UccxAppUpdate(BaseModel):
    description: str | None = None
    maxsession: int | None = None
    enabled: bool | None = None
    params: dict[str, str] = Field(default_factory=dict)


class CalendarUpdate(BaseModel):
    businessDays: dict[str, list[dict]] = Field(default_factory=dict)
    holidays: list[dict[str, str]] = Field(default_factory=list)
    specialDays: list[dict] = Field(default_factory=list)
    calendarType: str | None = None


class PromptAssignBody(BaseModel):
    param_name: str
    lang: str  # langue cible (case UI)
    relpath: str  # chemin source (sans langue)
    source_lang: str | None = None  # langue du fichier choisi (défaut = lang)
    transcript: str = ""
    transcript_source: str = ""


class PromptTtsBody(BaseModel):
    text: str
    lang: str
    voice: str | None = None
    engine: str = "piper"


class PromptTtsSegmentBody(BaseModel):
    text: str
    lang: str
    voice: str | None = None
    engine: str = "piper"


class PromptTtsConcatBody(BaseModel):
    segments: list[PromptTtsSegmentBody] = Field(..., min_length=1)
    gap_ms: int = Field(400, ge=0, le=5000)


class PromptLanguageBody(BaseModel):
    lang: str


class PromptSttSettingsBody(BaseModel):
    param_name: str
    lang: str
    stt_enabled: bool
    relpath: str = ""


def _ensure_config(db: Session, application: Application) -> AppConfig:
    if application.config:
        return application.config
    from app.xml_gen import default_payload

    payload = default_payload()
    cfg = AppConfig(application_id=application.id, payload_json=payload.model_dump_json())
    db.add(cfg)
    db.commit()
    db.refresh(application)
    return application.config


def _to_out(app: Application, cfg: AppConfig, settings) -> AppConfigOut:
    return AppConfigOut(
        application=app.name,
        config=load_payload(cfg),
        updated_at=cfg.updated_at,
        last_sync_at=cfg.last_sync_at,
        last_sync_status=cfg.last_sync_status or "never",
        last_sync_error=cfg.last_sync_error or "",
        public_xml_url=f"{settings.public_base_url.rstrip('/')}/api/public/apps/{app.name}/config.xml",
    )


@router.get("", response_model=list[ApplicationOut])
def list_my_apps(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # Import auto depuis UCCX à chaque chargement de liste (page load)
    try:
        from app.uccx_apps import sync_applications_from_uccx

        sync_applications_from_uccx(db, get_settings())
    except Exception as exc:  # noqa: BLE001 — ne bloque pas l'UI si UCCX HS
        import logging

        logging.getLogger(__name__).warning("Sync apps UCCX ignorée: %s", exc)

    from app.user_access import can_use_apps

    if not can_use_apps(user):
        return []
    if user.role == "admin":
        apps = db.query(Application).order_by(Application.name).all()
    else:
        apps = sorted(user.applications, key=lambda a: a.name)
    return apps


@router.get("/{app_name}/uccx")
def get_uccx_app(
    app_name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    try:
        detail = get_application(app_name, settings)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Lecture UCCX échouée: {exc}") from exc

    payload = application_to_dict(detail)
    payload["calendars"] = calendars_for_params(detail.params, settings)
    return payload


@router.put("/{app_name}/uccx")
def put_uccx_app(
    app_name: str,
    body: UccxAppUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    try:
        # Ne pas écraser les IDs calendrier via params génériques
        detail = get_application(app_name, settings)
        cal_names = {p.name for p in detail.params if p.type == CALENDAR_TYPE}
        safe_params = {k: v for k, v in body.params.items() if k not in cal_names}
        updated = update_application_values(
            app_name,
            description=body.description,
            maxsession=body.maxsession,
            enabled=body.enabled,
            param_values=safe_params or None,
            settings=settings,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Mise à jour UCCX échouée: {exc}") from exc

    payload = application_to_dict(updated)
    payload["calendars"] = calendars_for_params(updated.params, settings)
    return payload


@router.put("/{app_name}/calendar/{cal_id}")
def put_uccx_calendar(
    app_name: str,
    cal_id: str,
    body: CalendarUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    try:
        detail = get_application(app_name, settings)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Lecture UCCX échouée: {exc}") from exc

    referenced = {
        p.value.strip()
        for p in detail.params
        if p.type == CALENDAR_TYPE and p.value.strip()
    }
    if str(cal_id) not in referenced:
        raise HTTPException(400, f"Calendrier {cal_id} non référencé par l'application {app_name}")

    try:
        cal = update_calendar(
            cal_id,
            business_days=body.businessDays,
            holidays=body.holidays,
            special_days=body.specialDays,
            calendar_type=body.calendarType,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Mise à jour calendrier échouée: {exc}") from exc

    cal.pop("raw_xml", None)
    return cal


@router.get("/{app_name}/config", response_model=AppConfigOut)
def get_config(
    app_name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    app = get_accessible_app(db, user, app_name)
    cfg = _ensure_config(db, app)
    return _to_out(app, cfg, settings)


@router.put("/{app_name}/config", response_model=AppConfigOut)
def put_config(
    app_name: str,
    body: AppConfigPayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    settings = get_settings()
    app = get_accessible_app(db, user, app_name)
    cfg = _ensure_config(db, app)
    cfg.payload_json = body.model_dump_json()
    cfg.updated_at = datetime.utcnow()

    xml_path = write_config_xml(settings.xml_dir, app.name, body)
    ok, msg = sync_document(settings, app.name, xml_path)
    cfg.last_sync_at = datetime.utcnow()
    cfg.last_sync_status = "ok" if ok else "error"
    cfg.last_sync_error = "" if ok else msg
    db.commit()
    db.refresh(cfg)
    return _to_out(app, cfg, settings)


def _get_playable_or_400(app_name: str, param_name: str, settings):
    try:
        detail = get_application(app_name, settings)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Lecture UCCX échouée: {exc}") from exc
    playable = next(
        (p for p in detail.params if p.name == param_name and p.type == PLAYABLE_TYPE),
        None,
    )
    if not playable:
        raise HTTPException(400, f"Paramètre Playable introuvable: {param_name}")
    return detail, playable


# Même format que create_prompt_language (xx / xx_YY) + « default » pour preview TTS.
_TTS_LANG_RE = re.compile(r"^(default|[A-Za-z]{2,3}(_[A-Za-z0-9]+)?)$", re.I)


def _validate_lang(settings, lang: str) -> str:
    """Validation stricte : langue doit exister dans le repository prompts UCCX."""
    lang = (lang or "").strip()
    langs = list_prompt_languages(settings)
    if not langs:
        logger.error(
            "list_prompt_languages vide — UCCX inaccessible, credentials ou réseau"
        )
        raise HTTPException(502, "Impossible de lister les langues prompts UCCX")
    if lang not in langs:
        raise HTTPException(400, f"Langue inconnue: {lang}. Disponibles: {', '.join(langs)}")
    return lang


def _validate_lang_for_tts(lang: str) -> str:
    """Soft-validation pour preview TTS : format lang uniquement, sans liste UCCX live."""
    lang = (lang or "").strip().replace("-", "_")
    if not lang:
        raise HTTPException(400, "Langue TTS manquante")
    if lang.lower() == "default":
        return "default"
    if not _TTS_LANG_RE.match(lang):
        raise HTTPException(
            400, f"Format langue invalide: {lang!r} (attendu default, xx ou xx_YY)"
        )
    return lang


@router.get("/{app_name}/prompt-languages")
def get_prompt_languages(
    app_name: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    langs = list_prompt_languages(settings)
    default = "fr_LU" if "fr_LU" in langs else next((x for x in langs if x != "default"), None) or (
        langs[0] if langs else "default"
    )
    return {"languages": langs, "default": default}


@router.post("/{app_name}/prompt-languages")
def post_prompt_language(
    app_name: str,
    body: PromptLanguageBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Crée un dossier langue racine dans le repository prompts UCCX."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    ok, msg = create_prompt_language(settings, body.lang)
    if not ok:
        raise HTTPException(400 if "invalide" in msg.lower() or "format" in msg.lower() else 502, msg)
    langs = list_prompt_languages(settings)
    return {"ok": True, "lang": body.lang.strip().replace("-", "_"), "status": msg, "languages": langs}


@router.get("/{app_name}/prompt-files")
def get_prompt_files(
    app_name: str,
    lang: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sans lang : tous les prompts de toutes les langues (réutilisation cross-langue)."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    if not lang:
        return {"lang": None, "files": list_prompt_files_all_langs(settings)}
    lang = _validate_lang(settings, lang)
    return {"lang": lang, "files": list_prompt_files(settings, lang)}


@router.get("/{app_name}/prompt-status")
def get_prompt_status(
    app_name: str,
    param_name: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Présence du fichier Playable par langue (même relpath partout)."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    langs = list_prompt_languages(settings)
    if not langs:
        raise HTTPException(502, "Impossible de lister les langues prompts UCCX")
    try:
        detail = get_application(app_name, settings)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Lecture UCCX échouée: {exc}") from exc

    params_out: dict = {}
    for p in detail.params:
        if p.type != PLAYABLE_TYPE:
            continue
        if param_name and p.name != param_name:
            continue
        kind, rel = parse_playable_path(p.value)
        _ = kind
        by_lang = {lang: False for lang in langs}
        if rel:
            for lang in langs:
                by_lang[lang] = prompt_file_exists(settings, lang, rel)
        params_out[p.name] = {
            "name": p.name,
            "playable": p.value or "P[]",
            "relpath": rel or "",
            "byLang": by_lang,
        }
    return {
        "languages": langs,
        "params": params_out,
        "transcripts": transcripts_for_app(db, app_name),
    }


@router.get("/{app_name}/prompt-audio")
def get_prompt_audio(
    app_name: str,
    lang: str,
    relpath: str,
    download: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Proxy téléchargement WAV UCCX pour lecture navigateur."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    lang = _validate_lang(settings, lang)
    rel = (relpath or "").strip().lstrip("/").replace("\\", "/")
    if not rel:
        raise HTTPException(400, "relpath vide")
    ok, data, msg = download_prompt(settings, lang, rel)
    if not ok:
        raise HTTPException(404 if "404" in msg else 502, f"Audio indisponible: {msg}")
    fname = rel.split("/")[-1] or "prompt.wav"
    disp = "attachment" if download else "inline"
    return Response(
        content=data,
        media_type="audio/wav",
        headers={"Content-Disposition": f'{disp}; filename="{fname}"'},
    )


@router.get("/{app_name}/prompts/transcript")
def get_prompt_transcript(
    app_name: str,
    param_name: str,
    lang: str,
    relpath: str | None = None,
    force: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Texte prompt (TTS stocké ou Whisper lazy / force)."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    lang = _validate_lang(settings, lang)
    param = (param_name or "").strip()
    if not param:
        raise HTTPException(400, "param_name requis")

    row = get_transcript(db, app_name=app_name, param_name=param, lang=lang)
    stt_on = True if row is None else bool(getattr(row, "stt_enabled", True))

    # TTS stocké : renvoyer sans Whisper (même si force)
    if row and (row.text or "").strip() and row.source == SOURCE_TTS and not force:
        return {
            "param": param,
            "lang": lang,
            "relpath": row.relpath,
            "text": row.text,
            "source": row.source,
            "stt_enabled": stt_on,
        }

    if row and (row.text or "").strip() and not force:
        return {
            "param": param,
            "lang": lang,
            "relpath": row.relpath,
            "text": row.text,
            "source": row.source,
            "stt_enabled": stt_on,
        }

    if not stt_on:
        return {
            "param": param,
            "lang": lang,
            "relpath": (row.relpath if row else "") or (relpath or ""),
            "text": (row.text if row else "") or "",
            "source": row.source if row and (row.text or "").strip() else None,
            "stt_enabled": False,
        }

    rel = (relpath or (row.relpath if row else "") or "").strip().lstrip("/").replace("\\", "/")
    if not rel:
        _, playable = _get_playable_or_400(app_name, param, settings)
        _kind, rel = parse_playable_path(playable.value)
    if not rel:
        raise HTTPException(404, "Aucun fichier prompt à transcrire")
    if not prompt_file_exists(settings, lang, rel):
        raise HTTPException(404, f"Fichier introuvable: /{lang}/{rel}")

    # Libérer la connexion pool AVANT download UCCX + Whisper (peut durer >30s)
    db.close()

    ok, data, msg = download_prompt(settings, lang, rel)
    if not ok:
        raise HTTPException(404 if "404" in msg else 502, f"Audio indisponible: {msg}")

    try:
        text = transcribe_wav_bytes(data, lang_hint=lang, settings=settings)
    except SttError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    db2 = SessionLocal()
    try:
        saved = upsert_transcript(
            db2,
            app_name=app_name,
            param_name=param,
            lang=lang,
            relpath=rel,
            text=text,
            source=SOURCE_STT,
        )
        return {
            "param": param,
            "lang": lang,
            "relpath": saved.relpath,
            "text": saved.text,
            "source": saved.source,
            "stt_enabled": bool(getattr(saved, "stt_enabled", True)),
        }
    finally:
        db2.close()


@router.patch("/{app_name}/prompts/stt-settings")
def patch_prompt_stt_settings(
    app_name: str,
    body: PromptSttSettingsBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Active / désactive la transcription auto pour une case prompt."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    lang = _validate_lang(settings, body.lang)
    param = (body.param_name or "").strip()
    if not param:
        raise HTTPException(400, "param_name requis")
    row = set_stt_enabled(
        db,
        app_name=app_name,
        param_name=param,
        lang=lang,
        enabled=body.stt_enabled,
        relpath=body.relpath or "",
    )
    return {
        "ok": True,
        "param": param,
        "lang": lang,
        "stt_enabled": bool(row.stt_enabled),
        "text": row.text or "",
        "source": row.source if (row.text or "").strip() else None,
    }


@router.get("/{app_name}/tts/voices")
def get_tts_voices(
    app_name: str,
    lang: str | None = None,
    engine: str = "piper",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Voix installées filtrées pour la langue cellule (JWT)."""
    get_accessible_app(db, user, app_name)
    settings = get_effective_settings(db)
    notice = None
    try:
        eng = (engine or "piper").strip().lower()
        if eng == "elevenlabs":
            from app import elevenlabs_tts

            voices, notice = elevenlabs_tts.collect_voices_for_uccx_lang(lang, settings)
        else:
            voices = list_voices(engine, lang, settings)
    except TtsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    default_id = next((v["id"] for v in voices if v.get("default")), None)
    out = {
        "lang": lang,
        "engine": engine,
        "engines": list_engines(settings),
        "voices": voices,
        "default": default_id,
    }
    if notice:
        out["notice"] = notice
    return out


@router.post("/{app_name}/prompts/stt")
async def post_prompt_stt(
    app_name: str,
    lang: str = Form(...),
    param_name: str = Form(""),
    relpath: str = Form(""),
    source_lang: str = Form(""),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Transcription Whisper à la sélection (fichier local ou chemin UCCX), sans upload."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    lang = _validate_lang(settings, lang)
    param = (param_name or "").strip()
    rel = (relpath or "").strip().lstrip("/").replace("\\", "/")
    src_lang = _validate_lang(settings, source_lang) if (source_lang or "").strip() else lang

    if param and not is_stt_enabled(db, app_name=app_name, param_name=param, lang=lang):
        return {
            "ok": False,
            "text": "",
            "source": None,
            "stt_enabled": False,
        }

    audio: bytes | None = None
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                400, f"Fichier trop volumineux (max {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo)"
            )
        try:
            audio = convert_to_ulaw_wav(data, file.filename)
        except AudioConvertError as exc:
            raise HTTPException(400, f"Conversion audio échouée: {exc}") from exc
    elif rel:
        if not prompt_file_exists(settings, src_lang, rel):
            raise HTTPException(404, f"Fichier introuvable: /{src_lang}/{rel}")
        ok, data, msg = download_prompt(settings, src_lang, rel)
        if not ok:
            raise HTTPException(404 if "404" in msg else 502, f"Audio indisponible: {msg}")
        audio = data
    else:
        raise HTTPException(400, "Fichier ou relpath requis")

    db.close()

    try:
        text = transcribe_wav_bytes(audio, lang_hint=lang, settings=settings)
    except SttError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    return {
        "ok": True,
        "text": text,
        "source": SOURCE_STT,
        "stt_enabled": True,
        "lang": lang,
        "relpath": rel,
    }


@router.post("/{app_name}/prompts/tts")
def post_prompt_tts(
    app_name: str,
    body: PromptTtsBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Synthèse TTS → WAV PCM + 0,5 s silence pour préécoute / pending upload (JWT)."""
    get_accessible_app(db, user, app_name)
    settings = get_effective_settings(db)
    lang = _validate_lang_for_tts(body.lang)
    try:
        wav = synthesize_pcm(
            body.text,
            engine=body.engine,
            voice=body.voice,
            lang=lang,
            settings=settings,
        )
        wav = prepend_silence_wav(wav, 1.0)
    except TtsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except AudioConvertError as exc:
        raise HTTPException(400, f"Silence TTS échoué: {exc}") from exc
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"Content-Disposition": 'inline; filename="tts.wav"'},
    )


@router.post("/{app_name}/prompts/tts-concat")
def post_prompt_tts_concat(
    app_name: str,
    body: PromptTtsConcatBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Synthèse multi-segments (langues/voix) → WAV PCM concaténé + silence initial."""
    get_accessible_app(db, user, app_name)
    settings = get_effective_settings(db)
    if not body.segments:
        raise HTTPException(400, "Au moins un segment requis")
    wavs: list[bytes] = []
    try:
        for i, seg in enumerate(body.segments):
            text = (seg.text or "").strip()
            if not text:
                raise HTTPException(400, f"Segment {i + 1} : texte vide")
            lang = _validate_lang_for_tts(seg.lang)
            wavs.append(
                synthesize_pcm(
                    text,
                    engine=seg.engine,
                    voice=seg.voice,
                    lang=lang,
                    settings=settings,
                )
            )
        wav = concat_pcm_wavs(wavs, gap_ms=body.gap_ms)
        wav = prepend_silence_wav(wav, 1.0)
    except HTTPException:
        raise
    except TtsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except AudioConvertError as exc:
        raise HTTPException(400, f"Concat TTS échouée: {exc}") from exc
    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"Content-Disposition": 'inline; filename="tts-concat.wav"'},
    )


@router.post("/{app_name}/prompts/assign")
def assign_existing_prompt(
    app_name: str,
    body: PromptAssignBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Copie un prompt (toute langue) vers /{lang}/{app}/{nom} (+ seed default) ; MAJ Playable."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    target_lang = _validate_lang(settings, body.lang)
    source_lang = _validate_lang(settings, body.source_lang or body.lang)
    source_rel = (body.relpath or "").strip().lstrip("/").replace("\\", "/")
    if not source_rel:
        raise HTTPException(400, "relpath vide")
    if not prompt_file_exists(settings, source_lang, source_rel):
        raise HTTPException(404, f"Fichier introuvable: /{source_lang}/{source_rel}")

    _, playable_param = _get_playable_or_400(app_name, body.param_name, settings)
    canon_rel = canonical_prompt_relpath(app_name, playable_param.value, source_rel)

    ok_dl, data, msg_dl = download_prompt(settings, source_lang, source_rel)
    if not ok_dl:
        raise HTTPException(502, f"Téléchargement source échoué: {msg_dl}")

    try:
        data = prepend_silence_ulaw(data, 1.0)
    except AudioConvertError as exc:
        raise HTTPException(400, f"Silence initial échoué: {exc}") from exc

    ok_up, msg_up = upload_prompt_to_lang(
        settings, target_lang, canon_rel, data, overwrite=True
    )
    if not ok_up:
        raise HTTPException(502, f"Upload canonique ({target_lang}) échoué: {msg_up}")

    seed_msg = ""
    if target_lang != "default" and not prompt_file_exists(settings, "default", canon_rel):
        ok_seed, msg_seed = upload_prompt_to_lang(
            settings, "default", canon_rel, data, overwrite=True
        )
        if not ok_seed:
            raise HTTPException(502, f"Seed default échoué: {msg_seed}")
        seed_msg = f"seed default: {msg_seed}"

    # Playable UCCX AppAdmin : chemin nu (test/file.wav), sans P[]/SP[]
    playable_value = relpath_to_playable(canon_rel)
    try:
        updated = update_application_values(
            app_name,
            param_values={body.param_name: playable_value},
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Assignation échouée: {exc}") from exc

    got = next((p for p in updated.params if p.name == body.param_name), None)
    if not got or (got.value or "").strip() != playable_value:
        raise HTTPException(
            502,
            f"Fichier copié mais Playable non mis à jour "
            f"(attendu {playable_value!r}, obtenu {got.value if got else None!r})",
        )

    tx_text = (body.transcript or "").strip()
    tx_src = normalize_source(body.transcript_source)
    transcript_out = None
    if tx_text and tx_src in (SOURCE_TTS, SOURCE_STT):
        saved = upsert_transcript(
            db,
            app_name=app_name,
            param_name=body.param_name,
            lang=target_lang,
            relpath=canon_rel,
            text=tx_text,
            source=tx_src,
        )
        transcript_out = {
            "text": saved.text,
            "source": saved.source,
            "stt_enabled": bool(getattr(saved, "stt_enabled", True)),
        }
        stt_on = bool(getattr(saved, "stt_enabled", True))
    else:
        clear_transcript_text(
            db,
            app_name=app_name,
            param_name=body.param_name,
            lang=target_lang,
            relpath=canon_rel,
        )
        stt_on = is_stt_enabled(
            db, app_name=app_name, param_name=body.param_name, lang=target_lang
        )

    payload = application_to_dict(updated)
    for sp in payload.get("scriptParams") or []:
        if sp.get("name") == body.param_name:
            sp["value"] = playable_value
            break
    payload["calendars"] = calendars_for_params(updated.params, settings)
    return {
        "ok": True,
        "param": body.param_name,
        "prompt": playable_value,
        "relpath": canon_rel,
        "source": source_rel,
        "source_lang": source_lang,
        "lang": target_lang,
        "seed": seed_msg,
        "application": payload,
        "transcript": transcript_out,
        "stt_enabled": stt_on,
        "needs_stt": False,
    }


def _form_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


@router.post("/{app_name}/prompts")
async def post_prompt(
    app_name: str,
    param_name: str = Form(..., description="Nom du scriptParam Playable"),
    file: UploadFile = File(...),
    lang: str = Form(...),
    overwrite: str = Form("false"),
    write_default: str = Form("false"),
    transcript: str = Form(""),
    source: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    lang = _validate_lang(settings, lang)
    overwrite_b = _form_bool(overwrite)
    write_default_b = _form_bool(write_default)
    transcript_text = (transcript or "").strip()
    transcript_source = normalize_source(source)

    if not file.filename:
        raise HTTPException(400, "Nom de fichier manquant")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Extension non supportée ({ext or 'aucune'}). "
            f"Accepté: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"Fichier trop volumineux (max {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo)")

    _, playable_param = _get_playable_or_400(app_name, param_name, settings)

    try:
        wav = convert_to_ulaw_wav(data, file.filename)
        wav = prepend_silence_ulaw(wav, 1.0)
    except AudioConvertError as exc:
        raise HTTPException(400, f"Conversion audio échouée: {exc}") from exc

    # Même nom + dossier app : réutiliser Playable, sinon {app}/{param_file}.wav
    _kind, existing_rel = parse_playable_path(playable_param.value)
    if existing_rel:
        relpath = existing_rel
    else:
        wav_name = wav_filename_for_upload(file.filename, param_name)
        relpath = canonical_prompt_relpath(app_name, "", wav_name)
    # Seed default si absent (write_default force aussi l'écrasement default)
    targets = resolve_upload_targets(lang, write_default_b, settings, relpath)

    conflicts = [t for t in targets if prompt_file_exists(settings, t, relpath)]
    if conflicts and not overwrite_b:
        raise HTTPException(
            status_code=409,
            detail={
                "exists": True,
                "languages": conflicts,
                "relpath": relpath,
                "message": f"Fichier déjà présent pour: {', '.join(conflicts)}. Confirmer l'écrasement.",
                "write_default": write_default_b,
            },
        )

    uploaded: list[str] = []
    for target in targets:
        # overwrite si demandé, ou si on seed/écrase une cible déjà listée en conflit
        ow = overwrite_b or (target in conflicts)
        ok, msg = upload_prompt_to_lang(
            settings, target, relpath, wav, overwrite=ow
        )
        if not ok:
            raise HTTPException(502, f"Upload UCCX ({target}) échoué: {msg}")
        uploaded.append(target)

    playable_value = relpath_to_playable(relpath)
    try:
        updated = update_application_values(
            app_name,
            param_values={param_name: playable_value},
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Prompt uploadé mais mise à jour param échouée: {exc}") from exc

    # Vérifier que UCCX a bien persisté la valeur Playable
    got = next((p for p in updated.params if p.name == param_name), None)
    if not got or (got.value or "").strip() != playable_value:
        raise HTTPException(
            502,
            f"Upload OK mais Playable non mis à jour "
            f"(attendu {playable_value!r}, obtenu {got.value if got else None!r})",
        )

    payload = application_to_dict(updated)
    # Forcer la valeur affichée (évite décalage éventuel decode)
    for sp in payload.get("scriptParams") or []:
        if sp.get("name") == param_name:
            sp["value"] = playable_value
            break
    payload["calendars"] = calendars_for_params(updated.params, settings)

    transcript_out = None
    stt_on = is_stt_enabled(db, app_name=app_name, param_name=param_name, lang=lang)

    if transcript_text and transcript_source in (SOURCE_TTS, SOURCE_STT):
        saved = upsert_transcript(
            db,
            app_name=app_name,
            param_name=param_name,
            lang=lang,
            relpath=relpath,
            text=transcript_text,
            source=transcript_source,
        )
        transcript_out = {
            "text": saved.text,
            "source": saved.source,
            "stt_enabled": bool(getattr(saved, "stt_enabled", True)),
        }
        stt_on = bool(getattr(saved, "stt_enabled", True))
    else:
        # Pas de texte connu : invalider l'ancien (STT déjà fait à la sélection en principe)
        cleared = clear_transcript_text(
            db,
            app_name=app_name,
            param_name=param_name,
            lang=lang,
            relpath=relpath,
        )
        stt_on = bool(getattr(cleared, "stt_enabled", True))

    return {
        "ok": True,
        "prompt": playable_value,
        "relpath": relpath,
        "languages": uploaded,
        "converted": "WAV µ-law 8 kHz mono + 1s silence",
        "param": param_name,
        "application": payload,
        "transcript": transcript_out,
        "stt_enabled": stt_on,
        "needs_stt": False,
    }


@router.delete("/{app_name}/prompts")
def delete_app_prompt(
    app_name: str,
    param_name: str,
    lang: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Supprime le fichier pour une langue ; reset P[] si plus aucune langue."""
    get_accessible_app(db, user, app_name)
    settings = get_settings()
    lang = _validate_lang(settings, lang)
    _, playable = _get_playable_or_400(app_name, param_name, settings)

    kind, rel = parse_playable_path(playable.value)
    file_msg = ""
    reset_param = False

    if kind == "SP" and rel:
        ok, file_msg = delete_prompt(settings, rel, lang=lang)
        if not ok:
            file_msg = f"warning delete fichier: {file_msg}"
        remaining = prompt_exists_in_any_lang(settings, rel)
        reset_param = len(remaining) == 0
        if not reset_param:
            file_msg = f"{file_msg}; reste: {', '.join(remaining)}"
    elif kind == "P" and rel:
        # P[file] à la racine langue — delete le wav pour cette langue seulement
        ok, file_msg = delete_prompt(settings, rel, lang=lang)
        if not ok:
            file_msg = f"warning delete fichier: {file_msg}"
        remaining = prompt_exists_in_any_lang(settings, rel)
        reset_param = len(remaining) == 0
        if not reset_param:
            file_msg = f"{file_msg}; reste: {', '.join(remaining)}"
    else:
        reset_param = True
        file_msg = "aucune référence fichier"

    new_value = "P[]" if reset_param else playable.value
    try:
        updated = update_application_values(
            app_name,
            param_values={param_name: new_value},
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Mise à jour param échouée: {exc}") from exc

    delete_transcript(db, app_name=app_name, param_name=param_name, lang=lang)

    payload = application_to_dict(updated)
    payload["calendars"] = calendars_for_params(updated.params, settings)
    return {
        "ok": True,
        "param": param_name,
        "value": new_value,
        "lang": lang,
        "file": file_msg,
        "application": payload,
    }
