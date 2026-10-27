"""Génère le XML consommé par CheckBH / scripts UCCX.

Format horaires jour: HH:MM-HH:MM (ex. 07:30-18:30) ou Closed.
Str_Holidays: DD/MM/YYYY HH:MM-HH:MM;... (XXXX pour récurrent)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

from app.schemas import CLOSED, DAY_KEYS, AppConfigPayload


def holidays_to_str(payload: AppConfigPayload) -> str:
    chunks: list[str] = []
    for h in payload.holidays:
        chunks.append(f"{h.date} {h.closed}")
    return ";".join(chunks)


def week_hours_flat(payload: AppConfigPayload) -> str:
    """Forme plate pour extraction simple: Lu=07:30-18:30;Ma=...;Di=Closed"""
    return ";".join(f"{day}={getattr(payload.weekHours, day)}" for day in DAY_KEYS)


def build_config_xml(app_name: str, payload: AppConfigPayload) -> str:
    root = Element("config")
    root.set("app", app_name)
    root.set("version", "1")
    root.set("updatedAt", datetime.now(timezone.utc).isoformat())

    flags = SubElement(root, "flags")
    SubElement(flags, "forceClosed").text = "true" if payload.flags.forceClosed else "false"
    SubElement(flags, "forceOpen").text = "true" if payload.flags.forceOpen else "false"

    week = SubElement(root, "weekHours")
    for day in DAY_KEYS:
        SubElement(week, day).text = getattr(payload.weekHours, day) or CLOSED

    # Alias plat pour Get XML Document Data / scripts legacy
    SubElement(root, "Str_WeekHours").text = week_hours_flat(payload)
    SubElement(root, "Str_Holidays").text = holidays_to_str(payload)

    holidays_el = SubElement(root, "holidays")
    for h in payload.holidays:
        day_el = SubElement(holidays_el, "day")
        day_el.set("date", h.date)
        day_el.set("closed", h.closed)

    prompts = SubElement(root, "prompts")
    SubElement(prompts, "closed").text = payload.prompts.closed or ""
    SubElement(prompts, "welcome").text = payload.prompts.welcome or ""

    rough = tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8")
    # minidom ajoute la déclaration XML ; on retourne du texte
    return pretty.decode("utf-8")


def write_config_xml(xml_dir: str | Path, app_name: str, payload: AppConfigPayload) -> Path:
    base = Path(xml_dir) / app_name
    base.mkdir(parents=True, exist_ok=True)
    path = base / "config.xml"
    path.write_text(build_config_xml(app_name, payload), encoding="utf-8")
    return path


def default_payload() -> AppConfigPayload:
    from app.schemas import WeekHours

    return AppConfigPayload(
        weekHours=WeekHours(
            Lu="07:30-18:30",
            Ma="07:30-18:30",
            Me="07:30-18:30",
            Je="07:30-18:30",
            Ve="07:30-18:30",
            Sa=CLOSED,
            Di=CLOSED,
        )
    )
