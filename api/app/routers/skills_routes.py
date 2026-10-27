"""API SkillsManager — matrice agents, templates, planification."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_skills_access
from app.config import get_settings
from app.db import get_db
from app.models import SkillSchedule, SkillTemplate, SkillTemplateSkill, User
from app.schemas import (
    AgentSkillsUpdate,
    ApplyTemplateIn,
    ApplyTemplateOut,
    ScheduleCreate,
    ScheduleOut,
    TemplateCreate,
    TemplateFromAgentIn,
    TemplateOut,
    TemplateSkillIn,
    TemplateUpdate,
)
from app.skill_scheduler import (
    in_recurring_window,
    now_luxembourg,
    parse_hhmm,
    parse_weekdays,
    template_skills_payload,
)
from app.uccx_skills import (
    UccxSkillsError,
    apply_template_skills,
    get_resource,
    list_resources,
    list_skills,
    list_teams,
    update_resource_skills,
)
from app.user_access import allowed_skill_teams

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _settings():
    return get_settings()


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalise un datetime (éventuellement aware) en naive UTC pour Postgres/comparaisons."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _schedule_out(s: SkillSchedule) -> ScheduleOut:
    active = bool(s.done)
    return ScheduleOut(
        id=s.id,
        agent_id=s.agent_id,
        kind=(s.kind or "once"),
        start_at=s.start_at,
        end_at=s.end_at,
        weekdays=s.weekdays,
        time_start=s.time_start,
        time_end=s.time_end,
        old_template_id=s.old_template_id,
        new_template_id=s.new_template_id,
        old_template_name=s.old_template.name if s.old_template else "",
        new_template_name=s.new_template.name if s.new_template else "",
        active=active,
        done=active,
    )


def _assert_agents_in_scope(db: Session, user: User, agent_ids: list[str]) -> None:
    teams = allowed_skill_teams(db, user)
    if teams is None:
        return
    agents = list_resources(_settings(), teams_filter=teams)
    allowed = {a["id"] for a in agents}
    for aid in agent_ids:
        if aid not in allowed:
            raise HTTPException(403, f"Agent hors périmètre teams: {aid}")


@router.get("/catalog")
def skills_catalog(
    _: User = Depends(require_skills_access),
):
    try:
        return {"skills": list_skills(_settings()), "teams": list_teams(_settings())}
    except UccxSkillsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/agents")
def skills_agents(
    db: Session = Depends(get_db),
    user: User = Depends(require_skills_access),
):
    teams = allowed_skill_teams(db, user)
    try:
        agents = list_resources(_settings(), teams_filter=teams)
    except UccxSkillsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"agents": agents, "teams_filter": None if teams is None else sorted(teams)}


@router.put("/agents")
def save_agent_skills(
    body: AgentSkillsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_skills_access),
):
    teams = allowed_skill_teams(db, user)
    try:
        current = None
        if teams is not None:
            # Vérifier que l’agent est dans une team autorisée
            agents = list_resources(_settings(), teams_filter=teams)
            if not any(a["id"] == body.agent_id for a in agents):
                raise HTTPException(403, "Agent hors périmètre teams")
        skills = [s.model_dump() for s in body.skills]
        updated = update_resource_skills(
            body.agent_id,
            skills,
            team_name=body.team,
            settings=_settings(),
        )
        # Annuler schedules en cours pour cet agent (comme PHP clean_agent_entry)
        db.query(SkillSchedule).filter(SkillSchedule.agent_id == body.agent_id).delete()
        db.commit()
        return updated
    except UccxSkillsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _template_out(t: SkillTemplate) -> TemplateOut:
    return TemplateOut(
        id=t.id,
        name=t.name,
        description=t.description or "",
        skills=[
            TemplateSkillIn(
                skill_id=s.skill_id,
                skill_name=s.skill_name,
                skill_level=s.skill_level,
            )
            for s in t.skills
        ],
    )


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(
    db: Session = Depends(get_db),
    _: User = Depends(require_skills_access),
):
    rows = db.query(SkillTemplate).order_by(SkillTemplate.name).all()
    return [_template_out(t) for t in rows]


@router.post("/templates", response_model=TemplateOut)
def create_template(
    body: TemplateCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_skills_access),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Nom template requis")
    if db.query(SkillTemplate).filter(SkillTemplate.name == name).first():
        raise HTTPException(409, "Template déjà existant")
    t = SkillTemplate(name=name, description=body.description or "")
    db.add(t)
    db.flush()
    for s in body.skills:
        db.add(
            SkillTemplateSkill(
                template_id=t.id,
                skill_id=s.skill_id,
                skill_name=s.skill_name,
                skill_level=s.skill_level,
            )
        )
    db.commit()
    db.refresh(t)
    return _template_out(t)


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: int,
    body: TemplateUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_skills_access),
):
    t = db.query(SkillTemplate).filter(SkillTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template introuvable")
    if body.name is not None:
        t.name = body.name.strip()
    if body.description is not None:
        t.description = body.description
    if body.skills is not None:
        db.query(SkillTemplateSkill).filter(
            SkillTemplateSkill.template_id == t.id
        ).delete()
        for s in body.skills:
            db.add(
                SkillTemplateSkill(
                    template_id=t.id,
                    skill_id=s.skill_id,
                    skill_name=s.skill_name,
                    skill_level=s.skill_level,
                )
            )
    db.commit()
    db.refresh(t)
    return _template_out(t)


@router.delete("/templates/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_skills_access),
):
    t = db.query(SkillTemplate).filter(SkillTemplate.id == template_id).first()
    if not t:
        raise HTTPException(404, "Template introuvable")
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.post("/templates/from-agent", response_model=TemplateOut)
def create_template_from_agent(
    body: TemplateFromAgentIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_skills_access),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Nom template requis")
    if db.query(SkillTemplate).filter(SkillTemplate.name == name).first():
        raise HTTPException(409, "Template déjà existant")
    _assert_agents_in_scope(db, user, [body.agent_id])
    try:
        resource = get_resource(body.agent_id, settings=_settings())
    except UccxSkillsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    skills_in: list[TemplateSkillIn] = []
    for s in resource.get("skillMap") or []:
        level = int(s.get("level") or 0)
        if level <= 0:
            continue
        sid = str(s.get("id") or "").strip()
        sname = str(s.get("name") or sid).strip()
        if not sid:
            continue
        skills_in.append(
            TemplateSkillIn(
                skill_id=sid,
                skill_name=sname,
                skill_level=max(1, min(10, level)),
            )
        )
    t = SkillTemplate(
        name=name,
        description=body.description or f"Depuis agent {body.agent_id}",
    )
    db.add(t)
    db.flush()
    for s in skills_in:
        db.add(
            SkillTemplateSkill(
                template_id=t.id,
                skill_id=s.skill_id,
                skill_name=s.skill_name,
                skill_level=s.skill_level,
            )
        )
    db.commit()
    db.refresh(t)
    return _template_out(t)


@router.post("/apply", response_model=ApplyTemplateOut)
def apply_template(
    body: ApplyTemplateIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_skills_access),
):
    agent_ids = [a.strip() for a in body.agent_ids if a and str(a).strip()]
    if not agent_ids:
        raise HTTPException(400, "Au moins un agent requis")
    _assert_agents_in_scope(db, user, agent_ids)
    tpl = db.query(SkillTemplate).filter(SkillTemplate.id == body.template_id).first()
    if not tpl:
        raise HTTPException(404, "Template introuvable")
    skills = template_skills_payload(db, tpl.id)
    ok: list[str] = []
    failed: list[dict[str, str]] = []
    for aid in agent_ids:
        try:
            apply_template_skills(aid, skills, settings=_settings())
            db.query(SkillSchedule).filter(SkillSchedule.agent_id == aid).delete()
            ok.append(aid)
        except UccxSkillsError as exc:
            failed.append({"agent_id": aid, "error": str(exc)})
    db.commit()
    return ApplyTemplateOut(ok=ok, failed=failed)


@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(
    db: Session = Depends(get_db),
    _: User = Depends(require_skills_access),
):
    rows = db.query(SkillSchedule).order_by(SkillSchedule.start_at.desc()).all()
    return [_schedule_out(s) for s in rows]


@router.post("/schedules", response_model=list[ScheduleOut])
def create_schedule(
    body: ScheduleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_skills_access),
):
    agent_ids = [a.strip() for a in body.agent_ids if a and str(a).strip()]
    if not agent_ids:
        raise HTTPException(400, "Au moins un agent requis")
    _assert_agents_in_scope(db, user, agent_ids)

    old_t = db.query(SkillTemplate).filter(SkillTemplate.id == body.old_template_id).first()
    new_t = db.query(SkillTemplate).filter(SkillTemplate.id == body.new_template_id).first()
    if not old_t or not new_t:
        raise HTTPException(400, "Templates invalides")
    if old_t.id == new_t.id:
        raise HTTPException(400, "Old et New doivent différer")

    kind = (body.kind or "once").strip().lower()
    if kind not in ("once", "recurring"):
        raise HTTPException(400, "kind invalide (once|recurring)")

    start = _as_naive_utc(body.start_at) or _now()
    end_at = _as_naive_utc(body.end_at)
    weekdays = None
    time_start = None
    time_end = None
    now = _now()
    now_lu = now_luxembourg()

    if kind == "once":
        if end_at is None:
            raise HTTPException(400, "end_at requis pour kind=once")
        if end_at <= start:
            raise HTTPException(400, "end_at doit être après start_at")
    else:
        days = parse_weekdays(body.weekdays)
        if not days:
            raise HTTPException(400, "weekdays requis (ex. 1,2,3,4,5)")
        if parse_hhmm(body.time_start) is None or parse_hhmm(body.time_end) is None:
            raise HTTPException(400, "time_start et time_end requis (HH:MM)")
        weekdays = ",".join(str(d) for d in sorted(days))
        time_start = body.time_start.strip()
        time_end = body.time_end.strip()
        if end_at is not None and end_at <= start:
            raise HTTPException(400, "end_at (validité) doit être après start_at")

    skills_new = [
        {"id": s.skill_id, "name": s.skill_name, "level": s.skill_level}
        for s in new_t.skills
    ]
    created: list[SkillSchedule] = []
    for aid in agent_ids:
        db.query(SkillSchedule).filter(SkillSchedule.agent_id == aid).delete()
        active = False
        if kind == "once":
            if body.apply_now and start <= now < (end_at or now):
                try:
                    apply_template_skills(aid, skills_new, settings=_settings())
                    active = True
                except UccxSkillsError as exc:
                    raise HTTPException(exc.status_code, f"{aid}: {exc}") from exc
        else:
            if in_recurring_window(weekdays, time_start, time_end, now_local=now_lu):
                try:
                    apply_template_skills(aid, skills_new, settings=_settings())
                    active = True
                except UccxSkillsError as exc:
                    raise HTTPException(exc.status_code, f"{aid}: {exc}") from exc
        row = SkillSchedule(
            agent_id=aid,
            kind=kind,
            start_at=start,
            end_at=end_at,
            weekdays=weekdays,
            time_start=time_start,
            time_end=time_end,
            old_template_id=old_t.id,
            new_template_id=new_t.id,
            done=active,
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    return [_schedule_out(r) for r in created]


@router.delete("/schedules/{schedule_id}")
def delete_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_skills_access),
):
    row = db.query(SkillSchedule).filter(SkillSchedule.id == schedule_id).first()
    if not row:
        raise HTTPException(404, "Schedule introuvable")
    db.delete(row)
    db.commit()
    return {"ok": True}
