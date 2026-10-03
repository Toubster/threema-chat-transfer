#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""allowlist_block.py -- rewrite one managed block of scripts/scrub-allowlist.txt with the sha256 of the given files.

    allowlist_block.py --block "demo screenshots" docs/images/app/de/S00.png ...

The scrub check allows a binary file only when its sha256 is listed (DESIGN §10.3). Generated binaries (demo
screenshots) are listed in their own block between

    # BEGIN <block> ...
    # END <block>

The block must already exist (the allowlist belongs to the maintainer); this script never creates it and never touches
lines outside it. Prints counts only. Exit 2 when the block is missing.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALLOWLIST = REPO / "scripts" / "scrub-allowlist.txt"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--block", required=True)
    ap.add_argument("--allowlist", type=Path, default=ALLOWLIST)
    ap.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args(argv)
    lines = a.allowlist.read_text(encoding="utf-8").splitlines()
    begin = next((i for i, ln in enumerate(lines) if ln.startswith(f"# BEGIN {a.block}")), None)
    end = next((i for i, ln in enumerate(lines) if ln.startswith(f"# END {a.block}")), None)
    if begin is None or end is None or end < begin:
        print(f"allowlist_block: block '{a.block}' not found in {a.allowlist.name}; the maintainer adds it "
              "(devtools/CONTRACT-REQUESTS.md)", file=sys.stderr)
        return 2
    entries = []
    for f in sorted(a.files):
        rel = f.resolve().relative_to(REPO).as_posix()
        entries.append(f"sha256:{hashlib.sha256(f.read_bytes()).hexdigest()} {rel}")
    lines[begin + 1:end] = entries
    a.allowlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"allowlist_block: block '{a.block}' now lists {len(entries)} file(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
