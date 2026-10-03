# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'compat_ios' (DESIGN §6.2): only builds with status `verified` may receive a restore (v1, D5).
`static_checked`, `unknown`, beta/suffix builds and anything not listed are refused; `blocked` has its own code."""
from __future__ import annotations

from tmcore.guards import GuardResult


def check(*, stage: str, ios_version: str, ios_build: str) -> GuardResult:
    data = {"ios_version": ios_version, "ios_build": ios_build}
    if stage == "verified":
        return GuardResult("compat_ios", "pass", data={"stage": stage})
    code = "E_IOS_BLOCKED" if stage == "blocked" else "E_IOS_UNKNOWN"
    return GuardResult("compat_ios", "fail", code, data={"stage": stage}, error_data=data)


def ios_unchanged(*, device_build: str, pre_build: str | None) -> GuardResult:
    """The iPhone still runs the build the PRE backup was taken on (no update in the window)."""
    if pre_build and device_build == pre_build:
        return GuardResult("ios_unchanged", "pass")
    return GuardResult("ios_unchanged", "fail", "E_IOS_CHANGED", error_data={"ios_build": device_build})
