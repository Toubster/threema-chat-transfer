# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'battery' (DESIGN §6.1): iPhone >= 50 % or charging; Mac on AC power before the send."""
from __future__ import annotations

import re
import subprocess

from tmcore.guards import GuardResult

MIN_PCT = 50


def check(*, battery_pct: int | None, charging: bool) -> GuardResult:
    data = {"battery_pct": battery_pct, "charging": bool(charging)}
    if charging or (battery_pct is not None and battery_pct >= MIN_PCT):
        return GuardResult("battery", "pass", data=data)
    if battery_pct is None:
        return GuardResult("battery", "warn", data=data)          # unreadable: the user is asked to charge (S08)
    return GuardResult("battery", "fail", "E_DEV_BATTERY", data=data)


def mac_power() -> str:
    """'ac' | 'battery' | 'unknown' (pmset)."""
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if re.search(r"'AC Power'", out):
        return "ac"
    if re.search(r"'Battery Power'", out):
        return "battery"
    return "unknown"


def mac_check(*, power: str) -> GuardResult:
    if power == "battery":
        return GuardResult("power", "fail", "E_HOST_POWER", data={"power": power})
    return GuardResult("power", "pass" if power == "ac" else "warn", data={"power": power})
