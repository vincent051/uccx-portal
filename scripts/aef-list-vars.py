#!/usr/bin/env python3
"""Extrait des noms de variables candidats depuis un .aef UCCX (sérialisation Java).

Usage:
  python3 scripts/aef-list-vars.py /path/to/script.aef
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Mots / fragments typiques du runtime Cisco à ignorer
NOISE = {
    "true",
    "false",
    "null",
    "this",
    "super",
    "return",
    "String",
    "Integer",
    "Boolean",
    "Document",
    "Prompt",
    "User",
    "Contact",
    "Session",
    "java",
    "lang",
    "util",
    "com",
    "cisco",
    "Script",
    "Variable",
    "Expression",
    "Step",
    "Label",
    "Goto",
    "If",
    "Else",
    "End",
    "Call",
    "SubFlow",
    "Play",
    "Get",
    "Set",
    "Create",
    "Delete",
    "Accept",
    "Terminate",
}


def extract_strings(data: bytes) -> list[str]:
    # Chaînes Java sérialisées : length-prefixed UTF souvent visibles en ASCII
    raw = re.findall(rb"[\x20-\x7e]{3,80}", data)
    out: list[str] = []
    for b in raw:
        try:
            s = b.decode("ascii")
        except UnicodeDecodeError:
            continue
        out.append(s)
    return out


def is_var_candidate(s: str) -> bool:
    if s in NOISE or len(s) < 3:
        return False
    # Bruit sérialisation Java (suffixes q/t/w/Z/I fréquents)
    if re.search(r"[qtIwZ]$", s) and not re.match(
        r"^(Str_|str_|Bo_|bo_|int_|Int_|n_|N_|f_|F_|d_|D_)", s
    ):
        if not re.match(r"^(Str_|str_|Bo_|bo_|int_|Int_)", s):
            # garder seulement si préfixe métier clair
            pass
    # Préfixes UCCX / conventions i-hub
    if re.fullmatch(
        r"(Str_|str_|Bo_|bo_|int_|Int_|n_|f_|d_|GSM_|Calendar|Emergency|Closed|Open)[A-Za-z0-9_]{0,50}",
        s,
    ):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{2,60}", s):
        # Variables métier : CamelCase sans suffixes Java
        if s.endswith(("q", "t", "w", "Z", "I", "J", "L", "sq", "sr", "st", "xw")):
            return False
        if s[0].islower() and "_" in s:
            return True
        if re.match(r"^(Str_|str_|Bo_|bo_|int_|Int_|GSM_|Calendar)", s):
            return True
        # Identifiants exposés AppAdmin courants
        if s in {
            "pinCode",
            "Emergency",
            "Closed",
            "CalendarCustService",
            "int_Rona",
            "int_WaitingQueue",
            "str_CSQ_Display",
            "GSM_1",
            "GSM_2",
        }:
            return True
        if re.match(r"^(int_|str_|bo_)[A-Za-z0-9_]+$", s, re.I):
            return True
    return False


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: aef-list-vars.py <file.aef>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    data = path.read_bytes()
    if data[:1] in (b"<", b"{") or data[:4] == b"<!--":
        print(f"ERREUR: {path} n'est pas un .aef binaire (HTML/XML/JSON d'erreur?)", file=sys.stderr)
        return 1

    strings = extract_strings(data)
    vars_found: set[str] = set()
    for s in strings:
        if not is_var_candidate(s):
            continue
        # Nettoyer suffixes artefacts sérialisation Java (Str_WeekHoursw → Str_WeekHours)
        clean = re.sub(r"(xsq|sq|sr|st|xw|xpw)$", "", s)
        clean = re.sub(r"[wZItJL]$", "", clean) if re.match(
            r"^(Str_|str_|Bo_|bo_|int_|Int_)", clean
        ) else clean
        if len(clean) >= 3:
            vars_found.add(clean)
    vars_found = sorted(vars_found, key=str.lower)
    docs = sorted({s for s in strings if "DOC[" in s or s.endswith(".xml") or "http" in s.lower()})
    prompts = sorted({s for s in strings if "P[" in s or "SP[" in s or s.endswith(".wav")})

    print(f"# Fichier: {path} ({len(data)} octets)")
    print(f"# Chaînes brutes: {len(strings)} | candidats variables: {len(vars_found)}")
    print("\n## Variables candidates")
    for v in vars_found:
        print(v)
    if docs:
        print("\n## Documents / URL / XML")
        for d in docs:
            print(d)
    if prompts:
        print("\n## Prompts / wav")
        for p in prompts:
            print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
