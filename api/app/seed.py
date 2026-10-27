import json
import logging

from sqlalchemy.orm import Session

from app.auth import hash_password, verify_password
from app.config import Settings
from app.models import AppConfig, User

logger = logging.getLogger(__name__)


def ensure_admin(db: Session, settings: Settings) -> None:
    """Crée l'admin ou resynchronise son mot de passe depuis .env (chaque démarrage)."""
    admin = db.query(User).filter(User.username == settings.admin_user).first()
    if admin:
        admin.role = "admin"
        admin.can_access_apps = True
        admin.can_access_skills = True
        admin.auth_source = "local"
        if not admin.password_hash or not verify_password(
            settings.admin_password, admin.password_hash
        ):
            admin.password_hash = hash_password(settings.admin_password)
            logger.info(
                "Mot de passe admin « %s » resynchronisé depuis ADMIN_PASSWORD (.env)",
                settings.admin_user,
            )
        db.commit()
        return
    admin = User(
        username=settings.admin_user,
        password_hash=hash_password(settings.admin_password),
        role="admin",
        can_access_apps=True,
        can_access_skills=True,
        auth_source="local",
        display_name="Local Admin",
    )
    db.add(admin)
    db.commit()
    logger.info("Utilisateur admin créé: %s", settings.admin_user)


def load_payload(cfg: AppConfig):
    from app.schemas import AppConfigPayload

    return AppConfigPayload.model_validate(json.loads(cfg.payload_json))
