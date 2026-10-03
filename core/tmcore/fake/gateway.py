# SPDX-License-Identifier: AGPL-3.0-or-later
"""
gateway.py -- FakeGateway: the device gateway of --fake-device. Same interface and the same error normalisation
as tmcore.steps.iphone.RealGateway, built on the virtual usbmux/lockdown/mobilebackup2/afc of this package.
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Callable

from tmcore.fake import afc as _afc
from tmcore.fake import usbmux as _usbmux
from tmcore.fake.device import FakeConnectionTerminated, FakeDeviceLinkError, VirtualIPhone, clock_skip_minutes
from tmcore.fake.lockdown import FakeLockdown
from tmcore.fake.mobilebackup2 import FakeMobilebackup2


class FakeGateway:
    fake = True

    def __init__(self, ctx):
        self.ctx = ctx
        self.root = Path(ctx.session.root)
        self.name = ctx.fake_device
        self.dev = VirtualIPhone(self.root, self.name)

    def scan(self, *, watch: bool = False, pair_timeout: float | None = None):
        from tmcore.steps.iphone import Seen
        self.dev = VirtualIPhone(self.root, self.name)
        return [Seen(u, st, self.dev.sc.device["product_type"]) for u, st in _usbmux.list_devices(self.dev,
                                                                                                  watch=watch)]

    @contextlib.contextmanager
    def open(self, udid: str):
        from tmcore.steps.iphone import DeviceError
        if udid != self.dev.udid:
            raise DeviceError("E_DEV_DISCONNECTED")
        yield _FakeConnection(self.ctx, VirtualIPhone(self.root, self.name))

    def clock_skip_min(self, cmd: str) -> int:
        return clock_skip_minutes(self.root, self.name, cmd)


class _FakeConnection:
    def __init__(self, ctx, dev: VirtualIPhone):
        self.ctx = ctx
        self.dev = dev
        self.udid = dev.udid
        self.ld = FakeLockdown(dev)
        self.mb = FakeMobilebackup2(dev)

    def lockdown(self, domain: str | None) -> dict:
        return dict(self.ld.get_value(domain) or {})

    def find_my(self) -> dict | None:
        return self.lockdown("com.apple.fmip") or None

    def cloud_configuration(self) -> dict | None:
        return self.ld.cloud_configuration()

    def profiles(self) -> dict | None:
        return self.ld.profile_list()

    def will_encrypt(self) -> bool:
        return self.mb.get_will_encrypt()

    def apps(self) -> dict:
        return self.ld.installed_apps()

    def dcim(self) -> list[tuple[str, int]]:
        return _afc.FakeAfc(self.dev).dcim(self.ctx.cmd)

    def change_password(self, new: str, work_dir: Path) -> None:
        from tmcore.steps.iphone import DeviceError
        try:
            self.mb.change_password(new)
        except FakeDeviceLinkError as e:
            raise DeviceError("E_DEV_DISCONNECTED") from e

    def backup(self, dest_root: Path, progress: Callable[[float], None],
               notify: Callable[[str, bool], None]) -> None:
        from tmcore.steps.iphone import BackupDropped
        try:
            self.mb.backup(dest_root, progress, notify)
        except (FakeConnectionTerminated, FakeDeviceLinkError) as e:
            raise BackupDropped() from e

    def restore(self, src_root: Path, password: str, *, expect_build: str,
                progress: Callable[[float], None]) -> None:
        from tmcore.steps.iphone import RESTORE_OPTIONS, LinkLost, RestoreRefused, MBERROR_FINDMY
        vals = self.ld.all_values
        if vals.get("UniqueDeviceID") != self.udid or vals.get("BuildVersion") != expect_build:
            raise RestoreRefused(None, "device_refused")
        sent = [False]

        def cb(p):
            sent[0] = True
            progress(p)
        try:
            self.mb.restore(src_root, source=self.udid, password=password, progress_callback=cb, **RESTORE_OPTIONS)
        except FakeDeviceLinkError as e:
            if e.error_code == MBERROR_FINDMY or not sent[0]:
                raise RestoreRefused(e.error_code, "password" if e.reason == "password" else "device_refused") \
                    from e
            raise LinkLost() from e
        except FakeConnectionTerminated as e:
            if not sent[0]:
                raise RestoreRefused(None, "connection") from e
            raise LinkLost() from e
