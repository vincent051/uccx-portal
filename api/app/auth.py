from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Application, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
settings = get_settings()
ALGORITHM = "HS256"
# Header JWT séparé : Authorization est réservé au Basic Auth Traefik
JWT_HEADER = "X-UCCX-Token"
IMPERSONATE_EXPIRE_MINUTES = 60


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    subject: str,
    *,
    impersonator: str | None = None,
    expire_minutes: int | None = None,
) -> str:
    minutes = (
        expire_minutes
        if expire_minutes is not None
        else settings.access_token_expire_minutes
    )
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    payload: dict = {"sub": subject, "exp": expire}
    if impersonator:
        payload["imp"] = impersonator
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """Auth locale (break-glass admin / comptes à mot de passe). Pas SSO."""
    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        return None
    if not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def _extract_jwt(request: Request, bearer: str | None) -> str | None:
    # Préférer X-UCCX-Token pour cohabiter avec Basic Auth Traefik
    header_token = request.headers.get(JWT_HEADER)
    if header_token:
        return header_token.strip()
    if bearer:
        return bearer
    return None


def get_current_user(
    request: Request,
    bearer: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Non authentifié",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = _extract_jwt(request, bearer)
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise credentials_exception
        impersonator_name = payload.get("imp")
    except JWTError as exc:
        raise credentials_exception from exc

    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        raise credentials_exception

    request.state.impersonator = None
    if impersonator_name:
        imp_user = get_user_by_username(db, str(impersonator_name))
        if (
            not imp_user
            or not imp_user.is_active
            or imp_user.role != "admin"
        ):
            raise credentials_exception
        request.state.impersonator = imp_user.username

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Réservé aux administrateurs")
    return user


def require_skills_access(user: User = Depends(get_current_user)) -> User:
    from app.user_access import can_use_skills

    if not can_use_skills(user):
        raise HTTPException(status_code=403, detail="Accès SkillsManager refusé")
    return user


def require_apps_access(user: User = Depends(get_current_user)) -> User:
    from app.user_access import can_use_apps

    if not can_use_apps(user):
        raise HTTPException(status_code=403, detail="Accès applications refusé")
    return user


def user_can_access_app(user: User, application: Application) -> bool:
    if user.role == "admin":
        return True
    from app.user_access import can_use_apps

    if not can_use_apps(user):
        return False
    return any(a.id == application.id for a in user.applications)


def get_accessible_app(db: Session, user: User, app_name: str) -> Application:
    application = db.query(Application).filter(Application.name == app_name).first()
    if not application:
        raise HTTPException(status_code=404, detail=f"Application introuvable: {app_name}")
    if not user_can_access_app(user, application):
        raise HTTPException(status_code=403, detail="Accès refusé à cette application")
    return application
