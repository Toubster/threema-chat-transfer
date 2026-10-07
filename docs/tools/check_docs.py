#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""check_docs.py -- consistency checks for the public documentation (owner: docs). Runs in ci.yml "lint".

    check_docs.py                         guides, links, screenshot names (missing image files are only counted)
    check_docs.py --require-images app    additionally every referenced image of these groups must exist (release)
    check_docs.py --screenshots-dir DIR   DIR/<lang>/<id>.png exists for every app screenshot (demo-screenshots.yml)

Checks:
  1. docs/images/screenshots.json is well-formed: unique ids, known groups, app scenarios are DESIGN §13.3 names.
  2. Every image in docs/user/<lang>/README.md is docs/images/<group>/<lang>/<id>.png with <id> from the list and the
     guide's own language; every app screenshot appears in both guides.
  3. Both guides have the same chapter and section structure (number of "## " and "### " headings).
  4. Relative links in README*.md and docs/**/*.md point to existing files, and "#anchor" links to existing headings
     (GitHub slug rules).
Prints counts and file:line of problems only. Exit 0 = clean, 1 = problems.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIST = REPO / "docs" / "images" / "screenshots.json"
GUIDES = {lang: REPO / "docs" / "user" / lang / "README.md" for lang in ("de", "en")}
# DESIGN §13.3 scenario names (mock and fake device use the same names) + wrong_android_password (app/Tests/Scenarios)
SCENARIOS = {
    "happy", "happy_with_notes", "first_backup_dropped", "find_my_on", "airplane_off", "freshness_expired",
    "dcim_changed", "ios_unknown", "threema_missing", "threema_model_unknown", "id_mismatch", "wrong_backup_password",
    "iphone_space_low", "photos_limit", "link_lost_after_send", "app_crash_after_send", "threema_only_fail",
    "data_fail", "keychain_fail", "setup_full", "restore_state", "rollback_threema_ok", "android_two_backups",
    "android_incomplete", "android_format_new", "duplicate_chat", "wrong_android_password",
}
RX_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
RX_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
RX_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RX_FENCE = re.compile(r"^\s*```")


class Problems:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, where: str, what: str) -> None:
        self.items.append(f"{where}: {what}")


def slug(text: str) -> str:
    """GitHub heading anchor: lower case, drop punctuation except '-' and '_', spaces -> '-'."""
    text = unicodedata.normalize("NFC", text.replace("`", "")).lower()
    out = []
    for ch in text:
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch == " ":
            out.append("-")
    return "".join(out)


def md_lines(path: Path):
    """(line number, text) outside fenced code blocks."""
    fenced = False
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if RX_FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield no, line


def anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    for _, line in md_lines(path):
        m = RX_HEADING.match(line)
        if not m:
            continue
        s = slug(m.group(2))
        n = seen.get(s, 0)
        out.add(s if n == 0 else f"{s}-{n}")
        seen[s] = n + 1
    return out


def load_list(p: Problems) -> dict:
    data = json.loads(LIST.read_text(encoding="utf-8"))
    ids: dict[str, set[str]] = {}
    for group, g in data["groups"].items():
        seen = set()
        for item in g["items"]:
            if item["id"] in seen:
                p.add(LIST.name, f"duplicate id {group}/{item['id']}")
            seen.add(item["id"])
            if group == "app" and item.get("scenario") not in SCENARIOS:
                p.add(LIST.name, f"app/{item['id']}: unknown scenario {item.get('scenario')!r}")
        ids[group] = seen
    data["_ids"] = ids
    return data


def check_guides(data: dict, p: Problems, require: set[str]) -> int:
    missing_files = 0
    structure = {}
    for lang, guide in GUIDES.items():
        rel = guide.relative_to(REPO).as_posix()
        if not guide.exists():
            p.add(rel, "missing")
            continue
        used: dict[str, set[str]] = {g: set() for g in data["_ids"]}
        h2 = h3 = 0
        for no, line in md_lines(guide):
            if line.startswith("## "):
                h2 += 1
            elif line.startswith("### "):
                h3 += 1
            for target in RX_IMAGE.findall(line):
                path = (guide.parent / target).resolve()
                try:
                    parts = path.relative_to(REPO / "docs" / "images").parts
                except ValueError:
                    p.add(f"{rel}:{no}", f"image outside docs/images: {target}")
                    continue
                if len(parts) != 3 or not parts[2].endswith(".png"):
                    p.add(f"{rel}:{no}", f"image path not <group>/<lang>/<id>.png: {target}")
                    continue
                group, img_lang, name = parts[0], parts[1], parts[2][:-4]
                if group not in data["_ids"] or name not in data["_ids"][group]:
                    p.add(f"{rel}:{no}", f"image not in screenshots.json: {group}/{name}")
                    continue
                if img_lang != lang:
                    p.add(f"{rel}:{no}", f"image of language {img_lang} in the {lang} guide")
                used[group].add(name)
                if not path.exists():
                    missing_files += 1
                    if group in require:
                        p.add(f"{rel}:{no}", f"image file missing: {group}/{img_lang}/{name}.png")
        for name in sorted(data["_ids"]["app"] - used["app"]):
            p.add(rel, f"app screenshot never shown: {name}")
        structure[lang] = (h2, h3)
    if len(set(structure.values())) > 1:
        p.add("docs/user", f"guides differ in structure (## / ### headings): {structure}")
    return missing_files


def check_links(p: Problems) -> int:
    files = sorted(REPO.glob("README*.md")) + sorted((REPO / "docs").rglob("*.md"))
    n = 0
    for f in files:
        rel = f.relative_to(REPO).as_posix()
        for no, line in md_lines(f):
            for target in RX_LINK.findall(line) + RX_IMAGE.findall(line):
                if re.match(r"^[a-z]+:", target) or target.startswith("<"):
                    continue
                n += 1
                path_part, _, frag = target.partition("#")
                if path_part.startswith(("../../releases", "../../attestations")):   # GitHub UI routes from the repo root
                    continue
                dest = (f.parent / path_part).resolve() if path_part else f
                if "/images/" in target and target.endswith(".png"):
                    continue                                   # images: checked by check_guides
                if not dest.exists():
                    p.add(f"{rel}:{no}", f"broken link: {target}")
                    continue
                if frag and dest.suffix == ".md" and frag not in anchors(dest):
                    p.add(f"{rel}:{no}", f"missing anchor: {target}")
    return n


def check_dir(data: dict, shots: Path, p: Problems) -> None:
    for lang in data["languages"]:
        for name in sorted(data["_ids"]["app"]):
            if not (shots / lang / f"{name}.png").is_file():
                p.add(str(shots), f"missing {lang}/{name}.png")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--require-images", nargs="*", default=[], metavar="GROUP")
    ap.add_argument("--screenshots-dir", type=Path)
    a = ap.parse_args(argv)
    p = Problems()
    data = load_list(p)
    if a.screenshots_dir:
        check_dir(data, a.screenshots_dir, p)
    missing = check_guides(data, p, set(a.require_images))
    links = check_links(p)
    for item in p.items:
        print(item, file=sys.stderr)
    state = "CLEAN" if not p.items else "PROBLEMS"
    print(f"check_docs: {state} problems={len(p.items)} links={links} images_not_generated_yet={missing}",
          file=sys.stderr)
    return 1 if p.items else 0


if __name__ == "__main__":
    sys.exit(main())
