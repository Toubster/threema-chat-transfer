#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""source_bundle.py OUT.tar.gz PREFIX GIT_ARCHIVE.tar SDIST_DIR REFERENCES.txt -- deterministic source bundle
(DESIGN §11.1): PREFIX/project/ (git archive of the release commit), PREFIX/sdists/ (hash-checked sdists),
PREFIX/REFERENCES.txt (python-build-standalone, Threema iOS model source) and PREFIX/SHA256SUMS over all of it.
Fixed mtime (SOURCE_DATE_EPOCH or 0), uid/gid 0, sorted entries, so the same inputs give the same bytes.
Prints counts only. Owner: pack.
"""
import gzip
import hashlib
import io
import os
import sys
import tarfile

out, prefix, git_tar, sdist_dir, refs = sys.argv[1:6]
MTIME = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
entries: list[tuple[str, bytes, int]] = []           # (path inside PREFIX, data, mode)

with tarfile.open(git_tar) as t:
    for m in t.getmembers():
        if m.isfile():
            entries.append((f"project/{m.name}", t.extractfile(m).read(), 0o755 if m.mode & 0o111 else 0o644))
        elif m.issym():
            sys.exit(f"source_bundle: symlink in the project tree ({m.name}) -- not supported")
for n in sorted(os.listdir(sdist_dir)):
    with open(os.path.join(sdist_dir, n), "rb") as f:
        entries.append((f"sdists/{n}", f.read(), 0o644))
with open(refs, "rb") as f:
    entries.append(("REFERENCES.txt", f.read(), 0o644))
entries.sort()
sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {p}\n" for p, d, _ in entries).encode()
entries.append(("SHA256SUMS", sums, 0o644))

raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as t:
    dirs = sorted({"/".join(p.split("/")[:i]) for p, _, _ in entries for i in range(1, p.count("/") + 1)})
    for d in [""] + dirs:
        ti = tarfile.TarInfo(f"{prefix}/{d}".rstrip("/"))
        ti.type, ti.mode, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = tarfile.DIRTYPE, 0o755, MTIME, 0, 0, "", ""
        t.addfile(ti)
    for p, data, mode in entries:
        ti = tarfile.TarInfo(f"{prefix}/{p}")
        ti.size, ti.mode, ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = len(data), mode, MTIME, 0, 0, "", ""
        t.addfile(ti, io.BytesIO(data))
with open(out, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=MTIME, filename="") as g:
    g.write(raw.getvalue())
n_proj = sum(p.startswith("project/") for p, _, _ in entries)
n_sd = sum(p.startswith("sdists/") for p, _, _ in entries)
print(f"[source_bundle] {n_proj} project files, {n_sd} sdists, {os.path.getsize(out) // 1024} KiB", file=sys.stderr)
