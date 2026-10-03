# SPDX-License-Identifier: AGPL-3.0-or-later
"""Virtual com.apple.mobilebackup2 (DESIGN §13.2): ChangePassword, Backup (synthetic encrypted backup of the
device image, optional first-session drop), Restore (only the proven option set; MBError 211 when Find My is on;
iOS-27 annotation semantics, see fake/device.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from tmcore.fake.device import VirtualIPhone


class FakeMobilebackup2:
    def __init__(self, dev: VirtualIPhone):
        self.dev = dev

    def get_will_encrypt(self) -> bool:
        return bool(self.dev.state["encryption"])

    def change_password(self, new: str) -> None:
        self.dev.change_password(new)

    def backup(self, backup_directory: Path, progress_callback: Callable[[float], None],
               notify: Callable[[str, bool], None]) -> None:
        self.dev.backup(Path(backup_directory), progress_callback, notify)

    def restore(self, backup_directory: Path, *, source: str, password: str,
                progress_callback: Callable[[float], None], **options) -> None:
        self.dev.restore(Path(backup_directory), source, password, options, progress_callback)
