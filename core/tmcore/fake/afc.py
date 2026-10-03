# SPDX-License-Identifier: AGPL-3.0-or-later
"""Virtual AFC (DESIGN §13.2): the /DCIM listing (relative path, size) the dcim_unchanged guard compares with the
PRE backup. With `dcim_change_before_send` the user "takes a photo" right before the restore."""
from __future__ import annotations

from tmcore.fake.device import VirtualIPhone


class FakeAfc:
    def __init__(self, dev: VirtualIPhone):
        self.dev = dev

    def dcim(self, cmd: str) -> list[tuple[str, int]]:
        return self.dev.dcim(cmd)
