"""Liste / détail / mise à jour des applications Cisco Script via UCCX Configuration API."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import unescape
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Application

logger = logging.getLogger(__name__)

CALENDAR_TYPE = "com.cisco.cccalendar.CCCalendar"
PLAYABLE_TYPE = "com.cisco.prompt.Playable"
STRING_TYPE = "java.lang.String"

# SCRIPT[Ihub/foo.aef] | SCRIPT[user/Ihub/foo.aef]
_SCRIPT_REF_RE = re.compile(
    r"SCRIPT\[\s*(?:user/)?([^\]\s]+)\s*\]",
    re.IGNORECASE,
)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,63}$")


@dataclass
class UccxApp:
    name: str
    description: str = ""
    enabled: bool | None = None
    script: str = ""
    app_type: str = ""


@dataclass
class ScriptParam:
    name: str
    value: str
    type: str
    is_default: bool = False


@dataclass
class TriggerInfo:
    directory_number: str = ""
    device_name: str = ""
    display: str = ""
    trigger_enabled: bool | None = None
    max_sessions: str = ""
    locale: str = ""
    description: str = ""


@dataclass
class ApplicationDetail:
    name: str
    description: str = ""
    enabled: bool = True
    maxsession: int = 0
    script: str = ""
    app_type: str = ""
    params: list[ScriptParam] = field(default_factory=list)
    triggers: list[TriggerInfo] = field(default_factory=list)
    raw_xml: str = ""


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _client(settings: Settings) -> httpx.Client:
    if not settings.uccx_user or not settings.uccx_password:
        raise RuntimeError("UCCX_USER / UCCX_PASSWORD non configurés")
    return httpx.Client(
        verify=settings.uccx_verify_ssl,
        timeout=30.0,
        auth=(settings.uccx_user, settings.uccx_password),
        headers={"Accept": "application/xml"},
    )


def _base(settings: Settings) -> str:
    return f"https://{settings.uccx_host}/adminapi"


def decode_param_value(raw: str, type_name: str) -> str:
    """Valeur affichable (String UCCX stockée avec guillemets)."""
    value = unescape((raw or "").strip())
    if type_name == STRING_TYPE and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if type_name == PLAYABLE_TYPE:
        # AppAdmin : chemin nu (déplie d'éventuels P[…]/SP[…])
        from app.uccx_sync import relpath_to_playable

        return relpath_to_playable(value)
    return value


def encode_param_value(display: str, type_name: str, previous_raw: str = "") -> str:
    """Valeur à écrire dans le XML UCCX."""
    value = (display or "").strip()
    if type_name == STRING_TYPE:
        # Conserver le style guillemets des String UCCX
        return f'"{value}"'
    if type_name == PLAYABLE_TYPE:
        # AppAdmin : chemin nu (test/file.wav), jamais P[P[…]]
        from app.uccx_sync import relpath_to_playable

        return relpath_to_playable(value)
    if type_name == CALENDAR_TYPE:
        return value
    # Integer / Boolean / autres : tel quel
    return value if value != "" else previous_raw


def list_uccx_applications(settings: Settings | None = None) -> list[UccxApp]:
    settings = settings or get_settings()
    url = f"{_base(settings)}/application"
    with _client(settings) as client:
        resp = client.get(url)
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    apps: list[UccxApp] = []
    nodes = [n for n in root.iter() if _local(n.tag) == "application"]
    if not nodes and _local(root.tag) == "application":
        nodes = [root]

    for node in nodes:
        fields = {_local(c.tag): c for c in list(node)}
        name = _text(fields.get("applicationName"))
        if not name:
            continue
        script_el = fields.get("ScriptApplication")
        script = ""
        if script_el is not None:
            for c in script_el:
                if _local(c.tag) == "script":
                    script = _text(c)
                    break
        enabled_raw = _text(fields.get("enabled")).lower()
        enabled = None
        if enabled_raw in ("true", "false"):
            enabled = enabled_raw == "true"
        apps.append(
            UccxApp(
                name=name,
                description=_text(fields.get("description")),
                enabled=enabled,
                script=script,
                app_type=_text(fields.get("type")),
            )
        )

    by_name = {a.name: a for a in apps}
    return sorted(by_name.values(), key=lambda a: a.name.lower())


def _parse_script_params(script_app: ET.Element | None) -> list[ScriptParam]:
    if script_app is None:
        return []
    params: list[ScriptParam] = []
    for child in script_app:
        if _local(child.tag) != "scriptParams":
            continue
        fields = {_local(c.tag): c for c in list(child)}
        name = _text(fields.get("name"))
        if not name:
            continue
        is_default = _text(fields.get("isDefaultValue")).lower() == "true"
        params.append(
            ScriptParam(
                name=name,
                value=_text(fields.get("value")),
                type=_text(fields.get("type")),
                is_default=is_default,
            )
        )
    return params


def script_repo_relpath(script_ref: str) -> str | None:
    """Extrait le chemin dépôt depuis SCRIPT[Ihub/foo.aef]."""
    m = _SCRIPT_REF_RE.search(script_ref or "")
    if not m:
        return None
    return m.group(1).strip().lstrip("/").replace("\\", "/")


def _extract_java_serialized_strings(data: bytes) -> set[str]:
    """Identifiants UTF-8 issus de la sérialisation Java (.aef)."""
    out: set[str] = set()
    i = 0
    n = len(data)
    while i < n - 3:
        if data[i] == 0x74:  # TC_STRING
            ln = int.from_bytes(data[i + 1 : i + 3], "big")
            if 1 <= ln <= 80 and i + 3 + ln <= n:
                try:
                    s = data[i + 3 : i + 3 + ln].decode("utf-8")
                except UnicodeDecodeError:
                    i += 1
                    continue
                if _IDENT_RE.fullmatch(s):
                    out.add(s)
                i += 3 + ln
                continue
        i += 1
    return out


def download_script_aef(script_ref: str, settings: Settings | None = None) -> bytes | None:
    """Télécharge le binaire .aef depuis le dépôt UCCX (source de vérité des variables)."""
    settings = settings or get_settings()
    rel = script_repo_relpath(script_ref)
    if not rel:
        return None
    # UCCX exige souvent le préfixe default/ (voir Cisco DevNet / communauté)
    candidates = [
        f"/script/download/default/{rel}",
        f"/script/download//default/{rel}",
        f"/script/download/{rel}",
    ]
    with _client(settings) as client:
        client.timeout = 60.0
        for path in candidates:
            try:
                resp = client.get(
                    f"{_base(settings)}{path}",
                    headers={"Accept": "application/octet-stream, application/xml, */*"},
                )
            except httpx.HTTPError as exc:
                logger.debug("download aef %s: %s", path, exc)
                continue
            if resp.status_code != 200:
                continue
            body = resp.content or b""
            # Écarter les pages d'erreur XML/HTML
            if len(body) < 64:
                continue
            if body.lstrip().startswith(b"<?xml") or body.lstrip().startswith(b"<"):
                continue
            if body.lstrip().startswith(b"<!--") or b"<html" in body[:200].lower():
                continue
            logger.info("AEF téléchargé (%s octets) via %s", len(body), path)
            return body
    logger.warning("Impossible de télécharger le script AEF pour %s", script_ref)
    return None


def script_variable_names(script_ref: str, settings: Settings | None = None) -> set[str] | None:
    """Noms de variables présents dans le .aef courant, ou None si indisponible."""
    raw = download_script_aef(script_ref, settings)
    if not raw:
        return None
    names = _extract_java_serialized_strings(raw)
    # Complément : occurrences littérales d'identifiants (certains noms mal découpés)
    for m in re.finditer(rb"[A-Za-z_][A-Za-z0-9_]{1,63}", raw):
        try:
            s = m.group(0).decode("ascii")
        except UnicodeDecodeError:
            continue
        if _IDENT_RE.fullmatch(s):
            names.add(s)
    return names


def filter_params_to_script(
    params: list[ScriptParam],
    script_ref: str,
    settings: Settings | None = None,
) -> list[ScriptParam]:
    """Retire les scriptParams orphelins (ex. GSM_1) absents du .aef actuel.

    UCCX conserve souvent d'anciens paramètres sur l'application après un
    changement de script ; le dépôt .aef est la source de vérité.
    """
    names = script_variable_names(script_ref, settings)
    if not names:
        return params
    kept = [p for p in params if p.name in names]
    dropped = [p.name for p in params if p.name not in names]
    if dropped:
        logger.info(
            "Paramètres hors script filtrés (%s): %s",
            script_ref,
            ", ".join(dropped),
        )
    return kept


def _strip_orphan_script_params(
    script_app: ET.Element,
    active_names: set[str],
) -> list[str]:
    """Supprime du XML les <scriptParams> absents du script ; retourne les noms retirés."""
    removed: list[str] = []
    for sp in list(script_app):
        if _local(sp.tag) != "scriptParams":
            continue
        fields = {_local(c.tag): c for c in list(sp)}
        pname = _text(fields.get("name"))
        if pname and pname not in active_names:
            script_app.remove(sp)
            removed.append(pname)
    return removed


def get_application(name: str, settings: Settings | None = None) -> ApplicationDetail:
    settings = settings or get_settings()
    url = f"{_base(settings)}/application/{name}"
    with _client(settings) as client:
        resp = client.get(url, params={"allScriptParams": "true"})
        if resp.status_code == 404:
            raise LookupError(f"Application UCCX introuvable: {name}")
        resp.raise_for_status()
        raw_xml = resp.text

    root = ET.fromstring(raw_xml)
    fields = {_local(c.tag): c for c in list(root)}
    script_el = fields.get("ScriptApplication")
    script = ""
    if script_el is not None:
        for c in script_el:
            if _local(c.tag) == "script":
                script = _text(c)
                break

    enabled_raw = _text(fields.get("enabled")).lower()
    try:
        maxsession = int(_text(fields.get("maxsession")) or "0")
    except ValueError:
        maxsession = 0

    params = _parse_script_params(script_el)
    params = filter_params_to_script(params, script, settings)

    return ApplicationDetail(
        name=_text(fields.get("applicationName")) or name,
        description=_text(fields.get("description")),
        enabled=enabled_raw != "false",
        maxsession=maxsession,
        script=script,
        app_type=_text(fields.get("type")),
        params=params,
        triggers=list_triggers_for_app(name, settings),
        raw_xml=raw_xml,
    )


def list_triggers_for_app(app_name: str, settings: Settings | None = None) -> list[TriggerInfo]:
    settings = settings or get_settings()
    url = f"{_base(settings)}/trigger"
    with _client(settings) as client:
        resp = client.get(url)
        resp.raise_for_status()

    root = ET.fromstring(resp.text)
    out: list[TriggerInfo] = []
    for node in root.iter():
        if _local(node.tag) != "trigger":
            continue
        fields = {_local(c.tag): c for c in list(node)}
        app_el = fields.get("application")
        linked = ""
        if app_el is not None:
            linked = (app_el.attrib.get("name") or "").strip()
        if linked != app_name:
            continue
        enabled_raw = _text(fields.get("triggerEnabled")).lower()
        enabled = None
        if enabled_raw in ("true", "false"):
            enabled = enabled_raw == "true"
        out.append(
            TriggerInfo(
                directory_number=_text(fields.get("directoryNumber")),
                device_name=_text(fields.get("deviceName")),
                display=_text(fields.get("display")),
                trigger_enabled=enabled,
                max_sessions=_text(fields.get("maxNumOfSessions")),
                locale=_text(fields.get("locale")),
                description=_text(fields.get("description")),
            )
        )
    return out


def _set_child_text(parent: ET.Element, tag: str, text: str) -> None:
    for child in parent:
        if _local(child.tag) == tag:
            child.text = text
            return
    el = ET.SubElement(parent, tag)
    el.text = text


def update_application_values(
    name: str,
    *,
    description: str | None = None,
    maxsession: int | None = None,
    enabled: bool | None = None,
    param_values: dict[str, str] | None = None,
    settings: Settings | None = None,
) -> ApplicationDetail:
    """GET full app → patch valeurs → PUT. Ne change pas noms/types des params."""
    settings = settings or get_settings()
    detail = get_application(name, settings)
    root = ET.fromstring(detail.raw_xml)

    # Retirer <self> (souvent rejeté au PUT)
    for child in list(root):
        if _local(child.tag) == "self":
            root.remove(child)

    if description is not None:
        _set_child_text(root, "description", description)
    if maxsession is not None:
        _set_child_text(root, "maxsession", str(maxsession))
    if enabled is not None:
        _set_child_text(root, "enabled", "true" if enabled else "false")

    script_app = None
    for child in root:
        if _local(child.tag) == "ScriptApplication":
            script_app = child
            break

    # Purger les params orphelins (absents du .aef) pour aligner UCCX sur le script
    active_names = script_variable_names(detail.script, settings)
    if script_app is not None and active_names:
        removed = _strip_orphan_script_params(script_app, active_names)
        if removed:
            logger.info(
                "Purge scriptParams orphelins sur %s: %s",
                name,
                ", ".join(removed),
            )

    if param_values and script_app is not None:
        by_name = {p.name: p for p in detail.params}
        for sp in script_app:
            if _local(sp.tag) != "scriptParams":
                continue
            fields = {_local(c.tag): c for c in list(sp)}
            pname = _text(fields.get("name"))
            if pname not in param_values:
                continue
            if active_names is not None and pname not in active_names:
                continue
            meta = by_name.get(pname)
            type_name = meta.type if meta else _text(fields.get("type"))
            prev = meta.value if meta else _text(fields.get("value"))
            new_val = encode_param_value(param_values[pname], type_name, prev)
            if "value" in fields:
                fields["value"].text = new_val
            else:
                ve = ET.SubElement(sp, "value")
                ve.text = new_val

    xml_body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    url = f"{_base(settings)}/application/{name}"
    with _client(settings) as client:
        resp = client.put(
            url,
            content=xml_body,
            headers={"Content-Type": "application/xml", "Accept": "application/xml"},
        )
        if resp.status_code not in (200, 201, 204):
            raise RuntimeError(f"PUT application {resp.status_code}: {resp.text[:500]}")

    return get_application(name, settings)


def application_to_dict(detail: ApplicationDetail) -> dict[str, Any]:
    return {
        "applicationName": detail.name,
        "description": detail.description,
        "enabled": detail.enabled,
        "maxsession": detail.maxsession,
        "script": detail.script,
        "type": detail.app_type,
        "scriptParams": [
            {
                "name": p.name,
                "value": decode_param_value(p.value, p.type),
                "rawValue": p.value,
                "type": p.type,
                "isDefault": p.is_default,
                "kind": (
                    "calendar"
                    if p.type == CALENDAR_TYPE
                    else "prompt"
                    if p.type == PLAYABLE_TYPE
                    else "generic"
                ),
            }
            for p in detail.params
        ],
        "triggers": [
            {
                "directoryNumber": t.directory_number,
                "deviceName": t.device_name,
                "display": t.display,
                "triggerEnabled": t.trigger_enabled,
                "maxNumOfSessions": t.max_sessions,
                "locale": t.locale,
                "description": t.description,
            }
            for t in detail.triggers
        ],
    }


def sync_applications_from_uccx(db: Session, settings: Settings | None = None) -> dict:
    """Upsert les apps UCCX en base et purge les apps locales absentes d'UCCX."""
    settings = settings or get_settings()
    remote = list_uccx_applications(settings)
    imported: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    removed: list[str] = []

    remote_script_names: set[str] = set()
    for remote_app in remote:
        if remote_app.app_type and "script" not in remote_app.app_type.lower():
            skipped.append(remote_app.name)
            continue
        remote_script_names.add(remote_app.name)

        existing = db.query(Application).filter(Application.name == remote_app.name).first()
        desc = remote_app.description or remote_app.script or ""
        if existing:
            if desc and existing.description != desc[:255]:
                existing.description = desc[:255]
                updated.append(remote_app.name)
            else:
                skipped.append(remote_app.name)
            continue

        app = Application(name=remote_app.name, description=(desc or "")[:255])
        db.add(app)
        db.flush()
        imported.append(remote_app.name)

    # Purge apps locales qui n'existent plus (ou jamais) sur UCCX
    locals_apps = db.query(Application).all()
    for local in locals_apps:
        if local.name in remote_script_names:
            continue
        name = local.name
        db.delete(local)
        removed.append(name)

    db.commit()
    logger.info(
        "UCCX sync apps: imported=%s updated=%s skipped=%s removed=%s",
        imported,
        updated,
        skipped,
        removed,
    )
    return {
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "removed": removed,
        "total_remote": len(remote),
    }
