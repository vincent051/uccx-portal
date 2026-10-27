"""Client UCCX Admin API — skills, teams, resources (SkillsManager)."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class UccxSkillsError(Exception):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _resolve_settings(settings: Settings | None = None) -> Settings:
    if settings is not None:
        return settings
    try:
        from app.db import SessionLocal
        from app.secure_settings import apply_overrides_to_settings

        db = SessionLocal()
        try:
            return apply_overrides_to_settings(db)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return get_settings()


def _client(settings: Settings) -> httpx.Client:
    if not settings.uccx_user or not settings.uccx_password:
        raise UccxSkillsError("UCCX_USER / UCCX_PASSWORD non configurés", status_code=503)
    return httpx.Client(
        verify=settings.uccx_verify_ssl,
        timeout=60.0,
        auth=(settings.uccx_user, settings.uccx_password),
        headers={"Accept": "application/xml", "Content-Type": "application/xml"},
    )


def _base(settings: Settings) -> str:
    host = (settings.uccx_host or "").strip()
    scheme = "https"
    return f"{scheme}://{host}/adminapi"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _find(el: ET.Element, name: str) -> ET.Element | None:
    for child in el.iter():
        if _local(child.tag) == name:
            return child
    return None


def _findall_direct(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def list_skills(settings: Settings | None = None) -> list[dict[str, str]]:
    settings = _resolve_settings(settings)
    with _client(settings) as client:
        r = client.get(f"{_base(settings)}/skill")
    if r.status_code != 200:
        raise UccxSkillsError(f"GET /skill → {r.status_code}", status_code=502)
    root = ET.fromstring(r.text)
    out: list[dict[str, str]] = []
    for skill in root.iter():
        if _local(skill.tag) != "skill":
            continue
        sid = _text(_find(skill, "skillId")) or _text(_find(skill, "id"))
        name = _text(_find(skill, "skillName")) or _text(_find(skill, "name"))
        if sid or name:
            out.append({"id": sid, "name": name})
    out.sort(key=lambda x: x["name"].lower())
    return out


def list_teams(settings: Settings | None = None) -> list[dict[str, str]]:
    settings = _resolve_settings(settings)
    with _client(settings) as client:
        r = client.get(f"{_base(settings)}/team")
    if r.status_code != 200:
        raise UccxSkillsError(f"GET /team → {r.status_code}", status_code=502)
    root = ET.fromstring(r.text)
    out: list[dict[str, str]] = []
    for team in root.iter():
        if _local(team.tag) != "team":
            continue
        tid = _text(_find(team, "teamId")) or _text(_find(team, "id"))
        name = _text(_find(team, "teamname")) or _text(_find(team, "teamName"))
        if name:
            out.append({"id": tid, "name": name})
    out.sort(key=lambda x: x["name"].lower())
    return out


def _parse_skill_map(resource: ET.Element) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    skill_map = _find(resource, "skillMap")
    if skill_map is None:
        return skills
    for comp in skill_map.iter():
        if _local(comp.tag) != "skillCompetency":
            continue
        level = _text(_find(comp, "competencelevel")) or "0"
        pair = _find(comp, "skillNameUriPair")
        name = ""
        sid = ""
        if pair is not None:
            name = pair.attrib.get("name") or ""
            ref = _find(pair, "refURL")
            ref_url = _text(ref)
            m = re.search(r"/skill/([^/\s]+)\s*$", ref_url)
            if m:
                sid = m.group(1)
        try:
            lvl = int(level)
        except ValueError:
            lvl = 0
        skills.append({"id": sid, "name": name, "level": lvl})
    return skills


def _supervisor_team_names(container: ET.Element | None) -> list[str]:
    if container is None:
        return []
    names: list[str] = []
    for el in container.iter():
        tag = _local(el.tag)
        if tag not in ("supervisorOfTeamName", "team"):
            continue
        name = el.attrib.get("name") or _text(el)
        if not name:
            continue
        if name not in names:
            names.append(name)
    return names


def _parse_roles(resource: ET.Element) -> dict[str, Any]:
    """type 1=Agent, 2=Supervisor ; teams supervisées primaires/secondaires."""
    type_code = _text(_find(resource, "type")) or "1"
    primary = _supervisor_team_names(_find(resource, "primarySupervisorOf"))
    secondary = _supervisor_team_names(_find(resource, "secondarySupervisorOf"))
    is_supervisor = type_code == "2" or bool(primary or secondary)
    roles: list[str] = []
    if type_code != "2":
        roles.append("Agent")
    if is_supervisor:
        roles.append("Supervisor")
    if not roles:
        roles = ["Agent"]
    return {
        "typeCode": type_code,
        "roles": roles,
        "rolesLabel": " / ".join(roles),
        "primarySupervisorOf": primary,
        "secondarySupervisorOf": secondary,
    }


def _parse_resource(resource: ET.Element) -> dict[str, Any]:
    user_id = _text(_find(resource, "userID")) or _text(_find(resource, "userId"))
    first = _text(_find(resource, "firstName"))
    last = _text(_find(resource, "lastName"))
    ext = _text(_find(resource, "extension"))
    team_el = _find(resource, "team")
    team_name = ""
    if team_el is not None:
        team_name = team_el.attrib.get("name") or _text(_find(team_el, "teamname")) or ""
    roles_info = _parse_roles(resource)
    return {
        "id": user_id,
        "firstName": first,
        "lastName": last,
        "name": f"{first} {last}".strip() or user_id,
        "extension": ext,
        "team": team_name,
        "skillMap": _parse_skill_map(resource),
        **roles_info,
    }


def list_resources(
    settings: Settings | None = None,
    *,
    teams_filter: set[str] | None = None,
) -> list[dict[str, Any]]:
    settings = _resolve_settings(settings)
    with _client(settings) as client:
        r = client.get(f"{_base(settings)}/resource")
    if r.status_code != 200:
        raise UccxSkillsError(f"GET /resource → {r.status_code}", status_code=502)
    root = ET.fromstring(r.text)
    out: list[dict[str, Any]] = []
    for res in root.iter():
        if _local(res.tag) != "resource":
            continue
        # Skip nested resources inside other elements? top-level list usually has resource children
        parsed = _parse_resource(res)
        if not parsed["id"]:
            continue
        if teams_filter is not None and parsed["team"] not in teams_filter:
            continue
        out.append(parsed)
    # Dédupliquer (iter peut revisiter)
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for row in out:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        uniq.append(row)
    uniq.sort(key=lambda x: x["name"].lower())
    return uniq


def get_resource_xml(agent_id: str, settings: Settings | None = None) -> str:
    settings = _resolve_settings(settings)
    with _client(settings) as client:
        r = client.get(f"{_base(settings)}/resource/{agent_id}")
    if r.status_code != 200:
        raise UccxSkillsError(
            f"GET /resource/{agent_id} → {r.status_code}", status_code=502
        )
    return r.text


def get_resource(agent_id: str, settings: Settings | None = None) -> dict[str, Any]:
    xml = get_resource_xml(agent_id, settings)
    root = ET.fromstring(xml)
    return _parse_resource(root)


def _build_skill_map_xml(
    skills: list[dict[str, Any]],
    settings: Settings,
) -> str:
    host = settings.uccx_host
    parts = ["<skillMap>"]
    for s in skills:
        sid = str(s.get("id") or "").strip()
        name = str(s.get("name") or "").strip()
        level = int(s.get("level") or 5)
        level = max(1, min(10, level))
        if not sid and not name:
            continue
        parts.append(
            "<skillCompetency>"
            f"<competencelevel>{level}</competencelevel>"
            f'<skillNameUriPair name="{_xml_attr(name)}">'
            f"<refURL>https://{host}/adminapi/skill/{_xml_text(sid)}</refURL>"
            "</skillNameUriPair>"
            "</skillCompetency>"
        )
    parts.append("</skillMap>")
    return "".join(parts)


def _xml_attr(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _xml_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def update_resource_skills(
    agent_id: str,
    skills: list[dict[str, Any]],
    *,
    team_name: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """GET resource → remplace skillMap (et optionnellement team) → PUT."""
    settings = _resolve_settings(settings)
    body = get_resource_xml(agent_id, settings)
    new_map = _build_skill_map_xml(skills, settings)

    if "<skillMap/>" in body or "<skillMap />" in body:
        body = body.replace("<skillMap/>", new_map).replace("<skillMap />", new_map)
    elif "<skillMap>" in body and "</skillMap>" in body:
        start = body.index("<skillMap>")
        end = body.index("</skillMap>") + len("</skillMap>")
        body = body[:start] + new_map + body[end:]
    else:
        # Insérer avant </resource>
        body = body.replace("</resource>", new_map + "</resource>")

    if team_name is not None and team_name.strip():
        # Remplace name= sur l’élément team si présent
        body = re.sub(
            r'(<team\b[^>]*\bname=")([^"]*)(")',
            rf"\1{_xml_attr(team_name.strip())}\3",
            body,
            count=1,
        )

    with _client(settings) as client:
        r = client.put(f"{_base(settings)}/resource/{agent_id}", content=body.encode("utf-8"))
    if r.status_code not in (200, 204):
        raise UccxSkillsError(
            f"PUT /resource/{agent_id} → {r.status_code}: {(r.text or '')[:300]}",
            status_code=502,
        )
    return get_resource(agent_id, settings)


def apply_template_skills(
    agent_id: str,
    template_skills: list[dict[str, Any]],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Applique un profil (liste skill id/name/level) à un agent."""
    return update_resource_skills(agent_id, template_skills, settings=settings)
