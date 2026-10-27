import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent_debug_log import agent_debug_log
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import (  # noqa: F401 — register metadata
    PortalSecret,
    PromptTranscript,
    SkillSchedule,
    SkillTemplate,
    SkillTemplateSkill,
)
from app.routers import (
    admin_routes,
    apps_routes,
    auth_routes,
    public_routes,
    skills_routes,
    tts_settings_routes,
)
from app.seed import ensure_admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
Path(settings.data_dir).mkdir(parents=True, exist_ok=True)


async def _holiday_roll_loop() -> None:
    from app.uccx_calendar import roll_all_referenced_calendars

    interval = max(0, int(settings.holiday_roll_interval_sec or 0))
    if interval <= 0:
        logger.info("Holiday roll job désactivé (HOLIDAY_ROLL_INTERVAL_SEC=0)")
        return
    await asyncio.sleep(min(30, interval))
    while True:
        try:
            result = await asyncio.to_thread(roll_all_referenced_calendars, settings)
            logger.info(
                "Holiday roll job: scanned=%s rolled=%s errors=%s",
                result.get("scanned"),
                result.get("rolled"),
                result.get("errors"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Holiday roll job échec: %s", exc)
        await asyncio.sleep(interval)


async def _skill_scheduler_loop() -> None:
    from app.skill_scheduler import process_schedules

    while True:
        # Relit settings à chaque tour (overrides admin possibles)
        s = get_settings()
        interval = max(0, int(getattr(s, "skill_scheduler_interval_sec", 60) or 0))
        if interval <= 0:
            await asyncio.sleep(60)
            continue
        try:
            result = await asyncio.to_thread(process_schedules, s)
            if result.get("applied") or result.get("restored") or result.get("errors"):
                logger.info("Skill scheduler: %s", result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skill scheduler échec: %s", exc)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    from app.prompt_transcripts import ensure_prompt_transcript_schema
    from app.schema_migrate import ensure_portal_schema

    ensure_prompt_transcript_schema(engine)
    ensure_portal_schema(engine)
    db = SessionLocal()
    try:
        ensure_admin(db, settings)
    finally:
        db.close()
    logger.info("UCCX Portal prêt — data dir=%s", settings.data_dir)
    try:
        from app.tts_engine import warmup_engines

        await asyncio.to_thread(warmup_engines, settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Warmup TTS ignoré: %s", exc)
    roll_task = asyncio.create_task(_holiday_roll_loop(), name="holiday-roll")
    skill_task = asyncio.create_task(_skill_scheduler_loop(), name="skill-scheduler")
    try:
        yield
    finally:
        roll_task.cancel()
        skill_task.cancel()
        for t in (roll_task, skill_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="UCCX Portal", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def agent_debug_public_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/public"):
        peer = request.client.host if request.client else "unknown"
        agent_debug_log(
            "main.py:middleware:in",
            "public request",
            {
                "method": request.method,
                "path": path,
                "peer": peer,
                "content_type": request.headers.get("content-type", ""),
            },
            hypothesis_id="A",
        )
    response = await call_next(request)
    if path.startswith("/api/public"):
        agent_debug_log(
            "main.py:middleware:out",
            "public response",
            {"path": path, "status": response.status_code},
            hypothesis_id="A",
        )
    return response


app.include_router(auth_routes.router)
app.include_router(admin_routes.router)
app.include_router(apps_routes.router)
app.include_router(skills_routes.router)
app.include_router(tts_settings_routes.router)
app.include_router(public_routes.router)

static_dir = Path(__file__).parent / "static"
app.mount(
    "/static",
    StaticFiles(directory=static_dir, follow_symlink=True),
    name="static",
)


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")
