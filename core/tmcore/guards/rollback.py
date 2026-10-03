# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'rollback' (DESIGN §6.1, §8.5 R1): rollback-threema only after the FINAL restore, only for the verdict
threema_only, only with the PRE backup of that restore, only once, at most 6 h after the PRE backup."""
from __future__ import annotations

from tmcore.guards import GuardResult

WINDOW_MIN = 6 * 60


def allowed(*, last_kind: str | None, verdict: str | None, pre_matches: bool, used: bool) -> GuardResult:
    if last_kind == "final" and verdict == "threema_only" and pre_matches and not used:
        return GuardResult("rollback_allowed", "pass")
    v = verdict if verdict in ("setup_full", "data_keychain", "data", "threema_only", "restore_state",
                               "needs_answer", "ok_with_notes", "ok") else "none"
    sub = ("used" if used else "not_final" if last_kind != "final" else "pre_changed" if not pre_matches
           else "verdict")
    return GuardResult("rollback_allowed", "fail", "E_GUARD_ROLLBACK_NOT_ALLOWED", data={"sub": sub, "verdict": v},
                       error_data={"verdict": v})


def window(*, age_min: int) -> GuardResult:
    data = {"age_min": int(age_min), "limit_min": WINDOW_MIN}
    if 0 <= age_min <= WINDOW_MIN:
        return GuardResult("rollback_window", "pass", data=data)
    return GuardResult("rollback_window", "fail", "E_GUARD_ROLLBACK_WINDOW", data=data,
                       error_data={"age_min": max(0, int(age_min)), "limit_min": WINDOW_MIN})
