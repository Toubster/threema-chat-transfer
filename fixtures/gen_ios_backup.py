#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
gen_ios_backup.py -- fabricate REALISTIC encrypted iOS (MobileBackup2, Manifest.db v3.3) backups without a device:
for the ported tests (tmcore.lib backup_pipeline / restore set / backup_diff) and for the virtual iPhone of
--fake-device (tmcore/fake), which keeps the same structure as a plain "device image" and writes a fresh encrypted
backup from it on every `backup`.

Independent of backup_pipeline.py's writer (own MBFile builder); reuses only the low-level crypto primitives of
tmcore.lib.iosbackup_rw.

    img = build_image(variant="standard")                       # DeviceImage: plain entries per (domain, path)
    info = write_backup(img, root, passphrase, udid=...)        # -> <root>/<UDID>/ encrypted backup
    fabricate_realistic_backup(root, passphrase, ...)           # both in one call (the API of the ported tests)

Layout produced under <root>/<UDID>/:
  Info.plist (XML, pymobiledevice3-style, Applications with SINF/iTunesMetadata)
  Manifest.plist (binary; Lockdown, Applications{CFBundleVersion,...}, BackupKeyBag, ManifestKey, IsEncrypted,
                  Version 10.0, SystemDomainsVersion)
  Status.plist (binary; SnapshotState finished, Version 3.3)
  Manifest.db (Files + FilesDomainIdx/FilesRelativePathIdx/FilesFlagsIdx + Properties)
  NN/<sha1(domain-path)> per-file AES-256-CBC blobs

Domains: HomeDomain (plist, sqlite-ish file, zero-length file, symlink row, a "live" file whose Size != content,
ExtendedAttributes; with system_domains=True (default) also Preferences incl. .GlobalPreferences /
com.apple.migration.plist, TCC, Accounts, AddressBook, BulletinBoard, UserNotifications, Shortcuts, SpringBoard,
Calendar, each with an inline "Digest" = SHA1 of the stored ciphertext like a real iOS 27 backup, and one empty file
with a 0-byte stored blob), CameraRollDomain (DCIM, PhotoData, CPL, Caches, symlink, no Digest), KeyboardDomain
(2 files with Digest, 3 dirs; part of the restore set), KeychainDomain (keychain-backup.plist dummy with Digest),
decoy system domains MediaDomain/HealthDomain/SystemPreferencesDomain/RootDomain, AppDomain-ch.threema.iapp,
AppDomainPlugin-ch.threema.iapp.ThreemaNotificationExtension, AppDomainGroup-group.ch.threema (REAL Core Data store
V56 built by fixtures/make_store.swift from model/V56, with a LIVE WAL frame + shm + _EXTERNAL_DATA, group prefs
plist; no threema-fs.db: iOS excludes it from backups), decoys AppDomain-com.example.other,
AppDomainGroup-group.com.example.other, AppDomainGroup-group.ch.threema.work (must NOT survive a Threema trim).

variant="fresh": group container like a freshly Safe-restored device -- empty V56 store, no -wal/-shm rows, no
.ThreemaData_SUPPORT dirs, APP_SETUP marker, KeepMessagesDays=30 (retention warning test).
variant="store": the group container holds `store_dir` (e.g. a seeded store of the virtual iPhone).
variant="none": Threema not installed (no Threema domains, not in Applications).

extras=True (virtual iPhone only; the ported tests count rows and keep extras off): content the postcheck gate looks
at -- purplebuddy, real SQLite databases with >= 20 rows (TCC, Messages, Accounts, CallHistory, Calendar with
Store/Calendar/CalendarItem + sync tables, Shortcuts + ToolKit catalogue), a PosterBoard domain, a keychain with
items per class, a large keyboard model, Watch registry files.

Test-only: keys/passwords are ephemeral; nothing here touches a device. Every value is synthetic.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.keywrap import aes_key_wrap

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "core") not in sys.path:
    sys.path.insert(0, str(REPO / "core"))
from tmcore.lib import iosbackup_rw as rw  # noqa: E402

MOMD = REPO / "model" / "V56" / "ThreemaData.momd"
MAKE_STORE_SRC = Path(__file__).resolve().parent / "make_store.swift"
UID = plistlib.UID

DIR_MODE = 0o40755
FILE_MODE = 0o100644
LINK_MODE = 0o120755
FLAG_FILE, FLAG_DIR, FLAG_LINK = 1, 2, 4
DEFAULT_UDID = "00008150-ZZFAKEUDID000002"

# synthetic Threema store of the ported tests (ZZ fixture IDs); the virtual iPhone builds its own store from the
# scenario's spec (tmcore/fake/scenario.py default_store_spec)
SAMPLE_SPEC = {
    "own": "ZZFIXN01",
    "contacts": [{"identity": "ZZSAMP01", "publicKey": "5a" * 32, "firstName": "Sample"}],
    "groups": [{"groupId": "5a5a000000000001", "creator": "ZZSAMP01", "name": "Sample group",
                "members": ["ZZFIXN01", "ZZSAMP01"]}],
    "oneToOne": ["ZZSAMP01"],
    "messages": [{"chat": "contact:ZZSAMP01", "id": "5a5a5a5a00000001", "text": "sample text",
                  "dateMs": 1788000000000, "isOwn": False}],
    "fileMessage": {"chat": "contact:ZZSAMP01", "bytes": 300000},
}


# ------------------------------------------------------------------------------------------------ V56 stores
def cache_dir() -> Path:
    d = Path(os.environ.get("TMCORE_FIXTURE_CACHE") or (REPO / ".build" / "fixtures"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_store_bin() -> Path:
    """Compile fixtures/make_store.swift once per source version (needs the Xcode command line tools)."""
    src = MAKE_STORE_SRC.read_bytes()
    tag = hashlib.sha256(src).hexdigest()[:12]
    out = cache_dir() / f"make_store-{tag}"
    if out.is_file():
        return out
    tmp = out.with_name(f".{out.name}.{os.getpid()}")
    subprocess.run(["xcrun", "swiftc", "-O", "-swift-version", "5", "-o", str(tmp), str(MAKE_STORE_SRC)],
                   check=True, capture_output=True, timeout=600)
    os.replace(tmp, out)
    return out


def v56_store(spec: dict | str = "empty") -> Path:
    """Directory holding a synthetic V56 store (ThreemaData.sqlite [+ -wal/-shm, _EXTERNAL_DATA]).
    spec: "empty", "sample" or a make_store seed spec. Cached by content; callers must not modify the directory.
    Prebuilt stores can be provided through TMCORE_FIXTURE_CACHE (e.g. inside an app bundle without Swift)."""
    if spec == "sample":
        spec = SAMPLE_SPEC
    key = "empty" if spec == "empty" else json.dumps(spec, sort_keys=True)
    src = hashlib.sha256(MAKE_STORE_SRC.read_bytes()).hexdigest()[:12]     # a new store builder = new stores
    tag = hashlib.sha256((key + "|" + _momd_tag() + "|" + src).encode()).hexdigest()[:16]
    out = cache_dir() / "stores" / tag
    if (out / "ThreemaData.sqlite").is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".store-", dir=out.parent))
    try:
        cmd = [str(_make_store_bin()), str(MOMD), str(work / "s")]
        if spec == "empty":
            cmd.append("empty")
        else:
            (work / "spec.json").write_text(json.dumps(spec))
            cmd += ["seed", str(work / "spec.json")]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"make_store failed (exit {r.returncode})")
        try:
            os.replace(work / "s", out)
        except OSError:            # another process won the race: use its result
            pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def _momd_tag() -> str:
    h = hashlib.sha256()
    for p in sorted(MOMD.rglob("*")):
        if p.is_file():
            h.update(p.name.encode() + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()[:12]


# ------------------------------------------------------------------------------------------------ MBFile / keybag
def _tlv(tag: bytes, value) -> bytes:
    if isinstance(value, int):
        value = struct.pack(">I", value)
    return tag + struct.pack(">I", len(value)) + value


def derive_secret(passphrase: str, *, dpic: int = 1000, iterations: int = 1000) -> dict:
    """What a device keeps instead of the backup password: salts + the derived keybag wrapping key (hex).
    The virtual iPhone stores only this, never the password (DESIGN §9: no password files)."""
    salt, dpsl = os.urandom(20), os.urandom(20)
    r1 = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), dpsl, dpic, 32)
    pk = hashlib.pbkdf2_hmac("sha1", r1, salt, iterations, 32)
    return {"salt": salt.hex(), "dpsl": dpsl.hex(), "pk": pk.hex(), "dpic": dpic, "iterations": iterations}


def secret_matches(secret: dict, passphrase: str) -> bool:
    r1 = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), bytes.fromhex(secret["dpsl"]), secret["dpic"], 32)
    pk = hashlib.pbkdf2_hmac("sha1", r1, bytes.fromhex(secret["salt"]), secret["iterations"], 32)
    return pk.hex() == secret["pk"]


def make_keybag(passphrase: str | None, *, dpic: int, iterations: int,
                secret: dict | None = None) -> tuple[bytes, dict[int, bytes]]:
    if secret is None:
        secret = derive_secret(passphrase, dpic=dpic, iterations=iterations)
    salt, dpsl, pk = bytes.fromhex(secret["salt"]), bytes.fromhex(secret["dpsl"]), bytes.fromhex(secret["pk"])
    dpic, iterations = secret["dpic"], secret["iterations"]
    class_keys = {c: os.urandom(32) for c in range(1, 12)}
    kb = (_tlv(b"VERS", 4) + _tlv(b"TYPE", 1) + _tlv(b"UUID", os.urandom(16)) + _tlv(b"HMCK", os.urandom(40))
          + _tlv(b"WRAP", 0) + _tlv(b"SALT", salt) + _tlv(b"ITER", iterations) + _tlv(b"DPWT", 1)
          + _tlv(b"DPIC", dpic) + _tlv(b"DPSL", dpsl))
    for c, ck in class_keys.items():
        kb += (_tlv(b"UUID", os.urandom(16)) + _tlv(b"CLAS", c) + _tlv(b"WRAP", 2) + _tlv(b"KTYP", 0)
               + _tlv(b"WPKY", aes_key_wrap(pk, ck)))
    return kb, class_keys


def mbfile_blob(*, rel: str, mode: int, size: int, pclass: int, inode: int, mtime: int,
                enc_blob: bytes | None = None, target: str | None = None, xattrs: dict | None = None,
                uid: int = 501, gid: int = 501, digest: bytes | None = None,
                xattrs_raw: bytes | None = None, birth: int | None = None) -> bytes:
    """iOS-like MBFile NSKeyedArchiver object graph (independent of backup_pipeline.build_mbfile).
    digest / xattrs_raw: stored INLINE as raw data in the root dict -- that is how a real iOS 27 backup stores
    "Digest" (SHA1 of the stored ciphertext, system domains only) and most "ExtendedAttributes"."""
    objs: list = ["$null"]
    root = {"LastModified": mtime, "Flags": 0, "GroupID": gid, "LastStatusChange": mtime,
            "Birth": mtime - 60 if birth is None else birth,
            "InodeNumber": inode, "Mode": mode, "ProtectionClass": pclass, "Size": size, "UserID": uid}
    if digest is not None:
        root["Digest"] = digest
    if xattrs_raw is not None:
        root["ExtendedAttributes"] = xattrs_raw
    objs.append(root)
    objs.append(rel)
    root["RelativePath"] = UID(2)
    data_cls = None
    if enc_blob is not None:
        objs.append({"NS.data": enc_blob})
        root["EncryptionKey"] = UID(len(objs) - 1)
        objs.append({"$classname": "NSMutableData", "$classes": ["NSMutableData", "NSData", "NSObject"]})
        data_cls = UID(len(objs) - 1)
        objs[root["EncryptionKey"].data]["$class"] = data_cls
    if target is not None:
        objs.append(target)
        root["Target"] = UID(len(objs) - 1)
    if xattrs:
        objs.append({"NS.data": plistlib.dumps(xattrs, fmt=plistlib.FMT_BINARY)})
        root["ExtendedAttributes"] = UID(len(objs) - 1)
        if data_cls is None:
            objs.append({"$classname": "NSMutableData", "$classes": ["NSMutableData", "NSData", "NSObject"]})
            data_cls = UID(len(objs) - 1)
        objs[root["ExtendedAttributes"].data]["$class"] = data_cls
    objs.append({"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]})
    root["$class"] = UID(len(objs) - 1)
    return plistlib.dumps({"$version": 100000, "$archiver": "NSKeyedArchiver", "$top": {"root": UID(1)},
                           "$objects": objs}, fmt=plistlib.FMT_BINARY)


# ------------------------------------------------------------------------------------------------ device image
@dataclasses.dataclass
class Entry:
    domain: str
    rel: str
    flags: int                       # 1 file, 2 dir, 4 symlink
    data: bytes | None = None        # plain content (files)
    pclass: int = 0
    mode: int = FILE_MODE
    claimed_size: int | None = None  # MBFile Size when it differs from len(data) (live file, huge photos)
    xattrs: dict | None = None
    xattrs_raw: bytes | None = None
    digest: bool = False
    empty_blob: bool = False
    target: str | None = None
    inode: int = 0
    mtime: int = 1790000000
    birth: int | None = None

    @property
    def size(self) -> int:
        if self.flags != FLAG_FILE:
            return 0
        return len(self.data or b"") if self.claimed_size is None else self.claimed_size

    def to_json(self) -> dict:
        d = dataclasses.asdict(self)
        for k in ("data", "xattrs_raw"):
            if d[k] is not None:
                d[k] = d[k].hex()
        if d["xattrs"] is not None:
            d["xattrs"] = {k: v.hex() for k, v in d["xattrs"].items()}
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Entry":
        d = dict(d)
        for k in ("data", "xattrs_raw"):
            if d.get(k) is not None:
                d[k] = bytes.fromhex(d[k])
        if d.get("xattrs") is not None:
            d["xattrs"] = {k: bytes.fromhex(v) for k, v in d["xattrs"].items()}
        return cls(**d)


class DeviceImage:
    """Plain content of a (virtual) iPhone, keyed by (domain, relativePath); insertion order is kept."""

    def __init__(self):
        self.entries: dict[tuple[str, str], Entry] = {}
        self._inode = 1000
        self.mtime = 1790000000

    def next_inode(self) -> int:
        self._inode += 7
        return self._inode

    def d(self, domain: str, rel: str, pclass: int = 0) -> None:
        self.entries[(domain, rel)] = Entry(domain, rel, FLAG_DIR, None, pclass, DIR_MODE, inode=self.next_inode(),
                                            mtime=self.mtime)

    def f(self, domain: str, rel: str, data: bytes, pclass: int = 3, *, claimed_size: int | None = None,
          xattrs: dict | None = None, mode: int = FILE_MODE, digest: bool = False,
          xattrs_raw: bytes | None = None, empty_blob: bool = False) -> None:
        self.entries[(domain, rel)] = Entry(domain, rel, FLAG_FILE, data, pclass, mode, claimed_size, xattrs,
                                            xattrs_raw, digest, empty_blob, None, self.next_inode(), self.mtime)

    def link(self, domain: str, rel: str, target: str) -> None:
        self.entries[(domain, rel)] = Entry(domain, rel, FLAG_LINK, None, 0, LINK_MODE, target=target,
                                            inode=self.next_inode(), mtime=self.mtime)

    def domain(self, domain: str) -> list[Entry]:
        return [e for (dom, _r), e in self.entries.items() if dom == domain]

    def domains(self) -> set[str]:
        return {d for d, _ in self.entries}

    def remove_domain(self, domain: str) -> None:
        for k in [k for k in self.entries if k[0] == domain]:
            del self.entries[k]

    def save(self, path: Path) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"inode": self._inode, "entries": [e.to_json() for e in self.entries.values()]}))
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> "DeviceImage":
        raw = json.loads(path.read_text())
        img = cls()
        img._inode = raw["inode"]
        for d in raw["entries"]:
            e = Entry.from_json(d)
            img.entries[(e.domain, e.rel)] = e
        return img


def live_store_snapshot(store_dir: Path, work: Path) -> dict[str, bytes]:
    """Copy a store and snapshot DB/WAL/SHM while a committed-but-uncheckpointed write sits in the WAL (what a
    backup of a running app looks like)."""
    live = work / "live-store"
    if live.exists():
        shutil.rmtree(live)
    live.mkdir(parents=True)
    shutil.copy2(store_dir / "ThreemaData.sqlite", live / "ThreemaData.sqlite")
    for x in ("ThreemaData.sqlite-wal", "ThreemaData.sqlite-shm"):     # a live source store keeps data in its WAL
        if (store_dir / x).exists():
            shutil.copy2(store_dir / x, live / x)
    conn = sqlite3.connect(live / "ThreemaData.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("UPDATE ZCONTACT SET ZJOBTITLE='wal-marker' WHERE Z_PK=(SELECT min(Z_PK) FROM ZCONTACT)")
    conn.execute("UPDATE Z_METADATA SET Z_VERSION=Z_VERSION")      # a WAL frame even when the store has no contact
    conn.commit()
    snap = {n: (live / n).read_bytes() for n in ("ThreemaData.sqlite", "ThreemaData.sqlite-wal",
                                                 "ThreemaData.sqlite-shm")}
    conn.close()
    shutil.rmtree(live)
    assert len(snap["ThreemaData.sqlite-wal"]) > 0
    return snap


def _sqlite_bytes(tables: dict[str, int], *, seed: int = 0) -> bytes:
    """A real SQLite file with the given tables and row counts (synthetic rows, rollback journal)."""
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        os.unlink(tmp)
        c = sqlite3.connect(tmp)
        for t, n in tables.items():
            c.execute(f'CREATE TABLE "{t}" (Z_PK INTEGER PRIMARY KEY, ZVALUE BLOB)')
            c.executemany(f'INSERT INTO "{t}" (ZVALUE) VALUES (?)',
                          [(hashlib.sha256(f"{seed}:{t}:{i}".encode()).digest(),) for i in range(n)])
        c.commit()
        c.close()
        return Path(tmp).read_bytes()
    finally:
        Path(tmp).unlink(missing_ok=True)


# system databases of the virtual iPhone (extras): real SQLite files the gate's db probes can count
EXTRA_DBS = {
    "Library/TCC/TCC.db": {"access": 40, "policies": 6},
    "Library/SMS/sms.db": {"message": 60, "chat": 8, "handle": 8, "attachment": 6},
    "Library/Accounts/Accounts3.sqlite": {"ZACCOUNT": 12, "ZACCOUNTPROPERTY": 30},
    "Library/CallHistoryDB/CallHistory.storedata": {"ZCALLRECORD": 50},
    "Library/Calendar/Calendar.sqlitedb": {"Store": 3, "Calendar": 6, "CalendarItem": 40, "CalendarChanges": 70,
                                           "ClientSyncState": 30},
    "Library/Shortcuts/Shortcuts.sqlite": {"ZSHORTCUT": 25, "ZSHORTCUTACTIONS": 25},
}
TOOLKIT_CATALOGUE = "Library/Shortcuts/ToolKit/Tools-prod.v62.sqlite"
POSTER_DOMAIN = "AppDomain-com.apple.PosterBoard"
PURPLEBUDDY = "Library/Preferences/com.apple.purplebuddy.plist"


def purplebuddy_plist(**over) -> bytes:
    pb = {"SetupDone": True, "SetupFinishedAllSteps": True, "SetupState": "SetupUsingAssistant",
          "SetupLastExit": _dt.datetime(2026, 9, 1, 8, 0, 0), "Language": "de", "Locale": "de_CH@rg=chzzzz",
          "GuessedCountry": "CH", "PaymentPresented": True, "PrivacyPresented": True, "SetupVersion": 12,
          "lastPrepareLaunchSentinel": _dt.datetime(2026, 9, 1, 7, 59, 0)}
    pb.update(over)
    return plistlib.dumps(pb, fmt=plistlib.FMT_BINARY)


def keychain_plist(counts: dict[str, int]) -> bytes:
    kc = {k: [{"v_Data": hashlib.sha256(f"{k}{i}".encode()).digest(), "agrp": "synthetic"}
              for i in range(counts.get(k, 0))] for k in ("genp", "inet", "cert", "keys")}
    return plistlib.dumps(kc, fmt=plistlib.FMT_BINARY)


def add_extras(img: DeviceImage, *, photos: int = 2, photo_bytes: int = 24000,
               photos_claimed_bytes: int | None = None) -> None:
    """Gate-relevant system content of the virtual iPhone (see module doc)."""
    H = "HomeDomain"
    for dd in ("Library/CallHistoryDB", "Library/Shortcuts/ToolKit", "Library/DeviceRegistry",
               "Library/Passes", "Library/IdentityServices"):
        img.d(H, dd, 4)
    for i, (rel, tables) in enumerate(EXTRA_DBS.items()):
        img.f(H, rel, _sqlite_bytes(tables, seed=i), 3, digest=True)
    for sfx in ("", "-wal", "-shm"):
        img.f(H, TOOLKIT_CATALOGUE + sfx, _sqlite_bytes({"Tools": 30}, seed=99) if not sfx else os.urandom(4096), 3)
    img.f(H, PURPLEBUDDY, purplebuddy_plist(), 4, digest=True)
    for i in range(4):
        img.f(H, f"Library/DeviceRegistry/registry-{i}.plist", plistlib.dumps({"slot": i}, fmt=plistlib.FMT_BINARY),
              4, digest=True)
    for i in range(3):
        img.f(H, f"Library/Passes/pass-{i}.pkpass", os.urandom(5000), 3)
    img.f(H, "Library/IdentityServices/ids.db", _sqlite_bytes({"registration": 5}, seed=7), 3)
    img.f("KeychainDomain", "keychain-backup.plist", keychain_plist({"genp": 20, "inet": 5, "cert": 2, "keys": 6}),
          4, digest=True)
    # keyboard: the learned model the canary lost when KeyboardDomain was not in the set (collapse detector)
    img.f("KeyboardDomain", "Library/Keyboard/user_model_database.sqlite", os.urandom(160 * 1024), 3, digest=True)
    # PosterBoard: wallpaper / clock poster caches (class poster_cache_regenerated)
    P = POSTER_DOMAIN
    img.d(P, "")
    img.d(P, "Library")
    img.d(P, "Library/Caches")
    for i in range(12):
        img.f(P, f"Library/Caches/ClockPoster/{uuid.UUID(int=0x5000 + i)}.plist",
              plistlib.dumps({"i": i}, fmt=plistlib.FMT_BINARY), 3)
        img.f(P, f"Library/Caches/GalleryCache/{uuid.UUID(int=0x6000 + i)}.atx", os.urandom(3000), 3)
    for i in range(4):
        img.f(P, f"Library/Wallpaper/poster-{i}.heic", os.urandom(6000), 3)
    # photos: DCIM content (the dcim_unchanged guard compares AFC with these rows)
    C = "CameraRollDomain"
    for i in range(3, 3 + photos):
        img.f(C, f"Media/DCIM/100APPLE/IMG_{i:04d}.HEIC", os.urandom(photo_bytes), 3,
              claimed_size=photos_claimed_bytes)


def build_image(*, variant: str = "standard", ios_version: str = "27.0", threema_short: str = "7.4",
                store_dir: Path | None = None, omit_support_dirs: bool = False, system_domains: bool = True,
                airplane_mode: bool = True, extras: bool = False, threema_prefs: dict | None = None,
                **extra_opts) -> DeviceImage:
    img = DeviceImage()
    d, f, link = img.d, img.f, img.link

    # ---- HomeDomain -------------------------------------------------------------
    H = "HomeDomain"
    d(H, "")
    d(H, "Library")
    d(H, "Library/Preferences")
    d(H, "Library/SMS")
    f(H, "Library/Preferences/com.apple.springboard.plist",
      plistlib.dumps({"SBShowBatteryPercentage": True}, fmt=plistlib.FMT_BINARY), 4,
      xattrs={"com.apple.backup.test": b"x"})
    f(H, "Library/SMS/sms.db", os.urandom(12288), 3)
    f(H, "Library/SMS/sms.db-wal", os.urandom(4152), 3, claimed_size=4152 + 4096)   # live mismatch
    f(H, "Library/Zero.txt", b"", 4)
    link(H, "Library/PrefsLink", "Preferences")
    if system_domains:
        # realistic system rows: Digest (= SHA1 of the stored ciphertext) on HomeDomain/Keychain/other system files,
        # raw ExtendedAttributes, a 0-byte stored blob. NO Library/DeviceRegistry rows here (the backup_diff tests
        # count exactly the rows they add themselves; extras add their own).
        xa = plistlib.dumps({"com.apple.metadata:kMDItemWhereFroms": b"\x00"}, fmt=plistlib.FMT_BINARY)
        for dd in ("Library/Accounts", "Library/AddressBook", "Library/BulletinBoard", "Library/Calendar",
                   "Library/Shortcuts", "Library/SpringBoard", "Library/TCC", "Library/UserNotifications",
                   "Library/UserNotifications/Bundles"):
            d(H, dd, 4)

        def pl(o) -> bytes:
            return plistlib.dumps(o, fmt=plistlib.FMT_BINARY)
        for rel, data, pc in (
                ("Library/Preferences/.GlobalPreferences.plist",
                 pl({"AppleLanguages": ["en-CH", "de-CH"], "AppleLocale": "en_CH"}), 4),
                # no com.apple.purplebuddy.plist here: the backup_diff tests add and rewrite their own (extras: yes)
                ("Library/Preferences/com.apple.Preferences.plist", pl({"kKeyboardsEnabled": True}), 4),
                ("Library/Preferences/com.apple.migration.plist", pl({"LastSystemVersion": ios_version}), 4),
                ("Library/Accounts/Accounts3.sqlite", os.urandom(8192), 3),
                ("Library/AddressBook/AddressBook.sqlitedb", os.urandom(8192), 3),
                ("Library/BulletinBoard/VersionedSectionInfo.plist", pl({"version": 3, "sections": 12}), 4),
                ("Library/Calendar/Calendar.sqlitedb", os.urandom(16384), 3),
                ("Library/Shortcuts/Shortcuts.sqlite", os.urandom(8192), 3),
                ("Library/SpringBoard/IconState.plist", pl({"iconLists": [["a", "b"]]}), 4),
                ("Library/TCC/TCC.db", os.urandom(12288), 3),
                ("Library/UserNotifications/Bundles/com.apple.MobileSMS.plist", pl({"alertType": 1}), 4)):
            f(H, rel, data, pc, digest=True, xattrs_raw=xa if rel.endswith(".sqlite") else None)
        f(H, "Library/Preferences/com.apple.zerolength.plist", b"", 4, digest=True, empty_blob=True)
        # ---- CameraRollDomain (real: no Digest, some xattrs, one symlink) ------------------------------
        C = "CameraRollDomain"
        for dd in ("", "Media", "Media/DCIM", "Media/DCIM/100APPLE", "Media/PhotoData", "Media/PhotoData/CPL",
                   "Media/PhotoData/Caches"):
            d(C, dd, 4 if dd else 0)
        f(C, "Media/DCIM/100APPLE/IMG_0001.HEIC", os.urandom(24000), 3, xattrs_raw=xa)
        f(C, "Media/DCIM/100APPLE/IMG_0002.MOV", os.urandom(40000), 3)
        f(C, "Media/PhotoData/Photos.sqlite", os.urandom(16384), 3)
        f(C, "Media/PhotoData/CPL/storage.db", os.urandom(4096), 3)
        f(C, "Media/PhotoData/Caches/cache.plist", pl({"v": 1}), 3)
        link(C, "Media/PhotoData/Thumbs", "Caches")
        # ---- KeyboardDomain (real iOS 27: a handful of rows -- learned model + emoji adaptation; in the restore
        # set since the canary reset its model while it was absent) -----------------------------------------------
        KB = "KeyboardDomain"
        for dd in ("", "Library", "Library/Keyboard"):
            d(KB, dd, 4 if dd else 0)
        f(KB, "Library/Keyboard/emoji_adaptation.db", os.urandom(28672), 3, digest=True)
        f(KB, "Library/Keyboard/langlikelihood.dat", os.urandom(3000), 3, digest=True)
    # ---- KeychainDomain -----------------------------------------------------------
    K = "KeychainDomain"
    d(K, "")
    f(K, "keychain-backup.plist", plistlib.dumps({"genp": [], "inet": [], "cert": [], "keys": []},
                                                  fmt=plistlib.FMT_BINARY), 4, digest=system_domains)
    if system_domains:
        # ---- other system domains: must NEVER end up in a restore set -----------------------------------
        d("MediaDomain", "")
        d("MediaDomain", "Library/SMS/Attachments")
        f("MediaDomain", "Library/SMS/Attachments/00/att0.bin", os.urandom(2000), 3)
        d("HealthDomain", "")
        f("HealthDomain", "Health/healthdb_secure.sqlite", os.urandom(4096), 3, digest=True)
        d("SystemPreferencesDomain", "")
        f("SystemPreferencesDomain", "SystemConfiguration/com.apple.radios.plist",
          # flight mode ON = what the airplane guard requires before the PRE backup
          plistlib.dumps({"AirplaneMode": airplane_mode}, fmt=plistlib.FMT_BINARY), 4, digest=True)
        d("RootDomain", "")
        f("RootDomain", "Library/Preferences/com.apple.locationd.plist",
          plistlib.dumps({"x": 1}, fmt=plistlib.FMT_BINARY), 4, digest=True)
    # ---- other app (decoy) ---------------------------------------------------------
    O = "AppDomain-com.example.other"
    d(O, "")
    d(O, "Documents")
    f(O, "Documents/data.bin", os.urandom(3000), 3)
    OG = "AppDomainGroup-group.com.example.other"
    d(OG, "")
    f(OG, "shared.db", os.urandom(8192), 3)
    W = "AppDomainGroup-group.ch.threema.work"       # other Threema flavour: must NOT be trimmed in
    d(W, "")
    f(W, "ThreemaData.sqlite", os.urandom(4096), 3)
    if extras:
        add_extras(img, **extra_opts)
    if variant == "none":
        return img
    # ---- Threema app sandbox + extension --------------------------------------------
    A = "AppDomain-ch.threema.iapp"
    d(A, "")
    d(A, "Documents")
    d(A, "Library")
    d(A, "Library/Preferences")
    f(A, "Library/Preferences/ch.threema.iapp.plist",
      plistlib.dumps({"LastVersion": threema_short}, fmt=plistlib.FMT_BINARY), 3)
    f(A, "Documents/debug_log.txt", b"synthetic log line\n" * 20, 3)
    P = "AppDomainPlugin-ch.threema.iapp.ThreemaNotificationExtension"
    d(P, "")
    d(P, "Library")
    d(P, "Library/Preferences")
    f(P, "Library/Preferences/ch.threema.iapp.ThreemaNotificationExtension.plist",
      plistlib.dumps({"x": 1}, fmt=plistlib.FMT_BINARY), 3)
    # ---- Threema app group container ------------------------------------------------
    G = "AppDomainGroup-group.ch.threema"
    d(G, "")
    d(G, "Library")
    d(G, "Library/Preferences")
    d(G, "Library/Caches")
    work = Path(tempfile.mkdtemp(prefix="fixture-"))
    try:
        if variant in ("standard", "store"):
            src = v56_store("sample") if variant == "standard" else store_dir
            if src is None:
                raise ValueError("variant 'store' needs store_dir")
            snap = live_store_snapshot(Path(src), work)
            f(G, "ThreemaData.sqlite", snap["ThreemaData.sqlite"], 3)
            f(G, "ThreemaData.sqlite-wal", snap["ThreemaData.sqlite-wal"], 3)
            f(G, "ThreemaData.sqlite-shm", snap["ThreemaData.sqlite-shm"], 3)
            ext = Path(src) / ".ThreemaData_SUPPORT" / "_EXTERNAL_DATA"
            # omit_support_dirs: no .ThreemaData_SUPPORT rows at all -> inject must ADD the directory rows
            if ext.is_dir() and not omit_support_dirs:
                d(G, ".ThreemaData_SUPPORT")
                d(G, ".ThreemaData_SUPPORT/_EXTERNAL_DATA")
                for p in sorted(ext.iterdir()):
                    f(G, f".ThreemaData_SUPPORT/_EXTERNAL_DATA/{p.name}", p.read_bytes(), 3)
            prefs = {"AppSetupState": 40, "KeepMessagesDays": -1, "SendReadReceipts": True}
            # threema-fs.db is NOT in real backups: the app sets isExcludedFromBackup on its session store
        elif variant == "fresh":
            f(G, "ThreemaData.sqlite", (v56_store("empty") / "ThreemaData.sqlite").read_bytes(), 3)
            f(G, "APP_SETUP_NOT_COMPLETED", b"", 3)
            prefs = {"AppSetupState": 30, "KeepMessagesDays": 30}
        else:
            raise ValueError(variant)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if threema_prefs:
        prefs.update(threema_prefs)
    f(G, "Library/Preferences/group.ch.threema.plist", plistlib.dumps(prefs, fmt=plistlib.FMT_BINARY), 3)
    return img


# ------------------------------------------------------------------------------------------------ backup writer
def write_backup(img: DeviceImage, root: Path, passphrase: str | None, *, udid: str = DEFAULT_UDID, dpic: int = 1000,
                 iterations: int = 1000, ios_version: str = "27.0", build: str = "24A5260a",
                 product_type: str = "iPhone19,2", device_name: str = "Fixture iPhone",
                 serial: str = "FAKESERIAL01", phone: str | None = None, threema_version: str = "74051",
                 threema_short: str = "7.4", manifest_padding: str = "pkcs7",
                 date: _dt.datetime | None = None, threema_bundle: str | None = "ch.threema.iapp",
                 secret: dict | None = None, progress=None) -> dict:
    """Encrypt `img` into <root>/<udid>/ (MobileBackup2 layout). Returns {"device_dir", "udid", "plain", "flags"}.
    secret: derive_secret() result instead of the passphrase (virtual iPhone)."""
    dev = root / udid
    if dev.exists():
        raise FileExistsError(dev)
    dev.mkdir(parents=True)
    kb, class_keys = make_keybag(passphrase, dpic=dpic, iterations=iterations, secret=secret)
    plain: dict[tuple[str, str], bytes] = {}
    rows = []
    total = max(1, len(img.entries))
    for i, e in enumerate(img.entries.values()):
        if progress is not None and i % 16 == 0:
            progress(100.0 * i / total)
        if e.flags == FLAG_FILE:
            data = e.data or b""
            key = os.urandom(32)
            enc = struct.pack("<I", e.pclass) + aes_key_wrap(class_keys[e.pclass], key)
            # empty_blob: zero-length file stored as a 0-byte blob (such HomeDomain rows exist in real backups)
            ct = b"" if (e.empty_blob and not data) else rw.encrypt_file_content(data, key)
            blob = mbfile_blob(rel=e.rel, mode=e.mode, size=e.size, pclass=e.pclass, inode=e.inode, mtime=e.mtime,
                               enc_blob=enc, xattrs=e.xattrs, xattrs_raw=e.xattrs_raw, birth=e.birth,
                               digest=hashlib.sha1(ct).digest() if e.digest else None)
            plain[(e.domain, e.rel)] = data
        else:
            ct = None
            blob = mbfile_blob(rel=e.rel, mode=e.mode, size=0, pclass=e.pclass, inode=e.inode, mtime=e.mtime,
                               target=e.target, birth=e.birth)
        rows.append((e.domain, e.rel, e.flags, ct, blob))

    # ---- Manifest.db -------------------------------------------------------------------
    fd, tmp_name = tempfile.mkstemp(prefix=".manifest-", suffix=".sqlite", dir=str(root))
    os.close(fd)
    tmp_db = Path(tmp_name)
    tmp_db.unlink()
    conn = sqlite3.connect(tmp_db)
    conn.executescript("""
        CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB);
        CREATE INDEX FilesDomainIdx ON Files(domain);
        CREATE INDEX FilesRelativePathIdx ON Files(relativePath);
        CREATE INDEX FilesFlagsIdx ON Files(flags);
        CREATE TABLE Properties (key TEXT PRIMARY KEY, value BLOB);
    """)
    flags_of: dict[tuple[str, str], int] = {}
    for domain, rel, flags, ct, blob in sorted(rows, key=lambda e: (e[0], e[1])):
        fid = rw.file_id_for(domain, rel)
        conn.execute("INSERT INTO Files VALUES (?,?,?,?,?)", (fid, domain, rel, flags, blob))
        flags_of[(domain, rel)] = flags
        if ct is not None:
            (dev / fid[:2]).mkdir(exist_ok=True)
            (dev / fid[:2] / fid).write_bytes(ct)
    conn.commit()
    conn.close()
    pt = tmp_db.read_bytes()
    tmp_db.unlink()
    manifest_class = 4
    mkey = os.urandom(32)
    if manifest_padding == "pkcs7":      # what devices write (iphone_backup_decrypt strictly unpads)
        (dev / "Manifest.db").write_bytes(rw.encrypt_file_content(pt, mkey))
    else:                                 # legacy/unpadded variant (old iosbackup_rw selftest format)
        (dev / "Manifest.db").write_bytes(rw.encrypt_manifest_db(pt, mkey))

    # ---- plists --------------------------------------------------------------------------
    date = date or _dt.datetime(2026, 9, 28, 6, 0, 0)
    apps_manifest = {
        "com.example.other": {"CFBundleIdentifier": "com.example.other", "CFBundleVersion": "1",
                              "ContainerContentClass": "Data/Application",
                              "Path": f"/private/var/containers/Bundle/Application/{uuid.uuid4()}/Other.app"},
    }
    if threema_bundle:
        apps_manifest[threema_bundle] = {
            "CFBundleIdentifier": threema_bundle, "CFBundleVersion": threema_version,
            "ContainerContentClass": "Data/Application",
            "Path": f"/private/var/containers/Bundle/Application/{uuid.uuid4()}/Threema.app"}
    lockdown = {"ProductVersion": ios_version, "BuildVersion": build, "ProductType": product_type,
                "UniqueDeviceID": udid, "SerialNumber": serial, "DeviceName": device_name,
                "com.apple.MobileDeviceCrashCopy": {}, "com.apple.TerminalFlashr": {},
                "com.apple.mobile.data_sync": {}}
    manifest = {
        "BackupKeyBag": kb, "Version": "10.0", "Date": date, "SystemDomainsVersion": "24.0",
        "WasPasscodeSet": True, "IsEncrypted": True,
        "ManifestKey": struct.pack("<I", manifest_class) + aes_key_wrap(class_keys[manifest_class], mkey),
        "Lockdown": lockdown, "Applications": apps_manifest,
    }
    with (dev / "Manifest.plist").open("wb") as fh:
        plistlib.dump(manifest, fh, fmt=plistlib.FMT_BINARY)

    def itunes_meta(bid, short, ver):
        return plistlib.dumps({"softwareVersionBundleId": bid, "bundleShortVersionString": short,
                               "bundleVersion": ver, "itemName": bid.split(".")[-1]}, fmt=plistlib.FMT_BINARY)
    apps_info = {"com.example.other": {"ApplicationSINF": os.urandom(64),
                                       "iTunesMetadata": itunes_meta("com.example.other", "1.0", "1"),
                                       "PlaceholderIcon": b"\x89PNG fake"}}
    if threema_bundle:
        apps_info[threema_bundle] = {"ApplicationSINF": os.urandom(64),
                                     "iTunesMetadata": itunes_meta(threema_bundle, threema_short, threema_version),
                                     "PlaceholderIcon": b"\x89PNG fake"}
    info = {
        "Applications": apps_info,
        "Build Version": build, "Device Name": device_name, "Display Name": device_name,
        "GUID": uuid.uuid4().bytes, "Installed Applications": sorted(apps_info),
        "Product Type": product_type, "Product Version": ios_version, "Serial Number": serial,
        "Target Identifier": udid, "Target Type": "Device", "Unique Identifier": udid.upper(),
        "iTunes Files": {}, "iTunes Version": "10.0.1",
    }
    if phone:
        info["Phone Number"] = phone
    with (dev / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh, fmt=plistlib.FMT_XML)
    with (dev / "Status.plist").open("wb") as fh:
        plistlib.dump({"BackupState": "new", "Date": date, "IsFullBackup": True, "Version": "3.3",
                       "SnapshotState": "finished", "UUID": str(uuid.uuid4()).upper()}, fh, fmt=plistlib.FMT_BINARY)
    return {"device_dir": dev, "udid": udid, "plain": plain, "flags": flags_of}


def fabricate_realistic_backup(root: Path, passphrase: str, *, variant: str = "standard",
                               udid: str = DEFAULT_UDID, dpic: int = 1000,
                               iterations: int = 1000, ios_version: str = "27.0",
                               threema_version: str = "74051", threema_short: str = "7.4",
                               manifest_padding: str = "pkcs7", store_dir: Path | None = None,
                               omit_support_dirs: bool = False, system_domains: bool = True,
                               airplane_mode: bool = True) -> dict:
    """Returns {"device_dir": Path, "udid": str, "plain": {(domain, rel): bytes}, "flags": {...}}."""
    if (root / udid).exists():
        raise FileExistsError(root / udid)
    img = build_image(variant=variant, ios_version=ios_version, threema_short=threema_short, store_dir=store_dir,
                      omit_support_dirs=omit_support_dirs, system_domains=system_domains,
                      airplane_mode=airplane_mode)
    return write_backup(img, root, passphrase, udid=udid, dpic=dpic, iterations=iterations, ios_version=ios_version,
                        threema_version=threema_version, threema_short=threema_short,
                        manifest_padding=manifest_padding)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="fabricate a synthetic encrypted backup (test only). The password is "
                                             "read as ONE line from stdin.")
    ap.add_argument("root")
    ap.add_argument("--variant", default="standard", choices=["standard", "fresh", "store", "none"])
    ap.add_argument("--store", default=None, help="store dir for --variant store")
    ap.add_argument("--omit-support-dirs", action="store_true", help="store variant without .ThreemaData_SUPPORT rows")
    a = ap.parse_args()
    pw = sys.stdin.readline().rstrip("\r\n")
    r = fabricate_realistic_backup(Path(a.root), pw, variant=a.variant,
                                   store_dir=Path(a.store) if a.store else None,
                                   omit_support_dirs=a.omit_support_dirs)
    print(r["device_dir"])
    sys.exit(0)
