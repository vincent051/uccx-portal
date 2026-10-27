from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ApplicationOut(BaseModel):
    id: int
    name: str
    description: str

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str
    password: str = ""
    role: Literal["admin", "operator"] = "operator"
    application_ids: list[int] = Field(default_factory=list)
    skill_teams: list[str] = Field(default_factory=list)
    can_access_apps: bool = True
    can_access_skills: bool = False
    is_active: bool = True


class UserUpdate(BaseModel):
    role: Literal["admin", "operator"] | None = None
    is_active: bool | None = None
    can_access_apps: bool | None = None
    can_access_skills: bool | None = None
    password: str | None = None
    application_ids: list[int] | None = None
    skill_teams: list[str] | None = None


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    can_access_apps: bool = True
    can_access_skills: bool = False
    display_name: str = ""
    uccx_team: str = ""
    uccx_roles: str = ""
    auth_source: str = "local"
    has_local_password: bool = False
    application_ids: list[int] = Field(default_factory=list)
    skill_teams: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class MeOut(UserOut):
    saml_enabled: bool = False
    impersonating: bool = False
    impersonator: str | None = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginForm(BaseModel):
    username: str
    password: str


class SkillCompetency(BaseModel):
    id: str = ""
    name: str = ""
    level: int = Field(5, ge=1, le=10)


class AgentSkillsUpdate(BaseModel):
    agent_id: str
    skills: list[SkillCompetency] = Field(default_factory=list)
    team: str | None = None


class TemplateSkillIn(BaseModel):
    skill_id: str
    skill_name: str = ""
    skill_level: int = Field(5, ge=1, le=10)


class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    skills: list[TemplateSkillIn] = Field(default_factory=list)


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    skills: list[TemplateSkillIn] | None = None


class TemplateOut(BaseModel):
    id: int
    name: str
    description: str
    skills: list[TemplateSkillIn] = Field(default_factory=list)


class ApplyTemplateIn(BaseModel):
    agent_ids: list[str] = Field(min_length=1)
    template_id: int


class ApplyTemplateOut(BaseModel):
    ok: list[str] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)


class TemplateFromAgentIn(BaseModel):
    agent_id: str
    name: str
    description: str = ""


class ScheduleCreate(BaseModel):
    """Création multi-agents : once (fenêtre) ou recurring (jours + heures LU)."""

    agent_ids: list[str] = Field(min_length=1)
    old_template_id: int
    new_template_id: int
    kind: str = "once"  # once | recurring
    start_at: datetime | None = None
    end_at: datetime | None = None
    weekdays: str | None = None  # "1,2,3,4,5"
    time_start: str | None = None  # HH:MM
    time_end: str | None = None
    apply_now: bool = True


class ScheduleOut(BaseModel):
    id: int
    agent_id: str
    kind: str = "once"
    start_at: datetime
    end_at: datetime | None = None
    weekdays: str | None = None
    time_start: str | None = None
    time_end: str | None = None
    old_template_id: int
    new_template_id: int
    old_template_name: str = ""
    new_template_name: str = ""
    active: bool = False
    done: bool = False  # alias rétrocompat (= active)


class AdminSettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
