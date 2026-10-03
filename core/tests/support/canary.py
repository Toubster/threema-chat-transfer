# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Canary scan (DESIGN §9, §10.2 point 4; owner: coreA). The canary values of fixtures/canaries.json go into the
synthetic inputs; afterwards every output is searched for them byte-wise: UTF-8, UTF-16 (LE/BE), Base64 (all three
alignments, standard and URL-safe alphabet) and, for identifier-like values, upper/lower case.

    hits = scan_bytes(data, values)                    # -> [(name, encoding)]
    hits = scan_tree(root, values, allow=("android/missing-senders.json",))   # -> [(relative path, name, enc)]

The scan never prints a matched value; a hit names the canary key, the encoding and the file only.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from . import canary_values

# identifiers may be upper-/lower-cased by tools (UDIDs in paths, serials); free text keeps its case
_CASE_VARIANTS = ("threema_id", "udid", "serial")


def _base64_forms(raw: bytes) -> set[bytes]:
    """Every byte string that must appear inside a Base64 text that contains `raw` at any offset (3 alignments,
    standard and URL-safe alphabet). Edge characters that depend on neighbour bytes are cut off."""
    out: set[bytes] = set()
    for shift in range(3):
        enc = base64.b64encode(b"\0" * shift + raw)
        # only the 4-character groups whose 3 bytes all belong to `raw` are independent of the neighbours
        core = enc[(4 if shift else 0):(shift + len(raw)) // 3 * 4]
        if len(core) >= 8:
            out.add(core)
            out.add(core.translate(bytes.maketrans(b"+/", b"-_")))
    return out


def encodings(value: str, *, name: str = "") -> dict[str, set[bytes]]:
    forms = {value}
    if name in _CASE_VARIANTS:
        forms |= {value.lower(), value.upper()}
    res: dict[str, set[bytes]] = {"utf8": set(), "utf16le": set(), "utf16be": set(), "base64": set()}
    for f in forms:
        u8 = f.encode("utf-8")
        res["utf8"].add(u8)
        res["utf16le"].add(f.encode("utf-16-le"))
        res["utf16be"].add(f.encode("utf-16-be"))
        res["base64"] |= _base64_forms(u8)
    return res


def compile_values(values: dict[str, str] | None = None) -> list[tuple[str, str, bytes]]:
    values = canary_values() if values is None else values
    out = []
    for name, value in values.items():
        for enc, forms in encodings(value, name=name).items():
            out += [(name, enc, f) for f in forms]
    return out


def scan_bytes(data: bytes, values: dict[str, str] | None = None, *,
               compiled: list[tuple[str, str, bytes]] | None = None) -> list[tuple[str, str]]:
    compiled = compiled if compiled is not None else compile_values(values)
    return sorted({(name, enc) for name, enc, needle in compiled if needle in data})


def scan_tree(root: Path, values: dict[str, str] | None = None, *, allow: tuple[str, ...] = (),
              only: tuple[str, ...] | None = None) -> list[tuple[str, str, str]]:
    """Scan every regular file below root (symlinks are not followed). `allow`: session keys that may hold the
    values (DESIGN §10.2: android/missing-senders.json). `only`: restrict to these top-level keys."""
    compiled = compile_values(values)
    hits = []
    root = Path(root)
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if rel in allow or p.is_symlink():
                continue
            if only is not None and not any(rel == o or rel.startswith(o.rstrip("/") + "/") for o in only):
                continue
            try:
                data = p.read_bytes()
            except OSError:
                continue
            hits += [(rel, name, enc) for name, enc in scan_bytes(data, compiled=compiled)]
    return hits
