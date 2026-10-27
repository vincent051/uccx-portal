from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

DAY_KEYS = ("Lu", "Ma", "Me", "Je", "Ve", "Sa", "Di")
CLOSED = "Closed"


class WeekHours(BaseModel):
    Lu: str = CLOSED
    Ma: str = CLOSED
    Me: str = CLOSED
    Je: str = CLOSED
    Ve: str = CLOSED
    Sa: str = CLOSED
    Di: str = CLOSED

    @field_validator(*DAY_KEYS)
    @classmethod
    def validate_hours(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            return CLOSED
        if value.lower() == "closed":
            return CLOSED
        parts = value.split("-")
        if len(parts) not in (2, 4):
            raise ValueError(
                "Format attendu: 07:30-18:30, 07:30-12:00-14:00-18:30 ou Closed"
            )
        for part in parts:
            part = part.strip()
            if len(part) != 5 or part[2] != ":":
                raise ValueError(f"Heure invalide: {part} (attendu HH:MM)")
            h, m = part.split(":")
            if not (h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError(f"Heure invalide: {part}")
        return "-".join(p.strip() for p in parts)


class Holiday(BaseModel):
    date: str = Field(..., description="DD/MM/YYYY ou DD/MM/XXXX pour récurrent")
    closed: str = Field("00:00-23:59", description="Plage de fermeture HH:MM-HH:MM")

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        value = value.strip()
        parts = value.split("/")
        if len(parts) != 3:
            raise ValueError("Date attendue: DD/MM/YYYY ou DD/MM/XXXX")
        d, m, y = parts
        if not (d.isdigit() and m.isdigit() and len(d) == 2 and len(m) == 2):
            raise ValueError("Date attendue: DD/MM/YYYY ou DD/MM/XXXX")
        if y.upper() != "XXXX" and not (y.isdigit() and len(y) == 4):
            raise ValueError("Année YYYY ou XXXX")
        return f"{d}/{m}/{y.upper() if y.upper() == 'XXXX' else y}"


class Flags(BaseModel):
    forceClosed: bool = False
    forceOpen: bool = False


class Prompts(BaseModel):
    closed: str = ""
    welcome: str = ""


class AppConfigPayload(BaseModel):
    flags: Flags = Field(default_factory=Flags)
    weekHours: WeekHours = Field(default_factory=WeekHours)
    holidays: list[Holiday] = Field(default_factory=list)
    prompts: Prompts = Field(default_factory=Prompts)


class AppConfigOut(BaseModel):
    application: str
    config: AppConfigPayload
    updated_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_sync_status: str = "never"
    last_sync_error: str = ""
    public_xml_url: str = ""


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
