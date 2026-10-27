import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import (
    IMPERSONATE_EXPIRE_MINUTES,
    create_access_token,
    hash_password,
    require_admin,
)
from app.config import get_settings
from app.db import get_db
from app.models import Application, User
from app.schemas import (
    AdminSettingsUpdate,
    ApplicationOut,
    Token,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.secure_settings import list_settings_for_admin, save_settings
from app.uccx_skills import UccxSkillsError, list_resources, list_teams
from app.user_access import set_skill_teams, user_to_out

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return db.query(Application).order_by(Application.name).all()


@router.post("/applications/sync-uccx")
def sync_uccx_applications(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    from app.uccx_apps import sync_applications_from_uccx

    try:
        return sync_applications_from_uccx(db, get_settings())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Sync UCCX échouée: {exc}") from exc


def _out(db: Session, u: User) -> UserOut:
    return UserOut(**user_to_out(db, u))


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = db.query(User).order_by(User.username).all()
    return [_out(db, u) for u in users]


@router.post("/users", response_model=UserOut)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, "Utilisateur déjà existant")
    pwd = (body.password or "").strip()
    user = User(
        username=body.username.strip(),
        password_hash=hash_password(pwd) if pwd else None,
        role=body.role,
        can_access_apps=body.can_access_apps,
        can_access_skills=body.can_access_skills,
        is_active=body.is_active,
        auth_source="local" if pwd else "saml",
    )
    if body.application_ids:
        apps = (
            db.query(Application)
            .filter(Application.id.in_(body.application_ids))
            .all()
        )
        user.applications = apps
    db.add(user)
    db.flush()
    set_skill_teams(db, user, body.skill_teams)
    db.commit()
    db.refresh(user)
    return _out(db, user)


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.can_access_apps is not None:
        user.can_access_apps = body.can_access_apps
    if body.can_access_skills is not None:
        user.can_access_skills = body.can_access_skills
    if body.password is not None and body.password.strip():
        user.password_hash = hash_password(body.password.strip())
        user.auth_source = "local"
    if body.application_ids is not None:
        apps = (
            db.query(Application)
            .filter(Application.id.in_(body.application_ids))
            .all()
        )
        user.applications = apps
    if body.skill_teams is not None:
        set_skill_teams(db, user, body.skill_teams)
    db.commit()
    db.refresh(user)
    return _out(db, user)


@router.post("/users/{user_id}/impersonate", response_model=Token)
def impersonate_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """RunAs : JWT avec sub=cible et imp=admin (TTL court)."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "Utilisateur introuvable")
    if not target.is_active:
        raise HTTPException(400, "Compte cible désactivé")
    if target.id == admin.id:
        raise HTTPException(400, "Impossible de RunAs sur soi-même")
    if target.role == "admin":
        raise HTTPException(403, "RunAs vers un autre admin interdit")
    logger.info(
        "RunAs: admin=%s → user=%s (id=%s)",
        admin.username,
        target.username,
        target.id,
    )
    token = create_access_token(
        target.username,
        impersonator=admin.username,
        expire_minutes=IMPERSONATE_EXPIRE_MINUTES,
    )
    return Token(access_token=token)


@router.put("/users/{user_id}/applications", response_model=UserOut)
def set_user_applications(
    user_id: int,
    application_ids: list[int],
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    apps = db.query(Application).filter(Application.id.in_(application_ids)).all()
    user.applications = apps
    db.commit()
    db.refresh(user)
    return _out(db, user)


class SkillTeamsBody(BaseModel):
    teams: list[str]


@router.put("/users/{user_id}/skill-teams", response_model=UserOut)
def set_user_skill_teams_route(
    user_id: int,
    body: SkillTeamsBody,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Utilisateur introuvable")
    set_skill_teams(db, user, body.teams)
    db.commit()
    db.refresh(user)
    return _out(db, user)


@router.post("/users/sync-uccx")
def sync_users_from_uccx(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Upsert users depuis GET /adminapi/resource (sans mot de passe)."""
    try:
        resources = list_resources(get_settings())
    except UccxSkillsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    created = 0
    updated = 0
    for r in resources:
        uid = (r.get("id") or "").strip()
        if not uid:
            continue
        user = db.query(User).filter(User.username == uid).first()
        display = r.get("name") or uid
        team = r.get("team") or ""
        roles_label = r.get("rolesLabel") or " / ".join(r.get("roles") or [])
        if user:
            if user.role == "admin":
                continue  # ne pas écraser l’admin local
            user.display_name = display
            user.uccx_team = team
            user.uccx_roles = roles_label
            if user.auth_source == "local" and not user.password_hash:
                user.auth_source = "uccx"
            updated += 1
        else:
            user = User(
                username=uid,
                password_hash=None,
                role="operator",
                is_active=True,
                can_access_apps=False,
                can_access_skills=False,
                display_name=display,
                uccx_team=team,
                uccx_roles=roles_label,
                auth_source="uccx",
            )
            db.add(user)
            created += 1
    db.commit()
    return {"created": created, "updated": updated, "total": created + updated}


@router.get("/teams")
def admin_list_teams(_: User = Depends(require_admin)):
    try:
        return {"teams": list_teams(get_settings())}
    except UccxSkillsError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/settings")
def get_admin_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return {"settings": list_settings_for_admin(db)}


@router.put("/settings")
def put_admin_settings(
    body: AdminSettingsUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    rows = save_settings(db, body.values or {})
    return {"settings": rows}
