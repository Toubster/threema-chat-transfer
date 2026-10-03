# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guard 'iphone_space' (DESIGN §6.1): min(TotalDataAvailable, AmountDataAvailable) >= 1.5 x payload.
Guard 'free_space' (Mac, before every backup): the session's volume can hold the backup (lower-bound estimate:
local photos + Home/Keyboard estimate + margin); a full disk would only fail the backup, but late and confusingly."""
from __future__ import annotations

import math

from tmcore.guards import GuardResult

FACTOR = 1.5


def need_for(payload_bytes: int) -> int:
    return int(math.ceil(payload_bytes * FACTOR))


def check(*, free_bytes: int | None, need_bytes: int, estimate: bool = False) -> GuardResult:
    data = {"free_bytes": free_bytes, "need_bytes": need_bytes}
    if free_bytes is not None and free_bytes >= need_bytes:
        return GuardResult("iphone_space", "pass", data=data)
    if estimate:
        return GuardResult("iphone_space", "warn", "E_GUARD_IPHONE_SPACE", data=data)
    # free space unknown = refused (never send a payload the phone may not hold)
    return GuardResult("iphone_space", "fail", "E_GUARD_IPHONE_SPACE", data=data,
                       error_data={"need_bytes": need_bytes, "free_bytes": free_bytes or 0})


MAC_MARGIN_BYTES = 10**9


def mac_need_for_backup(*, photos_bytes: int | None, home_estimate: int) -> int:
    return int((photos_bytes or 0) + home_estimate + MAC_MARGIN_BYTES)


def mac_check(*, free_bytes: int, need_bytes: int) -> GuardResult:
    data = {"free_bytes": int(free_bytes), "need_bytes": int(need_bytes)}
    if free_bytes >= need_bytes:
        return GuardResult("free_space", "pass", data=data)
    return GuardResult("free_space", "fail", "E_HOST_SPACE", data=data)
