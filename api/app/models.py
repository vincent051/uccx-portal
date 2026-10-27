from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base

user_applications = Table(
    "user_applications",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("application_id", ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True),
)

user_skill_teams = Table(
    "user_skill_teams",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("team_name", String(128), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    # Nullable : comptes SSO synchro UCCX n’ont pas de mot de passe portail
    password_hash = Column(String(255), nullable=True)
    role = Column(String(32), nullable=False, default="operator")  # admin | operator
    is_active = Column(Boolean, default=True)
    can_access_apps = Column(Boolean, nullable=False, default=True)
    can_access_skills = Column(Boolean, nullable=False, default=False)
    # Métadonnées sync UCCX resource
    display_name = Column(String(255), nullable=False, default="")
    uccx_team = Column(String(128), nullable=False, default="")
    uccx_roles = Column(String(255), nullable=False, default="")  # ex. Agent / Supervisor
    auth_source = Column(String(32), nullable=False, default="local")  # local | saml | uccx
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    applications = relationship(
        "Application", secondary=user_applications, back_populates="users"
    )


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship(
        "User", secondary=user_applications, back_populates="applications"
    )
    config = relationship(
        "AppConfig",
        back_populates="application",
        uselist=False,
        cascade="all, delete-orphan",
    )


class AppConfig(Base):
    __tablename__ = "app_configs"
    __table_args__ = (UniqueConstraint("application_id"),)

    id = Column(Integer, primary_key=True)
    application_id = Column(
        Integer, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    payload_json = Column(Text, nullable=False, default="{}")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(64), default="never")
    last_sync_error = Column(Text, default="")

    application = relationship("Application", back_populates="config")


class PromptTranscript(Base):
    """Texte associé à un prompt Playable (input TTS ou transcription Whisper)."""

    __tablename__ = "prompt_transcripts"
    __table_args__ = (
        UniqueConstraint("app_name", "param_name", "lang", name="uq_prompt_transcript"),
    )

    id = Column(Integer, primary_key=True)
    app_name = Column(String(128), nullable=False, index=True)
    param_name = Column(String(128), nullable=False)
    lang = Column(String(32), nullable=False)
    relpath = Column(String(512), nullable=False, default="")
    text = Column(Text, nullable=False, default="")
    source = Column(String(16), nullable=False, default="stt")
    stt_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SkillTemplate(Base):
    __tablename__ = "skill_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    skills = relationship(
        "SkillTemplateSkill",
        back_populates="template",
        cascade="all, delete-orphan",
    )


class SkillTemplateSkill(Base):
    __tablename__ = "skill_template_skills"
    __table_args__ = (
        UniqueConstraint("template_id", "skill_id", name="uq_template_skill"),
    )

    id = Column(Integer, primary_key=True)
    template_id = Column(
        Integer, ForeignKey("skill_templates.id", ondelete="CASCADE"), nullable=False
    )
    skill_id = Column(String(64), nullable=False)
    skill_name = Column(String(128), nullable=False, default="")
    skill_level = Column(Integer, nullable=False, default=5)

    template = relationship("SkillTemplate", back_populates="skills")


class SkillSchedule(Base):
    """Planification skills : once (fenêtre unique) ou recurring (créneau jour/heure).

    `done` = New actuellement appliqué (alias logique `active`).
    """

    __tablename__ = "skill_schedules"

    id = Column(Integer, primary_key=True)
    agent_id = Column(String(128), nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="once")  # once | recurring
    start_at = Column(DateTime, nullable=False)
    end_at = Column(DateTime, nullable=True)  # once: fin fenêtre ; recurring: fin validité (opt.)
    weekdays = Column(String(32), nullable=True)  # "1,2,3,4,5" lun=1 … dim=7
    time_start = Column(String(5), nullable=True)  # HH:MM local LU
    time_end = Column(String(5), nullable=True)
    old_template_id = Column(
        Integer, ForeignKey("skill_templates.id", ondelete="CASCADE"), nullable=False
    )
    new_template_id = Column(
        Integer, ForeignKey("skill_templates.id", ondelete="CASCADE"), nullable=False
    )
    done = Column(Boolean, nullable=False, default=False)  # active: New appliqué
    created_at = Column(DateTime, default=datetime.utcnow)

    old_template = relationship("SkillTemplate", foreign_keys=[old_template_id])
    new_template = relationship("SkillTemplate", foreign_keys=[new_template_id])


class PortalSecret(Base):
    """Paramètres runtime chiffrés (override .env)."""

    __tablename__ = "portal_secrets"

    key = Column(String(128), primary_key=True)
    value_enc = Column(Text, nullable=False, default="")
    is_secret = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
