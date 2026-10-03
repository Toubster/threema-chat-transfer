# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'photos_limit' (DESIGN §6.1, D8): CameraRollDomain bytes of the PRE backup <= 20 GB in v1.
At S03 only an estimate (warn), hard after the PRE backup."""
from __future__ import annotations

from tmcore.guards import GuardResult

LIMIT_BYTES = 20 * 10**9


def check(*, photos_bytes: int | None, estimate: bool = False) -> GuardResult:
    data = {"photos_bytes": photos_bytes, "limit_bytes": LIMIT_BYTES}
    if photos_bytes is None:
        return GuardResult("photos_limit", "skip" if estimate else "fail",
                           None if estimate else "E_GUARD_PHOTOS_LIMIT", data=data,
                           error_data={"limit_bytes": LIMIT_BYTES, "photos_bytes": 0})
    if photos_bytes <= LIMIT_BYTES:
        return GuardResult("photos_limit", "pass", data=data)
    return GuardResult("photos_limit", "warn" if estimate else "fail", "E_GUARD_PHOTOS_LIMIT", data=data)
