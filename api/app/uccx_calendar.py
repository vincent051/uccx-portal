"""Client UCCX calendrier — GET/PUT /adminapi/calendar/{id}.

Types supportés (comme UCCX Admin) :
- FULLTIME      — 24 Hours × 7 Days (pas de businessDays)
- FIXEDHOURS    — Fixed Hours (mêmes plages pour les jours ouverts)
- FLEXIBLEHOURS — Flexible Hours (plages par jour)

Fériés récurrents : une seule date UCCX ; noms `Y Label` / `En Label` ;
roll auto (GET + job) quand la date est passée.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.uccx_apps import _base, _client, _local, _text

logger = logging.getLogger(__name__)

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DAY_LABELS_FR = {
    "MON": "Lundi",
    "TUE": "Mardi",
    "WED": "Mercredi",
    "THU": "Jeudi",
    "FRI": "Vendredi",
    "SAT": "Samedi",
    "SUN": "Dimanche",
}
CALENDAR_TYPES = ("FULLTIME", "FIXEDHOURS", "FLEXIBLEHOURS")
HOLIDAY_NAME_MAX = 50  # XSD ccNameOfDay
MAX_INTERVALS = 3  # XSD UCCX : max 3 plages / jour
TZ_LU = ZoneInfo("Europe/Luxembourg")

# UCCX XSD : caractères interdits dans name (ccNameOfDay) + virgule
_FORBIDDEN_NAME_CHARS = set("`~!@#$%^&*()=[]{};\\?\"<>'+|:./,")
_YEAR_SUFFIX_RE = re.compile(r"\s*·\s*(\d{4})\s*$")
# Ancien format portail
_LEGACY_EASTER_RE = re.compile(r"^\[E\+(\d+)\]\s*(.*)$", re.I)
_LEGACY_YEARLY_RE = re.compile(r"^\[Y\]\s*(.*)$", re.I)
# Nouveau format : Y Label / E39 Label
_NEW_EASTER_RE = re.compile(r"^E(\d+)\s+(.*)$", re.I)
_NEW_YEARLY_RE = re.compile(r"^Y\s+(.*)$", re.I)

_ACCENT_MAP = str.maketrans(
    {
        "à": "a",
        "á": "a",
        "â": "a",
        "ä": "a",
        "ã": "a",
        "å": "a",
        "è": "e",
        "é": "e",
        "ê": "e",
        "ë": "e",
        "ì": "i",
        "í": "i",
        "î": "i",
        "ï": "i",
        "ò": "o",
        "ó": "o",
        "ô": "o",
        "ö": "o",
        "õ": "o",
        "ù": "u",
        "ú": "u",
        "û": "u",
        "ü": "u",
        "ý": "y",
        "ÿ": "y",
        "ç": "c",
        "ñ": "n",
        "À": "A",
        "Á": "A",
        "Â": "A",
        "Ä": "A",
        "È": "E",
        "É": "E",
        "Ê": "E",
        "Ë": "E",
        "Ì": "I",
        "Í": "I",
        "Î": "I",
        "Ï": "I",
        "Ò": "O",
        "Ó": "O",
        "Ô": "O",
        "Ö": "O",
        "Ù": "U",
        "Ú": "U",
        "Û": "U",
        "Ü": "U",
        "Ç": "C",
        "Ñ": "N",
        "œ": "oe",
        "Œ": "OE",
        "æ": "ae",
        "Æ": "AE",
    }
)


def today_lu() -> date:
    return datetime.now(TZ_LU).date()


def normalize_calendar_type(value: str | None) -> str:
    raw = (value or "").strip().upper()
    if not raw:
        return "FLEXIBLEHOURS"
    if raw not in CALENDAR_TYPES:
        raise ValueError(
            f"calendarType invalide: {value!r} (attendu: {', '.join(CALENDAR_TYPES)})"
        )
    return raw


def _strip_year_suffix(label: str) -> str:
    return _YEAR_SUFFIX_RE.sub("", label or "").strip()


def sanitize_day_name(s: str, *, fallback: str = "Ferie") -> str:
    """Retire caractères interdits UCCX, translittère accents, max 50."""
    text = (s or "").translate(_ACCENT_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    cleaned: list[str] = []
    for ch in text:
        if ch in _FORBIDDEN_NAME_CHARS:
            cleaned.append(" ")
        elif ord(ch) < 32:
            continue
        else:
            cleaned.append(ch)
    name = re.sub(r"\s+", " ", "".join(cleaned)).strip()
    if not name:
        name = fallback
    if len(name) > HOLIDAY_NAME_MAX:
        name = name[:HOLIDAY_NAME_MAX].rstrip()
    return name


def sanitize_holiday_name(s: str) -> str:
    return sanitize_day_name(s, fallback="Ferie")


def parse_holiday_meta(name: str) -> dict[str, Any]:
    """Parse Y/En (nouveau), [Y]/[E+N] (legacy) et suffixe « · YYYY »."""
    raw = (name or "").strip()
    base = _strip_year_suffix(raw)
    is_follow = bool(_YEAR_SUFFIX_RE.search(raw))

    m = _LEGACY_EASTER_RE.match(base)
    if m:
        label = sanitize_day_name((m.group(2) or "").strip() or "Ferie")
        off = int(m.group(1))
        return {
            "kind": "easter",
            "offset": off,
            "label": label,
            "base_name": build_holiday_name("easter", off, label),
            "is_follow": is_follow,
        }

    m = _LEGACY_YEARLY_RE.match(base)
    if m:
        label = sanitize_day_name((m.group(1) or "").strip() or "Ferie")
        return {
            "kind": "yearly",
            "offset": None,
            "label": label,
            "base_name": build_holiday_name("yearly", None, label),
            "is_follow": is_follow,
        }

    m = _NEW_EASTER_RE.match(base)
    if m:
        label = sanitize_day_name((m.group(2) or "").strip() or "Ferie")
        off = int(m.group(1))
        return {
            "kind": "easter",
            "offset": off,
            "label": label,
            "base_name": build_holiday_name("easter", off, label),
            "is_follow": is_follow,
        }

    m = _NEW_YEARLY_RE.match(base)
    if m:
        label = sanitize_day_name((m.group(1) or "").strip() or "Ferie")
        return {
            "kind": "yearly",
            "offset": None,
            "label": label,
            "base_name": build_holiday_name("yearly", None, label),
            "is_follow": is_follow,
        }

    label = sanitize_day_name(base or "Ferie")
    return {
        "kind": "fixed",
        "offset": None,
        "label": label,
        "base_name": label,
        "is_follow": False,
    }


def build_holiday_name(kind: str, offset: int | None, label: str) -> str:
    clean = sanitize_day_name(label or "Ferie")
    if kind == "easter":
        prefix = f"E{int(offset)} "
        budget = HOLIDAY_NAME_MAX - len(prefix)
        return f"{prefix}{clean[: max(1, budget)]}".rstrip()
    if kind == "yearly":
        prefix = "Y "
        budget = HOLIDAY_NAME_MAX - len(prefix)
        return f"{prefix}{clean[: max(1, budget)]}".rstrip()
    return clean[:HOLIDAY_NAME_MAX]


def _easter_sunday(year: int) -> date:
    """Algorithme anonyme grégorien."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _next_yearly(mm: int, dd: int, today: date | None = None) -> date:
    today = today or today_lu()
    try:
        dt = date(today.year, mm, dd)
    except ValueError as exc:
        raise ValueError(f"Date annuelle invalide: {mm:02d}-{dd:02d}") from exc
    if dt < today:
        dt = date(today.year + 1, mm, dd)
    return dt


def _next_easter(offset: int, today: date | None = None) -> date:
    today = today or today_lu()
    y = today.year
    dt = _easter_sunday(y) + timedelta(days=offset)
    if dt < today:
        dt = _easter_sunday(y + 1) + timedelta(days=offset)
    return dt


def _clip_holiday_name(name: str) -> str:
    return sanitize_holiday_name(name)


def _holiday_pairs(holidays: list[dict[str, str]]) -> list[tuple[str, str]]:
    return sorted(
        (
            (sanitize_holiday_name(h.get("name") or ""), (h.get("date") or "").strip())
            for h in holidays or []
            if (h.get("date") or "").strip()
        ),
        key=lambda t: (t[1], t[0]),
    )


def normalize_holidays_for_put(
    holidays: list[dict[str, str]] | None,
    current: list[dict[str, str]] | None,
    *,
    today: date | None = None,
) -> list[dict[str, str]]:
    """Anti-wipe, roll-forward Y/En, une seule date (pas d'expand A+B).

    - holidays=[] alors que current non vide → conserve current (évite wipe DOM vide)
    - chaque logique Y / En → 1 entrée UCCX (prochaine occurrence ≥ aujourd'hui)
    - migre [Y]/[E+N]/· YYYY vers le format canonique
    """
    current = list(current or [])
    if holidays is not None and len(holidays) == 0 and current:
        logger.warning(
            "PUT holidays=[] ignoré — conservation de %s férié(s) existant(s)",
            len(current),
        )
        holidays = current
    elif holidays is None:
        holidays = current

    today = today or today_lu()
    fixed: list[dict[str, str]] = []
    logical: dict[str, dict[str, Any]] = {}

    for h in holidays or []:
        name = (h.get("name") or "").strip()
        date_s = (h.get("date") or "").strip()
        if not date_s or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_s):
            continue
        meta = parse_holiday_meta(name)
        if meta["kind"] == "fixed":
            y, m, d = (int(x) for x in date_s.split("-"))
            try:
                dt = date(y, m, d)
            except ValueError:
                continue
            while dt < today:
                try:
                    dt = date(dt.year + 1, m, d)
                except ValueError:
                    break
            if dt < today:
                continue
            fixed.append({"name": meta["base_name"], "date": dt.isoformat()})
            continue

        key = (
            f"easter:{meta['offset']}"
            if meta["kind"] == "easter"
            else f"yearly:{(meta['label'] or '').lower()}"
        )
        if key not in logical or not meta["is_follow"]:
            logical[key] = {**meta, "date_hint": date_s}

    out: list[dict[str, str]] = list(fixed)
    for meta in logical.values():
        hint = meta.get("date_hint") or ""
        mm, dd = 1, 1
        if re.match(r"^\d{4}-\d{2}-\d{2}$", hint):
            mm, dd = int(hint[5:7]), int(hint[8:10])

        if meta["kind"] == "easter":
            nxt = _next_easter(int(meta["offset"]), today)
        else:
            nxt = _next_yearly(mm, dd, today)

        out.append({"name": meta["base_name"], "date": nxt.isoformat()})

    for h in out:
        if len(h["name"]) > HOLIDAY_NAME_MAX:
            raise ValueError(
                f"Nom de férié trop long (max {HOLIDAY_NAME_MAX} car.): {h['name']!r}"
            )
    return out


def _special_day_fingerprint(sd: dict[str, Any]) -> tuple:
    ivs = tuple(
        (
            (iv.get("name") or "").strip(),
            (iv.get("startTime") or "").strip(),
            (iv.get("endTime") or "").strip(),
        )
        for iv in (sd.get("intervals") or [])[:MAX_INTERVALS]
    )
    return ((sd.get("name") or "").strip(), (sd.get("date") or "").strip(), ivs)


def normalize_special_days_for_put(
    special_days: list[dict[str, Any]] | None,
    current: list[dict[str, Any]] | None,
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Sanitize + roll Y/En + max 3 plages. ``None`` = conserver ; ``[]`` = vider."""
    current = list(current or [])
    if special_days is None:
        special_days = current
    # special_days=[] est intentionnel (suppression de tous les jours spéciaux)

    today = today or today_lu()
    out: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for sd in special_days or []:
        raw_name = (sd.get("name") or "").strip()
        date_s = (sd.get("date") or "").strip()
        if not date_s or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_s):
            continue
        meta = parse_holiday_meta(raw_name)
        label = meta["label"] or "Jour special"
        name = meta["base_name"] if meta["kind"] != "fixed" else sanitize_day_name(label)

        mm, dd = 1, 1
        try:
            y, m, d = (int(x) for x in date_s.split("-"))
            mm, dd = m, d
            dt = date(y, m, d)
        except ValueError:
            continue

        if meta["kind"] == "easter":
            dt = _next_easter(int(meta["offset"]), today)
            name = build_holiday_name("easter", int(meta["offset"]), label)
            key = f"easter:{meta['offset']}"
        elif meta["kind"] == "yearly":
            dt = _next_yearly(mm, dd, today)
            name = build_holiday_name("yearly", None, label)
            key = f"yearly:{label.lower()}"
        else:
            while dt < today:
                try:
                    dt = date(dt.year + 1, mm, dd)
                except ValueError:
                    break
            if dt < today:
                continue
            key = f"fixed:{dt.isoformat()}:{label.lower()}"

        if key in seen_keys:
            continue
        seen_keys.add(key)

        intervals: list[dict[str, str]] = []
        for idx, iv in enumerate((sd.get("intervals") or [])[:MAX_INTERVALS], start=1):
            start = (iv.get("startTime") or "").strip()
            end = (iv.get("endTime") or "").strip()
            if not start or not end:
                continue
            iv_name = sanitize_day_name((iv.get("name") or "").strip(), fallback=f"Plage {idx}")
            intervals.append(
                {
                    "name": iv_name,
                    "startTime": normalize_time_hm(start),
                    "endTime": normalize_time_hm(end),
                }
            )
        if not intervals:
            continue
        out.append({"name": name, "date": dt.isoformat(), "intervals": intervals})
    return out


def validate_calendar_dates_unique(
    holidays: list[dict[str, str]],
    special_days: list[dict[str, Any]],
) -> None:
    """UCCX : une seule date pour tout le calendrier (fériés + specialDays)."""
    by_date: dict[str, list[str]] = defaultdict(list)
    for h in holidays or []:
        d = (h.get("date") or "").strip()
        if d:
            by_date[d].append(f"Férié « {h.get('name') or d} »")
    for sd in special_days or []:
        d = (sd.get("date") or "").strip()
        if d:
            by_date[d].append(f"Jour spécial « {sd.get('name') or d} »")
    conflicts = {d: labels for d, labels in by_date.items() if len(labels) > 1}
    if not conflicts:
        return
    parts = [f"{d} ({', '.join(labels)})" for d, labels in sorted(conflicts.items())]
    raise ValueError(
        "UCCX n'autorise qu'une entrée par date (fériés et jours spéciaux confondus). "
        f"Conflit : {' ; '.join(parts)}"
    )


def normalize_time_hm(value: str | None) -> str:
    """Heure UCCX au format HH:MM (sans secondes)."""
    raw = (value or "").strip()
    if not raw:
        return ""
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", raw)
    if not m:
        return raw
    h, mm = int(m.group(1)), int(m.group(2))
    if h < 0 or h > 23 or mm < 0 or mm > 59:
        return raw
    return f"{h:02d}:{mm:02d}"


def _time_to_minutes(value: str) -> int:
    hm = normalize_time_hm(value)
    if not hm or ":" not in hm:
        raise ValueError(f"Heure invalide : {value!r}")
    h, m = (int(x) for x in hm.split(":"))
    return h * 60 + m


def validate_time_intervals(intervals: list[dict[str, Any]], context: str) -> None:
    """Début < fin et plages disjointes (règle UCCX)."""
    parsed: list[tuple[int, int, str]] = []
    for idx, iv in enumerate(intervals or [], start=1):
        label = (iv.get("name") or "").strip() or f"Plage {idx}"
        start_s = normalize_time_hm(iv.get("startTime"))
        end_s = normalize_time_hm(iv.get("endTime"))
        if not start_s or not end_s:
            continue
        start_m = _time_to_minutes(start_s)
        end_m = _time_to_minutes(end_s)
        if start_m >= end_m:
            raise ValueError(
                f"{context} — « {label} » : l'heure de fin ({end_s}) doit être "
                f"après l'heure de début ({start_s})."
            )
        parsed.append((start_m, end_m, label))
    parsed.sort(key=lambda t: t[0])
    for i in range(1, len(parsed)):
        prev_end = parsed[i - 1][1]
        cur_start, _, cur_label = parsed[i]
        prev_label = parsed[i - 1][2]
        if cur_start < prev_end:
            raise ValueError(
                f"{context} — chevauchement entre « {prev_label} » et « {cur_label} » "
                "(les plages d'un même jour doivent être disjointes)."
            )


def validate_business_day_intervals(business_days: dict[str, list]) -> None:
    for dow in DAYS:
        ivs = business_days.get(dow) or []
        if not ivs:
            continue
        label = DAY_LABELS_FR.get(dow, dow)
        validate_time_intervals(ivs, f"{label} ({dow})")


def validate_special_day_intervals(special_days: list[dict[str, Any]]) -> None:
    for sd in special_days or []:
        name = (sd.get("name") or "Jour special").strip()
        validate_time_intervals(sd.get("intervals") or [], f"Jour spécial « {name} »")


def parse_uccx_calendar_put_error(status_code: int, body: str) -> str:
    """Message lisible à partir du XML apiErrors UCCX."""
    messages: list[str] = []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return f"Erreur UCCX (HTTP {status_code})."

    for node in root.iter():
        if _local(node.tag) != "apiError":
            continue
        fields = {_local(c.tag): (_text(c) or "").strip() for c in list(node)}
        data = fields.get("errorData") or ""
        msg = fields.get("errorMessage") or ""
        key = ""
        for child in node.iter():
            if _local(child.tag) == "key" and (child.text or "").strip():
                key = (child.text or "").strip()
                break
        detail = f" ({data})" if data else ""
        low = msg.lower()
        if key == "adminapi.calendar.interval.overlap" or "overlap" in low:
            messages.append(
                f"Plage horaire invalide{detail} : l'heure de fin doit être après le début "
                "et les plages d'un même jour ne doivent pas se chevaucher."
            )
        elif "duplicate unique value" in low or "uniquedate" in low.replace(" ", ""):
            m = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", msg)
            d = m.group(1) if m else data
            messages.append(
                f"La date {d or 'indiquée'} est déjà utilisée par un férié ou un jour spécial "
                "(une seule entrée par date sur le calendrier UCCX)."
            )
        elif "future date" in low:
            messages.append(
                f"Date invalide{detail} : UCCX exige une date future pour les fériés et jours spéciaux."
            )
        elif msg:
            messages.append(f"{data + ' : ' if data else ''}{msg}")
        elif data:
            messages.append(data)

    if messages:
        return " ".join(messages)
    snippet = re.sub(r"\s+", " ", body)[:200]
    return f"Erreur UCCX (HTTP {status_code}) : {snippet}"


def _parse_intervals(intervals_el: ET.Element | None) -> list[dict[str, str]]:
    intervals: list[dict[str, str]] = []
    if intervals_el is None:
        return intervals
    for iv in intervals_el:
        if _local(iv.tag) != "interval":
            continue
        ivf = {_local(c.tag): c for c in list(iv)}
        intervals.append(
            {
                "name": _text(ivf.get("name")) or "Time Range 1",
                "startTime": _text(ivf.get("startTime")),
                "endTime": _text(ivf.get("endTime")),
            }
        )
    return intervals


def _fetch_calendar(cal_id: str | int, settings: Settings) -> dict[str, Any]:
    """GET brut UCCX sans roll."""
    url = f"{_base(settings)}/calendar/{cal_id}"
    with _client(settings) as client:
        resp = client.get(url)
        if resp.status_code == 404:
            raise LookupError(f"Calendrier UCCX introuvable: {cal_id}")
        resp.raise_for_status()
        raw = resp.text

    root = ET.fromstring(raw)
    fields = {_local(c.tag): c for c in list(root)}

    business_days: dict[str, list[dict[str, str]]] = {d: [] for d in DAYS}
    bd_el = fields.get("businessDays")
    if bd_el is not None:
        for day_node in bd_el:
            if _local(day_node.tag) != "businessDay":
                continue
            day_fields = {_local(c.tag): c for c in list(day_node)}
            dow = _text(day_fields.get("dayOfWeek")).upper()
            intervals = _parse_intervals(day_fields.get("intervals"))
            if dow in business_days:
                business_days[dow] = intervals

    holidays: list[dict[str, str]] = []
    hol_el = fields.get("holidays")
    if hol_el is not None:
        for h in hol_el:
            if _local(h.tag) != "holiday":
                continue
            hf = {_local(c.tag): c for c in list(h)}
            holidays.append(
                {
                    "name": _text(hf.get("name")),
                    "date": _text(hf.get("date")),
                }
            )

    special_days: list[dict[str, Any]] = []
    sd_el = fields.get("specialDays")
    if sd_el is not None:
        for sd in sd_el:
            if _local(sd.tag) != "specialDay":
                continue
            sdf = {_local(c.tag): c for c in list(sd)}
            special_days.append(
                {
                    "name": _text(sdf.get("name")),
                    "date": _text(sdf.get("date")),
                    "intervals": _parse_intervals(sdf.get("intervals")),
                }
            )

    return {
        "calId": _text(fields.get("calId")) or str(cal_id),
        "name": _text(fields.get("name")),
        "timeZone": _text(fields.get("timeZone")),
        "calendarType": _text(fields.get("calendarType")),
        "businessDays": business_days,
        "holidays": holidays,
        "specialDays": special_days,
        "raw_xml": raw,
    }


def holidays_need_normalize(
    holidays: list[dict[str, str]] | None,
    *,
    today: date | None = None,
) -> bool:
    """True si roll / migration noms / dédup · YYYY nécessaire."""
    current = list(holidays or [])
    normalized = normalize_holidays_for_put(current, current, today=today)
    return _holiday_pairs(current) != _holiday_pairs(normalized)


def special_days_need_normalize(
    special_days: list[dict[str, Any]] | None,
    *,
    today: date | None = None,
) -> bool:
    current = list(special_days or [])
    normalized = normalize_special_days_for_put(current, current, today=today)
    a = sorted(_special_day_fingerprint(sd) for sd in current)
    b = sorted(_special_day_fingerprint(sd) for sd in normalized)
    return a != b


def calendar_needs_roll(cal: dict[str, Any], *, today: date | None = None) -> bool:
    return holidays_need_normalize(cal.get("holidays") or [], today=today) or special_days_need_normalize(
        cal.get("specialDays") or [], today=today
    )


def ensure_holidays_rolled(
    cal_id: str | int,
    cal: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Si fériés / specialDays Y/En passés (ou format legacy), PUT normalisé."""
    settings = settings or get_settings()
    if cal is None:
        cal = _fetch_calendar(cal_id, settings)
    if not calendar_needs_roll(cal):
        return cal

    normalized_hol = normalize_holidays_for_put(cal.get("holidays") or [], cal.get("holidays") or [])
    normalized_sd = normalize_special_days_for_put(
        cal.get("specialDays") or [], cal.get("specialDays") or []
    )
    logger.info(
        "calendar rolled: calId=%s holidays %s→%s specialDays %s→%s",
        cal_id,
        len(cal.get("holidays") or []),
        len(normalized_hol),
        len(cal.get("specialDays") or []),
        len(normalized_sd),
    )
    for h in normalized_hol:
        logger.info("holiday rolled: calId=%s name=%s date=%s", cal_id, h["name"], h["date"])
    for sd in normalized_sd:
        logger.info(
            "specialDay rolled: calId=%s name=%s date=%s", cal_id, sd["name"], sd["date"]
        )

    return update_calendar(
        cal_id,
        business_days=cal.get("businessDays"),
        holidays=normalized_hol,
        special_days=normalized_sd,
        calendar_type=cal.get("calendarType"),
        settings=settings,
        auto_roll=False,
        _current=cal,
    )


def get_calendar(
    cal_id: str | int,
    settings: Settings | None = None,
    *,
    auto_roll: bool = True,
) -> dict[str, Any]:
    settings = settings or get_settings()
    cal = _fetch_calendar(cal_id, settings)
    if auto_roll and calendar_needs_roll(cal):
        try:
            cal = ensure_holidays_rolled(cal_id, cal, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Roll auto calendrier %s échoué: %s", cal_id, exc)
    return cal


def _build_calendar_xml(
    meta: dict[str, Any],
    business_days: dict[str, list],
    holidays: list,
    special_days: list | None = None,
) -> bytes:
    cal_type = normalize_calendar_type(meta.get("calendarType"))
    root = ET.Element("calendar")
    ET.SubElement(root, "calId").text = str(meta["calId"])
    ET.SubElement(root, "name").text = meta.get("name") or ""
    ET.SubElement(root, "timeZone").text = meta.get("timeZone") or "Europe/Luxembourg"
    ET.SubElement(root, "calendarType").text = cal_type

    # FULLTIME : omettre <businessDays> (élément vide → 400 XSD UCCX)
    if cal_type != "FULLTIME":
        bd_root = ET.SubElement(root, "businessDays")
        open_days = 0
        for dow in DAYS:
            intervals = business_days.get(dow) or []
            valid = []
            for idx, iv in enumerate(intervals, start=1):
                start = normalize_time_hm(iv.get("startTime"))
                end = normalize_time_hm(iv.get("endTime"))
                if not start or not end:
                    continue
                valid.append((idx, iv, start, end))
            if not valid:
                continue
            open_days += 1
            day_el = ET.SubElement(bd_root, "businessDay")
            ET.SubElement(day_el, "dayOfWeek").text = dow
            intervals_el = ET.SubElement(day_el, "intervals")
            for n, (idx, iv, start, end) in enumerate(valid, start=1):
                iv_el = ET.SubElement(intervals_el, "interval")
                ET.SubElement(iv_el, "name").text = iv.get("name") or f"Time Range {n}"
                ET.SubElement(iv_el, "startTime").text = start
                ET.SubElement(iv_el, "endTime").text = end
        if open_days == 0:
            raise ValueError(
                "Au moins un jour avec une plage horaire est requis "
                f"pour le type {cal_type}"
            )

    # Élément <holidays> vide → 400 XSD UCCX : n'émettre que s'il y a ≥1 holiday
    valid_holidays = []
    for h in holidays or []:
        name = _clip_holiday_name((h.get("name") or "").strip() or (h.get("date") or ""))
        date_s = (h.get("date") or "").strip()
        if not date_s:
            continue
        valid_holidays.append((name, date_s))
    if valid_holidays:
        hol_root = ET.SubElement(root, "holidays")
        for name, date_s in valid_holidays:
            h_el = ET.SubElement(hol_root, "holiday")
            ET.SubElement(h_el, "name").text = name
            ET.SubElement(h_el, "date").text = date_s

    valid_sd: list[dict[str, Any]] = []
    for sd in special_days or []:
        name = sanitize_day_name((sd.get("name") or "").strip() or "Jour special")
        date_s = (sd.get("date") or "").strip()
        if not date_s:
            continue
        # Préserver préfixe Y / En déjà normalisé
        meta = parse_holiday_meta(sd.get("name") or "")
        if meta["kind"] in ("yearly", "easter"):
            name = meta["base_name"]
        ivs = []
        for n, iv in enumerate((sd.get("intervals") or [])[:MAX_INTERVALS], start=1):
            start = normalize_time_hm(iv.get("startTime"))
            end = normalize_time_hm(iv.get("endTime"))
            if not start or not end:
                continue
            ivs.append(
                (
                    sanitize_day_name((iv.get("name") or "").strip(), fallback=f"Plage {n}"),
                    start,
                    end,
                )
            )
        if not ivs:
            continue
        valid_sd.append({"name": name, "date": date_s, "intervals": ivs})
    if valid_sd:
        sd_root = ET.SubElement(root, "specialDays")
        for sd in valid_sd:
            sd_el = ET.SubElement(sd_root, "specialDay")
            ET.SubElement(sd_el, "name").text = sd["name"]
            ET.SubElement(sd_el, "date").text = sd["date"]
            intervals_el = ET.SubElement(sd_el, "intervals")
            for iname, start, end in sd["intervals"]:
                iv_el = ET.SubElement(intervals_el, "interval")
                ET.SubElement(iv_el, "name").text = iname
                ET.SubElement(iv_el, "startTime").text = start
                ET.SubElement(iv_el, "endTime").text = end

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def update_calendar(
    cal_id: str | int,
    *,
    business_days: dict[str, list] | None = None,
    holidays: list[dict[str, str]] | None = None,
    special_days: list[dict[str, Any]] | None = None,
    calendar_type: str | None = None,
    settings: Settings | None = None,
    auto_roll: bool = False,
    _current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    current = _current if _current is not None else _fetch_calendar(cal_id, settings)
    if calendar_type is not None:
        current["calendarType"] = normalize_calendar_type(calendar_type)
    bd = business_days if business_days is not None else current["businessDays"]
    if normalize_calendar_type(current.get("calendarType")) == "FULLTIME":
        bd = {d: [] for d in DAYS}
    hol = normalize_holidays_for_put(holidays, current.get("holidays") or [])
    sd = normalize_special_days_for_put(special_days, current.get("specialDays") or [])
    validate_calendar_dates_unique(hol, sd)
    if normalize_calendar_type(current.get("calendarType")) != "FULLTIME":
        validate_business_day_intervals(bd)
    validate_special_day_intervals(sd)
    xml_body = _build_calendar_xml(current, bd, hol, sd)

    url = f"{_base(settings)}/calendar/{cal_id}"
    with _client(settings) as client:
        resp = client.put(
            url,
            content=xml_body,
            headers={"Content-Type": "application/xml", "Accept": "application/xml"},
        )
        if resp.status_code not in (200, 201, 204):
            raise ValueError(parse_uccx_calendar_put_error(resp.status_code, resp.text))

    return get_calendar(cal_id, settings, auto_roll=auto_roll)


def calendars_for_params(params: list, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Charge les calendriers référencés par des scriptParams CCCalendar (roll auto inclus)."""
    from app.uccx_apps import CALENDAR_TYPE, decode_param_value

    settings = settings or get_settings()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in params:
        type_name = p.type if hasattr(p, "type") else p.get("type", "")
        if type_name != CALENDAR_TYPE:
            continue
        raw = p.value if hasattr(p, "value") else p.get("rawValue") or p.get("value", "")
        cal_id = decode_param_value(str(raw), CALENDAR_TYPE).strip()
        if not cal_id or cal_id in seen:
            continue
        seen.add(cal_id)
        try:
            cal = get_calendar(cal_id, settings, auto_roll=True)
            cal.pop("raw_xml", None)
            cal["paramName"] = p.name if hasattr(p, "name") else p.get("name", "")
            out.append(cal)
        except Exception as exc:  # noqa: BLE001
            out.append(
                {
                    "calId": cal_id,
                    "name": "",
                    "error": str(exc),
                    "paramName": p.name if hasattr(p, "name") else p.get("name", ""),
                    "businessDays": {d: [] for d in DAYS},
                    "holidays": [],
                    "specialDays": [],
                }
            )
    return out


def collect_referenced_calendar_ids(settings: Settings | None = None) -> set[str]:
    """IDs calendriers référencés par toutes les apps UCCX."""
    from app.uccx_apps import CALENDAR_TYPE, get_application, list_uccx_applications

    settings = settings or get_settings()
    ids: set[str] = set()
    try:
        apps = list_uccx_applications(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Liste apps UCCX pour roll fériés: %s", exc)
        return ids
    for app in apps:
        name = app.name if hasattr(app, "name") else str(app.get("name", ""))
        if not name:
            continue
        try:
            detail = get_application(name, settings)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skip app %s pour roll: %s", name, exc)
            continue
        for p in detail.params:
            if p.type != CALENDAR_TYPE:
                continue
            cid = (p.value or "").strip()
            if cid:
                ids.add(cid)
    return ids


def roll_all_referenced_calendars(settings: Settings | None = None) -> dict[str, Any]:
    """Scan + roll de tous les calendriers référencés (job périodique)."""
    settings = settings or get_settings()
    ids = collect_referenced_calendar_ids(settings)
    rolled = 0
    errors = 0
    for cal_id in sorted(ids):
        try:
            before = _fetch_calendar(cal_id, settings)
            if calendar_needs_roll(before):
                ensure_holidays_rolled(cal_id, before, settings)
                rolled += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning("Roll calendrier %s: %s", cal_id, exc)
    return {"scanned": len(ids), "rolled": rolled, "errors": errors}
