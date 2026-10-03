# SPDX-License-Identifier: AGPL-3.0-or-later
"""
device.py -- the virtual iPhone of --fake-device (DESIGN §13.2).

State lives in <session>/fake-iphone/ (or $TMCORE_FAKE_HOME): state.json (scenario, encryption, counters -- the
backup password is never stored, only the derived keybag secret, like a real device) and image.json (the plain
content per (domain, path), fixtures/gen_ios_backup.DeviceImage). Every backup writes a fresh encrypted
MobileBackup2 backup of the image; every restore decrypts the payload and applies it with iOS-27 annotation
semantics:

  * HomeDomain, CameraRollDomain: the domain becomes exactly the payload rows -- missing in the payload = deleted
    (a domain absent from the payload is wiped: the partial-payload incident, the gate must turn red);
  * KeyboardDomain: in the payload = restored; absent = the learned model collapses to 4 KiB (canary finding);
  * every other domain in the payload (Threema): overlay, nothing removed (RemoveItemsNotRestored = false);
  * domains not in the payload: untouched.

Then, for the first restore only, the scenario's effects (harmless regenerations the gate must classify) and
damage (what the gate must catch) are applied. The restore options must be EXACTLY the proven set, otherwise the
virtual iPhone refuses.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import hashlib
import json
import os
import plistlib
import shutil
import signal
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from tmcore.fake import scenario as S


def fixtures():
    """fixtures/gen_ios_backup.py of the checkout ($TMCORE_FIXTURES in a bundle)."""
    d = Path(os.environ.get("TMCORE_FIXTURES") or (S.REPO / "fixtures"))
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    import gen_ios_backup
    return gen_ios_backup


# ------------------------------------------------------------------------------------------------ device errors
class FakeConnectionTerminated(ConnectionError):
    """The device closed the connection (pymobiledevice3: ConnectionTerminatedError)."""


class FakeDeviceLinkError(Exception):
    """DLMessageProcessMessage with an ErrorCode (pymobiledevice3: 'Device link error: {...}')."""

    def __init__(self, error_code: int | None, reason: str):
        super().__init__(f"Device link error: {{'ErrorCode': {error_code}}}")
        self.error_code = error_code
        self.reason = reason


class FakeLocked(Exception):
    pass


class FakeUntrusted(Exception):
    pass


def device_password(sc: S.Scenario) -> str:
    """The backup password the virtual iPhone of a scenario already has (encryption on). "canary" = the canary password of
    fixtures/canaries.json, so the privacy scan proves it never leaks (DESIGN §9)."""
    pw = sc.device.get("backup_password")
    if pw == "canary":
        return S.canaries()["password"]
    if not pw:
        raise ValueError("scenario with encryption on needs device.backup_password")
    return str(pw)


GROUP = "AppDomainGroup-group.ch.threema"
DB = "ThreemaData.sqlite"
FULL_DOMAINS = ("HomeDomain", "CameraRollDomain", "KeyboardDomain")
_clock_cache: dict[str, int] = {}


def state_dir(session_root: Path) -> Path:
    env = os.environ.get("TMCORE_FAKE_HOME")
    d = Path(env) if env else Path(session_root) / "fake-iphone"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def clock_skip_minutes(session_root: Path, scenario_name: str, cmd: str) -> int:
    """Fake runs only: the scenario may move the engine clock of the FIRST run of one command forward
    (freshness_expired: the PRE backup looks 61 min old at the first restore; the automatic new backup works)."""
    key = f"{scenario_name}:{cmd}"
    if key in _clock_cache:
        return _clock_cache[key]
    want = int(S.load(scenario_name).behaviour.get("clock_skip_min", {}).get(cmd, 0))
    if want:
        dev = VirtualIPhone(session_root, scenario_name)
        runs = dev.state.setdefault("clock_runs", {})
        if runs.get(cmd):
            want = 0
        runs[cmd] = runs.get(cmd, 0) + 1
        dev.save()
    _clock_cache[key] = want
    return want


class VirtualIPhone:
    def __init__(self, session_root: Path, scenario_name: str):
        self.sc = S.load(scenario_name)
        self.dir = state_dir(session_root)
        self.state_path = self.dir / "state.json"
        self.image_path = self.dir / "image.json"
        self.state = self._load_state()
        c = S.canaries()
        self.udid = c["udid"]
        self.device_name = c["device_name"]
        self.serial = c["serial"]
        self.phone = c.get("phone")

    # ---------------------------------------------------------------------------------------------- persistence
    def _load_state(self) -> dict:
        try:
            st = json.loads(self.state_path.read_text())
            if st.get("scenario") == self.sc.name:
                return st
        except (OSError, ValueError):
            pass
        g = fixtures()
        dev = self.sc.device
        st = {"scenario": self.sc.name, "encryption": bool(dev["encryption"]), "secret": None,
              "backup_sessions": 0, "backups": 0, "dropped": False, "restores": 0, "watch_pos": 0,
              "dcim_changed": False, "build": dev["build"]}
        if dev["encryption"]:
            st["secret"] = g.derive_secret(device_password(self.sc))
        self._write(self.state_path, json.dumps(st))
        return st

    @staticmethod
    def _write(p: Path, text: str) -> None:
        tmp = p.with_name(f".{p.name}.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, p)

    def save(self) -> None:
        self._write(self.state_path, json.dumps(self.state))

    def image(self):
        g = fixtures()
        if self.image_path.is_file():
            return g.DeviceImage.load(self.image_path)
        img = self._build_image()
        img.save(self.image_path)
        return img

    def save_image(self, img) -> None:
        img.save(self.image_path)

    def _build_image(self):
        g = fixtures()
        dev = self.sc.device
        spec = self.sc.store_spec()
        opts = dict(extras=True, airplane_mode=dev["airplane"], ios_version=dev["ios_version"],
                    threema_short=dev["threema_version"], threema_prefs=dev["threema_prefs"] or None,
                    photos=dev["photos"], photos_claimed_bytes=dev["photos_claimed_bytes"])
        if spec is None or dev["threema"] != "regular":
            return g.build_image(variant="none", **opts)
        store = g.v56_store(spec)
        with tempfile.TemporaryDirectory(prefix="fake-store-") as tmp:
            if dev["model"] != "V56":
                store = self._tampered(store, Path(tmp))
            return g.build_image(variant="store", store_dir=store, **opts)

    @staticmethod
    def _tampered(store: Path, tmp: Path) -> Path:
        """Same store, one NSStoreModelVersionHashes entry changed: an unknown Threema model (V57, Encrypted ...)."""
        out = tmp / "store"
        shutil.copytree(store, out)
        c = sqlite3.connect(out / DB)
        try:
            row = c.execute("SELECT Z_VERSION, Z_PLIST FROM Z_METADATA").fetchone()
            md = plistlib.loads(row[1])
            hashes = dict(md["NSStoreModelVersionHashes"])
            k = sorted(hashes)[0]
            hashes[k] = hashlib.sha256(b"zz-unknown-model" + hashes[k]).digest()
            md["NSStoreModelVersionHashes"] = hashes
            c.execute("UPDATE Z_METADATA SET Z_PLIST=? WHERE Z_VERSION=?", (plistlib.dumps(md, fmt=plistlib.FMT_BINARY),
                                                                      row[0]))
            c.commit()
        finally:
            c.close()
        return out

    # ---------------------------------------------------------------------------------------------- lockdown
    @property
    def threema_bundle(self) -> str | None:
        return {"regular": "ch.threema.iapp", "work": "ch.threema.work"}.get(self.sc.device["threema"])

    def lockdown(self, domain: str | None) -> dict:
        dev = self.sc.device
        if domain is None:
            v = {"UniqueDeviceID": self.udid, "DeviceName": self.device_name, "SerialNumber": self.serial,
                 "ProductType": dev["product_type"], "ProductVersion": dev["ios_version"],
                 "BuildVersion": self.state["build"], "DeviceClass": "iPhone", "PasswordProtected": True}
            if self.phone:
                v["PhoneNumber"] = self.phone
            return v
        if domain == "com.apple.mobile.battery":
            return {"BatteryCurrentCapacity": dev["battery_pct"], "BatteryIsCharging": dev["charging"],
                    "ExternalConnected": dev["charging"]}
        if domain == "com.apple.disk_usage":
            return {"TotalDataAvailable": dev["free_bytes"], "AmountDataAvailable": dev["free_bytes"] + 10**8,
                    "TotalDataCapacity": 128 * 10**9, "PhotoUsage": dev["photos_bytes_estimate"]}
        if domain == "com.apple.mobile.backup":
            return {"WillEncrypt": bool(self.state["encryption"])}
        if domain == "com.apple.fmip":
            return {"IsAssociated": dev["find_my"] == "on"} if dev["find_my"] in ("on", "off") else {}
        return {}

    def apps(self) -> dict:
        b = self.threema_bundle
        if not b:
            return {}
        return {b: {"CFBundleIdentifier": b, "CFBundleShortVersionString": self.sc.device["threema_version"],
                    "CFBundleVersion": self.sc.device["threema_build"]}}

    def cloud_configuration(self) -> dict:
        return {"IsSupervised": bool(self.sc.device["managed"]), "IsMDMUnremovable": False}

    def profiles(self) -> dict:
        if not self.sc.device["managed"]:
            return {"OrderedIdentifiers": [], "ProfileMetadata": {}}
        return {"OrderedIdentifiers": ["zz.example.mdm"],
                "ProfileMetadata": {"zz.example.mdm": {"PayloadType": "com.apple.mdm"}}}

    # ---------------------------------------------------------------------------------------------- services
    def change_password(self, new: str) -> None:
        if self.state["encryption"]:
            raise FakeDeviceLinkError(205, "already_encrypted")
        self.state["encryption"] = True
        self.state["secret"] = fixtures().derive_secret(new)
        self.save()

    def backup(self, dest_root: Path, progress: Callable[[float], None],
               notify: Callable[[str, bool], None]) -> None:
        g = fixtures()
        if not self.state["encryption"] or not self.state.get("secret"):
            raise FakeDeviceLinkError(None, "not_encrypted")
        self.state["backup_sessions"] += 1
        self.save()
        dest = Path(dest_root) / self.udid
        if self.sc.behaviour["drop_first_backup"] and not self.state["dropped"]:
            self.state["dropped"] = True
            self.save()
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "Snapshot").mkdir(exist_ok=True)
            progress(0.4)
            raise FakeConnectionTerminated("connection was terminated abruptly")
        if dest.exists():
            shutil.rmtree(dest)
        notify("passcode_on_device", True)
        notify("passcode_on_device", False)
        img = self.image()
        dev = self.sc.device
        g.write_backup(img, Path(dest_root), None, secret=self.state["secret"], udid=self.udid,
                       ios_version=dev["ios_version"], build=self.state["build"], product_type=dev["product_type"],
                       device_name=self.device_name, serial=self.serial, phone=self.phone,
                       threema_version=dev["threema_build"], threema_short=dev["threema_version"],
                       date=_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
                       threema_bundle=self.threema_bundle, progress=progress)
        progress(100.0)
        self.state["backups"] += 1
        self.save()

    def dcim(self, cmd: str) -> list[tuple[str, int]]:
        img = self.image()
        if self.sc.behaviour["dcim_change_before_send"] and cmd == "restore" and not self.state["dcim_changed"]:
            # the user took a photo after the PRE backup
            img.f("CameraRollDomain", "Media/DCIM/100APPLE/IMG_9001.HEIC", os.urandom(31000), 3)
            self.save_image(img)
            self.state["dcim_changed"] = True
            self.save()
        return sorted((e.rel[len("Media/"):], e.size) for e in img.domain("CameraRollDomain")
                      if e.flags == 1 and e.rel.startswith("Media/DCIM/"))

    def restore(self, src_root: Path, source: str, password: str, options: dict,
                progress: Callable[[float], None]) -> None:
        from tmcore.steps.iphone import RESTORE_OPTIONS
        if options != RESTORE_OPTIONS:
            raise FakeDeviceLinkError(None, "unexpected_options")       # only the proven option set
        if source != self.udid:
            raise FakeDeviceLinkError(None, "other_device")
        if self.sc.behaviour["find_my_on"]:
            raise FakeDeviceLinkError(211, "find_my")                 # nothing staged, nothing changed
        payload = self._read_payload(Path(src_root) / source, password)
        end = self.sc.behaviour["restore_end"]
        top = 97.0 if end == "link_lost_early" else 100.0
        for p in (5.0, 25.0, 50.0, 75.0, top):
            progress(p)
        img = self.image()
        first = self.state["restores"] == 0
        pre_group = {k: copy.deepcopy(e) for k, e in img.entries.items() if k[0] == GROUP}
        self._apply(img, payload)
        if first:
            self._effects(img, self.sc.behaviour["effects"])
            self._damage(img, self.sc.behaviour["damage"], pre_group)
        self.save_image(img)
        self.state["restores"] += 1
        self.save()
        if end == "crash":
            os.kill(os.getpid(), signal.SIGKILL)       # the engine process dies while critical is on
        if end in ("link_lost", "link_lost_early"):
            raise FakeConnectionTerminated("device rebooted")

    # ---------------------------------------------------------------------------------------------- restore
    def _read_payload(self, dev: Path, password: str) -> dict:
        from tmcore.lib import backup_pipeline as bp
        from tmcore.lib import iosbackup_rw as rw
        g = fixtures()
        try:
            bk = rw.EncryptedBackup(dev, password)
        except ValueError:
            raise FakeDeviceLinkError(207, "password") from None
        pt = rw.decrypt_manifest_db((dev / "Manifest.db").read_bytes(), bk._manifest_key)
        pt, _ = bp.strip_manifest_padding(pt)
        buf = bytearray(pt)
        if buf[18] == 2 or buf[19] == 2:
            buf[18] = buf[19] = 1
        conn = sqlite3.connect(":memory:")
        conn.deserialize(bytes(buf))
        out: dict = {}
        now = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        try:
            for fid, dom, rel, flags, blob in conn.execute(
                    "SELECT fileID, domain, relativePath, flags, file FROM Files"):
                mb = bp.MBFile(blob)
                rel = rel or ""
                data, target = None, None
                if flags == 1:
                    p = dev / fid[:2] / fid
                    if mb.enc_blob is None or not p.is_file() or p.stat().st_size == 0:
                        data = b""
                    else:
                        data = rw.decrypt_file_content(p.read_bytes(), bp.file_key(bk, mb))
                elif flags == 4:
                    target = mb.target
                xr = mb.root.get("ExtendedAttributes")
                if isinstance(xr, plistlib.UID):
                    o = mb._deref(xr)
                    xr = bytes(o["NS.data"]) if isinstance(o, dict) else None
                out[(dom, rel)] = g.Entry(dom, rel, int(flags), data, mb.pclass, mb.mode,
                                          claimed_size=(mb.size if flags == 1 and mb.size != len(data or b"")
                                                        else None),
                                          xattrs_raw=xr if isinstance(xr, bytes) else None,
                                          digest="Digest" in mb.root, target=target, mtime=mb.mtime or now,
                                          birth=now)
        finally:
            conn.close()
        return out

    def _apply(self, img, payload: dict) -> None:
        pdoms = {d for d, _ in payload}
        for dom in FULL_DOMAINS:
            if dom == "KeyboardDomain" and dom not in pdoms:
                for e in img.domain(dom):
                    if e.flags == 1 and e.size > 16 * 1024:
                        e.data, e.claimed_size = os.urandom(4096), None
                continue
            img.remove_domain(dom)                     # missing in the payload = deleted at the commit
        for k, e in payload.items():
            e.inode = img.next_inode()
            img.entries[k] = e

    @staticmethod
    def _sqlite_rows(data: bytes, keep: dict[str, int]) -> bytes:
        g = fixtures()
        fd, tmp = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            Path(tmp).write_bytes(data)
            c = sqlite3.connect(tmp)
            for t, n in keep.items():
                c.execute(f'DELETE FROM "{t}" WHERE Z_PK > ?', (n,))
            c.commit()
            c.execute("VACUUM")
            c.close()
            return Path(tmp).read_bytes()
        finally:
            Path(tmp).unlink(missing_ok=True)
            _ = g

    def _effects(self, img, effects: list[str]) -> None:
        g = fixtures()
        H = "HomeDomain"
        if "poster" in effects:                # PosterBoard caches rewritten under new UUIDs, no extension fewer
            P = g.POSTER_DOMAIN
            for sub, ext in (("ClockPoster", "plist"), ("GalleryCache", "atx")):
                old = [k for k in img.entries if k[0] == P and k[1].startswith(f"Library/Caches/{sub}/")]
                for k in old[:10]:
                    del img.entries[k]
                for i in range(12):
                    rel = f"Library/Caches/{sub}/{uuid.uuid4()}.{ext}"
                    img.f(P, rel, os.urandom(2500) if ext == "atx" else plistlib.dumps({"n": i}), 3)
        if "shortcuts" in effects:             # only the ToolKit tool catalogue replaced
            cat = g.TOOLKIT_CATALOGUE
            for sfx in ("", "-wal", "-shm"):
                img.entries.pop((H, cat + sfx), None)
                img.f(H, cat.replace("v62", "v63") + sfx, os.urandom(4096) if sfx else
                      g._sqlite_bytes({"Tools": 31}, seed=98), 3)
        if "calendar" in effects:              # sync/change tables rebuilt, Store/Calendar/CalendarItem kept
            e = img.entries[(H, "Library/Calendar/Calendar.sqlitedb")]
            e.data = self._sqlite_rows(e.data, {"CalendarChanges": 10, "ClientSyncState": 5})
            e.inode, e.claimed_size = img.next_inode(), None
        if "buddy_rerun" in effects:           # Setup Assistant re-ran for the Apple account only
            e = img.entries[(H, g.PURPLEBUDDY)]
            pb = plistlib.loads(e.data)
            pb["SetupLastExit"] = _dt.datetime(2026, 10, 1, 12, 0, 0)
            pb["PaymentPresented"] = True
            pb["AppleIDPresented"] = True
            pb["lastPrepareLaunchSentinel"] = _dt.datetime(2026, 10, 1, 11, 59, 0)
            e.data = plistlib.dumps(pb, fmt=plistlib.FMT_BINARY)

    def _damage(self, img, damage: list[str], pre_group: dict) -> None:
        g = fixtures()
        H = "HomeDomain"
        if "home_wipe" in damage:              # the partial-payload incident pattern (Setup Assistant state kept,
            for k in [k for k in img.entries if k[0] == H and img.entries[k].flags == 1   # see setup_reset)
                      and k[1] != g.PURPLEBUDDY
                      and k[1].startswith(("Library/Preferences/", "Library/TCC/", "Library/SpringBoard/",
                                           "Library/DeviceRegistry/", "Library/SMS/"))]:
                del img.entries[k]
        if "keyboard_collapse" in damage:
            for e in img.domain("KeyboardDomain"):
                if e.flags == 1 and e.size > 16 * 1024:
                    e.data, e.claimed_size = os.urandom(4096), None
        if "keychain_items_lost" in damage:
            e = img.entries[("KeychainDomain", "keychain-backup.plist")]
            e.data = g.keychain_plist({"genp": 15, "inet": 5, "cert": 2, "keys": 4})
        if "setup_reset" in damage:
            e = img.entries[(H, g.PURPLEBUDDY)]
            e.data = g.purplebuddy_plist(SetupDone=False, SetupFinishedAllSteps=False, Language="en",
                                         SetupLastExit=_dt.datetime(2026, 10, 1, 12, 0, 0))
        if "restore_state" in damage:
            e = img.entries[(H, g.PURPLEBUDDY)]
            pb = plistlib.loads(e.data)
            pb["SetupState"] = "RestoredFromiTunesBackup"
            e.data = plistlib.dumps(pb, fmt=plistlib.FMT_BINARY)
        if "threema_store_corrupt" in damage:  # Threema threw the imported database away at the first launch
            for k, e in pre_group.items():
                if k[1] in (DB, DB + "-wal", DB + "-shm"):
                    e.inode = img.next_inode()
                    img.entries[k] = e


@contextlib.contextmanager
def opened(session_root: Path, scenario_name: str):
    yield VirtualIPhone(session_root, scenario_name)
