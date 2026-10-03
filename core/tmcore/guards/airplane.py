# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'airplane' (DESIGN §6.1): SystemPreferencesDomain com.apple.radios.plist AirplaneMode = true in the PRE
backup (read in memory). Anything else (false, missing, unreadable) = refused: whatever arrives between backup and
restore would be lost at the commit."""
from __future__ import annotations

from tmcore.guards import GuardResult


def check(*, airplane: bool | None) -> GuardResult:
    if airplane is True:
        return GuardResult("airplane", "pass")
    return GuardResult("airplane", "fail", "E_GUARD_AIRPLANE",
                       data={"state": "off" if airplane is False else "unreadable"}, error_data={})
