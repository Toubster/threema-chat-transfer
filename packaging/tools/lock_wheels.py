#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""lock_wheels.py -- maintainer tool: turn exact pins into hash-pinned lines for core/requirements.lock.

    python3 packaging/tools/lock_wheels.py packaging/runtime-pins.txt > core/requirements.lock.new

For every `name==version` it asks the PyPI JSON API (network!) for the files of exactly that release and keeps the
wheels the bundled runtime can install: CPython 3.13 (cp313, abi3 or pure py313/py3/py2.py3) on macOS arm64
(macosx_*_arm64 / universal2 with a minimum macOS <= 14.0) or "any". Every wheel hash of the release that fits is
written (pip picks one; all are acceptable). A release without such a wheel is reported as an error, because the
bundle is built with `--only-binary :all:` (DESIGN §3.3); such packages go to packaging/sdist-vendor.lock instead.
Prints only package names, versions and hashes.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

MACOS_MAX = (14, 0)
RX_PLAT = re.compile(r"macosx_(\d+)_(\d+)_(arm64|universal2)$")


def fits(fn: str) -> bool:
    if not fn.endswith(".whl"):
        return False
    parts = fn[:-4].split("-")
    py, abi, plats = parts[-3], parts[-2], parts[-1]
    pys = set(py.split("."))
    if not ({"cp313", "py313", "py3", "py2"} & pys or (abi == "abi3" and any(p.startswith("cp3") for p in pys))):
        return False
    if abi not in ("none", "abi3", "cp313"):
        return False
    for plat in plats.split("."):
        if plat == "any":
            return True
        m = RX_PLAT.match(plat)
        if m and (int(m.group(1)), int(m.group(2))) <= MACOS_MAX:
            return True
    return False


def main() -> int:
    pins = []
    for line in open(sys.argv[1], encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line:
            name, ver = line.split("==")
            pins.append((name.strip(), ver.strip()))
    errors = 0
    out = []
    for name, ver in pins:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=30) as r:
            data = json.load(r)
        files = [f for f in data["urls"] if fits(f["filename"])]
        if not files:
            print(f"ERROR no compatible wheel: {name}=={ver}", file=sys.stderr)
            errors += 1
            continue
        hashes = sorted({f["digests"]["sha256"] for f in files})
        out.append(f"{data['info']['name']}=={ver} \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes))
    print("\n".join(out))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
