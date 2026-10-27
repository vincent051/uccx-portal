"""Helpers droits apps / skills / teams."""

from __future__ import annotations

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import User, user_skill_teams


def get_skill_teams(db: Session, user: User) -> list[str]:
    rows = db.execute(
        select(user_skill_teams.c.team_name).where(
            user_skill_teams.c.user_id == user.id
        )
    ).fetchall()
    return sorted({r[0] for r in rows if r[0]})


def set_skill_teams(db: Session, user: User, teams: list[str]) -> None:
    db.execute(delete(user_skill_teams).where(user_skill_teams.c.user_id == user.id))
    seen: set[str] = set()
    for t in teams:
        name = (t or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        db.execute(
            insert(user_skill_teams).values(user_id=user.id, team_name=name)
        )


def user_to_out(db: Session, user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": bool(user.is_active),
        "can_access_apps": bool(getattr(user, "can_access_apps", True)),
        "can_access_skills": bool(getattr(user, "can_access_skills", False)),
        "display_name": getattr(user, "display_name", "") or "",
        "uccx_team": getattr(user, "uccx_team", "") or "",
        "uccx_roles": getattr(user, "uccx_roles", "") or "",
        "auth_source": getattr(user, "auth_source", "local") or "local",
        "has_local_password": bool(getattr(user, "password_hash", None)),
        "application_ids": [a.id for a in user.applications],
        "skill_teams": get_skill_teams(db, user),
    }


def can_use_apps(user: User) -> bool:
    if user.role == "admin":
        return True
    return bool(getattr(user, "can_access_apps", True))


def can_use_skills(user: User) -> bool:
    if user.role == "admin":
        return True
    return bool(getattr(user, "can_access_skills", False))


def allowed_skill_teams(db: Session, user: User) -> set[str] | None:
    """None = toutes les teams (admin)."""
    if user.role == "admin":
        return None
    return set(get_skill_teams(db, user))
