# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'findmy' (DESIGN §6.1): Find My must be off. Live: lockdown com.apple.fmip (to be confirmed on iOS 27, so
'unknown' only warns). Hard: the device refuses the restore with MBError 211 before staging -> E_GUARD_FINDMY with
source mberror_211, nothing changed."""
from __future__ import annotations

from tmcore.guards import GuardResult


def check(*, find_my: str) -> GuardResult:
    if find_my == "on":
        return GuardResult("findmy", "fail", "E_GUARD_FINDMY", data={"source": "live"})
    if find_my == "off":
        return GuardResult("findmy", "pass")
    return GuardResult("findmy", "warn", data={"source": "live"})


def refused_211() -> GuardResult:
    return GuardResult("findmy", "fail", "E_GUARD_FINDMY", data={"source": "mberror_211"})
