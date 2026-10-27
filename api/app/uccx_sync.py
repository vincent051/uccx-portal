"""Upload prompts et gestion Document/Prompt UCCX Admin API."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_LANG_FOLDER_RE = re.compile(r"^[A-Za-z]{2,3}(_[A-Za-z0-9]+)?$")


def _base_url(settings: Settings) -> str:
    return f"https://{settings.uccx_host}/adminapi"


def _prompt_client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        verify=settings.uccx_verify_ssl,
        timeout=60.0,
        auth=(settings.uccx_user, settings.uccx_password),
    )


def _xml_local(tag: str) -> str:
    return tag.split("}")[-1] if tag else tag


def list_prompt_languages(settings: Settings) -> list[str]:
    """Dossiers langue racine du repository prompts (default, fr_LU, …)."""
    if not settings.uccx_user or not settings.uccx_password:
        return []
    import xml.etree.ElementTree as ET

    url = f"{_base_url(settings)}/prompt"
    try:
        with _prompt_client(settings) as client:
            resp = client.get(url, headers={"Accept": "application/xml"})
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("list_prompt_languages: %s", exc)
        return []
    langs: list[str] = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return []
    for el in root.iter():
        if _xml_local(el.tag) == "FolderName" and el.text:
            name = el.text.strip()
            if name and name not in langs:
                langs.append(name)
    # default en tête si présent
    if "default" in langs:
        langs = ["default"] + [x for x in langs if x != "default"]
    return langs


def create_prompt_language(settings: Settings, lang: str) -> tuple[bool, str]:
    """Crée un dossier langue racine sous /prompt (path=/)."""
    name = (lang or "").strip().replace("-", "_")
    if not name or name.lower() == "default":
        return False, "Nom de langue invalide (default réservé)"
    if not _LANG_FOLDER_RE.match(name):
        return False, f"Format invalide : {name!r} (attendu xx ou xx_YY)"
    existing = list_prompt_languages(settings)
    if name in existing:
        return True, "already-exists"
    meta = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Files><Prompt><Folder><path>/</path>"
        f"<FolderName>{name}</FolderName></Folder></Prompt></Files>"
    )
    try:
        with _prompt_client(settings) as client:
            resp = client.post(
                f"{_base_url(settings)}/prompt",
                content=meta.encode("utf-8"),
                headers={"Accept": "application/xml", "Content-Type": "application/xml"},
            )
    except httpx.HTTPError as exc:
        return False, str(exc)
    if resp.status_code in (200, 201, 204, 409):
        return True, "created" if resp.status_code != 409 else "already-exists"
    return False, f"{resp.status_code}: {resp.text[:300]}"


def safe_app_folder(app_name: str) -> str:
    """Nom de dossier prompt sous /{lang}/ (alphanum, - _)."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (app_name or ""))
    return safe or "app"


def canonical_prompt_basename(
    playable_value: str | None,
    selected_relpath: str,
) -> str:
    """Basename .wav unique pour toutes les langues (Playable actuel, sinon sélection)."""
    _kind, existing = parse_playable_path(playable_value or "")
    if existing:
        basename = existing.split("/")[-1]
    else:
        sel = (selected_relpath or "").strip().lstrip("/").replace("\\", "/")
        basename = sel.split("/")[-1] if sel else ""
    if not basename:
        basename = "prompt.wav"
    if not basename.lower().endswith(".wav"):
        basename = f"{basename}.wav"
    return basename


def canonical_prompt_relpath(
    app_name: str,
    playable_value: str | None,
    selected_relpath: str,
) -> str:
    """/{lang}/{safe_app}/{basename}.wav — dossier nommé comme l'app, même fichier partout.

    Retourne le relpath avec dossier app (ex. safe_app/file.wav).
    Le Playable AppAdmin est ce chemin nu (slash /), sans P[]/SP[].
    """
    folder = safe_app_folder(app_name)
    return f"{folder}/{canonical_prompt_basename(playable_value, selected_relpath)}"


def list_prompt_files_all_langs(settings: Settings) -> list[dict[str, str]]:
    """Catalogue global : tous les fichiers de toutes les langues (pour réutilisation)."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for lang in list_prompt_languages(settings):
        for f in list_prompt_files(settings, lang):
            key = (lang, f.get("relpath") or "")
            if not key[1] or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "lang": lang,
                    "relpath": f["relpath"],
                    "filename": f.get("filename") or f["relpath"].split("/")[-1],
                    "playable": f.get("playable") or relpath_to_playable(f["relpath"]),
                    "label": f"{lang}/{f['relpath']}",
                }
            )
    out.sort(key=lambda x: (x["lang"].lower(), x["relpath"].lower()))
    return out


def parse_playable_path(value: str) -> tuple[str, str]:
    """Retourne ('SP'|'P'|'', relative_path) à partir d'une valeur Playable.

    Déplie les encapsulations imbriquées (P[P[…]] / SP[P[…]]) et accepte un chemin
    nu (ex. 1001.wav ou test\\file.wav) tel que stocké parfois dans AppAdmin.
    """
    raw = (value or "").strip()
    if not raw:
        return "", ""
    kind = ""
    for _ in range(8):
        matched = False
        for k, prefix, suffix in (("SP", "SP[", "]"), ("P", "P[", "]")):
            if raw.startswith(prefix) and raw.endswith(suffix) and len(raw) >= len(prefix) + len(suffix):
                raw = raw[len(prefix) : -len(suffix)].strip()
                kind = k
                matched = True
                break
        if not matched:
            break
        if not raw:
            return kind, ""
    rel = raw.replace("\\", "/").strip("/")
    if not rel:
        return kind, ""
    # Chemin nu (sans P[]/SP[]) — traité comme Playable relatif
    if not kind:
        kind = "P"
    return kind, rel


def relpath_to_playable(relpath: str) -> str:
    """Chemin relatif nu tel que stocké par AppAdmin UCCX (sans P[]/SP[]).

    Exemples (idempotent si on reçoit déjà P[…] / SP[…] / chemin nu) :
      file.wav
      test/Welcome.wav   (slash /, pas backslash)

    Vide / P[] / P[P[]] → « P[] » (sentinelle unique, jamais ré-enveloppée).
    """
    raw = (relpath or "").strip()
    if not raw or raw in ("P[]", "SP[]"):
        return "P[]"
    _kind, rel = parse_playable_path(raw)
    if not rel:
        # Nested empty (P[P[]], SP[P[]], …) or garbage wrappers
        return "P[]"
    return rel

def prompt_file_exists(settings: Settings, lang: str, relpath: str) -> bool:
    """True si GET /prompt/{lang}/{relpath} = 200."""
    lang = (lang or "").strip().strip("/")
    rel = (relpath or "").strip().lstrip("/").replace("\\", "/")
    if not lang or not rel:
        return False
    if not settings.uccx_user or not settings.uccx_password:
        return False
    url = f"{_base_url(settings)}/prompt/{lang}/{rel}"
    try:
        with _prompt_client(settings) as client:
            resp = client.get(url, headers={"Accept": "application/xml"})
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


def list_prompt_files(settings: Settings, lang: str) -> list[dict[str, str]]:
    """Liste fichiers sous une langue via /prompt/files/prompts (+ fallback dossiers)."""
    import xml.etree.ElementTree as ET

    lang = (lang or "").strip().strip("/")
    if not lang or not settings.uccx_user or not settings.uccx_password:
        return []
    base = _base_url(settings)
    out: list[dict[str, str]] = []
    prefix = f"/{lang}/"

    def add_rel(rel: str) -> None:
        rel = rel.strip().lstrip("/").replace("\\", "/")
        if not rel:
            return
        fname = rel.split("/")[-1]
        out.append(
            {
                "relpath": rel,
                "filename": fname,
                "playable": relpath_to_playable(rel),
                "lang": lang,
            }
        )

    try:
        with _prompt_client(settings) as client:
            # Source agrégée (plus fiable pour sous-dossiers)
            agg = client.get(
                f"{base}/prompt/files/prompts",
                headers={"Accept": "application/xml"},
            )
            if agg.status_code == 200:
                try:
                    root = ET.fromstring(agg.text)
                    for el in root.iter():
                        if _xml_local(el.tag) != "Path" or not el.text:
                            continue
                        path = el.text.strip().replace("\\", "/")
                        if not path.startswith(prefix):
                            continue
                        add_rel(path[len(prefix) :])
                except ET.ParseError as exc:
                    logger.warning("list_prompt_files parse aggregate: %s", exc)

            if out:
                # dédup
                seen: set[str] = set()
                deduped: list[dict[str, str]] = []
                for item in out:
                    if item["relpath"] in seen:
                        continue
                    seen.add(item["relpath"])
                    deduped.append(item)
                deduped.sort(key=lambda x: x["relpath"].lower())
                return deduped

            # Fallback: listing dossier + 1 niveau
            def parse_listing(xml_text: str) -> tuple[list[str], list[str]]:
                files: list[str] = []
                folders: list[str] = []
                try:
                    root = ET.fromstring(xml_text)
                except ET.ParseError:
                    return files, folders
                for el in root.iter():
                    tag = _xml_local(el.tag)
                    if tag == "FileName" and el.text:
                        files.append(el.text.strip())
                    elif tag == "FolderName" and el.text:
                        folders.append(el.text.strip())
                return files, folders

            resp = client.get(f"{base}/prompt/{lang}/", headers={"Accept": "application/xml"})
            if resp.status_code != 200:
                return []
            files, folders = parse_listing(resp.text)
            for fname in files:
                add_rel(fname)
            for folder in folders:
                sub = client.get(
                    f"{base}/prompt/{lang}/{folder}/",
                    headers={"Accept": "application/xml"},
                )
                if sub.status_code != 200:
                    continue
                sub_files, _ = parse_listing(sub.text)
                for fname in sub_files:
                    add_rel(f"{folder}/{fname}")
    except httpx.HTTPError as exc:
        logger.warning("list_prompt_files: %s", exc)
        return []

    seen2: set[str] = set()
    deduped2: list[dict[str, str]] = []
    for item in out:
        if item["relpath"] in seen2:
            continue
        seen2.add(item["relpath"])
        deduped2.append(item)
    deduped2.sort(key=lambda x: x["relpath"].lower())
    return deduped2


def download_prompt(settings: Settings, lang: str, relpath: str) -> tuple[bool, bytes, str]:
    lang = (lang or "").strip().strip("/")
    rel = (relpath or "").strip().lstrip("/").replace("\\", "/")
    if not lang or not rel:
        return False, b"", "chemin vide"
    if not settings.uccx_user or not settings.uccx_password:
        return False, b"", "UCCX_USER / UCCX_PASSWORD non configurés"
    url = f"{_base_url(settings)}/prompt/download/{lang}/{rel}"
    try:
        with _prompt_client(settings) as client:
            resp = client.get(url)
            if resp.status_code == 200 and resp.content:
                return True, resp.content, "ok"
            return False, b"", f"{resp.status_code}: {resp.text[:200]}"
    except httpx.HTTPError as exc:
        return False, b"", str(exc)


def _ensure_prompt_folder(client: httpx.Client, settings: Settings, lang: str, folder: str) -> None:
    folder = (folder or "").strip().strip("/")
    if not folder:
        return
    meta = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Files><Prompt><Folder><path>/{lang}/</path>"
        f"<FolderName>{folder}</FolderName></Folder></Prompt></Files>"
    )
    resp = client.post(
        f"{_base_url(settings)}/prompt",
        content=meta.encode("utf-8"),
        headers={"Accept": "application/xml", "Content-Type": "application/xml"},
    )
    # 201 created, 409 already exists
    if resp.status_code not in (200, 201, 204, 409):
        logger.warning("ensure prompt folder %s/%s: %s %s", lang, folder, resp.status_code, resp.text[:200])


def _commit_prompt_file(client: httpx.Client, settings: Settings, lang: str, relpath: str) -> tuple[bool, str]:
    """Commit le fichier temporaire uploadFile vers le repository (POST meta File)."""
    rel = relpath.strip().lstrip("/").replace("\\", "/")
    parts = rel.split("/")
    fname = parts[-1]
    parent = "/".join(parts[:-1])
    path = f"/{lang}/{parent}/" if parent else f"/{lang}/"
    meta = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Files><Prompt><File><path>{path}</path><FileName>{fname}</FileName></File></Prompt></Files>"
    )
    resp = client.post(
        f"{_base_url(settings)}/prompt",
        content=meta.encode("utf-8"),
        headers={"Accept": "application/xml", "Content-Type": "application/xml"},
    )
    if resp.status_code in (200, 201, 204):
        return True, "created"
    # 409 = déjà présent (contenu temp peut avoir été appliqué)
    if resp.status_code == 409:
        return True, "already-exists"
    logger.warning(
        "commit prompt %s/%s → %s %s",
        lang,
        rel,
        resp.status_code,
        (resp.text or "")[:300],
    )
    return False, f"create {resp.status_code}: {resp.text[:300]}"


def _prompt_file_exists_client(client: httpx.Client, settings: Settings, lang: str, rel: str) -> bool:
    url = f"{_base_url(settings)}/prompt/{lang}/{rel}"
    try:
        resp = client.get(url, headers={"Accept": "application/xml"})
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def upload_prompt_to_lang(
    settings: Settings,
    lang: str,
    relpath: str,
    wav_bytes: bytes,
    *,
    overwrite: bool = False,
) -> tuple[bool, str]:
    """Upload WAV vers /{lang}/{relpath} (uploadFile + commit File), avec verify + retry."""
    import time

    if not settings.uccx_user or not settings.uccx_password:
        return False, "UCCX_USER / UCCX_PASSWORD non configurés"
    lang = (lang or "").strip().strip("/")
    rel = (relpath or "").strip().lstrip("/").replace("\\", "/")
    if not lang or not rel or not rel.lower().endswith(".wav"):
        return False, "lang/relpath invalide"
    fname = rel.split("/")[-1]
    parent = "/".join(rel.split("/")[:-1])
    name_field = rel.replace("/", "\\")
    last_err = ""

    try:
        with _prompt_client(settings) as client:
            if parent:
                top = parent.split("/")[0]
                _ensure_prompt_folder(client, settings, lang, top)

            for attempt in range(1, 4):
                if overwrite or attempt > 1:
                    client.delete(
                        f"{_base_url(settings)}/prompt/{lang}/{rel}",
                        headers={"Accept": "application/xml"},
                    )

                up = client.post(
                    f"{_base_url(settings)}/prompt/uploadFile",
                    files={"file": (fname, wav_bytes, "audio/wav")},
                    data={"Language": lang, "Name": name_field},
                    headers={"Accept": "application/xml"},
                )
                if up.status_code not in (200, 201, 204):
                    last_err = f"uploadFile {up.status_code}: {up.text[:300]}"
                    logger.warning("upload attempt %s %s/%s: %s", attempt, lang, rel, last_err)
                    time.sleep(0.4 * attempt)
                    continue

                ok, msg = _commit_prompt_file(client, settings, lang, rel)
                if not ok:
                    last_err = msg
                    time.sleep(0.4 * attempt)
                    continue

                # Vérifier présence réelle
                if _prompt_file_exists_client(client, settings, lang, rel):
                    return True, f"OK /{lang}/{rel} (try {attempt})"

                last_err = f"commit OK mais fichier absent /{lang}/{rel}"
                logger.warning("%s (attempt %s)", last_err, attempt)
                time.sleep(0.5 * attempt)

            return False, last_err or f"échec upload /{lang}/{rel}"
    except httpx.HTTPError as exc:
        return False, str(exc)


def upload_prompt(
    settings: Settings,
    filename: str,
    wav_bytes: bytes,
    folder: str | None = None,
    lang: str = "default",
) -> tuple[bool, str, str]:
    """Upload sous /{lang}/{folder}/{filename} → playable nu folder/filename."""
    folder = (folder or "app").strip("\\/")
    fname = (filename or "").strip().lstrip("/")
    rel = f"{folder}/{fname}"
    ok, msg = upload_prompt_to_lang(settings, lang, rel, wav_bytes)
    if not ok:
        return False, msg, ""
    return True, msg, relpath_to_playable(rel)


def copy_prompt_to_lang(
    settings: Settings,
    source_lang: str,
    relpath: str,
    dest_lang: str,
    *,
    overwrite: bool = False,
) -> tuple[bool, str]:
    """Télécharge depuis source_lang et upload vers dest_lang (même relpath)."""
    ok, data, msg = download_prompt(settings, source_lang, relpath)
    if not ok:
        return False, f"download: {msg}"
    return upload_prompt_to_lang(
        settings, dest_lang, relpath, data, overwrite=overwrite
    )


def delete_prompt(settings: Settings, relative_path: str, lang: str | None = None) -> tuple[bool, str]:
    """DELETE /adminapi/prompt/{lang}/{relpath}. relative_path sans langue."""
    if not settings.uccx_user or not settings.uccx_password:
        return False, "UCCX_USER / UCCX_PASSWORD non configurés"
    rel = (relative_path or "").strip().lstrip("/").replace("\\", "/")
    lang = (lang or "").strip().strip("/")
    if not rel:
        return False, "chemin prompt vide"
    if not lang:
        return False, "langue manquante"
    url = f"{_base_url(settings)}/prompt/{lang}/{rel}"
    try:
        with _prompt_client(settings) as client:
            response = client.delete(url, headers={"Accept": "application/xml"})
            if response.status_code in (200, 201, 204, 404):
                return True, f"delete {response.status_code}"
            return False, f"{response.status_code}: {response.text[:400]}"
    except httpx.HTTPError as exc:
        return False, str(exc)


def prompt_exists_in_any_lang(settings: Settings, relpath: str, languages: list[str] | None = None) -> list[str]:
    """Retourne les langues où /{lang}/{relpath} existe."""
    langs = languages or list_prompt_languages(settings)
    found: list[str] = []
    for lang in langs:
        if prompt_file_exists(settings, lang, relpath):
            found.append(lang)
    return found


def resolve_upload_targets(
    lang: str,
    write_default: bool,
    settings: Settings,
    relpath: str,
) -> list[str]:
    """Cibles d'écriture : lang + seed default (si absent) ou overwrite si write_default."""
    lang = (lang or "").strip()
    targets = [lang]
    if lang != "default":
        default_exists = prompt_file_exists(settings, "default", relpath)
        # Toujours seed si default absent ; case « Aussi default » force aussi default
        if not default_exists or write_default:
            targets.append("default")
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
