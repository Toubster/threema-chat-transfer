# SPDX-License-Identifier: AGPL-3.0-or-later
"""
iphone.py -- shared infrastructure of the device steps (owner: coreB; DESIGN §5.4, §6.1, §13.2).

    gw = gateway(ctx)                      # RealGateway (pymobiledevice3 over usbmuxd, USB only) or fake.FakeGateway
    udid, seen = select_device(ctx, gw)    # E_DEV_NONE / LOCKED / UNTRUSTED / MULTIPLE / OTHER
    with gw.open(udid) as dev:             # one lockdown connection
        f = read_facts(dev)                # Facts: product type, build, battery, space, Find My, managed, Threema
    paths = Paths(ctx.session)             # session keys shared with prepare (devtools/CONTRACT-REQUESTS.md)
    rc, rep = bp_run(ctx, "extract", password, backup_udid_dir=..., out_dir=...)

The gateway is the ONLY code that talks to an iPhone. Both gateways deliver the raw lockdown dictionaries to the same
parsers here, so the virtual iPhone exercises the same decisions as a real one. Device errors are normalised into
DeviceError / BackupDropped / RestoreRefused / LinkLost. Raw identifiers (UDID, device name) never leave this module
except as session hashes.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as _dt
import importlib
import logging
import os
import re
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

from tmcore.protocol import EngineError

# ------------------------------------------------------------------------------------------------ fixed rules
FRESH_LIMIT_MIN = 60                 # DESIGN §6.1 freshness
ROLLBACK_LIMIT_MIN = 6 * 60          # R1 only while the PRE backup is <= 6 h old
FUTURE_TOLERANCE_S = 5 * 60
PHOTOS_LIMIT_BYTES = 20 * 10**9      # D8: local photos <= 20 GB in v1 (decimal, as shown in the UI)
SPACE_FACTOR = 1.5                   # iPhone free space >= 1.5 x payload
BATTERY_MIN_PCT = 50
BACKUP_ATTEMPTS = 3                  # first session is often dropped by the device after ~10 s
BACKUP_RETRY_WAIT_S = 10.0
PASSWORD_ATTEMPTS = 5                # keybag check, no new backup needed
HOME_ESTIMATE_BYTES = 2 * 10**9      # S03 estimate for HomeDomain + KeyboardDomain before the PRE backup exists
THREEMA_BUNDLES = {"ch.threema.iapp": "regular", "ch.threema.work": "work", "ch.threema.onprem": "onprem"}
THREEMA_BUNDLE = "ch.threema.iapp"
# THE restore options (pymobiledevice3 Mobilebackup2Service.restore keyword names), proven on iOS 27.0 24A437:
# CLI equivalent `backup2 restore --system --reboot --no-settings --no-copy --no-remove --skip-apps`.
RESTORE_OPTIONS: dict[str, bool] = {"system": True, "reboot": True, "copy": False, "settings": False,
                                    "remove": False, "skip_apps": True}
MBERROR_FINDMY = 211                 # "Find My iPhone is on": the device refuses before staging, nothing changed


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def now(ctx) -> _dt.datetime:
    """Engine clock. A --fake-device scenario may move it forward for one command (freshness_expired); a real run
    never does (there is no option for it)."""
    t = utc_now()
    if ctx.fake_device and ctx.session is not None:
        from tmcore.fake.device import clock_skip_minutes
        t += _dt.timedelta(minutes=clock_skip_minutes(ctx.session.root, ctx.fake_device, ctx.cmd))
    return t


def iso(t: _dt.datetime) -> str:
    t = t.astimezone(_dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def parse_ts(s: Any) -> _dt.datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def as_utc(t: Any) -> _dt.datetime | None:
    """plistlib dates are naive and mean UTC."""
    if not isinstance(t, _dt.datetime):
        return None
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def minutes(a: _dt.datetime, b: _dt.datetime) -> int:
    return int((a - b).total_seconds() // 60)


# ------------------------------------------------------------------------------------------------ session paths
class Paths:
    """Session keys of the device steps (devtools/CONTRACT-REQUESTS.md, 'session paths')."""

    def __init__(self, session):
        self.s = session

    def backup_root(self, role: str) -> Path:
        return self.s.path(f"ios/{role}")

    def backup_dev(self, role: str) -> Path | None:
        """<session>/ios/<role>/<UDID>/ -- exactly one device directory, else None."""
        root = self.backup_root(role)
        devs = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")] if root.is_dir() else []
        return devs[0] if len(devs) == 1 else None

    @property
    def extract(self) -> Path:
        return self.s.path("work/extract")

    @property
    def extract_post(self) -> Path:
        return self.s.path("work/extract-post")

    @property
    def restoreset(self) -> Path:
        return self.s.path("work/restoreset")

    @property
    def rollbackset(self) -> Path:
        return self.s.path("work/rollback")

    @property
    def store_out(self) -> Path:
        return self.s.path("work/store_out")

    @property
    def android(self) -> Path:
        return self.s.path("android")

    @property
    def normalized(self) -> Path:
        return self.s.path("android/normalized.sqlite")


def make_writable(root: Path) -> None:
    for dirpath, _dirs, files in os.walk(root):
        with contextlib.suppress(OSError):
            os.chmod(dirpath, os.stat(dirpath).st_mode | stat.S_IRWXU)
        for fn in files:
            fp = os.path.join(dirpath, fn)
            with contextlib.suppress(OSError):
                st = os.lstat(fp)
                if not stat.S_ISLNK(st.st_mode):
                    os.chmod(fp, st.st_mode | stat.S_IWUSR)


def remove_tree(p: Path) -> None:
    if p.is_symlink() or p.is_file():
        p.unlink(missing_ok=True)
        return
    if p.exists():
        make_writable(p)
        shutil.rmtree(p, ignore_errors=False)


def freeze(root: Path) -> None:
    """chmod -R a-w (a frozen backup or restore set cannot be edited by accident)."""
    for dirpath, dirs, files in os.walk(root, topdown=False):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            st = os.lstat(fp)
            if not stat.S_ISLNK(st.st_mode):
                os.chmod(fp, st.st_mode & ~0o222)
        os.chmod(dirpath, os.stat(dirpath).st_mode & ~0o222)


def tree_bytes(root: Path) -> tuple[int, int]:
    n = total = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(dirpath, fn)).st_size
                n += 1
    return n, total


def write_report(ctx, name: str, kind: str, code: str, counts: dict, checks: list[dict] | None = None,
                 data: dict | None = None) -> None:
    """reports/<name>.json (schema report.v1: counts and codes only)."""
    from tmcore import __version__
    from tmcore.protocol import check_safe, utc_now as ts
    rep = {"schema": "report.v1", "kind": kind, "cmd": ctx.cmd, "created_at": ts(), "engine_version": __version__,
           "code": code, "counts": {k: v for k, v in counts.items() if isinstance(v, (int, float))
                                    and not isinstance(v, bool)}}
    if checks:
        rep["checks"] = checks
    if data:
        check_safe(data)
        rep["data"] = data
    p = ctx.session.path(f"reports/{name}.json")
    tmp = p.with_name(f".{p.name}.tmp")
    import json
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)


# ------------------------------------------------------------------------------------------------ lib adapters
def lib(name: str):
    return importlib.import_module(f"tmcore.lib.{name}")


def _use_tmp(ctx) -> None:
    """Every temporary file of the libraries (decrypted Manifest copies, DB copies) stays in <session>/work/tmp."""
    lib("backup_pipeline").use_tmp(ctx.session.tmp)


@contextlib.contextmanager
def quiet() -> Iterator[None]:
    """Library code may print summaries: stdout belongs to the protocol, so everything goes to stderr (redacted)."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def bp(ctx):
    mod = lib("backup_pipeline")
    _use_tmp(ctx)
    return mod


def bp_run(ctx, cmd: str, password: str, **kw) -> tuple[int, dict | None]:
    """backup_pipeline extract|verify|restoreset in-process; the password never touches a file."""
    mod = bp(ctx)
    with quiet():
        return mod.run(cmd, password=password, **kw)


# ------------------------------------------------------------------------------------------------ device errors
class DeviceError(Exception):
    """A device state that ends the command: code in E_DEV_* (never during critical)."""

    def __init__(self, code: str, **data):
        super().__init__(code)
        self.code = code
        self.data = data


class BackupDropped(Exception):
    """The device ended the backup session early (typical for the first session after connecting)."""


class RestoreRefused(Exception):
    """The device refused the restore before staging: nothing changed (e.g. MBError 211 = Find My)."""

    def __init__(self, mberror: int | None = None, reason: str = "device_refused"):
        super().__init__(reason)
        self.mberror = mberror
        self.reason = reason


class LinkLost(Exception):
    """The connection dropped while or after sending (reboot at the end of a restore is the normal case)."""


@dataclasses.dataclass
class Seen:
    udid: str
    state: str                     # ready | locked | untrusted
    product_type: str | None = None


# ------------------------------------------------------------------------------------------------ facts
@dataclasses.dataclass
class Facts:
    udid: str
    product_type: str
    ios_version: str
    ios_build: str
    battery_pct: int | None
    charging: bool
    free_bytes: int | None
    photos_bytes_estimate: int | None
    find_my: str                   # on | off | unknown
    managed: bool
    encryption: bool
    threema_installed: bool
    threema_variant: str           # regular | work | onprem | other | none
    threema_version: str | None


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


_PRODUCT = re.compile(r"^(iPhone|iPad|iPod)[0-9]{1,3},[0-9]{1,2}$")
_BUILD = re.compile(r"^[0-9]{2}[A-Z][0-9]{1,5}[a-z]?$")
_VERSION = re.compile(r"^[0-9]{1,4}(\.[0-9]{1,4}){0,3}$")


def parse_facts(values: dict, battery: dict, disk: dict, fmip: dict | None, cloud_cfg: dict | None,
                profiles: dict | None, will_encrypt: bool, apps: dict) -> Facts:
    """Raw lockdown/installation_proxy/mobile_config answers -> Facts (shared by both gateways).
    Values that do not look like public versions/model ids become 'unknown' instead of leaking free text."""
    product = str(values.get("ProductType") or "")
    version = str(values.get("ProductVersion") or "")
    build = str(values.get("BuildVersion") or "")
    if not _PRODUCT.match(product) or not _VERSION.match(version) or not _BUILD.match(build):
        raise DeviceError("E_DEV_DISCONNECTED", sub="lockdown_values")
    pct = _int(battery.get("BatteryCurrentCapacity"))
    charging = battery.get("BatteryIsCharging") is True or (battery.get("ExternalConnected") is True
                                                            and battery.get("FullyCharged") is True)
    avail = [v for v in (_int(disk.get("TotalDataAvailable")), _int(disk.get("AmountDataAvailable"))) if v is not None]
    free = min(avail) if avail else None          # never over-estimate the space the device can use
    photos = None
    usage = [_int(disk.get(k)) for k in ("PhotoUsage", "CameraUsage")]
    if any(v is not None for v in usage):
        photos = sum(v for v in usage if v is not None)
    if fmip is None:
        find_my = "unknown"
    else:
        assoc = fmip.get("IsAssociated", fmip.get("FMIPAssociated"))
        find_my = "on" if assoc is True else "off" if assoc is False else "unknown"
    managed = bool((cloud_cfg or {}).get("IsSupervised")) or bool((cloud_cfg or {}).get("IsMDMUnremovable"))
    for meta in ((profiles or {}).get("ProfileMetadata") or {}).values():
        if isinstance(meta, dict) and str(meta.get("PayloadType", "")).lower().endswith("mdm"):
            managed = True
    if (profiles or {}).get("OrderedIdentifiers") and any(
            "mdm" in str(i).lower() for i in (profiles or {}).get("OrderedIdentifiers") or []):
        managed = True
    variant, tver = "none", None
    installed = False
    for bid, var in THREEMA_BUNDLES.items():
        if bid in apps:
            installed = installed or var == "regular"
            if var == "regular" or variant == "none":
                variant = var
                v = str((apps.get(bid) or {}).get("CFBundleShortVersionString") or "")
                tver = v if _VERSION.match(v) else None
    if variant == "none" and any(b.startswith("ch.threema.") for b in apps):
        variant = "other"
    return Facts(udid=str(values.get("UniqueDeviceID") or ""), product_type=product, ios_version=version,
                 ios_build=build, battery_pct=pct, charging=charging, free_bytes=free, photos_bytes_estimate=photos,
                 find_my=find_my, managed=managed, encryption=bool(will_encrypt), threema_installed=installed,
                 threema_variant=variant, threema_version=tver)


def read_facts(dev) -> Facts:
    return parse_facts(dev.lockdown(None), dev.lockdown("com.apple.mobile.battery") or {},
                       dev.lockdown("com.apple.disk_usage") or {}, dev.find_my(), dev.cloud_configuration(),
                       dev.profiles(), dev.will_encrypt(), dev.apps())


# ------------------------------------------------------------------------------------------------ compat
def compat_ios(ctx) -> dict:
    """compat/ios.json -- for --fake-device the list comes from the scenario (never from an engine switch)."""
    if ctx.fake_device:
        from tmcore.fake import scenario as fake_scenario
        return fake_scenario.load(ctx.fake_device).compat_ios
    return ctx.compat("ios")


def compat_entry(ctx, build: str) -> dict | None:
    for b in compat_ios(ctx).get("builds") or []:
        if b.get("build") == build:
            return b
    return None


def compat_stage(ctx, build: str) -> str:
    e = compat_entry(ctx, build)
    st = (e or {}).get("status") or "unknown"
    st = st.replace("-", "_")
    return st if st in ("verified", "static_checked", "unknown", "blocked") else "unknown"


# ------------------------------------------------------------------------------------------------ gateway
def gateway(ctx):
    if ctx.fake_device:
        from tmcore.fake.gateway import FakeGateway
        return FakeGateway(ctx)
    return RealGateway()


def select_device(ctx, gw, *, expect: str | None = None, retries: int = 0) -> tuple[str, Seen]:
    """The one connected iPhone, ready (trusted, unlocked). expect: device hash the session belongs to."""
    seen = gw.scan()
    if not seen:
        raise DeviceError("E_DEV_NONE")
    if len(seen) > 1:
        raise DeviceError("E_DEV_MULTIPLE", count=len(seen))
    s = seen[0]
    h = ctx.session.hasher.h(s.udid)
    if expect and h != expect:
        raise DeviceError("E_DEV_OTHER")
    if s.state == "locked":
        raise DeviceError("E_DEV_LOCKED")
    if s.state == "untrusted":
        raise DeviceError("E_DEV_UNTRUSTED")
    return s.udid, s


def engine_error(e: DeviceError) -> EngineError:
    data = {k: v for k, v in e.data.items() if k in ("count",)}
    return EngineError(e.code, **data)


class RealGateway:
    """pymobiledevice3 11.19.4 over Apple's usbmuxd, USB only, never auto-pairing during data steps."""
    fake = False

    def __init__(self):
        for name in ("pymobiledevice3", "asyncio"):
            logging.getLogger(name).setLevel(logging.WARNING)

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def scan(self, *, pair_timeout: float | None = None) -> list[Seen]:
        async def run() -> list[Seen]:
            from pymobiledevice3 import usbmux
            from pymobiledevice3.exceptions import (NotPairedError, PairingDialogResponsePendingError,
                                                    PasswordRequiredError, PyMobileDevice3Exception,
                                                    UserDeniedPairingError)
            from pymobiledevice3.lockdown import create_using_usbmux
            out = []
            for d in await usbmux.list_devices():
                if d.connection_type != "USB":
                    continue
                state, product = "ready", None
                try:
                    ld = await create_using_usbmux(serial=d.serial, connection_type="USB",
                                                   autopair=pair_timeout is not None, pair_timeout=pair_timeout)
                    async with ld:
                        product = (ld.all_values or {}).get("ProductType")
                        if not ld.paired:
                            state = "untrusted"
                except PasswordRequiredError:
                    state = "locked"
                except (PairingDialogResponsePendingError, NotPairedError, UserDeniedPairingError):
                    state = "untrusted"
                except PyMobileDevice3Exception:
                    state = "untrusted"
                out.append(Seen(d.serial, state, product if isinstance(product, str) else None))
            return out
        try:
            return self._run(run())
        except (ConnectionError, OSError) as e:
            raise DeviceError("E_DEV_NONE") from e

    @contextlib.contextmanager
    def open(self, udid: str):
        conn = _RealConnection(udid)
        try:
            conn.connect()
            yield conn
        finally:
            conn.close()


class _PasscodeLog(logging.Handler):
    """pymobiledevice3 announces the passcode prompt of a backup only through its logger."""

    def __init__(self, cb: Callable[[str, bool], None]):
        super().__init__(logging.INFO)
        self.cb = cb

    def emit(self, record):
        msg = str(record.getMessage())
        if msg.startswith("Please enter the device passcode"):
            self.cb("passcode_on_device", True)
        elif msg.startswith("Device passcode prompt dismissed"):
            self.cb("passcode_on_device", False)


def _mberror(exc: BaseException) -> int | None:
    m = re.search(r"ErrorCode['\"]?\s*:\s*(\d+)", str(exc))
    return int(m.group(1)) if m else None


class _RealConnection:
    def __init__(self, udid: str):
        self.udid = udid
        self.loop = asyncio.new_event_loop()
        self.ld: Any = None

    def _await(self, coro):
        return self.loop.run_until_complete(coro)

    def connect(self) -> None:
        from pymobiledevice3.exceptions import NotPairedError, PasswordRequiredError, PyMobileDevice3Exception
        from pymobiledevice3.lockdown import create_using_usbmux
        try:
            self.ld = self._await(create_using_usbmux(serial=self.udid, connection_type="USB", autopair=False))
        except PasswordRequiredError as e:
            raise DeviceError("E_DEV_LOCKED") from e
        except NotPairedError as e:
            raise DeviceError("E_DEV_UNTRUSTED") from e
        except (PyMobileDevice3Exception, ConnectionError, OSError) as e:
            raise DeviceError("E_DEV_DISCONNECTED") from e
        if not self.ld.paired:
            raise DeviceError("E_DEV_UNTRUSTED")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self.ld is not None:
                self._await(self.ld.close())
        self.loop.close()

    def lockdown(self, domain: str | None) -> dict:
        from pymobiledevice3.exceptions import PyMobileDevice3Exception
        try:
            if domain is None:
                return dict(self.ld.all_values or {})
            v = self._await(self.ld.get_value(domain=domain))
            return dict(v or {})
        except PyMobileDevice3Exception:
            return {}

    def find_my(self) -> dict | None:
        v = self.lockdown("com.apple.fmip")
        return v or None                              # empty: not readable on this iOS -> "unknown"

    def cloud_configuration(self) -> dict | None:
        from pymobiledevice3.services.mobile_config import MobileConfigService
        with contextlib.suppress(Exception):
            async def run():
                async with MobileConfigService(self.ld) as mc:
                    return await mc.get_cloud_configuration()
            return self._await(run())
        return None

    def profiles(self) -> dict | None:
        from pymobiledevice3.services.mobile_config import MobileConfigService
        with contextlib.suppress(Exception):
            async def run():
                async with MobileConfigService(self.ld) as mc:
                    return await mc.get_profile_list()
            return self._await(run())
        return None

    def will_encrypt(self) -> bool:
        return bool(self.lockdown("com.apple.mobile.backup").get("WillEncrypt"))

    def apps(self) -> dict:
        from pymobiledevice3.services.installation_proxy import InstallationProxyService
        async def run():
            async with InstallationProxyService(self.ld) as ip:
                return await ip.get_apps(application_type="User", bundle_identifiers=list(THREEMA_BUNDLES))
        try:
            return dict(self._await(run()) or {})
        except Exception:  # noqa: BLE001 -- unknown means "not seen", the PRE backup decides hard
            return {}

    def dcim(self) -> list[tuple[str, int]]:
        from pymobiledevice3.services.afc import AfcService
        async def run():
            out = []
            async with AfcService(self.ld) as afc:
                async for dirpath, _dirs, files in afc.walk("/DCIM"):
                    for fn in files:
                        st = await afc.stat(f"{dirpath}/{fn}")
                        rel = f"{dirpath}/{fn}".lstrip("/")
                        out.append((rel, int(st.get("st_size") or 0)))
            return out
        try:
            return self._await(run())
        except Exception as e:  # noqa: BLE001
            raise DeviceError("E_DEV_DISCONNECTED") from e

    def change_password(self, new: str, work_dir: Path) -> None:
        from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
        async def run():
            async with Mobilebackup2Service(self.ld) as mb:
                await mb.change_password(backup_directory=str(work_dir), new=new)
        try:
            self._await(run())
        except Exception as e:  # noqa: BLE001
            raise DeviceError("E_DEV_DISCONNECTED") from e

    def backup(self, dest_root: Path, progress: Callable[[float], None],
               notify: Callable[[str, bool], None]) -> None:
        from pymobiledevice3.exceptions import ConnectionTerminatedError, PyMobileDevice3Exception
        from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
        h = _PasscodeLog(notify)
        lg = logging.getLogger("pymobiledevice3.services.mobilebackup2")
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        async def run():
            async with Mobilebackup2Service(self.ld) as mb:
                await mb.backup(full=True, backup_directory=str(dest_root), progress_callback=progress)
        try:
            self._await(run())
        except (ConnectionTerminatedError, ConnectionError, asyncio.IncompleteReadError, EOFError) as e:
            raise BackupDropped() from e
        except PyMobileDevice3Exception as e:
            raise BackupDropped() from e
        finally:
            lg.removeHandler(h)

    def restore(self, src_root: Path, password: str, *, expect_build: str,
                progress: Callable[[float], None]) -> None:
        from pymobiledevice3.exceptions import ConnectionTerminatedError, PyMobileDevice3Exception
        from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
        vals = self.ld.all_values or {}
        # same lockdown session as the restore: closes the gap between the device guard and the send
        if vals.get("UniqueDeviceID") != self.udid or vals.get("BuildVersion") != expect_build:
            raise RestoreRefused(None, "device_refused")
        sent = [False]

        def cb(p):
            sent[0] = True
            progress(p)

        async def run():
            async with Mobilebackup2Service(self.ld) as mb:
                await mb.restore(backup_directory=str(src_root), source=self.udid, password=password,
                                 progress_callback=cb, **RESTORE_OPTIONS)
        try:
            self._await(run())
        except (ConnectionTerminatedError, ConnectionError, asyncio.IncompleteReadError, EOFError) as e:
            if not sent[0]:
                raise RestoreRefused(None, "connection") from e
            raise LinkLost() from e
        except PyMobileDevice3Exception as e:
            code = _mberror(e)
            if code == MBERROR_FINDMY or (code is not None and not sent[0]):
                raise RestoreRefused(code, "device_refused") from e
            if not sent[0]:
                raise RestoreRefused(None, "other") from e
            raise LinkLost() from e
