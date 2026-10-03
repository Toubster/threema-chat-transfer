# SPDX-License-Identifier: AGPL-3.0-or-later
"""
host-check (S02): macOS >= 14, Apple silicon, APFS work folder, free space, power adapter, FileVault.
Owner: coreA (devtools/OWNERSHIP.md). No session, no device, read only.

Checks (events.v1 check ids, in this order): macos, arch, fs_apfs, free_space, power, filevault.
Every check is emitted first; then the first failing one ends the command:
    E_HOST_MACOS_OLD, E_HOST_ARCH (data macos, arch) · E_HOST_FS_NOT_APFS (fs) · E_HOST_SPACE (need_bytes,
    free_bytes) · E_HOST_POWER (power)
Notes: W_FILEVAULT_OFF (FileVault off or unknown), W_LOW_HOST_SPACE_MARGIN (free < 1.5 x need).

The probes are module functions so tests can replace them; each one fails soft (unknown -> the safe answer).\nWith --fake-device the command reports the fixed VIRTUAL_MAC instead (recordings, demo screenshots).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from tmcore.cli import Context, StepResult
from tmcore.protocol import EngineError

MIN_MACOS = (14, 0)
# Space for one session at S02, before anything about the iPhone is known: PRE + POST backup (encrypted, about the
# size of the iPhone data without photos in iCloud), the readable Android copy and the work folder. The restore set is
# an APFS clone (almost free). 30 GB covers the canonical 6.4 GB example with a wide margin; S03 refines it.
NEED_BYTES = 30 * 10**9
MARGIN_FACTOR = 1.5
DEFAULT_WORKDIR = "~/Library/Application Support/Chat Transfer for Threema/sessions"
FS_NAMES = {"apfs": "apfs", "hfs": "hfs", "exfat": "exfat", "msdos": "msdos", "smbfs": "smbfs", "nfs": "nfs"}


# ------------------------------------------------------------------------------------------------ probes
def _run(cmd: list[str], timeout: float = 10) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def macos_version() -> str:
    v = platform.mac_ver()[0] or _run(["/usr/bin/sw_vers", "-productVersion"]).strip()
    m = re.match(r"^(\d{1,4})(?:\.(\d{1,4}))?(?:\.(\d{1,4}))?$", v or "")
    return ".".join(x for x in m.groups() if x is not None) if m else "0"


def arch() -> str:
    """arm64 on Apple silicon (also when a process runs translated), x86_64 on Intel."""
    if platform.machine() == "arm64":
        return "arm64"
    return "arm64" if _run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"]).strip() == "1" else "x86_64"


class _Statfs(ctypes.Structure):          # struct statfs, 64-bit inode layout (macOS)
    _fields_ = [("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32), ("f_blocks", ctypes.c_uint64),
                ("f_bfree", ctypes.c_uint64), ("f_bavail", ctypes.c_uint64), ("f_files", ctypes.c_uint64),
                ("f_ffree", ctypes.c_uint64), ("f_fsid", ctypes.c_int32 * 2), ("f_owner", ctypes.c_uint32),
                ("f_type", ctypes.c_uint32), ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
                ("f_fstypename", ctypes.c_char * 16), ("f_mntonname", ctypes.c_char * 1024),
                ("f_mntfromname", ctypes.c_char * 1024), ("f_flags_ext", ctypes.c_uint32),
                ("f_reserved", ctypes.c_uint32 * 7)]


def fs_type(path: Path) -> str:
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        fn = getattr(libc, "statfs$INODE64", None) if platform.machine() == "x86_64" else None
        fn = fn or libc.statfs
        buf = _Statfs()
        if fn(os.fsencode(str(path)), ctypes.byref(buf)) != 0:
            return "other"
        return FS_NAMES.get(buf.f_fstypename.decode("ascii", "replace").lower(), "other")
    except (OSError, AttributeError, ValueError):
        return "other"


def free_bytes(path: Path) -> int:
    try:
        return int(shutil.disk_usage(path).free)
    except OSError:
        return 0


def power() -> tuple[str, int | None]:
    """('ac'|'battery'|'unknown', battery percent or None) from `pmset -g batt` (desktops: ac, None)."""
    out = _run(["/usr/bin/pmset", "-g", "batt"])
    src = "unknown"
    if "'AC Power'" in out:
        src = "ac"
    elif "'Battery Power'" in out:
        src = "battery"
    m = re.search(r"(\d{1,3})%", out)
    return src, (int(m.group(1)) if m else None)


def filevault() -> bool | None:
    out = _run(["/usr/bin/fdesetup", "status"])
    if "FileVault is On" in out:
        return True
    if "FileVault is Off" in out:
        return False
    return None


# ------------------------------------------------------------------------------------------------ step
def _existing(path: Path) -> Path:
    """The nearest existing ancestor (the work folder may not exist yet)."""
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".") if x.isdigit()) or (0,)


# --fake-device (CI, demo screenshots): a fixed virtual Mac, so recordings and screenshots never show the facts of the
# machine they were made on (canonical example values, DESIGN §10.4). Never used without --fake-device.
Probe = tuple[str, str, str, int, str, "int | None", "bool | None"]   # macos, arch, fs, free, power, battery, filevault
VIRTUAL_MAC: Probe = ("15.1", "arm64", "apfs", 220_000_000_000, "ac", None, True)


def _probe(ctx: Context) -> Probe:
    if ctx.fake_device:
        return VIRTUAL_MAC
    workdir = _existing(Path(os.path.expanduser(getattr(ctx.args, "workdir", None) or DEFAULT_WORKDIR)))
    pw_src, batt = power()
    return macos_version(), arch(), fs_type(workdir), free_bytes(workdir), pw_src, batt, filevault()


def host_check(ctx: Context) -> StepResult:
    proto = ctx.proto
    proto.phase("checks", 1, 1)
    mac, cpu, fs, free, pw_src, batt, fv = _probe(ctx)

    failures: list[EngineError] = []
    mac_ok = _version_tuple(mac) >= MIN_MACOS
    proto.check("macos", "pass" if mac_ok else "fail", None if mac_ok else "E_HOST_MACOS_OLD", macos=mac)
    if not mac_ok:
        failures.append(EngineError("E_HOST_MACOS_OLD", macos=mac, arch=cpu))
    proto.check("arch", "pass" if cpu == "arm64" else "fail", None if cpu == "arm64" else "E_HOST_ARCH", arch=cpu)
    if cpu != "arm64":
        failures.append(EngineError("E_HOST_ARCH", macos=mac, arch=cpu))
    proto.check("fs_apfs", "pass" if fs == "apfs" else "fail", None if fs == "apfs" else "E_HOST_FS_NOT_APFS", fs=fs)
    if fs != "apfs":
        failures.append(EngineError("E_HOST_FS_NOT_APFS", fs=fs))
    if free < NEED_BYTES:
        proto.check("free_space", "fail", "E_HOST_SPACE", need_bytes=NEED_BYTES, free_bytes=free)
        failures.append(EngineError("E_HOST_SPACE", need_bytes=NEED_BYTES, free_bytes=free))
    elif free < MARGIN_FACTOR * NEED_BYTES:
        proto.check("free_space", "warn", "W_LOW_HOST_SPACE_MARGIN", need_bytes=NEED_BYTES, free_bytes=free)
        proto.note("W_LOW_HOST_SPACE_MARGIN", need_bytes=NEED_BYTES, free_bytes=free)
    else:
        proto.check("free_space", "pass", need_bytes=NEED_BYTES, free_bytes=free)
    power_data: dict[str, Any] = {"power": pw_src} | ({"battery_pct": batt} if batt is not None else {})
    if pw_src == "battery":
        proto.check("power", "fail", "E_HOST_POWER", **power_data)
        failures.append(EngineError("E_HOST_POWER", power=pw_src))
    else:
        proto.check("power", "pass" if pw_src == "ac" else "warn", None, **power_data)
    if fv:
        proto.check("filevault", "pass", filevault=True)
    else:
        proto.check("filevault", "warn", "W_FILEVAULT_OFF", filevault=False)
        proto.note("W_FILEVAULT_OFF")
    if failures:
        raise failures[0]
    return StepResult(data={"macos": mac, "arch": cpu, "fs": fs, "free_bytes": free, "need_bytes": NEED_BYTES,
                            "power": pw_src, "battery_pct": batt, "filevault": bool(fv)})
