# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'managed' (DESIGN §6.1): supervised/MDM iPhones are not supported (fail-closed)."""
from __future__ import annotations

from tmcore.guards import GuardResult


def check(*, managed: bool) -> GuardResult:
    if managed:
        return GuardResult("managed", "fail", "E_DEV_MANAGED", error_data={})
    return GuardResult("managed", "pass")
