#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""find_macho.py DIR -- every Mach-O file below DIR (thin or fat; symlinks skipped), deepest path first, one per line.
Used by sign-adhoc.sh (inside-out signing, DESIGN §14.1 step 5), build-core.sh (thin to arm64) and verify-bundle.sh.
Owner: pack."""
import os
import struct
import sys

THIN = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"}           # MH_MAGIC_64 / MH_MAGIC (little endian)
FAT = {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}            # FAT_MAGIC / FAT_MAGIC_64 (big endian)


def is_macho(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return False
    if head[:4] in THIN:
        return True
    if head[:4] in FAT and len(head) == 8:
        n = struct.unpack(">I", head[4:8])[0]
        return 0 < n < 16                                      # Java class files share 0xcafebabe
    return False


def main() -> int:
    hits = []
    for dirpath, _dirs, files in os.walk(sys.argv[1]):
        for name in files:
            p = os.path.join(dirpath, name)
            if not os.path.islink(p) and is_macho(p):
                hits.append(p)
    hits.sort(key=lambda p: (-p.count(os.sep), p))
    print("\n".join(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())
