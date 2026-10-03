#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
backup_pipeline.py -- Threema-focused operations on ENCRYPTED iOS (MobileBackup2) backups (tmcore.lib).
Never talks to a device. Ported from the private proof of concept; behaviour unchanged except the product rules
of DESIGN §4.3: no `trim`, no `inject` with overrides, no password files.

In-process API (tmcore steps):
    rc, rep = run("extract", password=pw, backup_udid_dir=..., out_dir=...)
    rc, rep = run("restoreset", password=pw, backup_udid_dir=..., out_root=..., store_out=DIR | noop=True)
    rc, rep = run("verify", password=pw, backup_udid_dir=..., against=None, source=None, restoreset=False)
`rep` is the short summary the CLI prints; the full reports are written next to the outputs (session work/ only).

Maintainer CLI (password = ONE line on stdin, never argv, never a file):
    python3 -m tmcore.lib.backup_pipeline extract|restoreset|verify ... < password-line

Subcommands
-----------
  extract <backup_udid_dir> <out_dir>
      Decrypt every file of AppDomainGroup-group.ch.threema (ThreemaData.sqlite,
      -wal, -shm, .ThreemaData_SUPPORT/**, Library/Preferences/group.ch.threema.plist,
      APP_SETUP_NOT_COMPLETED, ...) into <out_dir>/AppDomainGroup-group.ch.threema/,
      list (not decrypt) AppDomain-ch.threema.iapp + AppDomainPlugin-ch.threema.iapp.*,
      write <out_dir>/manifest.json + <out_dir>/report.json, and build
      <out_dir>/store/ = ThreemaData.sqlite with the WAL folded in + _EXTERNAL_DATA
      (the importer's input). Reports app/iOS version, encryption, keychain presence,
      AppSetupState, KeepMessagesDays (retention), model hashes (+ the compat model id), integrity_check.

  restoreset <backup_udid_dir> <out_root> (--store-out DIR | --noop) [--report FILE]
      THE ONLY RESTORE PAYLOAD (DESIGN §7). From one fresh full encrypted backup: the Threema domains (the
      importer output injected: validated strictly -- integrity, same Core Data model, no pending WAL, no lost or
      retyped rows, no dangling _EXTERNAL_DATA refs, no unknown MBFile keys -- DB replaced, -wal/-shm set to
      ZERO-LENGTH files, new _EXTERNAL_DATA files + directory rows added; or unchanged with --noop for the
      rollback -- --noop still adds ZERO-LENGTH ThreemaData.sqlite-wal/-shm rows when the source has none, so a
      live device WAL is never replayed onto the restored DB) + the COMPLETE HomeDomain, CameraRollDomain and
      KeyboardDomain, rows and stored blobs bit-identical to the source; nothing else (no KeychainDomain, no other
      app/system domain). Plists as in the field-proven payload (Status.plist copied, Manifest.plist Applications
      filtered to Threema, Info.plist without Applications) -- rationale in cmd_restoreset.
      Self-checks, then writes <out_root>/<UDID>.restoreset.json and <out_root>/<UDID>/.RESTORESET_OK
      (= sha256 of that report). The restore refuses anything without that marker.
      There is no partial ("trim") payload in the product: the engine cannot build one (guard never_partial).

  verify <backup_udid_dir> [--against STORE_DIR] [--source SRC_UDID_DIR [--restoreset]] [--report FILE]
      Re-open from scratch with two INDEPENDENT readers (pyiosbackup: own keybag +
      bpylist2 MBFile parser; iphone_backup_decrypt: own Manifest/FilePlist path),
      decrypt every Threema file with both, compare sha256 (and with STORE_DIR),
      PRAGMA integrity_check on the extracted DB, Manifest.db consistency (fileID ==
      sha1(domain-path), blob exists, blob length == PKCS7(Size), flags vs Mode).
      --source: every non-Threema row (fileID, flags, MBFile blob) and stored blob identical to SRC;
      with --restoreset: exactly Threema + HomeDomain + CameraRollDomain + KeyboardDomain, those three identical,
      Threema identical except the inject-managed paths (--noop set: all identical except zero-length
      -wal/-shm rows added where the source had none), -wal/-shm rows present, no stray
      files, no DO_NOT_RESTORE, plists consistent, marker == sha256(builder report), report matches.
      Writes <root>/<UDID>.restoreset-verify.json in --restoreset mode.

Security
--------
* The password arrives in-process (tmcore: from the app via stdin) and is never printed, logged, or written
  anywhere. pyiosbackup's debug logger (which logs the derived key) is forced to WARNING.
* All temporary files (decrypted Manifest.db, DB copies) live in TMP_ROOT (tmcore: <session>/work/tmp, 0700,
  set with use_tmp()) and are deleted in finally blocks.
* Originals are opened read-only; restoreset/extract fingerprint the source backup before/after and fail if
  anything changed.
* Reports contain counts/structure only -- no message texts, names or identities. They stay inside the session's
  work/ folder; tmcore copies only counts into reports/ (schema report.v1).
"""
from __future__ import annotations

import argparse
import base64
import collections
import contextlib
import copy
import datetime as _dt
import hashlib
import io
import json
import logging
import os
import plistlib
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
TMP_ROOT = Path(tempfile.gettempdir())   # tmcore sets <session>/work/tmp via use_tmp(); never the bundle


def use_tmp(path: str | os.PathLike) -> Path:
    """Put every temporary file of this module and of pyiosbackup/iphone_backup_decrypt under `path` (0700)."""
    global TMP_ROOT
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    TMP_ROOT = root
    tempfile.tempdir = str(root)
    os.environ["TMPDIR"] = str(root)
    return root


from cryptography.hazmat.primitives import padding  # noqa: E402
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

try:
    from . import iosbackup_rw as rw  # noqa: E402
except ImportError:  # pragma: no cover -- loaded top-level by a lib module that is not ported yet
    import iosbackup_rw as rw  # type: ignore[no-redef]  # noqa: E402

for _name in ("pyiosbackup", "pymobiledevice3", "iphone_backup_decrypt", "bpylist2"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
THREEMA_BUNDLE = "ch.threema.iapp"
APP_DOMAIN = "AppDomain-ch.threema.iapp"
GROUP_DOMAIN = "AppDomainGroup-group.ch.threema"
PLUGIN_DOMAIN_PREFIX = "AppDomainPlugin-ch.threema.iapp."
# pymobiledevice3 regex_filter_callback semantics: re.search against
# "<domain>/<path>", "<domain>-<path>" and "<path>". The trailing "/" anchors the
# exact domain (so group.ch.threema.work / other flavours are NOT matched).
THREEMA_REGEXES = (
    r"^AppDomain-ch\.threema\.iapp/",
    r"^AppDomainGroup-group\.ch\.threema/",
    r"^AppDomainPlugin-ch\.threema\.iapp\.[^/]+/",
)

DB = "ThreemaData.sqlite"
WAL = "ThreemaData.sqlite-wal"
SHM = "ThreemaData.sqlite-shm"
JOURNAL = "ThreemaData.sqlite-journal"
SUPPORT = ".ThreemaData_SUPPORT"
EXT = ".ThreemaData_SUPPORT/_EXTERNAL_DATA"
GROUP_PLIST = "Library/Preferences/group.ch.threema.plist"
SETUP_MARKER = "APP_SETUP_NOT_COMPLETED"
METADATA_FILES = ("Info.plist", "Manifest.plist", "Manifest.db", "Status.plist")
# Restore set (docs/ROOTCAUSE.md, lead decision 2026-10-01): Threema domains + the two domains iOS 27 annotates even
# with RemoveItemsNotRestored=false ("domainsWithSystemFilesToAlwaysRemoveOnRestore") + KeyboardDomain (canary
# 2026-10-01, iOS 27.0 24A437: absent from the set it lost emoji_adaptation.db and its learned model collapsed
# to 4 KiB; the incident reset it too), complete and bit-identical to the source.
RESTORESET_SYSTEM_DOMAINS = ("HomeDomain", "CameraRollDomain", "KeyboardDomain")
RESTORESET_MARKER = ".RESTORESET_OK"      # inside <out_root>/<UDID>/, content = sha256 of <UDID>.restoreset.json
DO_NOT_RESTORE = "DO_NOT_RESTORE"         # inside <out>/<UDID>/ of diagnostics-only outputs (trim, failed builds)

FLAG_FILE, FLAG_DIR, FLAG_SYMLINK = 1, 2, 4
S_IFMT, S_IFREG, S_IFDIR, S_IFLNK = 0o170000, 0o100000, 0o040000, 0o120000
DEFAULT_PCLASS = 3          # NSFileProtectionCompleteUntilFirstUserAuthentication
FS_DB = "threema-fs.db"     # Forward-Security session store: isExcludedFromBackup=true (SQLDHSessionStore.swift:91,703-708)
# MBFile root keys we know how to carry (review restoresafety M4). Anything else on a Threema row -> REFUSED:
# e.g. a "Digest" (content hash) would go stale when we replace the content.
MBFILE_KNOWN_KEYS = {"$class", "RelativePath", "Size", "LastModified", "LastStatusChange", "Birth", "Mode", "UserID",
                     "GroupID", "InodeNumber", "ProtectionClass", "Flags", "EncryptionKey", "Target",
                     "ExtendedAttributes"}
CHUNK = 8 << 20
UUID_RE = re.compile(rb"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
Z_SYSTEM_TABLES = {"Z_METADATA", "Z_MODELCACHE", "Z_PRIMARYKEY"}
COUNT_TABLES = ("ZCONTACT", "ZCONVERSATION", "ZMESSAGE", "ZGROUP", "ZDISTRIBUTIONLIST",
                "ZFILEDATA", "ZIMAGEDATA", "ZAUDIODATA", "ZVIDEODATA", "ZMESSAGEREACTION",
                "ZBALLOT", "ZBALLOTCHOICE", "ZBALLOTRESULT", "ZCALL", "ZNONCE")


class PipelineError(Exception):
    pass


def is_threema_domain(domain: str) -> bool:
    return (domain in (APP_DOMAIN, GROUP_DOMAIN)
            or (domain.startswith(PLUGIN_DOMAIN_PREFIX) and len(domain) > len(PLUGIN_DOMAIN_PREFIX)
                and "/" not in domain))


def is_restoreset_domain(domain: str) -> bool:
    return is_threema_domain(domain) or domain in RESTORESET_SYSTEM_DOMAINS


def is_managed_group_path(rel: str) -> bool:
    """Files whose content inject owns (DB, WAL, SHM, external data)."""
    return rel in (DB, WAL, SHM) or rel.startswith(EXT + "/")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _password(args) -> str:
    """The backup password, handed over in-process (tmcore: from stdin). Never read from a file."""
    pw = getattr(args, "password", None)
    if not isinstance(pw, str) or not pw:
        raise PipelineError("no backup password given")
    return pw


def _emit(args, obj) -> None:
    """Short summary: returned to in-process callers (args.summary), printed only by the maintainer CLI."""
    args.summary = jsonable(obj)
    if not getattr(args, "quiet", False):
        print(json.dumps(args.summary, indent=1))


def load_plist(path: Path):
    data = path.read_bytes()
    fmt = plistlib.FMT_BINARY if data.startswith(b"bplist00") else plistlib.FMT_XML
    return plistlib.loads(data), fmt


def dump_plist(path: Path, obj, fmt) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(plistlib.dumps(obj, fmt=fmt))
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [jsonable(v) for v in o]
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    if isinstance(o, (bytes, bytearray)):
        return f"<{len(o)} bytes>"
    if isinstance(o, Path):
        return str(o)
    return o


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(jsonable(obj), indent=1, sort_keys=False))
    os.replace(tmp, path)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def safe_join(base: Path, rel: str) -> Path:
    parts = rel.split("/") if rel else []
    if rel.startswith("/") or any(p in ("..",) for p in parts):
        raise PipelineError(f"unsafe relative path in manifest: {rel!r}")
    return base.joinpath(*[p for p in parts if p not in ("", ".")])


def make_writable(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, os.stat(dirpath).st_mode | 0o700)
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            st = os.lstat(fp)
            if not (st.st_mode & 0o200):
                os.chmod(fp, st.st_mode | 0o200)


def clone_tree(src: Path, dst: Path) -> str:
    """APFS clone (cp -c) of a directory tree; falls back to a real copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["/bin/cp", "-c", "-R", "-p", str(src), str(dst)], capture_output=True)
    method = "apfs-clone"
    if r.returncode != 0:
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, copy_function=shutil.copy2, symlinks=True)
        method = "copy"
    make_writable(dst)
    return method


def clone_files(pairs: list[tuple[Path, Path]]) -> str:
    """Clone many single files (src -> dst), grouped per destination directory."""
    by_dir: dict[Path, list[tuple[Path, Path]]] = {}
    for s, d in pairs:
        by_dir.setdefault(d.parent, []).append((s, d))
    method = "apfs-clone"
    for ddir, items in by_dir.items():
        ddir.mkdir(parents=True, exist_ok=True)
        for i in range(0, len(items), 400):
            chunk = items[i:i + 400]
            ok = all(s.name == d.name for s, d in chunk)
            if ok:
                r = subprocess.run(["/bin/cp", "-c", "-p", *[str(s) for s, _ in chunk], str(ddir) + "/"],
                                   capture_output=True)
                ok = r.returncode == 0
            if not ok:
                method = "copy"
                for s, d in chunk:
                    shutil.copy2(s, d)
    return method


def fingerprint(dev: Path) -> dict:
    """Cheap-but-strict fingerprint of a backup dir (metadata hashes + blob stats)."""
    fp = {"metadata": {}}
    for n in METADATA_FILES:
        p = dev / n
        fp["metadata"][n] = sha256_file(p) if p.exists() else None
    count = total = 0
    newest = 0
    for dirpath, _dirs, files in os.walk(dev):
        for fn in files:
            st = os.lstat(os.path.join(dirpath, fn))
            count += 1
            total += st.st_size
            newest = max(newest, st.st_mtime_ns)
    fp.update({"files": count, "bytes": total, "newest_mtime_ns": newest})
    return fp


def check_backup_dir(dev: Path, *, require_encrypted: bool = True) -> dict:
    if not dev.is_dir():
        raise PipelineError(f"not a directory: {dev}")
    for n in METADATA_FILES:
        p = dev / n
        if not p.is_file() or p.stat().st_size == 0:
            raise PipelineError(f"backup incomplete: {n} missing or empty")
    if (dev / "Snapshot").exists():
        raise PipelineError("backup has an in-progress Snapshot/ directory (unfinished backup)")
    status, status_fmt = load_plist(dev / "Status.plist")
    if status.get("SnapshotState") != "finished":
        raise PipelineError(f"Status.plist SnapshotState={status.get('SnapshotState')!r} (need 'finished')")
    manifest, manifest_fmt = load_plist(dev / "Manifest.plist")
    info, info_fmt = load_plist(dev / "Info.plist")
    if require_encrypted and not manifest.get("IsEncrypted"):
        raise PipelineError("backup is NOT encrypted (keychain missing; this pipeline only writes "
                            "encrypted backups) -- enable backup encryption and back up again")
    return {"status": status, "manifest": manifest, "info": info,
            "fmt": {"Status.plist": status_fmt, "Manifest.plist": manifest_fmt, "Info.plist": info_fmt}}


def resolve_out_device_dir(out: Path, udid: str) -> Path:
    return out if out.name == udid else out / udid


def guard_out(src: Path, out_dev: Path) -> None:
    src_r, out_r = src.resolve(), out_dev.resolve()
    if out_r == src_r or src_r in out_r.parents or out_r in src_r.parents:
        raise PipelineError("output must not overlap the source backup")
    if out_dev.exists():
        raise PipelineError(f"output already exists (refusing to overwrite): {out_dev}")


# --------------------------------------------------------------------------- #
# Streaming per-file crypto (AES-256-CBC, zero IV, PKCS7(128))
# --------------------------------------------------------------------------- #
def _cbc(key: bytes):
    return Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16))


def encrypt_to(dst: Path, key: bytes, *, src: Path | None = None, data: bytes | None = None) -> tuple[int, str]:
    enc = _cbc(key).encryptor()
    pad = padding.PKCS7(128).padder()
    h = hashlib.sha256()
    n = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp-{uuid.uuid4().hex}")
    try:
        with open(tmp, "wb") as out:
            def feed(chunk: bytes) -> None:
                nonlocal n
                h.update(chunk)
                n += len(chunk)
                out.write(enc.update(pad.update(chunk)))
            if data is not None:
                feed(data)
            else:
                with open(src, "rb") as f:
                    while chunk := f.read(CHUNK):
                        feed(chunk)
            out.write(enc.update(pad.finalize()) + enc.finalize())
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()
    return n, h.hexdigest()


def decrypt_to(src: Path, key: bytes, dst: Path | None = None) -> tuple[int, str]:
    dec = _cbc(key).decryptor()
    unpad = padding.PKCS7(128).unpadder()
    h = hashlib.sha256()
    n = 0
    out = open(dst, "wb") if dst is not None else None
    try:
        with open(src, "rb") as f:
            while chunk := f.read(CHUNK):
                pt = unpad.update(dec.update(chunk))
                h.update(pt)
                n += len(pt)
                if out:
                    out.write(pt)
        pt = unpad.update(dec.finalize()) + unpad.finalize()
        h.update(pt)
        n += len(pt)
        if out:
            out.write(pt)
    finally:
        if out:
            out.close()
    return n, h.hexdigest()


def padded_len(size: int) -> int:
    return (size // 16 + 1) * 16


# --------------------------------------------------------------------------- #
# MBFile (NSKeyedArchiver) -- parse/modify preserving every original field
# --------------------------------------------------------------------------- #
class MBFile:
    def __init__(self, blob: bytes):
        self.arch = plistlib.loads(blob)
        self.objs = self.arch["$objects"]
        self.root = self.objs[self.arch["$top"]["root"].data]

    def _deref(self, v):
        return self.objs[v.data] if isinstance(v, plistlib.UID) else v

    @property
    def relative_path(self) -> str | None:
        v = self.root.get("RelativePath")
        return None if v is None else self._deref(v)

    @property
    def enc_blob(self) -> bytes | None:
        v = self.root.get("EncryptionKey")
        if v is None:
            return None
        o = self._deref(v)
        return bytes(o["NS.data"]) if isinstance(o, dict) else bytes(o)

    @property
    def target(self) -> str | None:
        v = self.root.get("Target")
        return None if v is None else self._deref(v)

    def i(self, key: str, default: int = 0) -> int:
        v = self.root.get(key, default)
        return int(v) if v is not None else default

    size = property(lambda s: s.i("Size"))
    pclass = property(lambda s: s.i("ProtectionClass"))
    mode = property(lambda s: s.i("Mode"))
    uid = property(lambda s: s.i("UserID", 501))
    gid = property(lambda s: s.i("GroupID", 501))
    inode = property(lambda s: s.i("InodeNumber"))
    mtime = property(lambda s: s.i("LastModified"))
    mbflags = property(lambda s: s.i("Flags"))

    def key_class(self) -> int | None:
        b = self.enc_blob
        return struct.unpack("<I", b[:4])[0] if b and len(b) >= 4 else None

    def set_fields(self, **kw) -> None:
        self.root.update(kw)

    def set_encryption_key(self, enc_blob: bytes) -> None:
        """Set/replace EncryptionKey (NSMutableData) -- used when a row had no key (empty file)."""
        UID = plistlib.UID
        v = self.root.get("EncryptionKey")
        if v is not None and isinstance(self._deref(v), dict):
            self._deref(v)["NS.data"] = enc_blob
        else:
            cls_idx = next((i for i, o in enumerate(self.objs) if isinstance(o, dict)
                            and o.get("$classname") == "NSMutableData"), None)
            if cls_idx is None:
                self.objs.append({"$classname": "NSMutableData",
                                  "$classes": ["NSMutableData", "NSData", "NSObject"]})
                cls_idx = len(self.objs) - 1
            self.objs.append({"NS.data": enc_blob, "$class": UID(cls_idx)})
            self.root["EncryptionKey"] = UID(len(self.objs) - 1)
        self.root["ProtectionClass"] = struct.unpack("<I", enc_blob[:4])[0]

    def dumps(self) -> bytes:
        return plistlib.dumps(self.arch, fmt=plistlib.FMT_BINARY)

    def unknown_keys(self) -> set[str]:
        return set(self.root) - MBFILE_KNOWN_KEYS

    def summary(self) -> dict:
        return {"size": self.size, "protectionClass": self.pclass, "keyClass": self.key_class(),
                "mode": oct(self.mode), "uid": self.uid, "gid": self.gid, "inode": self.inode,
                "mtime": self.mtime, "birth": self.i("Birth"), "mbflags": self.mbflags,
                "hasKey": self.enc_blob is not None}


def build_mbfile(*, relative_path: str, mode: int, uid: int, gid: int, size: int, pclass: int,
                 mtime: int, inode: int, mbflags: int = 0, enc_blob: bytes | None = None) -> bytes:
    """Canonical iOS MBFile archive layout:
    [$null, MBFile, RelativePath, (EncryptionKey NSMutableData, NSMutableData class), MBFile class]."""
    UID = plistlib.UID
    objs: list = ["$null"]
    root: dict = {}
    objs.append(root)                 # 1
    objs.append(relative_path)        # 2
    root.update({"Size": size, "LastModified": mtime, "LastStatusChange": mtime, "Birth": mtime,
                 "Mode": mode, "UserID": uid, "GroupID": gid, "InodeNumber": inode,
                 "ProtectionClass": pclass, "Flags": mbflags, "RelativePath": UID(2)})
    if enc_blob is not None:
        objs.append({"NS.data": enc_blob, "$class": UID(4)})                       # 3
        objs.append({"$classname": "NSMutableData",
                     "$classes": ["NSMutableData", "NSData", "NSObject"]})         # 4
        root["EncryptionKey"] = UID(3)
    objs.append({"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]})
    root["$class"] = UID(len(objs) - 1)
    return plistlib.dumps({"$version": 100000, "$archiver": "NSKeyedArchiver",
                           "$top": {"root": UID(1)}, "$objects": objs}, fmt=plistlib.FMT_BINARY)


# --------------------------------------------------------------------------- #
# Manifest.db working session (decrypted copy under build/tmp)
# --------------------------------------------------------------------------- #
@dataclass
class Row:
    file_id: str
    domain: str
    rel: str
    flags: int
    blob: bytes
    _mb: MBFile | None = None

    @property
    def mb(self) -> MBFile:
        if self._mb is None:
            self._mb = MBFile(self.blob)
        return self._mb


def strip_manifest_padding(pt: bytes) -> tuple[bytes, bool]:
    """Real device Manifest.db ciphertext = AES-CBC(PKCS7(sqlite)) -- iphone_backup_decrypt >= 0.10
    strictly unpads it. pymobiledevice3/pyiosbackup decrypt without unpadding (the 16 pad bytes then
    trail the SQLite file harmlessly). Strip only a *provable* PKCS7 tail (page-size arithmetic), so
    an unpadded legacy file is never shortened."""
    if not pt.startswith(b"SQLite format 3\x00"):
        raise PipelineError("Manifest.db did not decrypt to SQLite (wrong key/password or corrupt)")
    page = struct.unpack(">H", pt[16:18])[0]
    page = 65536 if page == 1 else page
    n = pt[-1]
    if (1 <= n <= 16 and pt[-n:] == bytes([n]) * n and len(pt) % page != 0
            and (len(pt) - n) % page == 0):
        return pt[:-n], True
    return pt, False


def encrypt_manifest_bytes(sqlite_bytes: bytes, key: bytes, padded: bool) -> bytes:
    if padded:
        p = padding.PKCS7(128).padder()
        data = p.update(sqlite_bytes) + p.finalize()
    else:
        data = sqlite_bytes + b"\x00" * ((16 - len(sqlite_bytes) % 16) % 16)
    enc = _cbc(key).encryptor()
    return enc.update(data) + enc.finalize()


class ManifestSession:
    def __init__(self, bk: rw.EncryptedBackup):
        self.bk = bk
        self.tmpdir = Path(tempfile.mkdtemp(prefix="manifest-", dir=TMP_ROOT))
        self.path = self.tmpdir / "Manifest.db"
        try:
            ct = (bk.device_dir / "Manifest.db").read_bytes()
            if len(ct) % 16:
                raise PipelineError("Manifest.db ciphertext not 16-byte aligned")
            pt, self.padded = strip_manifest_padding(rw.decrypt_manifest_db(ct, bk._manifest_key))
        except BaseException:
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            raise
        self.path.write_bytes(pt)
        self.conn = sqlite3.connect(self.path)
        try:
            self.journal_mode = self.conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.conn.execute("SELECT count(*) FROM Files").fetchone()
        except sqlite3.DatabaseError as e:
            self.close()
            raise PipelineError(f"Manifest.db did not decrypt to a valid SQLite DB ({e})") from None

    def rows(self, domain: str | None = None, where: str = "", params: tuple = ()) -> list[Row]:
        q = "SELECT fileID, domain, relativePath, flags, file FROM Files"
        cond, prm = [], []
        if domain is not None:
            cond.append("domain = ?")
            prm.append(domain)
        if where:
            cond.append(where)
            prm.extend(params)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY domain, relativePath"
        return [Row(*r) for r in self.conn.execute(q, prm)]

    def get(self, domain: str, rel: str) -> Row | None:
        r = self.conn.execute("SELECT fileID, domain, relativePath, flags, file FROM Files "
                              "WHERE domain = ? AND relativePath = ?", (domain, rel)).fetchone()
        return Row(*r) if r else None

    def exists_id(self, file_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM Files WHERE fileID = ?", (file_id,)).fetchone() is not None

    def update_blob(self, file_id: str, blob: bytes) -> None:
        self.conn.execute("UPDATE Files SET file = ? WHERE fileID = ?", (blob, file_id))

    def insert(self, file_id: str, domain: str, rel: str, flags: int, blob: bytes) -> None:
        self.conn.execute("INSERT INTO Files(fileID, domain, relativePath, flags, file) VALUES(?,?,?,?,?)",
                          (file_id, domain, rel, flags, blob))

    def commit_and_encrypt(self) -> None:
        self.conn.commit()
        if str(self.journal_mode).lower() == "wal":
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()
        self.conn = None
        wal = self.path.with_name(self.path.name + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise PipelineError("Manifest.db WAL not checkpointed")
        ct = encrypt_manifest_bytes(self.path.read_bytes(), self.bk._manifest_key, self.padded)
        dst = self.bk.device_dir / "Manifest.db"
        tmp = dst.with_name(".Manifest.db.tmp")
        tmp.write_bytes(ct)
        os.replace(tmp, dst)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)


def file_key(bk: rw.EncryptedBackup, mb: MBFile) -> bytes:
    blob = mb.enc_blob
    if not blob or len(blob) != 4 + rw.WRAPPED_KEY_LEN:
        raise PipelineError(f"row has no usable EncryptionKey: {mb.relative_path!r}")
    cls = struct.unpack("<I", blob[:4])[0]
    return bk._keybag.unwrap_key_for_class(cls, blob[4:])


def stored_path(dev: Path, file_id: str) -> Path:
    return dev / file_id[:2] / file_id


# --------------------------------------------------------------------------- #
# Core Data store analysis (counts only, no content)
# --------------------------------------------------------------------------- #
def _uri(path: Path, *, immutable: bool) -> str:
    q = "mode=ro&immutable=1" if immutable else "mode=ro"
    return f"file:{urllib.parse.quote(str(path))}?{q}"


def header_journal_mode(db: Path) -> str:
    with open(db, "rb") as f:
        head = f.read(20)
    if not head.startswith(b"SQLite format 3\x00"):
        return "not-sqlite"
    return {1: "rollback", 2: "wal"}.get(head[18], f"unknown({head[18]})")


def _resources_dir() -> Path:
    env = os.environ.get("TMCORE_RESOURCES")
    return Path(env) if env else LIB_DIR.parents[2]


def known_models() -> dict[str, str]:
    """compat/threema-ios.json: {version_hashes_sha256: model id} of the models the importer may write (DESIGN §6.3).
    version_hashes_sha256 = sha256 of json.dumps(NSStoreModelVersionHashes as base64, sort_keys=True)."""
    try:
        data = json.loads((_resources_dir() / "compat" / "threema-ios.json").read_text(encoding="utf-8"))
        return {m["version_hashes_sha256"]: m["id"] for m in data.get("models", [])
                if m.get("status") == "verified" and m.get("version_hashes_sha256")}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def store_metadata(conn: sqlite3.Connection, schema: str = "main") -> dict:
    out: dict = {}
    try:
        row = conn.execute(f"SELECT Z_VERSION, Z_UUID, Z_PLIST FROM {schema}.Z_METADATA").fetchone()
    except sqlite3.DatabaseError:
        return {"error": "no Z_METADATA"}
    if not row:
        return {"error": "empty Z_METADATA"}
    out["store_uuid"] = row[1]
    try:
        md = plistlib.loads(row[2])
    except Exception:
        return {**out, "error": "Z_PLIST unreadable"}
    hashes = {k: base64.b64encode(v).decode() for k, v in (md.get("NSStoreModelVersionHashes") or {}).items()}
    out["model_version_identifiers"] = list(md.get("NSStoreModelVersionIdentifiers") or [])
    out["model_hashes"] = hashes
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    out["model_hashes_digest"] = digest[:16]
    models = known_models()
    out["model_id"] = models.get(digest) if hashes else None
    out["model_matches_v56_reference"] = (out["model_id"] == "V56") if models else None
    out["framework_version"] = md.get("NSPersistenceFrameworkVersion")
    return out


def external_refs(conn: sqlite3.Connection, schema: str = "main") -> set[str]:
    refs: set[str] = set()
    tables = [r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")]
    for t in tables:
        cols = [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info('{t}')") if (r[2] or "").upper() == "BLOB"]
        for c in cols:
            q = (f'SELECT "{c}" FROM {schema}."{t}" WHERE typeof("{c}")=\'blob\' AND length("{c}")=38 '
                 f'AND substr("{c}",1,1)=x\'02\'')
            for (v,) in conn.execute(q):
                body = bytes(v[1:37])
                if v[37] == 0 and UUID_RE.match(body):
                    refs.add(body.decode())
    return refs


def analyze_store(db: Path, *, immutable: bool, ext_names: set[str] | None) -> dict:
    """Integrity + counts + model metadata + external-ref consistency. Opens read-only."""
    res: dict = {"sha256": sha256_file(db), "size": db.stat().st_size,
                 "journal_mode_header": header_journal_mode(db)}
    conn = sqlite3.connect(_uri(db, immutable=immutable), uri=True)
    try:
        ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchmany(20)]
        res["integrity_check"] = "ok" if ic == ["ok"] else ic
        counts = {}
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in COUNT_TABLES:
            if t in names:
                counts[t] = conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        if "ZMESSAGE" in names and "Z_PRIMARYKEY" in names:
            ent = dict(conn.execute("SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY"))
            counts["ZMESSAGE_by_entity"] = {ent.get(e, str(e)): n for e, n in
                                            conn.execute("SELECT Z_ENT, count(*) FROM ZMESSAGE GROUP BY Z_ENT")}
        res["counts"] = counts
        res["metadata"] = store_metadata(conn)
        refs = external_refs(conn)
        res["external_refs"] = len(refs)
        if ext_names is not None:
            missing = sorted(refs - ext_names)
            res["missing_external_refs"] = len(missing)
            res["unreferenced_external_files"] = len(ext_names - refs)
        res["_refs"] = refs
    finally:
        conn.close()
    return res


def public(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def fold_store_copy(src_dir: Path, dst_dir: Path) -> dict:
    """Copy DB+WAL+SHM into dst_dir and checkpoint the WAL into the main file (copy only)."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for n in (DB, WAL, SHM):
        if (src_dir / n).exists():
            shutil.copy2(src_dir / n, dst_dir / n)
    info = {"wal_bytes_before": (src_dir / WAL).stat().st_size if (src_dir / WAL).exists() else None}
    conn = sqlite3.connect(dst_dir / DB)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        info["journal_mode"] = mode
        if str(mode).lower() == "wal":
            busy, log, ckpt = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            info["checkpoint"] = {"busy": busy, "log_frames": log, "checkpointed": ckpt}
            if busy:
                raise PipelineError("WAL checkpoint busy while folding store copy")
    finally:
        conn.close()
    for n in (WAL, SHM):
        p = dst_dir / n
        if p.exists():
            if n == WAL and p.stat().st_size:
                raise PipelineError("WAL still non-empty after checkpoint")
            p.unlink()
    return info


# --------------------------------------------------------------------------- #
# Reporting helpers for plists
# --------------------------------------------------------------------------- #
def app_versions(manifest: dict, info: dict) -> dict:
    out: dict = {"bundle": THREEMA_BUNDLE}
    ma = (manifest.get("Applications") or {}).get(THREEMA_BUNDLE)
    if isinstance(ma, dict):
        out["manifest_plist"] = {k: ma.get(k) for k in
                                 ("CFBundleVersion", "CFBundleShortVersionString", "ContainerContentClass")
                                 if k in ma}
    ia = (info.get("Applications") or {}).get(THREEMA_BUNDLE)
    if isinstance(ia, dict):
        d = {k: ia.get(k) for k in ("CFBundleVersion", "CFBundleShortVersionString") if k in ia}
        meta = ia.get("iTunesMetadata")
        if isinstance(meta, (bytes, bytearray)):
            try:
                m = plistlib.loads(bytes(meta))
                d["itunes_bundleVersion"] = m.get("bundleVersion")
                d["itunes_bundleShortVersionString"] = m.get("bundleShortVersionString")
            except Exception:
                d["itunes_metadata"] = "unparseable"
        out["info_plist"] = d
    out["listed_in_installed_applications"] = THREEMA_BUNDLE in (info.get("Installed Applications") or [])
    cands = [(out.get("manifest_plist") or {}).get("CFBundleVersion"),
             (out.get("info_plist") or {}).get("CFBundleVersion"),
             (out.get("info_plist") or {}).get("itunes_bundleVersion")]
    out["CFBundleVersion"] = next((c for c in cands if c), None)
    short = [(out.get("manifest_plist") or {}).get("CFBundleShortVersionString"),
             (out.get("info_plist") or {}).get("CFBundleShortVersionString"),
             (out.get("info_plist") or {}).get("itunes_bundleShortVersionString")]
    out["CFBundleShortVersionString"] = next((c for c in short if c), None)
    return out


def device_summary(pl: dict, dev: Path) -> dict:
    m, i, s = pl["manifest"], pl["info"], pl["status"]
    lock = m.get("Lockdown") or {}
    return {"udid": dev.name, "encrypted": bool(m.get("IsEncrypted")),
            "ios_version": lock.get("ProductVersion") or i.get("Product Version"),
            "ios_build": lock.get("BuildVersion") or i.get("Build Version"),
            "info_plist_product_version": i.get("Product Version"),
            "product_type": lock.get("ProductType") or i.get("Product Type"),
            "manifest_version": m.get("Version"), "system_domains_version": m.get("SystemDomainsVersion"),
            "was_passcode_set": m.get("WasPasscodeSet"),
            "backup_date": s.get("Date"),
            "status": {k: s.get(k) for k in ("BackupState", "SnapshotState", "IsFullBackup", "Version")},
            "manifest_apps": len(m.get("Applications") or {}),
            "info_apps": len(i.get("Applications") or {})}


def group_prefs_summary(plist_path: Path | None) -> dict:
    out: dict = {"present": bool(plist_path and plist_path.exists())}
    if not out["present"]:
        return out
    try:
        d, _ = load_plist(plist_path)
    except Exception:
        out["error"] = "unparseable"
        return out
    kmd = d.get("KeepMessagesDays")
    out["AppSetupState"] = d.get("AppSetupState")
    out["KeepMessagesDays"] = kmd
    # MessageRetentionManagerModel.swift:61 -- deletion only when keepMessagesDays > 0
    out["retention_active"] = isinstance(kmd, int) and kmd > 0
    other = {k: d[k] for k in d if "keep" in k.lower() and "message" in k.lower() and k != "KeepMessagesDays"}
    if other:
        out["other_keep_message_keys"] = other
    return out


# --------------------------------------------------------------------------- #
# extract
# --------------------------------------------------------------------------- #
def cmd_extract(args) -> int:
    dev = Path(args.backup_udid_dir).expanduser().resolve()
    out = Path(args.out_dir).expanduser().resolve()
    pl = check_backup_dir(dev, require_encrypted=False)
    if not pl["manifest"].get("IsEncrypted"):
        write_json(out / "report.json", {"tool": "backup_pipeline extract", "generated_at": now_iso(),
                                         "backup": device_summary(pl, dev), "error": "backup not encrypted"})
        raise PipelineError("backup is NOT encrypted -- enable encryption and create a new backup")
    if out.exists() and any(out.iterdir()):
        raise PipelineError(f"out_dir exists and is not empty: {out}")
    pw = _password(args)
    fp0 = fingerprint(dev)
    bk = rw.EncryptedBackup(dev, pw)
    ms = ManifestSession(bk)
    warnings: list[str] = []
    errors: list[str] = []
    report: dict = {"tool": "backup_pipeline extract", "generated_at": now_iso(),
                    "backup": device_summary(pl, dev), "threema_app": app_versions(pl["manifest"], pl["info"])}
    try:
        out.mkdir(parents=True, exist_ok=True)
        os.chmod(out, 0o700)
        all_counts = ms.conn.execute("SELECT count(*), count(DISTINCT domain) FROM Files").fetchone()
        report["backup"]["manifest_rows"], report["backup"]["domains"] = all_counts
        kc = ms.rows("KeychainDomain")
        kc_file = [r for r in kc if r.flags == FLAG_FILE]
        report["keychain"] = {"present": bool(kc_file), "rows": len(kc),
                              "files": [{"relativePath": r.rel, "size": r.mb.size,
                                         "protectionClass": r.mb.pclass} for r in kc_file]}
        if not kc_file:
            warnings.append("no KeychainDomain file rows (keychain not in backup)")

        dom_dir = out / GROUP_DOMAIN
        entries = []
        dom_stats: dict = {}
        threema_domains = [d for (d,) in ms.conn.execute("SELECT DISTINCT domain FROM Files ORDER BY domain")
                           if is_threema_domain(d)]
        for domain in threema_domains:
            st = dom_stats.setdefault(domain, {"rows": 0, "files": 0, "dirs": 0, "symlinks": 0, "bytes": 0})
            for r in ms.rows(domain):
                mb = r.mb
                st["rows"] += 1
                e = {"domain": domain, "relativePath": r.rel, "fileID": r.file_id, "flags": r.flags,
                     **mb.summary()}
                if r.flags == FLAG_DIR:
                    st["dirs"] += 1
                    if domain == GROUP_DOMAIN:
                        safe_join(dom_dir, r.rel).mkdir(parents=True, exist_ok=True)
                elif r.flags == FLAG_SYMLINK:
                    st["symlinks"] += 1
                    e["target"] = mb.target
                elif r.flags == FLAG_FILE:
                    st["files"] += 1
                    st["bytes"] += mb.size
                    blob = stored_path(dev, r.file_id)
                    e["hasBlob"] = blob.exists()
                    e["blobSize"] = blob.stat().st_size if blob.exists() else None
                    if domain == GROUP_DOMAIN and mb.enc_blob is None:
                        if mb.size:
                            errors.append(f"group-domain file without EncryptionKey: {r.rel}")
                        else:
                            dst = safe_join(dom_dir, r.rel)
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            dst.write_bytes(b"")
                            e.update({"decrypted": True, "sha256": hashlib.sha256(b"").hexdigest(),
                                      "decryptedSize": 0, "extractedPath": str(dst.relative_to(out)),
                                      "note": "empty file without key"})
                    elif domain == GROUP_DOMAIN:
                        if not blob.exists():
                            errors.append(f"missing blob for group-domain file {r.rel}")
                        else:
                            dst = safe_join(dom_dir, r.rel)
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            n, h = decrypt_to(blob, file_key(bk, mb), dst)
                            os.utime(dst, (mb.mtime or time.time(), mb.mtime or time.time()))
                            e.update({"decrypted": True, "sha256": h, "decryptedSize": n,
                                      "extractedPath": str(dst.relative_to(out))})
                            if n != mb.size:
                                warnings.append(f"size mismatch (live file?) {r.rel}: manifest {mb.size}, "
                                                f"decrypted {n}")
                    else:
                        e["decrypted"] = False
                entries.append(e)
        report["threema_domains"] = dom_stats
        if GROUP_DOMAIN not in dom_stats:
            errors.append("AppDomainGroup-group.ch.threema not in backup (Threema not installed/backed up?)")
        if APP_DOMAIN not in dom_stats:
            warnings.append("AppDomain-ch.threema.iapp not in backup")
        write_json(out / "manifest.json", {"generated_at": now_iso(), "udid": dev.name, "entries": entries})

        # group container facts
        ext_dir = dom_dir / EXT
        ext_files = sorted(p for p in ext_dir.iterdir() if p.is_file()) if ext_dir.is_dir() else []
        gc = {"db_present": (dom_dir / DB).exists(),
              "wal_size": (dom_dir / WAL).stat().st_size if (dom_dir / WAL).exists() else None,
              "shm_size": (dom_dir / SHM).stat().st_size if (dom_dir / SHM).exists() else None,
              "external_files": len(ext_files), "external_bytes": sum(p.stat().st_size for p in ext_files),
              "app_setup_not_completed_marker": (dom_dir / SETUP_MARKER).exists(),
              "threema_fs_db_in_backup": (dom_dir / FS_DB).exists(),
              "group_prefs": group_prefs_summary(dom_dir / GROUP_PLIST)}
        report["group_container"] = gc
        if not gc["threema_fs_db_in_backup"]:
            warnings.append(f"{FS_DB} is not in the backup (Threema marks it excluded from backup). If the restore "
                            "empties the group container first, the Forward-Security sessions are gone: once online "
                            "iOS answers with FS reject/terminate and adds 'session reset' notes (new traffic, no "
                            "history loss). Rollback cannot restore it either.")
        if gc["app_setup_not_completed_marker"]:
            warnings.append("APP_SETUP_NOT_COMPLETED marker present: Threema setup not completed on device")
        gp = gc["group_prefs"]
        if gp.get("retention_active"):
            warnings.append(f"KeepMessagesDays={gp.get('KeepMessagesDays')} > 0: message retention would DELETE "
                            "old imported messages -- set 'Keep messages' to Forever on the iPhone first")
        if gp.get("present") and gp.get("AppSetupState") not in (None, 40):
            warnings.append(f"AppSetupState={gp.get('AppSetupState')} (40 = complete)")

        # importer input: folded store copy
        if gc["db_present"]:
            store = out / "store"
            fold = fold_store_copy(dom_dir, store)
            if ext_dir.is_dir():
                clone_tree(dom_dir / SUPPORT, store / SUPPORT)
            ext_names = {p.name for p in ext_files}
            a = analyze_store(store / DB, immutable=True, ext_names=ext_names)
            report["store"] = {"path": "store/" + DB, "fold": fold, **public(a)}
            if a["integrity_check"] != "ok":
                errors.append("integrity_check of folded store failed")
            if a.get("missing_external_refs"):
                warnings.append(f"{a['missing_external_refs']} external-data references without file")
            if a["metadata"].get("model_matches_v56_reference") is False:
                warnings.append("store model hashes are not a verified model of compat/threema-ios.json "
                                "(installed app model != V56?)")
    finally:
        ms.close()
    fp1 = fingerprint(dev)
    report["source_unchanged"] = fp0 == fp1
    if fp0 != fp1:
        errors.append("SOURCE BACKUP CHANGED during extract")
    report["warnings"], report["errors"] = warnings, errors
    write_json(out / "report.json", report)
    _emit(args, _short(report))
    return 1 if errors else 0


def _short(report: dict) -> dict:
    keep = ("tool", "backup", "threema_app", "keychain", "threema_domains", "group_container", "store",
            "result", "source_unchanged", "warnings", "errors", "output", "report")
    r = {k: report[k] for k in keep if k in report}
    if "store" in r:
        r["store"] = {k: v for k, v in r["store"].items() if k != "metadata"} | {
            "model": {k: r["store"].get("metadata", {}).get(k) for k in
                      ("model_version_identifiers", "model_matches_v56_reference", "model_hashes_digest",
                       "model_id")}}
    return r


# --------------------------------------------------------------------------- #
# inject
# --------------------------------------------------------------------------- #
def _inspect_store_out(store: Path) -> dict:
    if not (store / DB).is_file():
        raise PipelineError(f"store_out_dir has no {DB}")
    errs = []
    if header_journal_mode(store / DB) == "not-sqlite":
        errs.append(f"{DB} is not a SQLite file")
    wal = store / WAL
    if wal.exists() and wal.stat().st_size:
        errs.append(f"{WAL} is non-empty: fold the WAL into the main file first "
                    "(checkpoint / journal_mode=DELETE) -- the main file alone would be incomplete")
    j = store / JOURNAL
    if j.exists() and j.stat().st_size:
        errs.append(f"{JOURNAL} (hot rollback journal) present: store not cleanly closed")
    ext = store / EXT
    files = []
    if ext.exists():
        for p in sorted(ext.iterdir()):
            if p.is_symlink() or not p.is_file():
                errs.append(f"unexpected non-file in {EXT}: {p.name}")
            else:
                files.append(p)
    ignored = []
    for dirpath, dirs, fns in os.walk(store):
        for fn in fns:
            rel = os.path.relpath(os.path.join(dirpath, fn), store)
            if rel in (DB, WAL, SHM) or rel.startswith(EXT + "/") or fn == ".DS_Store":
                continue
            ignored.append(rel)
    return {"errors": errs, "external_files": files, "ignored": ignored}


def _compare_stores(new_db: Path, orig_db: Path) -> dict:
    """Row-loss / schema / model comparison between importer output and the device store."""
    res: dict = {}
    conn = sqlite3.connect(_uri(new_db, immutable=True), uri=True)
    try:
        conn.execute(f"ATTACH DATABASE '{_uri(orig_db, immutable=True)}' AS o")

        def schema(s):
            return sorted(conn.execute(f"SELECT type, name, tbl_name, sql FROM {s}.sqlite_master "
                                       "WHERE name NOT LIKE 'sqlite_%'").fetchall(), key=lambda r: (r[0], r[1]))
        res["schema_identical"] = schema("main") == schema("o")
        mo, mn = store_metadata(conn, "o"), store_metadata(conn, "main")
        res["model_identical"] = (mo.get("model_hashes") == mn.get("model_hashes")
                                  and bool(mo.get("model_hashes")))
        res["store_uuid_identical"] = mo.get("store_uuid") == mn.get("store_uuid")
        res["model_version_identifiers"] = {"device": mo.get("model_version_identifiers"),
                                            "import": mn.get("model_version_identifiers")}
        main_tables = {r[0] for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
        lost, retyped, modified = {}, {}, {}
        for (t,) in conn.execute("SELECT name FROM o.sqlite_master WHERE type='table' AND name LIKE 'Z%'").fetchall():
            if t in Z_SYSTEM_TABLES:
                continue
            n_orig = conn.execute(f'SELECT count(*) FROM o."{t}"').fetchone()[0]
            if t not in main_tables:
                if n_orig:
                    lost[t] = n_orig
                continue
            cols_o = [r[1] for r in conn.execute(f"PRAGMA o.table_info('{t}')")]
            cols_n = [r[1] for r in conn.execute(f"PRAGMA main.table_info('{t}')")]
            if "Z_PK" in cols_o:
                n = conn.execute(f'SELECT count(*) FROM o."{t}" WHERE Z_PK NOT IN (SELECT Z_PK FROM main."{t}")'
                                 ).fetchone()[0]
                if "Z_ENT" in cols_o and "Z_ENT" in cols_n:
                    rt = conn.execute(f'SELECT count(*) FROM o."{t}" a JOIN main."{t}" b USING(Z_PK) '
                                      'WHERE a.Z_ENT != b.Z_ENT').fetchone()[0]
                    if rt:
                        retyped[t] = rt
                if cols_o == cols_n:
                    cmp = [c for c in cols_o if c != "Z_OPT"]
                    sel = ", ".join(f'"{c}"' for c in cmp)
                    md = conn.execute(f'SELECT count(*) FROM (SELECT {sel} FROM o."{t}" EXCEPT '
                                      f'SELECT {sel} FROM main."{t}" WHERE Z_PK IN (SELECT Z_PK FROM o."{t}"))'
                                      ).fetchone()[0] - n
                    if md > 0:
                        modified[t] = md
            elif cols_o == cols_n:
                n = conn.execute(f'SELECT count(*) FROM (SELECT * FROM o."{t}" EXCEPT SELECT * FROM main."{t}")'
                                 ).fetchone()[0]
            else:
                n = n_orig
            if n:
                lost[t] = n
        res["lost_rows"] = lost
        res["retyped_rows"] = retyped
        res["modified_existing_rows"] = modified
    finally:
        conn.close()
    return res


def _alloc_inodes(rows: list[Row]):
    cur = max([r.mb.inode for r in rows] + [0])

    def nxt() -> int:
        nonlocal cur
        cur += 1
        return cur
    return nxt


def _dir_template(rows: list[Row], rel: str) -> MBFile | None:
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    dirs = [r for r in rows if r.flags == FLAG_DIR]
    siblings = [r for r in dirs if (r.rel.rsplit("/", 1)[0] if "/" in r.rel else "") == parent and r.rel]
    for cand in (siblings, [r for r in dirs if r.rel == parent], [r for r in dirs if r.rel == ""], dirs):
        if cand:
            return cand[0].mb
    return None


def _inject_validate(src: Path, store: Path, bk_src: rw.EncryptedBackup) -> tuple[dict, list, list, dict]:
    """Phase 1 of inject (read-only): validate the importer output against the device store of `src`. Strict, no
    switches (the proof of concept's --allow-* options are gone, DESIGN §6.1).
    Returns (report_fields, errors, warnings, store_out_inspection)."""
    warnings: list[str] = []
    errors: list[str] = []
    report: dict = {}
    so = _inspect_store_out(store)
    errors += so["errors"]
    if so["ignored"]:
        warnings.append(f"{len(so['ignored'])} file(s) in store_out ignored (only DB + {EXT} are injected)")
    ms_src = ManifestSession(bk_src)
    work = Path(tempfile.mkdtemp(prefix="inject-", dir=TMP_ROOT))
    try:
        grp_rows = ms_src.rows(GROUP_DOMAIN)
        by_rel = {r.rel: r for r in grp_rows}
        unknown = collections.Counter(k for r in grp_rows for k in r.mb.unknown_keys())
        report["mbfile_unknown_keys_in_group_domain"] = dict(unknown)
        if unknown:
            errors.append(f"group-domain MBFile rows carry keys this tool does not understand {sorted(unknown)} "
                          "(e.g. Digest would go stale) - refusing (review M4)")
        if DB not in by_rel or by_rel[DB].flags != FLAG_FILE:
            raise PipelineError(f"backup has no {GROUP_DOMAIN}/{DB} row (open Threema once on the iPhone, "
                                "then back up again)")
        orig_dir = work / "orig"
        orig_dir.mkdir()
        for n in (DB, WAL, SHM):
            r = by_rel.get(n)
            if r and r.flags == FLAG_FILE:
                decrypt_to(stored_path(src, r.file_id), file_key(bk_src, r.mb), orig_dir / n)
        fold_store_copy(orig_dir, work / "orig-folded")
        backup_ext = {r.rel.split("/")[-1]: r for r in grp_rows
                      if r.flags == FLAG_FILE and r.rel.startswith(EXT + "/")}
        ext_names = set(backup_ext) | {p.name for p in so["external_files"]}
        if not errors:
            a = analyze_store(store / DB, immutable=True, ext_names=ext_names)
            report["store"] = public(a)
            if a["integrity_check"] != "ok":
                errors.append("store_out integrity_check failed")
            if a.get("missing_external_refs"):
                errors.append(f"{a['missing_external_refs']} external-data reference(s) have no file in "
                              f"store_out/{EXT} nor in the backup")
            if a.get("unreferenced_external_files"):
                warnings.append(f"{a['unreferenced_external_files']} external file(s) not referenced by the DB")
            cmp = _compare_stores(store / DB, work / "orig-folded" / DB)
            report["comparison_with_device_store"] = cmp
            if not cmp["model_identical"]:
                errors.append("Core Data model hashes differ from the device store (importer used another model)")
            if not cmp["schema_identical"]:
                errors.append("SQLite schema differs from the device store")
            if cmp["lost_rows"]:
                errors.append(f"rows of the device store are missing in store_out: {cmp['lost_rows']}")
            if cmp["retyped_rows"]:
                errors.append(f"existing Z_PKs changed entity type: {cmp['retyped_rows']}")
            unexpected = {t: n for t, n in cmp["modified_existing_rows"].items() if t not in ("ZCONVERSATION",)}
            if unexpected:
                warnings.append(f"existing rows modified (besides ZCONVERSATION): {unexpected}")
            if not cmp["store_uuid_identical"]:
                warnings.append("Z_METADATA store UUID differs from the device store")
    finally:
        ms_src.close()
        shutil.rmtree(work, ignore_errors=True)
    return report, errors, warnings, so


def _inject_apply(bk: rw.EncryptedBackup, out_dev: Path, store: Path, so: dict, report: dict,
                  warnings: list[str]) -> dict:
    """Phase 2 of inject: modify the backup in `out_dev` (a clone / restore-set copy, never the original):
    replace the DB, zero -wal/-shm, add/replace _EXTERNAL_DATA, re-encrypt Manifest.db. Returns the actions."""
    ms = ManifestSession(bk)
    actions = {"replaced": [], "zeroed": [], "added_files": 0, "added_dirs": [], "external_unchanged": 0,
               "external_replaced": 0, "external_added": 0, "bytes_written": 0}
    try:
        grp_rows = ms.rows(GROUP_DOMAIN)
        by_rel = {r.rel: r for r in grp_rows}
        db_row = by_rel[DB]
        db_mb = db_row.mb
        tmpl_mode = db_mb.mode if (db_mb.mode & S_IFMT) == S_IFREG else (S_IFREG | 0o644)
        pclass = db_mb.pclass or DEFAULT_PCLASS          # new files: same class as the DB row (expected 3)
        if pclass != DEFAULT_PCLASS:
            warnings.append(f"new files use protection class {pclass} (expected {DEFAULT_PCLASS})")
        if db_mb.key_class() != db_mb.pclass:
            warnings.append("DB row: EncryptionKey class != ProtectionClass")
        report["new_file_protection_class"] = pclass
        next_inode = _alloc_inodes(grp_rows)
        now = int(time.time())

        def replace(row: Row, *, src_path: Path | None = None, data: bytes | None = None) -> str:
            mb = row.mb
            blob = mb.enc_blob
            if blob and len(blob) == 4 + rw.WRAPPED_KEY_LEN:
                key = file_key(bk, mb)
            else:                      # device row without key (e.g. empty file): give it one
                key = os.urandom(32)
                mb.set_encryption_key(rw.make_encryption_key_blob(pclass, key, bk.class_key(pclass)))
            n, h = encrypt_to(stored_path(out_dev, row.file_id), key, src=src_path, data=data)
            mb.set_fields(Size=n, LastModified=now, LastStatusChange=now, Birth=now)
            ms.update_blob(row.file_id, mb.dumps())
            actions["bytes_written"] += n
            return h

        def add_file(rel: str, *, src_path: Path | None = None, data: bytes | None = None) -> str:
            fid = rw.file_id_for(GROUP_DOMAIN, rel)
            if ms.exists_id(fid):
                raise PipelineError(f"fileID collision for {rel}")
            key = os.urandom(32)
            enc_blob = rw.make_encryption_key_blob(pclass, key, bk.class_key(pclass))
            n, h = encrypt_to(stored_path(out_dev, fid), key, src=src_path, data=data)
            blob = build_mbfile(relative_path=rel, mode=tmpl_mode, uid=db_mb.uid, gid=db_mb.gid, size=n,
                                pclass=pclass, mtime=now, inode=next_inode(), mbflags=db_mb.mbflags,
                                enc_blob=enc_blob)
            ms.insert(fid, GROUP_DOMAIN, rel, FLAG_FILE, blob)
            actions["added_files"] += 1
            actions["bytes_written"] += n
            return h

        def ensure_dir(rel: str) -> None:
            r = by_rel.get(rel)
            if r is not None:
                if r.flags != FLAG_DIR:
                    raise PipelineError(f"{rel} exists in backup but is not a directory row")
                return
            t = _dir_template(list(by_rel.values()), rel)
            mode = t.mode if t and (t.mode & S_IFMT) == S_IFDIR else (S_IFDIR | 0o755)
            blob = build_mbfile(relative_path=rel, mode=mode, uid=t.uid if t else db_mb.uid,
                                gid=t.gid if t else db_mb.gid, size=0, pclass=t.pclass if t else 0,
                                mtime=now, inode=next_inode(), mbflags=t.mbflags if t else 0)
            fid = rw.file_id_for(GROUP_DOMAIN, rel)
            ms.insert(fid, GROUP_DOMAIN, rel, FLAG_DIR, blob)
            by_rel[rel] = Row(fid, GROUP_DOMAIN, rel, FLAG_DIR, blob)
            actions["added_dirs"].append(rel)

        def put_empty(rel: str, row: Row | None) -> None:
            """Zero-length file in the DEVICE format. The real iOS 27 backup (pre-go review 2026-09-28) stores
            every empty file WITH an EncryptionKey (most with a 16-byte blob = AES-CBC of one PKCS7 padding
            block, the rest with a 0-byte blob, none keyless). So: new rows via add_file, existing rows via replace
            (which also gives a keyless row a fresh wrapped key); both write the 16-byte blob and Size 0."""
            if row is None:
                add_file(rel, data=b"")
            else:
                replace(row, data=b"")

        # DB
        report["db_sha256"] = replace(db_row, src_path=store / DB)
        actions["replaced"].append(DB)
        # WAL / SHM -> zero-length (never delete: a stale device-side WAL would corrupt the new DB)
        for n in (WAL, SHM):
            r = by_rel.get(n)
            if r is not None and r.flags != FLAG_FILE:
                raise PipelineError(f"{n} exists but is not a file row")
            put_empty(n, r)
            actions["zeroed"].append(n)
        # external data
        if so["external_files"]:
            ensure_dir(SUPPORT)
            ensure_dir(EXT)
        backup_ext = {r.rel: r for r in grp_rows if r.flags == FLAG_FILE and r.rel.startswith(EXT + "/")}
        for p in so["external_files"]:
            rel = f"{EXT}/{p.name}"
            r = backup_ext.get(rel)
            if r is None:
                add_file(rel, src_path=p)
                actions["external_added"] += 1
                continue
            same = False
            if r.mb.size == p.stat().st_size and stored_path(out_dev, r.file_id).exists():
                _, h_old = decrypt_to(stored_path(out_dev, r.file_id), file_key(bk, r.mb))
                same = h_old == sha256_file(p)
            if same:
                actions["external_unchanged"] += 1
            else:
                replace(r, src_path=p)
                actions["external_replaced"] += 1
                warnings.append(f"external file content differs from backup, replaced: {p.name}")
        orphans = set(backup_ext) - {f"{EXT}/{p.name}" for p in so["external_files"]}
        if orphans:
            warnings.append(f"{len(orphans)} backup external file(s) not in store_out (kept)")
        ms.commit_and_encrypt()
    finally:
        ms.close()
    return actions


# --------------------------------------------------------------------------- #
# trim
# --------------------------------------------------------------------------- #
def trim_app_plists(out_dev: Path) -> dict:
    """Manifest.plist Applications -> only Threema (container mapping for the AppDomain rows).
    Info.plist Applications -> REMOVED (review restoresafety M1): pymobiledevice3 restore writes
    /iTunesRestore/RestoreApplications.plist from it unless --skip-apps (mobilebackup2.py:407-418); without the key it
    writes nothing even if --skip-apps is forgotten. Field-proven partial restores use an empty Info.plist."""
    res = {}
    for name in ("Manifest.plist", "Info.plist"):
        p = out_dev / name
        d, fmt = load_plist(p)
        apps = d.get("Applications")
        if name == "Info.plist":
            res[name] = {"before": len(apps) if isinstance(apps, dict) else None, "after": None,
                         "applications_key_removed": "Applications" in d}
            d.pop("Applications", None)
            dump_plist(p, d, fmt)
            continue
        if isinstance(apps, dict):
            keep = {k: v for k, v in apps.items() if k == THREEMA_BUNDLE or k.startswith(THREEMA_BUNDLE + ".")}
            res[name] = {"before": len(apps), "after": len(keep), "threema_present": THREEMA_BUNDLE in keep}
            d["Applications"] = keep
            dump_plist(p, d, fmt)
        else:
            res[name] = {"before": None, "after": None, "threema_present": False}
    return res


def write_do_not_restore(out_dev: Path, reason: str) -> None:
    """Marker that tools/restore.py refuses (ROOTCAUSE.md §3): this directory must never be sent to a device."""
    (out_dev / DO_NOT_RESTORE).write_text("DO NOT RESTORE THIS BACKUP DIRECTORY TO A DEVICE.\n" + reason + "\n")


# --------------------------------------------------------------------------- #
# restore set (= the ONLY restore payload) + comparison with its source backup
# --------------------------------------------------------------------------- #


def _iso_utc(v) -> str | None:
    if not isinstance(v, _dt.datetime):
        return None
    return (v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc)).isoformat()


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except FileNotFoundError:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(CHUNK), fb.read(CHUNK)
            if x != y:
                return False
            if not x:
                return True


def _all_rows(bk: rw.EncryptedBackup) -> tuple[dict, bool]:
    """{(domain, relativePath): (fileID, flags, MBFile blob)} + Manifest.db PKCS7 state (decrypted copy is temporary)."""
    ms = ManifestSession(bk)
    try:
        rows = {(d, rel or ""): (fid, int(flags or 0), bytes(blob) if blob is not None else None)
                for fid, d, rel, flags, blob in ms.conn.execute(
                    "SELECT fileID, domain, relativePath, flags, file FROM Files")}
        return rows, ms.padded
    finally:
        ms.close()


def _domain_label(d: str) -> str:
    """Domain names for messages: Apple system domains and Threema in clear, app containers anonymised."""
    if is_threema_domain(d) or not d.startswith(("AppDomain", "SysContainerDomain", "SysSharedContainerDomain")):
        return d
    return d.split("-", 1)[0] + "-<app>"


def _expected_app_plists(src_manifest: dict, src_info: dict) -> tuple[object, dict]:
    apps = src_manifest.get("Applications")
    if isinstance(apps, dict):
        apps = {k: v for k, v in apps.items() if k == THREEMA_BUNDLE or k.startswith(THREEMA_BUNDLE + ".")}
    return apps, {k: v for k, v in src_info.items() if k != "Applications"}


def plist_consistency(dev: Path, src: Path, *, restoreset: bool) -> list[str]:
    """restoreset: Status.plist byte-identical; Manifest.plist identical except Applications (= source filtered to
    Threema, exactly what trim_app_plists writes); Info.plist identical except Applications (removed).
    full (inject output): all three byte-identical."""
    errs: list[str] = []
    if (dev / "Status.plist").read_bytes() != (src / "Status.plist").read_bytes():
        errs.append("Status.plist differs from the source backup")
    if not restoreset:
        for n in ("Manifest.plist", "Info.plist"):
            if (dev / n).read_bytes() != (src / n).read_bytes():
                errs.append(f"{n} differs from the source backup")
        return errs
    m, _ = load_plist(dev / "Manifest.plist")
    sm, _ = load_plist(src / "Manifest.plist")
    i, _ = load_plist(dev / "Info.plist")
    si, _ = load_plist(src / "Info.plist")
    exp_apps, exp_info = _expected_app_plists(sm, si)
    if m.get("Applications") != exp_apps:
        errs.append("Manifest.plist Applications != source Applications filtered to Threema")
    if {k: v for k, v in m.items() if k != "Applications"} != {k: v for k, v in sm.items() if k != "Applications"}:
        errs.append("Manifest.plist differs from the source outside 'Applications' (Lockdown/keybag/Containers ...)")
    if "Applications" in i:
        errs.append("Info.plist still has an Applications key (would drive RestoreApplications.plist)")
    if i != exp_info:
        errs.append("Info.plist differs from the source outside 'Applications'")
    return errs


def compare_with_source(dev: Path, src: Path, bk_dev: rw.EncryptedBackup, bk_src: rw.EncryptedBackup, *,
                        scope: str, noop: bool) -> dict:
    """Row + blob identity of `dev` against the backup it was built from.

    scope 'restoreset': `dev` holds exactly the restore-set domains; every HomeDomain/CameraRollDomain/
      KeyboardDomain row (fileID, domain, path, flags, MBFile blob incl. Digest) and stored blob is identical to the source, none
      missing, none extra; Threema rows identical except the inject-managed group-container paths (DB, -wal,
      -shm, _EXTERNAL_DATA/* and their two directory rows) -- with noop=True ALL Threema rows+blobs identical
      except -wal/-shm rows ADDED (zero-length) where the source had none (review-fix.md M4). In both cases the
      -wal and -shm rows must exist.
    scope 'full' (inject output): the same identity rule for every non-Threema domain of the source.
    Messages never contain relative paths of non-Threema rows (fileID prefixes only)."""
    if scope not in ("restoreset", "full"):
        raise ValueError(scope)
    errors: list[str] = []
    warnings: list[str] = []
    rs_rows, rs_padded = _all_rows(bk_dev)
    src_rows, src_padded = _all_rows(bk_src)
    in_scope = is_restoreset_domain if scope == "restoreset" else (lambda d: True)
    st: collections.Counter = collections.Counter()
    samples: dict[str, list[str]] = collections.defaultdict(list)
    per_domain: collections.Counter = collections.Counter()
    blob_bytes: collections.Counter = collections.Counter()

    def bad(kind: str, dom: str, fid: str) -> None:
        st[kind] += 1
        if len(samples[kind]) < 3:
            samples[kind].append(f"{_domain_label(dom)} fileID {fid[:10]}")

    foreign = sorted({d for d, _ in rs_rows if not in_scope(d)})
    if foreign:
        st["foreign_rows"] = sum(1 for d, _ in rs_rows if not in_scope(d))
        labels = sorted({_domain_label(d) for d in foreign})
        errors.append(f"{len(foreign)} domain(s) outside the restore set present ({st['foreign_rows']} rows): "
                      f"{labels[:8]}")
    for key in sorted({k for k in src_rows if in_scope(k[0])} | {k for k in rs_rows if in_scope(k[0])}):
        dom, rel = key
        a, b = src_rows.get(key), rs_rows.get(key)
        managed = (not noop and dom == GROUP_DOMAIN
                   and (is_managed_group_path(rel) or rel in (SUPPORT, EXT)))
        if b is None:
            bad("missing_rows", dom, a[0])
            continue
        per_domain[dom] += 1
        if a is None:
            if managed:
                st["added_managed_rows"] += 1
            elif noop and dom == GROUP_DOMAIN and rel in (WAL, SHM) and b[1] == FLAG_FILE \
                    and b[2] is not None and MBFile(b[2]).size == 0:
                st["added_empty_wal_shm"] += 1
            else:
                bad("extra_rows", dom, b[0])
        elif a == b:
            st["identical_rows"] += 1
        elif managed:
            st["changed_managed_rows"] += 1
        else:
            bad("changed_rows", dom, b[0])
            continue
        if b[1] != FLAG_FILE:
            continue
        bpath = stored_path(dev, b[0])
        if not bpath.is_file():
            bad("missing_blobs", dom, b[0])
            continue
        size = bpath.stat().st_size
        blob_bytes["threema" if is_threema_domain(dom) else dom] += size
        if a is not None and a == b:
            spath = stored_path(src, a[0])
            if not spath.is_file():
                bad("source_missing_blobs", dom, a[0])
            elif _same_bytes(spath, bpath):
                st["identical_blobs"] += 1
                st["identical_blob_bytes"] += size
            else:
                bad("changed_blobs", dom, b[0])
    msgs = {"missing_rows": "row(s) of the source missing", "extra_rows": "row(s) not in the source",
            "changed_rows": "row(s) differ from the source (fileID/flags/MBFile blob)",
            "missing_blobs": "file row(s) without stored blob", "changed_blobs": "stored blob(s) differ from the source",
            "source_missing_blobs": "file row(s) whose blob is missing in the SOURCE backup"}
    for kind, text in msgs.items():
        if st[kind]:
            errors.append(f"{st[kind]} {text}, e.g. {samples[kind]}")

    # files on disk that no row references (restore set must be exactly its rows)
    referenced = {v[0] for v in rs_rows.values() if v[1] == FLAG_FILE}
    stray, unexpected = 0, []
    for pth in dev.iterdir():
        if pth.is_dir() and len(pth.name) == 2:
            stray += sum(1 for q in pth.iterdir() if q.name not in referenced)
        elif pth.name in METADATA_FILES or pth.name == RESTORESET_MARKER:
            continue
        elif pth.name == ".DS_Store":
            warnings.append(".DS_Store in the backup directory (harmless, the device never requests it)")
        elif pth.name != DO_NOT_RESTORE:
            unexpected.append(pth.name)
    st["stray_blob_files"] = stray
    do_not_restore = (dev / DO_NOT_RESTORE).exists()
    if scope == "restoreset":
        if stray:
            errors.append(f"{stray} stored file(s) not referenced by any Manifest.db row")
        if unexpected:
            errors.append(f"unexpected top-level entries in the backup directory: {sorted(unexpected)[:5]}")
        if do_not_restore:
            errors.append(f"{DO_NOT_RESTORE} marker present: this directory is not a restore payload")
        if not per_domain.get("HomeDomain"):
            errors.append("restore set has no HomeDomain rows")
        if not per_domain.get(GROUP_DOMAIN):
            errors.append(f"restore set has no {GROUP_DOMAIN} rows")
        for n in (WAL, SHM):
            r = rs_rows.get((GROUP_DOMAIN, n))
            if r is None or r[1] != FLAG_FILE:
                errors.append(f"restore set has no {n} file row: a live device WAL would be replayed onto the "
                              f"restored DB (review-fix.md M4)")
    elif stray or unexpected:
        warnings.append(f"{stray} unreferenced stored file(s), {len(unexpected)} unexpected top-level entries")
    if rs_padded != src_padded:
        errors.append("Manifest.db PKCS7 padding state differs from the source")
    errors += plist_consistency(dev, src, restoreset=(scope == "restoreset"))
    threema_rows = sum(n for d, n in per_domain.items() if is_threema_domain(d))
    return {"scope": scope, "noop": noop, "stats": dict(st), "domains": dict(sorted(per_domain.items())),
            "home_rows": per_domain.get("HomeDomain", 0), "cameraroll_rows": per_domain.get("CameraRollDomain", 0),
            "keyboard_rows": per_domain.get("KeyboardDomain", 0), "threema_rows": threema_rows,
            "source_rows": {"HomeDomain": sum(1 for d, _ in src_rows if d == "HomeDomain"),
                            "CameraRollDomain": sum(1 for d, _ in src_rows if d == "CameraRollDomain"),
                            "KeyboardDomain": sum(1 for d, _ in src_rows if d == "KeyboardDomain"),
                            "threema": sum(1 for d, _ in src_rows if is_threema_domain(d)),
                            "total": len(src_rows)},
            "blob_bytes": dict(blob_bytes), "do_not_restore_marker": do_not_restore,
            "errors": errors, "warnings": warnings}


def _noop_empty_wal_shm(bk: rw.EncryptedBackup, out_dev: Path) -> list[str]:
    """--noop restore set (canary / rollback): add ZERO-LENGTH ThreemaData.sqlite-wal/-shm rows when the source
    backup has none (real iOS 27 backups carry none at all, review-fix.md M4). A restore is an OVERLAY for app
    domains: without these rows the device keeps its live -wal (e.g. the WAL of the imported store after the final
    restore + app launch) next to the restored original DB, and SQLite would replay those foreign frames onto it.
    Same device format as inject (16-byte encrypted padding block, wrapped key, Size 0). Existing source rows (a WAL
    that belongs to the source DB) stay untouched. Returns the added paths."""
    ms = ManifestSession(bk)
    added: list[str] = []
    try:
        grp_rows = ms.rows(GROUP_DOMAIN)
        by_rel = {r.rel: r for r in grp_rows}
        db_mb = by_rel[DB].mb if DB in by_rel else None
        if db_mb is None:
            raise PipelineError(f"{GROUP_DOMAIN}/{DB} row missing")
        pclass = db_mb.pclass or DEFAULT_PCLASS
        mode = db_mb.mode if (db_mb.mode & S_IFMT) == S_IFREG else (S_IFREG | 0o644)
        next_inode = _alloc_inodes(grp_rows)
        now = int(time.time())
        for n in (WAL, SHM):
            r = by_rel.get(n)
            if r is not None:
                if r.flags != FLAG_FILE:
                    raise PipelineError(f"{n} exists but is not a file row")
                continue
            fid = rw.file_id_for(GROUP_DOMAIN, n)
            if ms.exists_id(fid):
                raise PipelineError(f"fileID collision for {n}")
            key = os.urandom(32)
            enc_blob = rw.make_encryption_key_blob(pclass, key, bk.class_key(pclass))
            size, _ = encrypt_to(stored_path(out_dev, fid), key, data=b"")
            blob = build_mbfile(relative_path=n, mode=mode, uid=db_mb.uid, gid=db_mb.gid, size=size, pclass=pclass,
                                mtime=now, inode=next_inode(), mbflags=db_mb.mbflags, enc_blob=enc_blob)
            ms.insert(fid, GROUP_DOMAIN, n, FLAG_FILE, blob)
            added.append(n)
        if added:
            ms.commit_and_encrypt()
    finally:
        ms.close()
    return added


def _refuse_marked_source(src: Path) -> None:
    for m in (DO_NOT_RESTORE, RESTORESET_MARKER):
        if (src / m).exists():
            raise PipelineError(f"source carries {m}: it is a tool output, not a full device backup")


def _dir_bytes(d: Path) -> int:
    return sum(os.lstat(os.path.join(dp, f)).st_size for dp, _dn, fns in os.walk(d) for f in fns)


def cmd_restoreset(args) -> int:
    """Build the restore payload: from ONE fresh full encrypted backup of the device, keep
      * the Threema domains (AppDomain-ch.threema.iapp, AppDomainGroup-group.ch.threema,
        AppDomainPlugin-ch.threema.iapp.*), with the imported store injected (--store-out) or unchanged (--noop),
      * the COMPLETE HomeDomain, CameraRollDomain and KeyboardDomain, bit-identical to the source (rows + stored
        blobs); KeyboardDomain since the canary 2026-10-01 (its learned model was reset while it was absent),
    and nothing else (KeychainDomain and all other domains stay OUT on purpose: apart from KeyboardDomain -- now in
    the set -- no absent domain lost FILES in the 2026-10-01 incident or the canary, and a keychain replacement
    would cost banking/2FA registrations; but the keychain CONTENT
    did change as a follow-on (genp/keys item counts dropped, review-fix.md M1), which the postcheck now counts).
    --noop (canary / rollback): Threema store unchanged, zero-length -wal/-shm rows added where the source has none
    (review-fix.md M4).

    Plists (decision, see docs/rootcause-protocol.md / forensics): kept exactly as in the field-proven 2026-10-01
    payload, which iOS 27 accepted and which left every absent app domain untouched:
      * Status.plist: byte-identical copy.
      * Manifest.plist: identical (Lockdown, BackupKeyBag, ManifestKey, Containers, SystemDomainsVersion, Date ...)
        except "Applications" = source filtered to ch.threema.iapp(*). Listing ~2000 apps whose domains are absent
        from the payload is an UNTESTED combination for the device; the Threema-only list is the proven one.
      * Info.plist: identical except "Applications" REMOVED (host-only key; pymobiledevice3 builds
        RestoreApplications.plist from it unless --skip-apps -> no app re-install even if the flag is forgotten).
    Writes <out_root>/<UDID>/, <out_root>/<UDID>.restoreset.json and, only when every self-check passed,
    <out_root>/<UDID>/.RESTORESET_OK (= sha256 of the report). A failed build keeps its output for diagnostics,
    marked DO_NOT_RESTORE, without the OK marker."""
    src = Path(args.backup_udid_dir).expanduser().resolve()
    udid = src.name
    out_dev = resolve_out_device_dir(Path(args.out_root).expanduser().resolve(), udid)
    report_path = out_dev.parent / f"{udid}.restoreset.json"
    pl = check_backup_dir(src)
    _refuse_marked_source(src)
    guard_out(src, out_dev)
    if report_path.exists():
        raise PipelineError(f"report already exists (refusing to overwrite): {report_path}")
    store = Path(args.store_out).expanduser().resolve() if args.store_out else None
    pw = _password(args)
    fp0 = fingerprint(src)
    bk_src = rw.EncryptedBackup(src, pw)
    del pw
    lock = pl["manifest"].get("Lockdown") or {}
    errors: list[str] = []
    warnings: list[str] = []
    report: dict = {"kind": "restoreset", "tool": "backup_pipeline restoreset", "generated_at": now_iso(),
                    "source": str(src), "source_manifest_sha256": sha256_file(src / "Manifest.db"),
                    "noop": bool(args.noop), "store_out": str(store) if store else None, "output": str(out_dev),
                    "source_backup_date": _iso_utc(pl["status"].get("Date")) or _iso_utc(pl["manifest"].get("Date")),
                    "lockdown": {k: lock.get(k) for k in ("UniqueDeviceID", "BuildVersion", "ProductVersion")}}
    if str(lock.get("UniqueDeviceID") or "").lower() != udid.lower():
        errors.append("Manifest.plist Lockdown.UniqueDeviceID != backup directory name")
    if not report["source_backup_date"]:
        errors.append("source Status.plist has no Date (freshness cannot be checked)")
    ms_src = ManifestSession(bk_src)
    try:
        dom_counts = dict(ms_src.conn.execute("SELECT domain, count(*) FROM Files GROUP BY domain").fetchall())
    finally:
        ms_src.close()
    outside = sum(n for d, n in dom_counts.items() if not is_restoreset_domain(d))
    report["source_counts"] = {"rows": sum(dom_counts.values()), "domains": len(dom_counts),
                               "HomeDomain": dom_counts.get("HomeDomain", 0),
                               "CameraRollDomain": dom_counts.get("CameraRollDomain", 0),
                               "KeyboardDomain": dom_counts.get("KeyboardDomain", 0),
                               "threema": sum(n for d, n in dom_counts.items() if is_threema_domain(d)),
                               "KeychainDomain": dom_counts.get("KeychainDomain", 0), "outside_rows": outside}
    if not dom_counts.get("HomeDomain"):
        errors.append("source has no HomeDomain rows: not a full device backup")
    if not dom_counts.get("KeychainDomain"):
        errors.append("source has no KeychainDomain rows: not a full ENCRYPTED device backup")
    if not outside:
        errors.append("source holds only restore-set domains (trimmed backup / restore set?): need a FULL backup")
    if GROUP_DOMAIN not in dom_counts:
        errors.append(f"source has no {GROUP_DOMAIN} rows (Threema not installed/opened?)")
    if not dom_counts.get("CameraRollDomain"):
        warnings.append("source has no CameraRollDomain rows")
    if not dom_counts.get("KeyboardDomain"):
        warnings.append("source has no KeyboardDomain rows (keyboard learning cannot be carried)")
    so = None
    if store is not None and not errors:
        part, e2, w2, so = _inject_validate(src, store, bk_src)
        report["inject_validation"] = part
        errors += e2
        warnings += w2
    if errors:
        report.update({"result": "REFUSED", "errors": errors, "warnings": warnings})
        _emit(args, {"result": "REFUSED", "errors": errors, "warnings": warnings})
        return 1

    out_dev.mkdir(parents=True)
    try:
        for n in METADATA_FILES:
            shutil.copy2(src / n, out_dev / n)
        make_writable(out_dev)
        bk = copy.copy(bk_src)            # same keybag -> no second PBKDF2
        bk.device_dir = out_dev
        ms = ManifestSession(bk)
        try:
            ms.conn.create_function("rs_keep", 1, lambda d: 1 if is_restoreset_domain(d or "") else 0,
                                    deterministic=True)
            ms.conn.execute("DELETE FROM Files WHERE rs_keep(domain) = 0")
            kept = [(fid, flags) for fid, flags in ms.conn.execute("SELECT fileID, flags FROM Files")]
            ms.commit_and_encrypt()
        finally:
            ms.close()
        pairs, missing = [], 0
        for fid, flags in kept:
            if flags == FLAG_FILE:
                sp = stored_path(src, fid)
                if sp.is_file():
                    pairs.append((sp, stored_path(out_dev, fid)))
                else:
                    missing += 1
        if missing:
            raise PipelineError(f"{missing} file row(s) of the restore-set domains have no blob in the source backup")
        report["copy_method"] = clone_files(pairs)
        make_writable(out_dev)
        if store is not None:
            inj: dict = {}
            report["inject"] = {"actions": _inject_apply(bk, out_dev, store, so, inj, warnings),
                                **inj}
        else:
            report["noop_added_empty"] = _noop_empty_wal_shm(bk, out_dev)
        report["app_plists"] = trim_app_plists(out_dev)
        report["status_plist"] = "copied unchanged"
        chk = compare_with_source(out_dev, src, bk, bk_src, scope="restoreset", noop=bool(args.noop))
    except BaseException:
        shutil.rmtree(out_dev, ignore_errors=True)
        raise
    errors += chk["errors"]
    warnings += chk["warnings"]
    report["self_check"] = {k: chk[k] for k in ("stats", "source_rows", "blob_bytes")}
    report["domains"] = chk["domains"]
    report["home_rows"], report["cameraroll_rows"], report["keyboard_rows"], report["threema_rows"] = (
        chk["home_rows"], chk["cameraroll_rows"], chk["keyboard_rows"], chk["threema_rows"])
    if chk["home_rows"] != dom_counts.get("HomeDomain", 0):
        errors.append("HomeDomain row count != source")
    if chk["cameraroll_rows"] != dom_counts.get("CameraRollDomain", 0):
        errors.append("CameraRollDomain row count != source")
    if chk["keyboard_rows"] != dom_counts.get("KeyboardDomain", 0):
        errors.append("KeyboardDomain row count != source")
    report["payload_bytes"] = _dir_bytes(out_dev)
    report["domain_bytes"] = chk["blob_bytes"]
    fp1 = fingerprint(src)
    report["source_unchanged"] = fp0 == fp1
    if fp0 != fp1:
        errors.append("SOURCE BACKUP CHANGED during restoreset")
    report["warnings"], report["errors"] = warnings, errors
    report["result"] = "OK" if not errors else "FAILED"
    if errors:
        write_do_not_restore(out_dev, "restoreset self-check FAILED - see " + report_path.name)
    write_json(report_path, report)
    if args.report:
        write_json(Path(args.report).expanduser(), report)
    if not errors:
        (out_dev / RESTORESET_MARKER).write_text(sha256_file(report_path))
    _emit(args, {k: report.get(k) for k in (
        "result", "output", "noop", "domains", "home_rows", "cameraroll_rows", "keyboard_rows", "threema_rows",
        "payload_bytes",
        "domain_bytes", "source_backup_date", "source_unchanged", "warnings", "errors")} | {
        "report": str(report_path), "inject_actions": (report.get("inject") or {}).get("actions")})
    return 1 if errors else 0


def check_restoreset_report(dev: Path, src: Path, cmp: dict) -> tuple[dict, list[str], bool]:
    """Marker/report written by cmd_restoreset must belong to exactly this directory, this source and these
    counts. Returns (summary, errors, noop)."""
    errs: list[str] = []
    rp = dev.parent / f"{dev.name}.restoreset.json"
    marker = dev / RESTORESET_MARKER
    out: dict = {"report": str(rp), "marker": marker.exists()}
    if not rp.is_file():
        return out, [f"builder report missing: {rp.name}"], False
    rep = json.loads(rp.read_text())
    noop = bool(rep.get("noop"))
    out["noop"] = noop
    if not marker.is_file():
        errs.append(f"{RESTORESET_MARKER} marker missing (builder did not finish OK)")
    elif marker.read_text().strip() != sha256_file(rp):
        errs.append(f"{RESTORESET_MARKER} does not match the report sha256 (report edited or replaced)")
    if rep.get("kind") != "restoreset" or rep.get("result") != "OK":
        errs.append(f"builder report kind/result = {rep.get('kind')}/{rep.get('result')}")
    if rep.get("source_manifest_sha256") != sha256_file(src / "Manifest.db"):
        errs.append("--source is not the backup this restore set was built from (Manifest.db sha256 differs)")
    for k in ("domains", "home_rows", "cameraroll_rows", "keyboard_rows", "threema_rows"):
        if rep.get(k) != cmp.get(k):
            # a set built before KeyboardDomain joined the restore set has no keyboard_rows: rebuild it
            errs.append(f"builder report {k} != actual")
    lock = (load_plist(dev / "Manifest.plist")[0].get("Lockdown") or {})
    if rep.get("lockdown") != {k: lock.get(k) for k in ("UniqueDeviceID", "BuildVersion", "ProductVersion")}:
        errs.append("builder report lockdown != Manifest.plist Lockdown")
    out["source_backup_date"] = rep.get("source_backup_date")
    return out, errs, noop


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
class _VerifyAbort(Exception):
    pass


def cmd_verify(args) -> int:
    from pyiosbackup import Backup as PyiosBackup
    from pyiosbackup.entry import Entry as PyiosEntry
    from pyiosbackup.manifest_dbs.sqlite3 import ManifestDbSqlite3
    from iphone_backup_decrypt import EncryptedBackup as IbdBackup

    dev = Path(args.backup_udid_dir).expanduser().resolve()
    store = Path(args.against).expanduser().resolve() if args.against else None
    src_dir = Path(args.source).expanduser().resolve() if args.source else None
    if args.restoreset and src_dir is None:
        raise PipelineError("--restoreset needs --source <the full backup the restore set was built from>")
    if src_dir is not None and src_dir == dev:
        raise PipelineError("--source must be a different backup directory")
    pw = _password(args)
    errors: list[str] = []
    warnings: list[str] = []
    report: dict = {"tool": "backup_pipeline verify", "generated_at": now_iso(), "backup": str(dev),
                    "against": str(store) if store else None,
                    "restoreset": bool(args.restoreset), "source": str(src_dir) if src_dir else None,
                    "do_not_restore_marker": (dev / DO_NOT_RESTORE).exists()}
    try:
        pl = check_backup_dir(dev)
    except PipelineError as e:
        errors.append(str(e))
        report["errors"] = errors
        _emit(args, report)
        return 1
    report["device"] = device_summary(pl, dev)
    uid_info = str(pl["info"].get("Unique Identifier") or pl["info"].get("Target Identifier") or "")
    if uid_info and uid_info.lower() != dev.name.lower():
        warnings.append("Info.plist Unique/Target Identifier != directory name")

    work = Path(tempfile.mkdtemp(prefix="verify-", dir=TMP_ROOT))
    pyb = ibd = None
    try:
        # ---- reader 1: pyiosbackup (own keybag, own MBFile decoding) --------------
        try:
            pyb = PyiosBackup.from_path(dev, pw)
        except Exception as ex:
            errors.append(f"pyiosbackup cannot open the backup (wrong password?): {type(ex).__name__}")
            raise _VerifyAbort() from None
        conn = pyb._manifest_db._conn
        rows = conn.execute("SELECT fileID, domain, relativePath, flags, file FROM Files").fetchall()
        stats = {"rows": len(rows), "files": 0, "dirs": 0, "symlinks": 0, "bad_file_id": 0,
                 "missing_blob": 0, "blob_size_mismatch": 0, "blob_not_aligned": 0, "flag_mode_mismatch": 0,
                 "threema_rows": 0, "non_threema_rows": 0}
        th_files: dict[tuple[str, str], dict] = {}
        mb_unknown: collections.Counter = collections.Counter()
        row_digest = hashlib.sha256()
        for r in sorted(rows, key=lambda r: r["fileID"]):
            fid, domain, rel, flags = r["fileID"], r["domain"], r["relativePath"], r["flags"]
            row_digest.update(f"{fid}|{domain}|{rel}|{flags}\n".encode())
            meta = ManifestDbSqlite3._load_entry(r)
            th = is_threema_domain(domain)
            if th:
                uk = MBFile(r["file"]).unknown_keys()
                for k in uk:
                    mb_unknown[k] += 1

            stats["threema_rows" if th else "non_threema_rows"] += 1
            # privacy: non-Threema rows are named by domain + fileID prefix only (HomeDomain/app paths can be
            # user-named folders); Threema paths are structural (DB, prefs, _EXTERNAL_DATA/<uuid>)
            where = f"{domain}/{rel}" if th else f"{_domain_label(domain)} fileID {fid[:10]}"
            if hashlib.sha1(f"{domain}-{rel}".encode()).hexdigest() != fid:
                stats["bad_file_id"] += 1
                (errors if th else warnings).append(f"fileID != sha1(domain-path) for {where}")
            ftype = meta["mode"] & S_IFMT
            expect = {FLAG_FILE: S_IFREG, FLAG_DIR: S_IFDIR, FLAG_SYMLINK: S_IFLNK}.get(flags)
            if expect is None or ftype != expect:
                stats["flag_mode_mismatch"] += 1
                (errors if th else warnings).append(f"flags/mode mismatch for {where}")
            if flags == FLAG_DIR:
                stats["dirs"] += 1
                continue
            if flags == FLAG_SYMLINK:
                stats["symlinks"] += 1
                continue
            stats["files"] += 1
            if not meta["encryption_key"]:
                stats["no_key"] = stats.get("no_key", 0) + 1
                if meta["size"]:
                    (errors if th else warnings).append(f"file row without EncryptionKey: {where}")
                elif th:
                    th_files[(domain, rel)] = {"file_id": fid, "size": 0, "meta": meta, "blob": None,
                                               "empty_no_key": True}
                    sp = stored_path(dev, fid)
                    if sp.exists() and sp.stat().st_size:
                        errors.append(f"empty file row without key has a non-empty stored blob: {domain}/{rel}")
                continue
            blob = stored_path(dev, fid)
            if not blob.exists():
                stats["missing_blob"] += 1
                (errors if th or args.restoreset else warnings).append(f"missing blob {where}")
                continue
            bsz = blob.stat().st_size
            if bsz % 16:
                stats["blob_not_aligned"] += 1
                errors.append(f"blob not 16-byte aligned: {where}")
            elif bsz != padded_len(meta["size"]):
                stats["blob_size_mismatch"] += 1
            if th:
                th_files[(domain, rel)] = {"file_id": fid, "size": meta["size"], "meta": meta, "blob": bsz}
        stats["distinct_domains"] = len({r["domain"] for r in rows})
        stats["threema_mbfile_unknown_keys"] = dict(mb_unknown)
        if mb_unknown:
            errors.append(f"Threema MBFile rows carry unknown keys {sorted(mb_unknown)} (review M4)")
        report["manifest"] = stats
        if stats["blob_size_mismatch"]:
            warnings.append(f"{stats['blob_size_mismatch']} blob(s) whose length != PKCS7(Size) "
                            "(normal for live files in device-made backups)")

        # Manifest.db padding format (device writes PKCS7; see strip_manifest_padding)
        try:
            raw_pt = pyb.keybag.decrypt((dev / "Manifest.db").read_bytes(), pl["manifest"]["ManifestKey"])
            report["manifest_padding"] = "pkcs7" if strip_manifest_padding(raw_pt)[1] else "none"
            del raw_pt
        except PipelineError as ex:
            errors.append(str(ex))
        if report.get("manifest_padding") == "none":
            # review m5: device-made backups are PKCS7-padded; iphone_backup_decrypt >= 0.10 unpads strictly
            errors.append("Manifest.db has no PKCS7 padding (device-made backups have it)")

        # ---- reader 2: iphone_backup_decrypt (own Manifest decrypt + FilePlist) --------
        try:
            ibd = IbdBackup(backup_directory=str(dev), passphrase=pw)
            ibd.test_decryption()
            d2 = hashlib.sha256()
            with ibd.manifest_db_cursor() as cur:
                cur.execute("SELECT fileID, domain, relativePath, flags FROM Files ORDER BY fileID")
                for fid, domain, rel, flags in cur:
                    d2.update(f"{fid}|{domain}|{rel}|{flags}\n".encode())
            report["readers_agree_on_manifest"] = d2.hexdigest() == row_digest.hexdigest()
        except Exception as ex:
            report["readers_agree_on_manifest"] = False
            errors.append(f"iphone_backup_decrypt cannot open the backup: {type(ex).__name__}: {ex}")
            if ibd is not None:
                with contextlib.suppress(Exception):
                    ibd._cleanup()
                ibd._temporary_folder = None
            ibd = None
        if not report["readers_agree_on_manifest"]:
            errors.append("pyiosbackup and iphone_backup_decrypt disagree on Manifest.db rows")

        # ---- decrypt every Threema file with both readers -------------------------
        managed_strict = store is not None
        th_hash: dict[tuple[str, str], str] = {}
        grp_dir = work / "grp"
        for (domain, rel), f in sorted(th_files.items()):
            if f.get("empty_no_key"):
                th_hash[(domain, rel)] = hashlib.sha256(b"").hexdigest()
                if domain == GROUP_DOMAIN and rel in (DB, WAL, SHM):
                    (grp_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                    (grp_dir / rel).write_bytes(b"")
                continue
            e = PyiosEntry(pyb, **f["meta"])
            try:
                data = e.read_bytes()
            except Exception as ex:
                errors.append(f"pyiosbackup cannot decrypt {domain}/{rel}: {type(ex).__name__}")
                continue
            h1 = hashlib.sha256(data).hexdigest()
            if ibd is not None:
                tmpf = work / "ibd.bin"
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        ibd.extract_file(relative_path=rel, domain_like=domain, output_filename=str(tmpf))
                    h2 = sha256_file(tmpf)
                except Exception as ex:
                    h2 = f"error:{type(ex).__name__}"
                finally:
                    if tmpf.exists():
                        tmpf.unlink()
                if h1 != h2:
                    errors.append(f"readers disagree on content of {domain}/{rel}")
            if len(data) != f["size"]:
                strict = managed_strict and domain == GROUP_DOMAIN and is_managed_group_path(rel)
                (errors if strict else warnings).append(
                    f"decrypted length != manifest Size for {domain}/{rel}")
            th_hash[(domain, rel)] = h1
            if domain == GROUP_DOMAIN and (rel in (DB, WAL, SHM)):
                p = grp_dir / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            del data
        report["threema_files_decrypted"] = len(th_hash)

        # ---- group container / DB checks --------------------------------------------
        grp = {rel: h for (d, rel), h in th_hash.items() if d == GROUP_DOMAIN}
        backup_ext = {rel.split("/")[-1]: h for rel, h in grp.items() if rel.startswith(EXT + "/")}
        if DB not in grp:
            errors.append(f"{GROUP_DOMAIN}/{DB} missing")
        else:
            try:
                wal_len = (grp_dir / WAL).stat().st_size if (grp_dir / WAL).exists() else None
                report["wal_size"] = wal_len
                report["shm_size"] = (grp_dir / SHM).stat().st_size if (grp_dir / SHM).exists() else None
                fold = fold_store_copy(grp_dir, work / "folded")
                a = analyze_store(work / "folded" / DB, immutable=True, ext_names=set(backup_ext))
                report["db"] = {"fold": fold, **{k: v for k, v in public(a).items() if k != "metadata"},
                                "model": {k: a["metadata"].get(k) for k in
                                          ("model_version_identifiers", "model_matches_v56_reference",
                                           "model_hashes_digest", "model_id", "store_uuid")}}
                if a["integrity_check"] != "ok":
                    errors.append("PRAGMA integrity_check failed on the extracted DB")
                if a.get("missing_external_refs"):
                    errors.append(f"{a['missing_external_refs']} DB external-data reference(s) without file "
                                  "in the backup")
            except (sqlite3.DatabaseError, PipelineError) as ex:
                errors.append(f"extracted DB unusable: {ex}")
        # parent directory rows for group-domain files
        grp_dirs = {rel for (d, rel) in [(r["domain"], r["relativePath"]) for r in rows
                                         if r["flags"] == FLAG_DIR] if d == GROUP_DOMAIN}
        orphan_parent = [rel for rel in grp if "/" in rel and rel.rsplit("/", 1)[0] not in grp_dirs]
        if orphan_parent:
            (errors if store else warnings).append(
                f"{len(orphan_parent)} group-domain file(s) without a parent directory row")

        # ---- compare with the injected store ------------------------------------------
        if store is not None:
            cmp = {"db": None, "external_checked": 0, "external_mismatch": 0, "external_missing": 0}
            if (store / DB).exists():
                cmp["db"] = sha256_file(store / DB) == grp.get(DB)
                if not cmp["db"]:
                    errors.append("ThreemaData.sqlite in backup != store_dir")
            else:
                errors.append("store_dir has no ThreemaData.sqlite")
            for n in (WAL, SHM):
                if n not in grp:
                    errors.append(f"{n} row missing or undecryptable (must exist as zero-length file)")
                elif (th_files.get((GROUP_DOMAIN, n), {}).get("size") != 0
                      or grp[n] != hashlib.sha256(b"").hexdigest()):
                    errors.append(f"{n} is not zero-length")
            sext = store / EXT
            for p in (sorted(sext.iterdir()) if sext.is_dir() else []):
                if not p.is_file():
                    continue
                cmp["external_checked"] += 1
                h = backup_ext.get(p.name)
                if h is None:
                    cmp["external_missing"] += 1
                elif h != sha256_file(p):
                    cmp["external_mismatch"] += 1
            if cmp["external_missing"] or cmp["external_mismatch"]:
                errors.append(f"external data mismatch: {cmp['external_missing']} missing, "
                              f"{cmp['external_mismatch']} differing")
            for d in (SUPPORT, EXT):
                if cmp["external_checked"] and d not in grp_dirs:
                    errors.append(f"directory row missing: {d}")
            report["against_store"] = cmp

        # ---- identity with the source backup (restore set / inject output) ------------
        if src_dir is not None:
            try:
                check_backup_dir(src_dir)
                bk_dev = rw.EncryptedBackup(dev, pw)
                sm = load_plist(src_dir / "Manifest.plist")[0]
                if (sm.get("BackupKeyBag") == pl["manifest"].get("BackupKeyBag")
                        and sm.get("ManifestKey") == pl["manifest"].get("ManifestKey")):
                    bk_src = copy.copy(bk_dev)          # same keybag -> no second PBKDF2
                    bk_src.device_dir = src_dir
                else:
                    bk_src = rw.EncryptedBackup(src_dir, pw)
                noop = False
                rp = dev.parent / f"{dev.name}.restoreset.json"
                if args.restoreset and rp.is_file():
                    noop = bool(json.loads(rp.read_text()).get("noop"))
                scmp = compare_with_source(dev, src_dir, bk_dev, bk_src,
                                           scope="restoreset" if args.restoreset else "full", noop=noop)
                report["source_comparison"] = scmp
                errors += scmp["errors"]
                warnings += scmp["warnings"]
                if args.restoreset:
                    summ, rerrs, _ = check_restoreset_report(dev, src_dir, scmp)
                    report["restoreset_report"] = summ
                    errors += rerrs
            except (PipelineError, ValueError, OSError) as ex:
                errors.append(f"source comparison failed: {type(ex).__name__}: {ex}")
    except _VerifyAbort:
        pass
    finally:
        if pyb is not None:
            try:
                pyb._manifest_db._conn.close()
                Path(pyb._manifest_db.path).unlink(missing_ok=True)
            except Exception:
                pass
        if ibd is not None:
            with contextlib.suppress(Exception):
                ibd._cleanup()
            ibd._temporary_folder = None
        shutil.rmtree(work, ignore_errors=True)
    report["warnings"] = warnings[:200] + ([f"... {len(warnings) - 200} more"] if len(warnings) > 200 else [])
    report["errors"] = errors
    report["result"] = "PASS" if not errors else "FAIL"
    if args.report:
        write_json(Path(args.report).expanduser(), report)
    if args.restoreset:      # fixed place next to the payload; latest verify wins
        fixed = dev.parent / f"{dev.name}.restoreset-verify.json"
        try:
            write_json(fixed, report)
        except PermissionError:
            # migrate.sh freezes a verified restore set (chmod -R a-w); tools/restore.py re-runs this verify on the
            # frozen set and uses the exit code only -> never fail (or unfreeze) just for this convenience copy
            print(f"note: {fixed.name} not updated (payload root is read-only / frozen)", file=sys.stderr)
    short = {k: report.get(k) for k in ("result", "backup", "against", "restoreset", "source",
                                        "manifest", "readers_agree_on_manifest", "threema_files_decrypted",
                                        "wal_size", "shm_size", "db", "against_store", "restoreset_report",
                                        "errors")}
    if "source_comparison" in report:
        short["source_comparison"] = {k: report["source_comparison"].get(k) for k in (
            "scope", "noop", "stats", "domains", "home_rows", "cameraroll_rows", "keyboard_rows", "threema_rows",
            "source_rows")}
    short["warnings"] = report["warnings"][:20]
    short["warning_count"] = len(warnings)
    _emit(args, short)
    return 0 if not errors else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="backup_pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract", help="decrypt the Threema group container + report")
    p.add_argument("backup_udid_dir")
    p.add_argument("out_dir")
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("verify", help="independent re-read + consistency checks")
    p.add_argument("backup_udid_dir")
    p.add_argument("--against", help="store dir (ThreemaData.sqlite + .ThreemaData_SUPPORT/_EXTERNAL_DATA)")
    p.add_argument("--source", help="the full backup this one was built from: every non-Threema row + blob must be "
                                    "identical (with --restoreset: HomeDomain/CameraRollDomain/KeyboardDomain "
                                    "identical, nothing else outside Threema)")
    p.add_argument("--restoreset", action="store_true",
                   help="verify a 'restoreset' payload (needs --source); also checks marker + builder report and "
                        "writes <root>/<UDID>.restoreset-verify.json")
    p.add_argument("--report", help="write the full JSON report here")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("restoreset", help="THE restore payload: Threema (+import) + complete Home/CameraRoll/Keyboard")
    p.add_argument("backup_udid_dir", help="fresh FULL encrypted backup <root>/<UDID> of the target device")
    p.add_argument("out_root", help="result goes to <out_root>/<UDID>/ + <out_root>/<UDID>.restoreset.json")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--store-out", help="importer output (ThreemaData.sqlite + _EXTERNAL_DATA) to inject")
    g.add_argument("--noop", action="store_true", help="rollback payload: Threema store exactly as in the source")
    p.add_argument("--report", help="additionally write the report here")
    p.set_defaults(fn=cmd_restoreset)
    return ap


_DEFAULTS = {"extract": {},
             "verify": {"against": None, "source": None, "restoreset": False, "report": None},
             "restoreset": {"store_out": None, "noop": False, "report": None}}
_FN = {"extract": "cmd_extract", "verify": "cmd_verify", "restoreset": "cmd_restoreset"}


def run(cmd: str, *, password: str, **kw) -> tuple[int, dict | None]:
    """In-process call (tmcore steps): never prints, never reads a file for the password.
    Returns (exit code as the CLI would, short summary). PipelineError / ValueError propagate."""
    if cmd not in _FN:
        raise ValueError(f"unknown backup_pipeline command {cmd!r}")
    args = argparse.Namespace(**{**_DEFAULTS[cmd], **kw}, cmd=cmd, password=password, quiet=True, summary=None)
    try:
        rc = globals()[_FN[cmd]](args)
    finally:
        args.password = None
    return rc, args.summary


def read_password_line(stream=None) -> str:
    """Maintainer CLI: exactly one line on stdin (never argv, never a file)."""
    stream = stream if stream is not None else sys.stdin
    line = stream.readline()
    pw = line.rstrip("\r\n")
    if not pw:
        raise PipelineError("no password on stdin (one line expected)")
    return pw


def main(argv: list[str] | None = None, *, password: str | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.password = password if password is not None else read_password_line()
        args.quiet = False
        if os.environ.get("TMPDIR"):
            use_tmp(os.environ["TMPDIR"])
        return args.fn(args)
    except PipelineError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        # keybag unlock failures etc. -- messages never contain the password
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    finally:
        args.password = None


if __name__ == "__main__":
    raise SystemExit(main())
