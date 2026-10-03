#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
canary_scan.py -- privacy canary scan (DESIGN §10.2 point 4). Owner: coreB.

    python3 scripts/canary_scan.py [--canaries fixtures/canaries.json] DIR [DIR ...]

The synthetic fixtures and the virtual iPhone carry canary values (contact name, group name, message text, Threema ID
ZZCANARY, device name, UDID, serial, e-mail, password). After a fixture E2E run, NONE of them may appear in anything
the engine emits or keeps as a report: events (the recorded stdout, *.jsonl), logs (debug/stderr logs, *.log),
reports/*.json, session.json, engine.json and diag/ of every session. Each value is searched byte-wise as UTF-8,
UTF-16-LE, UTF-16-BE and Base64 (all three alignments), case-sensitive.

Not scanned, by design (they ARE the user's data or the device): inside a session folder ios/ (encrypted backups),
work/ (decrypted Threema store, restore set), android/ (normalized chats; android/missing-senders.json is the one
file the UI may show IDs from), fake-iphone/ (the virtual iPhone's own state); outside sessions the fixture inputs
(*.zip Android backups, src-media/). Exit 1 on a hit (prints file + canary NAME, never the value), 0 otherwise.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SESSION_SKIP = ("ios", "work", "android", "fake-iphone")
OUTSIDE_SKIP_SUFFIXES = (".zip", ".sqlite", ".sqlite-wal", ".sqlite-shm", ".png", ".jpg", ".jpeg", ".gif", ".mp4",
                         ".m4a", ".pdf", ".heic", ".vcf", ".bin")
OUTSIDE_SKIP_DIRS = ("src-media", "__pycache__")


def load_canaries(path: Path) -> dict[str, str]:
    c = json.loads(path.read_text(encoding="utf-8"))
    out = {k: v for k, v in c.items() if isinstance(v, str) and k not in ("about", "udid_note")}
    if isinstance(c.get("udid_parts"), list):
        out["udid"] = "-".join(c["udid_parts"])
    return out


def needles(values: dict[str, str]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for name, v in values.items():
        if len(v) < 4:
            continue
        raw = v.encode("utf-8")
        out += [(name, raw), (name, v.encode("utf-16-le")), (name, v.encode("utf-16-be"))]
        for shift, drop in ((0, 0), (1, 2), (2, 3)):    # the value starting at byte 0, 1 or 2 of a 3-byte group
            b64 = base64.b64encode(b"\0" * shift + raw)[drop:]
            core = b64.rstrip(b"=")[:-4]                 # the last group depends on what follows the value
            if len(core) >= 6:
                out.append((name, core))
    return out


def is_session(d: Path) -> bool:
    return (d / "session.json").is_file() and (d / "work").is_dir()


def files_to_scan(root: Path):
    if root.is_file():
        yield root
        return
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        sess = is_session(d)
        for p in entries:
            if p.is_symlink():
                continue
            if p.is_dir():
                if (sess and p.name in SESSION_SKIP) or p.name in OUTSIDE_SKIP_DIRS:
                    continue
                stack.append(p)
            elif p.is_file():
                if not sess and p.suffix.lower() in OUTSIDE_SKIP_SUFFIXES:
                    continue
                yield p


def scan(roots: list[Path], values: dict[str, str]) -> list[tuple[Path, str]]:
    ns = needles(values)
    hits: list[tuple[Path, str]] = []
    for root in roots:
        for p in files_to_scan(root):
            try:
                data = p.read_bytes()
            except OSError:
                continue
            seen = set()
            for name, n in ns:
                if name not in seen and n in data:
                    seen.add(name)
                    hits.append((p, name))
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--canaries", type=Path, default=REPO / "fixtures" / "canaries.json")
    ap.add_argument("dirs", nargs="+", type=Path)
    a = ap.parse_args(argv)
    hits = scan(a.dirs, load_canaries(a.canaries))
    for p, name in hits:
        print(f"CANARY {name}: {p}")
    print(f"canary scan: {len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
