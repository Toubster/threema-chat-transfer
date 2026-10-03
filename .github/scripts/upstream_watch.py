#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""upstream_watch.py -- weekly look at the three upstreams Chat Transfer for Threema depends on; opens one issue per finding.

    upstream_watch.py ios-builds        new iOS builds in blacktop/ipsw-diffs that compat/ios.json does not list
    upstream_watch.py threema-ios       new Threema iOS tags: Core Data model source unchanged or changed
    upstream_watch.py threema-android   new Threema Android tag: backup format version vs compat/android.json
    options: --dry-run (print the issues instead of creating them), --max-issues N (default 10)

Reads public data only (GitHub API, git ls-remote, a sparse/blobless git fetch). Creates issues with the GitHub CLI
(`gh`, token from GH_TOKEN). Never changes code, compat lists or releases; deduplicates by exact issue title.
DESIGN §6.2-§6.4, §12; triage: docs/MAINTAINER.md §8.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
DIFFS_REPO = "blacktop/ipsw-diffs"
THREEMA_IOS = "https://github.com/threema-ch/threema-ios.git"
THREEMA_ANDROID = "https://github.com/threema-ch/threema-android.git"
ANDROID_VERSION_FILE = "app/src/main/java/ch/threema/app/backuprestore/csv/RestoreSettings.java"
RX_ANDROID_VERSION = re.compile(r"\bCURRENT_VERSION\s*=\s*(\d+)\s*;")
RX_DIFF_DIR = re.compile(r"^(?P<from>.+?)_+vs_(?P<ver>\d+(?:_\d+)*)_(?P<build>\d{2}[A-Z]\d+[a-z]?)$")
RX_IOS_TAG = re.compile(r"^(?P<ver>\d+(?:\.\d+)*)b(?P<build>\d+)$")
RX_ANDROID_TAG = re.compile(r"^(?P<ver>\d+(?:\.\d+)*)-(?P<build>\d+)$")
KEYWORDS = re.compile(r"(?i)annotat|RemoveOnRestore|NotRestored|AlwaysRemove|RemoveItemsNotRestored|validate backup")
LABEL = "upstream-watch"


# ---------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------
def http_get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    headers = {"Accept": accept, "User-Agent": "threema-chat-transfer-upstream-watch"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(API):
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        return r.read()


def http_get_json(url: str):
    return json.loads(http_get(url))


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def ls_remote_tags(url: str) -> dict[str, str]:
    """tag -> commit (annotated tags dereferenced)."""
    tags: dict[str, str] = {}
    for line in git("ls-remote", "--tags", url).splitlines():
        sha, ref = line.split("\t", 1)
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            tags[name[:-3]] = sha
        else:
            tags.setdefault(name, sha)
    return tags


def kv_file(path: Path) -> dict[str, str]:
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class Issues:
    def __init__(self, dry_run: bool, max_issues: int):
        self.dry_run, self.max_issues, self.created = dry_run, max_issues, 0
        self._titles: set[str] | None = None

    def _existing(self) -> set[str]:
        if self._titles is None:
            if self.dry_run:
                self._titles = set()
            else:
                out = subprocess.run(["gh", "issue", "list", "--state", "all", "--label", LABEL, "--limit", "500",
                                      "--json", "title"], check=True, capture_output=True, text=True).stdout
                self._titles = {i["title"] for i in json.loads(out)}
        return self._titles

    def open(self, title: str, body: str, kind: str) -> None:
        if title in self._existing():
            print(f"exists: {title}")
            return
        if self.created >= self.max_issues:
            print(f"limit reached, skipped: {title}")
            return
        self.created += 1
        if self.dry_run:
            print(f"--- would open: {title} [{LABEL}, {kind}]\n{body}\n")
            return
        for label in (LABEL, kind):
            subprocess.run(["gh", "label", "create", label, "--force", "--color", "ededed"], check=False,
                           capture_output=True)
        subprocess.run(["gh", "issue", "create", "--title", title, "--body", body, "--label", LABEL, "--label", kind],
                       check=True)
        self._existing().add(title)
        print(f"opened: {title}")


# ---------------------------------------------------------------------------------------------------------------
# iOS builds
# ---------------------------------------------------------------------------------------------------------------
def _vtuple(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.split(r"[._]", s) if x)


def ios_builds(issues: Issues) -> int:
    compat = json.loads((REPO / "compat" / "ios.json").read_text(encoding="utf-8"))
    known = {b["build"] for b in compat["builds"]}
    newest = max((_vtuple(b["ios"]) for b in compat["builds"]), default=(0,))
    dirs = [e["name"] for e in http_get_json(f"{API}/repos/{DIFFS_REPO}/contents/iOS") if e.get("type") == "dir"]
    by_build: dict[str, dict] = {}
    for d in dirs:
        m = RX_DIFF_DIR.match(d)
        if not m:
            continue
        ver, build = _vtuple(m["ver"]), m["build"]
        beta = build[-1].islower()
        if build in known:
            continue
        # newer versions (betas included: canary runs start in June), or a release build of the newest known version
        if not (ver > newest or (ver == newest and not beta)):
            continue
        entry = by_build.setdefault(build, {"ver": ".".join(map(str, ver)), "beta": beta, "dirs": []})
        entry["dirs"].append(d)
    for build, e in sorted(by_build.items(), key=lambda kv: (_vtuple(kv[1]["ver"]), kv[0])):
        lines = [f"`upstream-watch` found iOS **{e['ver']} ({build})**{' (beta)' if e['beta'] else ''}, which "
                 "`compat/ios.json` does not list. Until it is `verified`, Chat Transfer for Threema refuses restores on it "
                 "(fail-closed).", "", "Diffs:"]
        for d in e["dirs"]:
            lines.append(f"- https://github.com/{DIFFS_REPO}/tree/main/iOS/{d}")
            path = f"iOS/{d}/MACHOS/filesystem/usr/libexec/BackupAgent2.md"
            try:
                text = http_get(f"{RAW}/{DIFFS_REPO}/main/{path}", accept="text/plain").decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 -- a missing diff file is a normal case
                lines.append("  - `BackupAgent2`: no diff file (unchanged or not published yet)")
                continue
            hits = [ln.strip() for ln in text.splitlines() if ln.lstrip()[:1] in "+-" and KEYWORDS.search(ln)]
            lines.append(f"  - `BackupAgent2`: https://github.com/{DIFFS_REPO}/blob/main/{path} "
                         f"({len(hits)} changed lines about annotation/restore)")
            if hits:
                lines += ["", "```diff", *hits[:40], "```", ""]
        lines += ["", "Maintainer checklist (docs/MAINTAINER.md §3):",
                  "- [ ] read the `BackupAgent2` / `MobileBackup.framework` / `Domains.plist` changes",
                  "- [ ] C0 on the test device", "- [ ] C3 ×2 with the release candidate",
                  "- [ ] record `compat/records/ios/<build>.json` (counts only), update `compat/ios.json`, release"]
        issues.open(f"iOS {e['ver']} ({build}): new build, not in compat/ios.json", "\n".join(lines), "compat-ios")
    print(f"ios-builds: {len(by_build)} candidate build(s)")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# Threema iOS model
# ---------------------------------------------------------------------------------------------------------------
def threema_ios(issues: Issues) -> int:
    models = json.loads((REPO / "compat" / "threema-ios.json").read_text(encoding="utf-8"))["models"]
    sources = sorted((REPO / "model").glob("*/SOURCE"))
    if not sources:
        print("threema-ios: no model/*/SOURCE file", file=sys.stderr)
        return 2
    newest_src = kv_file(sources[-1])
    pinned_commit, model_path = newest_src["COMMIT"], newest_src["XCDATAMODELD"]
    tags = ls_remote_tags(THREEMA_IOS)
    parsed = {t: int(m["build"]) for t in tags if (m := RX_IOS_TAG.match(t))}
    pinned_tags = [t for t, c in tags.items() if c == pinned_commit and t in parsed]
    if not pinned_tags:
        print("threema-ios: pinned commit has no release tag upstream", file=sys.stderr)
        return 2
    base = max(parsed[t] for t in pinned_tags)
    new = sorted((t for t, b in parsed.items() if b > base), key=lambda t: parsed[t])
    if not new:
        print("threema-ios: no tag newer than the pinned model source")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        g = Path(tmp)
        git("init", "-q", cwd=g)
        git("remote", "add", "origin", THREEMA_IOS, cwd=g)
        git("fetch", "-q", "--filter=blob:none", "--depth", "1", "origin", pinned_commit,
            *[f"refs/tags/{t}:refs/tags/{t}" for t in new], cwd=g)
        base_tree = git("rev-parse", f"{pinned_commit}:{model_path}", cwd=g).strip()
        for t in new:
            tree = git("rev-parse", f"refs/tags/{t}^{{commit}}:{model_path}", cwd=g).strip()
            ids = ", ".join(m["id"] for m in models)
            if tree == base_tree:
                body = (f"Threema iOS tag `{t}` has the **same** Core Data model source as the bundled model ({ids}): "
                        f"`{model_path}` is tree-identical to the pinned commit. Newer Threema versions with an "
                        "identical model hash are allowed (docs/COMPAT-POLICY.md §2) -- usually nothing to do. "
                        "Close after a glance at the release notes.")
                issues.open(f"Threema iOS {t}: model unchanged", body, "compat-threema")
            else:
                changed = git("diff", "--name-only", pinned_commit, f"refs/tags/{t}", "--", model_path, cwd=g)
                body = (f"Threema iOS tag `{t}` **changed** the Core Data model source `{model_path}` compared with the "
                        f"pinned commit of the bundled model ({ids}). Chat Transfer for Threema refuses unknown model hashes "
                        "(`E_THREEMA_MODEL_UNKNOWN`), so users on this Threema version are blocked until a new "
                        "release.\n\nChanged files:\n```\n" + changed.strip()[:3000] + "\n```\n\n"
                        "Next steps (docs/MAINTAINER.md §4.2): compile with `model/build-momd.sh`, compare the "
                        "version hashes, review the importer mapping, tests, simulator gallery, C3, release.")
                issues.open(f"Threema iOS {t}: model changed", body, "compat-threema")
    return 0


# ---------------------------------------------------------------------------------------------------------------
# Threema Android backup format
# ---------------------------------------------------------------------------------------------------------------
def threema_android(issues: Issues) -> int:
    compat = json.loads((REPO / "compat" / "android.json").read_text(encoding="utf-8"))
    known = int(compat["max_known_format"])
    tags = ls_remote_tags(THREEMA_ANDROID)
    parsed = {t: int(m["build"]) for t in tags if (m := RX_ANDROID_TAG.match(t))}
    if not parsed:
        print("threema-android: no release tags found", file=sys.stderr)
        return 2
    latest = max(parsed, key=lambda t: parsed[t])
    with tempfile.TemporaryDirectory() as tmp:
        g = Path(tmp) / "src"
        git("clone", "-q", "--depth", "1", "--branch", latest, "--filter=blob:none", "--sparse", THREEMA_ANDROID, str(g))
        git("sparse-checkout", "set", "--no-cone", ANDROID_VERSION_FILE, cwd=g)
        f = g / ANDROID_VERSION_FILE
        m = RX_ANDROID_VERSION.search(f.read_text(encoding="utf-8")) if f.exists() else None
    if m is None:
        issues.open(f"Threema Android {latest}: backup format version not found",
                    f"`{ANDROID_VERSION_FILE}` or its `CURRENT_VERSION` constant is missing in tag `{latest}`. "
                    "Find the new location and update `.github/scripts/upstream_watch.py`.", "compat-android")
        return 0
    found = int(m.group(1))
    print(f"threema-android: {latest} writes backup format {found}, compat knows up to {known}")
    if found > known:
        issues.open(f"Threema Android {latest}: backup format {found}",
                    f"Threema for Android `{latest}` writes data backups in format **{found}** "
                    f"(`RestoreSettings.CURRENT_VERSION`); `compat/android.json` knows up to {known}. Backups from "
                    "this version are refused (`E_ANDROID_FORMAT_NEW`) until a new release.\n\n"
                    "Next steps (docs/MAINTAINER.md §4.3): read the format change in the Android source, extend the "
                    "normalizer and the fixture generator, verify with a fixture and a real test backup, release.",
                    "compat-android")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("ios-builds", "threema-ios", "threema-android"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-issues", type=int, default=10)
    a = ap.parse_args(argv)
    issues = Issues(a.dry_run, a.max_issues)
    return {"ios-builds": ios_builds, "threema-ios": threema_ios, "threema-android": threema_android}[a.what](issues)


if __name__ == "__main__":
    sys.exit(main())
