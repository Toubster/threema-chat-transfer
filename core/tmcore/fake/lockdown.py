# SPDX-License-Identifier: AGPL-3.0-or-later
"""Virtual lockdownd (DESIGN §13.2): the raw value domains a real iPhone answers (all values, battery,
disk_usage, mobile.backup WillEncrypt, fmip) plus installation_proxy and mobile_config answers. The engine parses
them with the same code as real answers (tmcore.steps.iphone.parse_facts)."""
from __future__ import annotations

from tmcore.fake.device import VirtualIPhone


class FakeLockdown:
    def __init__(self, dev: VirtualIPhone):
        self.dev = dev

    @property
    def all_values(self) -> dict:
        return self.dev.lockdown(None)

    def get_value(self, domain: str | None = None, key: str | None = None):
        v = self.dev.lockdown(domain)
        return v.get(key) if key else v

    def installed_apps(self) -> dict:
        return self.dev.apps()

    def cloud_configuration(self) -> dict:
        return self.dev.cloud_configuration()

    def profile_list(self) -> dict:
        return self.dev.profiles()
