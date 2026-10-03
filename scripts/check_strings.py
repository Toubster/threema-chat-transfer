#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
check_strings.py -- the app's string catalog is complete in German and English (DESIGN §5.5, §8.2).

Checks app/Resources/Localizable.xcstrings:
  * every code of core/schema/codes.v1.json has code.<CODE>.title|body|action, every action has action.<id>;
  * EVERY key (managed and app-owned) has a non-empty, 'translated' DE and EN value (DE is the source language);
  * placeholders {name} are the same set in DE and EN;
  * German uses "Sie" (no du/dich/dir/dein...), the product name is the placeholder {App} (never hard-coded);
  * the generated files are up to date (scripts/gen_codes.py --check).
Exit 0 = complete. Output names keys and rules, nothing else.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
XCSTRINGS = REPO / "app" / "Resources" / "Localizable.xcstrings"
CODES = REPO / "core" / "schema" / "codes.v1.json"
RX_PH = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
RX_DU = re.compile(r"\b(du|dich|dir|dein|deine|deinen|deinem|deiner|deines)\b")
HARD_NAMES = ("Chat Transfer",)      # the product name only via {App} (AppInfo.productName is the one place)


def value(entry: dict, lang: str) -> tuple[str | None, str | None]:
    unit = (entry.get("localizations", {}).get(lang) or {}).get("stringUnit") or {}
    return unit.get("value"), unit.get("state")


def main() -> int:
    errors: list[str] = []
    if not XCSTRINGS.exists():
        print("check_strings: app/Resources/Localizable.xcstrings missing (run scripts/gen_codes.py)", file=sys.stderr)
        return 1
    doc = json.loads(XCSTRINGS.read_text(encoding="utf-8"))
    if doc.get("sourceLanguage") != "de":
        errors.append("sourceLanguage must be 'de'")
    strings = doc.get("strings", {})
    cat = json.loads(CODES.read_text(encoding="utf-8"))
    required = [f"code.{c}.{p}" for c in cat["codes"] for p in ("title", "body", "action")]
    required += [f"action.{a}" for a in cat["actions"]]
    for k in required:
        if k not in strings:
            errors.append(f"{k}: missing")
    for k, entry in sorted(strings.items()):
        de, de_state = value(entry, "de")
        en, en_state = value(entry, "en")
        if not de or not de.strip():
            errors.append(f"{k}: empty German text")
        if not en or not en.strip():
            errors.append(f"{k}: empty English text")
        for lang, st in (("de", de_state), ("en", en_state)):
            if st not in (None, "translated"):
                errors.append(f"{k}: {lang} state {st!r} (must be translated)")
        if de and en and set(RX_PH.findall(de)) != set(RX_PH.findall(en)):
            errors.append(f"{k}: placeholders differ between DE and EN")
        if de and RX_DU.search(de):
            errors.append(f"{k}: German text uses 'du' -- use 'Sie' (DESIGN §8.2)")
        for txt in (de or "", en or ""):
            if any(n in txt for n in HARD_NAMES):
                errors.append(f"{k}: hard-coded product name -- use {{App}}")
                break
    gen = subprocess.run([sys.executable, str(REPO / "scripts" / "gen_codes.py"), "--check"], capture_output=True,
                         text=True)
    if gen.returncode != 0:
        errors.append("generated files stale: " + gen.stderr.strip().replace("\n", "; "))
    for e in errors:
        print(f"check_strings: {e}", file=sys.stderr)
    print(f"check_strings: {'PASS' if not errors else 'FAIL'} keys={len(strings)} required={len(required)} "
          f"errors={len(errors)}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
