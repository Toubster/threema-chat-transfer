# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Guards (DESIGN §6.1). Every guard runs inside tmcore, has NO override, and reports through one check event:

    from tmcore.guards import GuardResult, report
    res = freshness.check(age_min=7)            # pure: facts in, GuardResult out (unit-tested without a device)
    report(ctx, res)            # emits 'check'; raises EngineError(res.code, **res.error_data) when res.status == "fail"

Owner: coreB (devtools/OWNERSHIP.md). Modules: compat airplane freshness dcim identity space setintegrity findmy
battery managed photos_limit threema rollback device -- facts are read by guards/facts.py.
There is no parameter, environment variable or option that turns a guard off or loosens it. The only built-in
loosening is the rollback window (6 h instead of 60 min), decided by the engine itself in rollback-threema.
"""
from __future__ import annotations

import dataclasses

from tmcore.protocol import EngineError


@dataclasses.dataclass
class GuardResult:
    id: str                       # check id from events.v1 (e.g. "freshness", "dcim_unchanged")
    status: str                   # pass | warn | fail | skip
    code: str | None = None       # E_... when status == fail (or the code a warn points at)
    data: dict = dataclasses.field(default_factory=dict)   # numbers/enums only (check event data)
    error_data: dict | None = None  # data of the result when it fails (catalog keys of the code); default: data

    @property
    def failed(self) -> bool:
        return self.status == "fail"


def report(ctx, res: GuardResult) -> GuardResult:
    ctx.proto.check(res.id, res.status, res.code, **res.data)
    if res.status == "fail":
        raise EngineError(res.code or "E_INTERNAL", **(res.data if res.error_data is None else res.error_data))
    return res


def emit(ctx, res: GuardResult) -> GuardResult:
    """Report without raising (device-status shows failed checks; the hard check comes later)."""
    ctx.proto.check(res.id, res.status, res.code, **res.data)
    return res
