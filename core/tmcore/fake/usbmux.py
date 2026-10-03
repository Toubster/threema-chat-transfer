# SPDX-License-Identifier: AGPL-3.0-or-later
"""Virtual usbmuxd (DESIGN §13.2): which iPhones are plugged in, and in which pairing/lock state.

device-watch walks the scenario's `watch` sequence (none -> locked -> untrusted -> ready by default), one state per
poll; every other command sees the final state directly. `devices: 2` simulates a second iPhone (E_DEV_MULTIPLE).
"""
from __future__ import annotations

from tmcore.fake.device import VirtualIPhone


def list_devices(dev: VirtualIPhone, *, watch: bool = False) -> list[tuple[str, str]]:
    """[(udid, state)] with state ready | locked | untrusted."""
    seq = dev.sc.device["watch"] or ["ready"]
    if watch:
        pos = min(dev.state.get("watch_pos", 0), len(seq) - 1)
        state = seq[pos]
        if pos < len(seq) - 1:
            dev.state["watch_pos"] = pos + 1
            dev.save()
    else:
        state = seq[-1]
    if state == "none":
        return []
    out = [(dev.udid, state)]
    for i in range(1, int(dev.sc.device.get("devices", 1))):
        out.append((dev.udid[:-2] + f"{i:02d}", state))
    return out
