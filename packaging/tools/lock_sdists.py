#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""lock_sdists.py -- maintainer tool: hash-pinned source distributions for packaging/source-bundle.lock.

    python3 packaging/tools/lock_sdists.py core/requirements.lock packaging/licenses.txt > packaging/source-bundle.lock.new

Selects every distribution of core/requirements.lock whose licence in packaging/licenses.txt is a GPL licence
(the components whose source the AGPL/GPL "Corresponding Source" must carry, DESIGN §11.1) and asks the PyPI JSON
API (network!) for the sdist of exactly the pinned version. Output lines: NAME VERSION SHA256 URL. Never downloads or
executes anything else. Owner: pack.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(lock: str) -> list[tuple[str, str]]:
    out = []
    for line in open(lock, encoding="utf-8"):
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def gpl(licenses: str) -> set[str]:
    names = set()
    for raw in open(licenses, encoding="utf-8"):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("ALLOW ", "@runtime ")):
            continue
        name, expr = re.split(r"\s+", line, maxsplit=1)
        if "GPL" in expr:
            names.add(canon(name))
    return names


def main() -> int:
    wanted = gpl(sys.argv[2])
    errors = 0
    for name, ver in pins(sys.argv[1]):
        if canon(name) not in wanted:
            continue
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=30) as r:
            data = json.load(r)
        sd = [f for f in data["urls"] if f["packagetype"] == "sdist"]
        if len(sd) != 1:
            print(f"ERROR {name}=={ver}: {len(sd)} sdists on PyPI", file=sys.stderr)
            errors += 1
            continue
        print(f"{canon(name)} {ver} {sd[0]['digests']['sha256']} {sd[0]['url']}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
