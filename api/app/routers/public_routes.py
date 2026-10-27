import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.agent_debug_log import agent_debug_log
from app.config import get_settings
from app.db import SessionLocal
from app.piper_tts import (
    TtsError,
    check_ip_allowed,
    check_rate_limit,
    client_ip_from_request,
    parse_cidrs,
)
from app.portal_tts_settings import resolve_public_engine
from app.secure_settings import get_effective_settings
from app.tts_engine import synthesize_ulaw


def _tts_settings():
    """Settings + overrides portal_secrets (clé ElevenLabs, etc.)."""
    db = SessionLocal()
    try:
        return get_effective_settings(db)
    finally:
        db.close()

router = APIRouter(prefix="/api/public", tags=["public"])


def _field_str(value: object | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


async def parse_tts_input(
    request: Request,
) -> tuple[str, str | None, str | None, str | None]:
    """JSON (tests) ou application/x-www-form-urlencoded (Create URL Document UCCX).

    Retourne ``(text, lang, voice, engine)``.
    """
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, "Corps JSON invalide") from exc
        if not isinstance(data, dict):
            raise HTTPException(400, "Corps JSON invalide")
        text = _field_str(data.get("text"))
        lang = _field_str(data.get("lang"))
        voice = _field_str(data.get("voice"))
        engine = _field_str(data.get("engine"))
    elif (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
        or not content_type
    ):
        form = await request.form()
        text = _field_str(form.get("text"))
        lang = _field_str(form.get("lang"))
        voice = _field_str(form.get("voice"))
        engine = _field_str(form.get("engine"))
    else:
        raise HTTPException(
            415,
            "Content-Type attendu : application/x-www-form-urlencoded ou application/json",
        )

    if not text:
        raise HTTPException(400, "Texte vide")
    return text, lang, voice, engine


@router.get("/apps/{app_name}/config.xml")
def get_public_xml(app_name: str):
    """Endpoint consommé par UCCX (Create URL Document). Sans auth.

    Sécuriser éventuellement via firewall (IP UCCX uniquement).
    """
    settings = get_settings()
    # Empêche path traversal
    if "/" in app_name or "\\" in app_name or ".." in app_name:
        raise HTTPException(400, "Nom d'application invalide")
    path = Path(settings.xml_dir) / app_name / "config.xml"
    if not path.is_file():
        raise HTTPException(404, f"config.xml introuvable pour {app_name}")
    return FileResponse(
        path,
        media_type="application/xml",
        filename="config.xml",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/tts")
async def post_tts(request: Request):
    """Synthèse TTS → WAV µ-law 8 kHz mono (corps binaire pour UCCX Document).

    UCCX Create URL Document (POST) : form ``text``, ``lang`` ;
    optionnels ``voice``, ``engine`` (piper|kokoro|edge|elevenlabs).
    Tests : JSON ``{"text","lang","voice","engine"}``.

    Moteur : param ``engine`` > réglage portail > ``TTS_PUBLIC_ENGINE``.
    Kokoro sans voix pour la langue (ex. ``de_DE``) bascule sur Piper.
    Accès restreint au subnet UCCX (TTS_ALLOWED_CIDRS).

    Note UCCX : Kokoro CPU ~5–15 s pour un texte long — augmenter le timeout
    Create URL Document / Cache Document (recommandé ≥ 20 s), ou utiliser ``piper``.
    """
    settings = _tts_settings()
    content_type = (request.headers.get("content-type") or "").lower()
    peer = request.client.host if request.client else "unknown"
    try:
        text, lang, voice, engine = await parse_tts_input(request)
    except HTTPException as exc:
        # #region agent log
        agent_debug_log(
            "public_routes.py:post_tts",
            "parse_tts_input failed",
            {
                "peer": peer,
                "content_type": content_type,
                "status": exc.status_code,
                "detail": str(exc.detail),
            },
            hypothesis_id="C",
        )
        # #endregion
        raise

    client_ip = client_ip_from_request(
        peer,
        request.headers.get("x-forwarded-for"),
        trusted_proxy_cidrs=parse_cidrs(settings.tts_trusted_proxy_cidrs),
    )
    # #region agent log
    agent_debug_log(
        "public_routes.py:post_tts",
        "tts parsed",
        {
            "peer": peer,
            "client_ip": client_ip,
            "xff": request.headers.get("x-forwarded-for"),
            "content_type": content_type,
            "text_len": len(text),
            "lang": lang,
            "engine": engine,
            "tts_enabled": settings.tts_enabled,
        },
        hypothesis_id="B",
    )
    # #endregion
    used_engine = "piper"
    t0 = time.monotonic()
    try:
        check_ip_allowed(client_ip, settings)
        check_rate_limit(client_ip, settings)
        resolved = resolve_public_engine(engine, settings)
        # CPU-bound (Kokoro/Piper) hors event loop — évite de bloquer l’API
        wav, used_engine = await asyncio.to_thread(
            synthesize_ulaw,
            text,
            engine=resolved,
            voice=voice,
            lang=lang,
            settings=settings,
        )
    except TtsError as exc:
        # #region agent log
        agent_debug_log(
            "public_routes.py:post_tts",
            "tts error",
            {"client_ip": client_ip, "status": exc.status_code, "detail": str(exc)},
            hypothesis_id="B",
        )
        # #endregion
        raise HTTPException(exc.status_code, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        # #region agent log
        agent_debug_log(
            "public_routes.py:post_tts",
            "tts unexpected",
            {"client_ip": client_ip, "error": type(exc).__name__},
            hypothesis_id="D",
        )
        # #endregion
        raise HTTPException(502, f"TTS échoué : {exc}") from exc

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    # #region agent log
    agent_debug_log(
        "public_routes.py:post_tts",
        "tts ok",
        {
            "client_ip": client_ip,
            "wav_bytes": len(wav),
            "engine": used_engine,
            "elapsed_ms": elapsed_ms,
        },
        hypothesis_id="D",
    )
    # #endregion

    return Response(
        content=wav,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="tts.wav"',
            "X-TTS-Client-IP": client_ip,
            "X-TTS-Engine": used_engine,
            "X-TTS-Elapsed-Ms": str(elapsed_ms),
        },
    )


@router.get("/health")
def health():
    return Response(content="ok", media_type="text/plain")
