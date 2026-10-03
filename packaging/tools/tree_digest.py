#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""tree_digest.py DIR [--list] -- one sha256 over (relative path, type, mode, content sha256 / link target) of every
file and symlink below DIR, sorted. Used to prove that running the engine never writes into the bundle
(DESIGN §14.1 step 6). --list prints one line per entry instead (to diff two states). Owner: pack."""
import hashlib
import os
import stat
import sys


def entries(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                yield f"L {rel} {os.readlink(p)}"
            elif stat.S_ISREG(st.st_mode):
                h = hashlib.sha256()
                with open(p, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                yield f"F {rel} {stat.S_IMODE(st.st_mode):o} {h.hexdigest()}"
        for d in dirnames:
            p = os.path.join(dirpath, d)
            if not os.path.islink(p):
                yield f"D {os.path.relpath(p, root)} {stat.S_IMODE(os.lstat(p).st_mode):o}"


if __name__ == "__main__":
    lines = sorted(entries(sys.argv[1]))
    if "--list" in sys.argv[2:]:
        print("\n".join(lines))
    else:
        print(hashlib.sha256("\n".join(lines).encode()).hexdigest())
