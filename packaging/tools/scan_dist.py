#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""scan_dist.py DIR [--xattrs] -- privacy checks of what a release ships (verify-bundle.sh). Owner: pack.

DIR is the app bundle or the mounted DMG volume. Prints rule and relative path only, never a matched value.

  build_machine   the builder's home folder, login name, full name, host names or this repository's path appear
                  in a file (values read from this Mac at run time; none of them lives in the repository)
  account_name    the repository account appears outside a https://github.com/<account>/... URL (owner rule: the
                  account name only ever appears in repository links; the account is read from NOTICE)
  xattr           (--xattrs) an extended attribute on any item, e.g. com.apple.provenance: it is machine-local and
                  links releases to the Mac that built them. A DMG must carry none (make-dmg.sh)

Exit 0 = clean, 1 = findings, 2 = usage error.
"""
from __future__ import annotations

import ctypes
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GENERIC_HOST_WORDS = {"mac", "macbook", "pro", "air", "mini", "studio", "imac", "s", "the", "local", "localhost",
                      "computer", "max", "ultra"}
GENERIC_LOGINS = {"admin", "user", "runner", "root", "test", "mac", "build", "ci"}


_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.listxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
_LIBC.listxattr.restype = ctypes.c_ssize_t
XATTR_NOFOLLOW = 0x0001


def xattr_names_size(p: Path) -> int:
    """byte size of the extended-attribute name list (0 = none); macOS listxattr(2), never follows symlinks"""
    n = _LIBC.listxattr(os.fsencode(p), None, 0, XATTR_NOFOLLOW)
    if n < 0:
        raise OSError(ctypes.get_errno(), "listxattr")
    return n


def _scutil(key: str) -> str:
    try:
        return subprocess.run(["scutil", "--get", key], capture_output=True, text=True, timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build_machine_needles() -> list[bytes]:
    needles: set[str] = set()
    home = os.path.expanduser("~")
    # on GitHub Actions the home folder is the generic, public runner home folder, which upstream wheels built on GitHub
    # runners embed themselves; there only the checkout path (REPO) identifies this build
    if home not in ("/", "/var/empty") and len(home) > 6 and os.environ.get("GITHUB_ACTIONS") != "true":
        needles.add(home.rstrip("/") + "/")
    needles.add(str(REPO) + "/")
    try:
        pw = pwd.getpwuid(os.getuid())
        login, full = pw.pw_name, (pw.pw_gecos or "").split(",")[0].strip()
    except KeyError:
        login, full = "", ""
    if len(login) >= 5 and login.lower() not in GENERIC_LOGINS:
        needles.add(login)
    if " " in full and len(full) >= 6:
        needles.add(full)
    for key in ("LocalHostName", "ComputerName", "HostName"):
        v = _scutil(key)
        words = [w for w in re.split(r"[\s'’._-]+", v.lower()) if w]
        if v and any(w not in GENERIC_HOST_WORDS for w in words):
            needles.add(v)
    return sorted({n.encode() for n in needles} | {n.encode("utf-16-le") for n in needles})


def repo_account() -> str:
    notice = REPO / "NOTICE"
    m = re.search(r"https://github\.com/([A-Za-z0-9-]+)/", notice.read_text(encoding="utf-8")) if notice.exists() else None
    return m.group(1) if m else ""


def account_outside_url(data: bytes, account: str) -> bool:
    if not account:
        return False
    for enc in ("latin-1", "utf-16-le"):
        low = data.decode(enc, errors="ignore").lower()
        acc = account.lower()
        i = low.find(acc)
        while i >= 0:
            if low[max(0, i - 11):i] != "github.com/":
                return True
            i = low.find(acc, i + 1)
    return False


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    root = Path(argv[0])
    want_xattrs = "--xattrs" in argv[1:]
    needles = build_machine_needles()
    account = repo_account()
    findings: list[tuple[str, str]] = []
    items = 0
    paths = [root]
    for dirpath, dirnames, filenames in os.walk(root):
        paths += [Path(dirpath) / n for n in dirnames + filenames]
    for p in paths:
        rel = str(p.relative_to(root)) or "."
        items += 1
        if want_xattrs:
            try:
                if xattr_names_size(p):
                    findings.append(("xattr", rel))
            except OSError:
                findings.append(("xattr_unreadable", rel))
        if p.is_symlink() or not p.is_file():
            continue
        data = p.read_bytes()
        if any(n in data for n in needles):
            findings.append(("build_machine", rel))
        if account_outside_url(data, account):
            findings.append(("account_name", rel))
    rules: dict[str, int] = {}
    for rule, rel in findings:
        rules[rule] = rules.get(rule, 0) + 1
    for rule, rel in findings[:40]:
        print(f"scan_dist: {rule}: {rel}", file=sys.stderr)
    if len(findings) > 40:
        print(f"scan_dist: ... {len(findings) - 40} more", file=sys.stderr)
    print(f"scan_dist: items={items} findings={len(findings)} rules={rules} account_check={'on' if account else 'off'}",
          file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
