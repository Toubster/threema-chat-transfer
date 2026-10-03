# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'dcim_unchanged' (DESIGN §6.1, new): the AFC listing of /DCIM (relative paths and sizes, in memory only)
equals the DCIM rows of the CameraRollDomain in the PRE backup -- directly before the send. A photo taken (or
deleted) after the PRE backup would otherwise be lost at the commit."""
from __future__ import annotations

import collections

from tmcore.guards import GuardResult


def _norm(rows) -> collections.Counter:
    c: collections.Counter[tuple[str, int]] = collections.Counter()
    for rel, size in rows:
        parts = [p for p in str(rel).strip("/").split("/") if p]
        if parts and parts[0].upper() == "DCIM":
            parts = parts[1:]
        if not parts or any(p.startswith(".") for p in parts):      # .MISC and other hidden helpers
            continue
        c[("/".join(parts).lower(), int(size))] += 1
    return c


def check(*, pre_rows, device_rows) -> GuardResult:
    a, b = _norm(pre_rows), _norm(device_rows)
    files = sum(b.values())
    if a == b:
        return GuardResult("dcim_unchanged", "pass", data={"files": files})
    return GuardResult("dcim_unchanged", "fail", "E_GUARD_DCIM_CHANGED",
                       data={"files": files, "added": sum((b - a).values()), "removed": sum((a - b).values())},
                       error_data={"files": files})
