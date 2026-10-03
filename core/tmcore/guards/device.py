# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'device' (DESIGN §6.1): the connected iPhone is the one of the session (hash comparison, in memory)."""
from __future__ import annotations

from tmcore.guards import GuardResult


def check(*, session_device: str | None, connected: str) -> GuardResult:
    if session_device and session_device == connected:
        return GuardResult("device", "pass")
    return GuardResult("device", "fail", "E_DEV_OTHER", error_data={})
