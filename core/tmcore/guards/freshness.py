# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'freshness' (DESIGN §6.1): the PRE backup is at most 60 min old when the restore starts and right before
the first byte is sent. The rollback window (6 h) is internal to rollback-threema and has its own check id."""
from __future__ import annotations

from tmcore.guards import GuardResult

LIMIT_MIN = 60
FUTURE_TOLERANCE_MIN = 5


def check(*, age_min: int, limit_min: int = LIMIT_MIN, check_id: str = "freshness",
          code: str = "E_GUARD_FRESHNESS") -> GuardResult:
    data: dict[str, int | str] = {"age_min": int(age_min), "limit_min": int(limit_min)}
    if -FUTURE_TOLERANCE_MIN <= age_min <= limit_min:
        return GuardResult(check_id, "pass", data=data)
    if age_min < -FUTURE_TOLERANCE_MIN:
        data["sub"] = "future"                  # the clock moved backwards: refuse like a stale backup
    return GuardResult(check_id, "fail", code, data=data,
                       error_data={"age_min": max(0, int(age_min)), "limit_min": int(limit_min)})
