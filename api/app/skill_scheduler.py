"""Job planification changements de skills (once + créneaux récurrents LU)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import SkillSchedule, SkillTemplateSkill
from app.uccx_skills import UccxSkillsError, apply_template_skills

logger = logging.getLogger(__name__)

TZ_LU = ZoneInfo("Europe/Luxembourg")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_luxembourg() -> datetime:
    return datetime.now(TZ_LU)


def parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return time(h, mi)


def parse_weekdays(csv: str | None) -> set[int]:
    if not csv:
        return set()
    out: set[int] = set()
    for part in csv.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            d = int(part)
        except ValueError:
            continue
        if 1 <= d <= 7:
            out.add(d)
    return out


def in_recurring_window(
    weekdays: str | None,
    time_start: str | None,
    time_end: str | None,
    now_local: datetime | None = None,
) -> bool:
    """True si now (Europe/Luxembourg) est dans le créneau récurrent."""
    now_local = now_local or now_luxembourg()
    if now_local.tzinfo is not None:
        now_local = now_local.astimezone(TZ_LU)
    days = parse_weekdays(weekdays)
    t0 = parse_hhmm(time_start)
    t1 = parse_hhmm(time_end)
    if not days or t0 is None or t1 is None:
        return False
    wd = now_local.isoweekday()  # lun=1 … dim=7
    cur = time(now_local.hour, now_local.minute, now_local.second)

    if t0 <= t1:
        # Même jour : besoin du bon weekday et heure dans [t0, t1)
        return wd in days and t0 <= cur < t1

    # Créneau nuit (ex. 22:00 → 06:00) : après t0 le jour J ∈ days, ou avant t1 le lendemain
    if cur >= t0 and wd in days:
        return True
    prev = 7 if wd == 1 else wd - 1
    if cur < t1 and prev in days:
        return True
    return False


def template_skills_payload(db: Session, template_id: int) -> list[dict]:
    rows = (
        db.query(SkillTemplateSkill)
        .filter(SkillTemplateSkill.template_id == template_id)
        .all()
    )
    return [
        {"id": r.skill_id, "name": r.skill_name, "level": r.skill_level} for r in rows
    ]


def _apply(db: Session, agent_id: str, template_id: int, settings: Settings) -> None:
    skills = template_skills_payload(db, template_id)
    apply_template_skills(agent_id, skills, settings=settings)


def process_schedules(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    now = _now_utc_naive()
    now_lu = now_luxembourg()
    applied = 0
    restored = 0
    errors = 0
    db = SessionLocal()
    try:
        tasks = db.query(SkillSchedule).all()
        for task in tasks:
            try:
                kind = (task.kind or "once").strip().lower()
                if kind == "recurring":
                    a, r = _process_recurring(db, task, now, now_lu, settings)
                    applied += a
                    restored += r
                else:
                    a, r = _process_once(db, task, now, settings)
                    applied += a
                    restored += r
            except UccxSkillsError as exc:
                errors += 1
                logger.warning("Skill schedule error agent=%s: %s", task.agent_id, exc)
                db.rollback()
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning("Skill schedule unexpected: %s", exc)
                db.rollback()
    finally:
        db.close()
    return {
        "applied": applied,
        "restored": restored,
        "errors": errors,
        "checked_at": now.isoformat(),
    }


def _process_once(
    db: Session, task: SkillSchedule, now: datetime, settings: Settings
) -> tuple[int, int]:
    start_at = _as_naive_utc(task.start_at) or now
    end_at = _as_naive_utc(task.end_at)
    if end_at is None:
        return 0, 0
    applied = restored = 0
    if start_at <= now < end_at and not task.done:
        _apply(db, task.agent_id, task.new_template_id, settings)
        task.done = True
        db.commit()
        applied = 1
        logger.info(
            "Skill schedule applied agent=%s template=%s",
            task.agent_id,
            task.new_template_id,
        )
    elif end_at <= now:
        _apply(db, task.agent_id, task.old_template_id, settings)
        db.delete(task)
        db.commit()
        restored = 1
        logger.info(
            "Skill schedule restored agent=%s template=%s",
            task.agent_id,
            task.old_template_id,
        )
    return applied, restored


def _process_recurring(
    db: Session,
    task: SkillSchedule,
    now: datetime,
    now_lu: datetime,
    settings: Settings,
) -> tuple[int, int]:
    end_at = _as_naive_utc(task.end_at)
    applied = restored = 0
    # Fin de validité optionnelle
    if end_at is not None and end_at <= now:
        if task.done:
            _apply(db, task.agent_id, task.old_template_id, settings)
            restored = 1
            logger.info(
                "Skill recurring expired restore agent=%s template=%s",
                task.agent_id,
                task.old_template_id,
            )
        db.delete(task)
        db.commit()
        return applied, restored

    inside = in_recurring_window(
        task.weekdays, task.time_start, task.time_end, now_local=now_lu
    )
    if inside and not task.done:
        _apply(db, task.agent_id, task.new_template_id, settings)
        task.done = True
        db.commit()
        applied = 1
        logger.info(
            "Skill recurring applied agent=%s template=%s",
            task.agent_id,
            task.new_template_id,
        )
    elif not inside and task.done:
        _apply(db, task.agent_id, task.old_template_id, settings)
        task.done = False
        db.commit()
        restored = 1
        logger.info(
            "Skill recurring restored agent=%s template=%s",
            task.agent_id,
            task.old_template_id,
        )
    return applied, restored
