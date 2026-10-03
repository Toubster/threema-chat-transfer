# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'identity' (DESIGN §6.1, new): the Threema ID of the Android backup equals the Threema ID on the iPhone
(PRE backup). Compared in memory as session HMACs; neither ID is written anywhere. Fail-closed: when the iPhone ID
cannot be read from the PRE backup -> E_THREEMA_ID_UNREADABLE (DESIGN §18 open fact)."""
from __future__ import annotations

from tmcore.guards import GuardResult


def check(*, same: bool | None, iphone_readable: bool, android_known: bool) -> GuardResult:
    if not iphone_readable:
        return GuardResult("identity", "fail", "E_THREEMA_ID_UNREADABLE", data={"sub": "iphone"}, error_data={})
    if not android_known:
        return GuardResult("identity", "fail", "E_THREEMA_ID_UNREADABLE", data={"sub": "android"}, error_data={})
    if same:
        return GuardResult("identity", "pass")
    return GuardResult("identity", "fail", "E_THREEMA_ID_MISMATCH", error_data={})
