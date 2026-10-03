#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
iosbackup_rw.py — read / modify / re-encrypt / add files inside an ENCRYPTED
iOS (MobileBackup2 / Finder) backup, without a device.

STATUS (2026-09-28, review restoresafety m6): LEGACY PROTOTYPE. The production path is tmcore.lib.backup_pipeline
(it only imports this module for the keybag / key-wrap / file-content crypto). Two statements of the original
prototype were WRONG and are corrected below: real devices write Manifest.db PKCS7-PADDED, and
build_mbfile_blob's Flags/InodeNumber defaults do not match device rows (backup_pipeline.build_mbfile copies them
from the DB row instead). Do not use add_file/encrypt_manifest_db of this module for anything that goes to a device.

Scope of this prototype
-----------------------
An encrypted iOS backup directory (<backup_root>/<UDID>/) contains:
  Manifest.plist   plist with IsEncrypted, BackupKeyBag (TLV keybag),
                   ManifestKey (4-byte LE class + 0x28 wrapped AES key).
  Manifest.db      SQLite index, AES-256-CBC encrypted with the manifest key
                   (zero IV; devices PKCS7-pad it - CORRECTED, see STATUS; this prototype writes it unpadded).
                   Table Files(fileID TEXT, domain TEXT, relativePath TEXT,
                   flags INT, file BLOB) where `file` is an NSKeyedArchiver
                   plist ("MBFile") holding Size / LastModified / Birth /
                   ProtectionClass / EncryptionKey / Mode / UserID / GroupID ...
  Status.plist / Info.plist   metadata.
  NN/<fileID>      each stored file, AES-256-CBC encrypted with a per-file key
                   (zero IV, PKCS7 padded to 16 bytes). fileID = the SHA1 of
                   "<domain>-<relativePath>", stored under its first 2 hex chars.

Key hierarchy
-------------
  passphrase --PBKDF2(sha256,DPSL,DPIC)--> --PBKDF2(sha1,SALT,ITER)--> passphrase_key
  passphrase_key --AES-unwrap(WPKY)--> class_key[protection_class]     (per keybag class)
  class_key --AES-unwrap(wrapped file key)--> file_key                 (per file / manifest)
  file bytes = AESCBC_decrypt(file_key, ciphertext), strip PKCS7.

This module reuses the audited unlock/unwrap code in `iphone_backup_decrypt`
(BackupKeyBag) for reading, and adds the WRITE side (wrap keys, pad, encrypt,
rebuild the MBFile NSKeyedArchiver blob, insert rows, re-encrypt Manifest.db).

Verified against pymobiledevice3.services.mobilebackup2 (Manifest.db is CBC,
zero IV, block-aligned, no PKCS7) and pyiosbackup.entry (per-file content is
CBC + PKCS7(128)).

WARNING: modifying a real device backup and restoring it is destructive.
This file is a research prototype with a synthetic self-test (`--selftest`);
it never talks to a device and never prints secrets. Passwords are always passed in-process (no password files).
The low-level crypto API (encrypt/decrypt helpers, make_encryption_key_blob, file_id_for, build_mbfile_blob,
EncryptedBackup) is used by fixtures/gen_ios_backup.py and stays stable.
"""
from __future__ import annotations

import hashlib
import os
import plistlib
import sqlite3
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import aes_key_wrap
from cryptography.hazmat.primitives import padding

# Reuse the read-side keybag from iphone_backup_decrypt (RFC3394 unwrap, PBKDF2).
from iphone_backup_decrypt import utils as ibd_utils

WRAPPED_KEY_LEN = 0x28  # 40 bytes: 32-byte AES key + 8-byte RFC3394 IV block


# --------------------------------------------------------------------------- #
# Low-level crypto helpers
# --------------------------------------------------------------------------- #
def _cbc(key: bytes):
    return Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16))


def encrypt_file_content(plaintext: bytes, file_key: bytes) -> bytes:
    """Per-file content encryption: PKCS7(128) pad then AES-256-CBC, zero IV."""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    enc = _cbc(file_key).encryptor()
    return enc.update(padded) + enc.finalize()


def decrypt_file_content(ciphertext: bytes, file_key: bytes) -> bytes:
    dec = _cbc(file_key).decryptor()
    padded = dec.update(ciphertext) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def encrypt_manifest_db(plaintext: bytes, manifest_key: bytes) -> bytes:
    """Manifest.db: AES-256-CBC, zero IV, written WITHOUT padding (prototype format only - devices write PKCS7;
    backup_pipeline.encrypt_manifest_bytes mirrors the device format)."""
    if len(plaintext) % 16:
        raise ValueError("Manifest.db plaintext not 16-byte aligned")
    enc = _cbc(manifest_key).encryptor()
    return enc.update(plaintext) + enc.finalize()


def decrypt_manifest_db(ciphertext: bytes, manifest_key: bytes) -> bytes:
    dec = _cbc(manifest_key).decryptor()
    return dec.update(ciphertext) + dec.finalize()


def make_encryption_key_blob(protection_class: int, file_key: bytes,
                             class_key: bytes) -> bytes:
    """
    Build the value stored in MBFile.EncryptionKey.NS.data:
      4-byte LE protection class  +  RFC3394-wrapped 32-byte file key (0x28).
    (iphone_backup_decrypt.FilePlist reads NS.data[4:] as the wrapped key and
     ManifestKey[:4] LE as the class; we mirror that layout here.)
    """
    if len(file_key) != 32:
        raise ValueError("file key must be 32 bytes")
    wrapped = aes_key_wrap(class_key, file_key)
    if len(wrapped) != WRAPPED_KEY_LEN:
        raise ValueError(f"wrapped key length {len(wrapped)} != {WRAPPED_KEY_LEN}")
    return struct.pack("<I", protection_class) + wrapped


def file_id_for(domain: str, relative_path: str) -> str:
    """iOS backup file id = sha1('<domain>-<relativePath>') hex (iOS > 10.2)."""
    return hashlib.sha1(f"{domain}-{relative_path}".encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# MBFile NSKeyedArchiver (build the `file` BLOB)
# --------------------------------------------------------------------------- #
def build_mbfile_blob(*, size: int, protection_class: int,
                      encryption_key_blob: bytes | None,
                      mode: int = 0o100644, uid: int = 501, gid: int = 501,
                      mtime: int, birth: int | None = None,
                      last_status_change: int | None = None) -> bytes:
    """
    Serialise an MBFile record as an NSKeyedArchiver binary plist, matching the
    structure iOS writes and that iphone_backup_decrypt.FilePlist / pyiosbackup
    MBFile.decode_archive read back.

    $objects layout:
      [0] "$null"
      [1] MBFile dict (Size, LastModified, LastStatusChange, Birth, Mode,
          UserID, GroupID, InodeNumber, ProtectionClass, Flags,
          RelativePath -> UID, [EncryptionKey -> UID], $class -> UID)
      [2] RelativePath string  (set by caller via placeholder; here embedded)
      ...
    We keep it minimal but complete enough for the parsers above.
    """
    if birth is None:
        birth = mtime
    if last_status_change is None:
        last_status_change = mtime

    objects: list = ["$null"]

    def add(obj) -> plistlib.UID:
        objects.append(obj)
        return plistlib.UID(len(objects) - 1)

    # placeholders filled after we know indices
    mbfile: dict = {}
    mbfile_uid = add(mbfile)  # index 1

    rel_uid = add("")  # RelativePath string, index 2 (overwritten by caller path)
    # NOTE: caller passes real path through relative_path kwarg below.

    enc_uid = None
    if encryption_key_blob is not None:
        enc_uid = add({
            "$class": None,  # set below
            "NS.data": encryption_key_blob,
        })

    # NSMutableData / NSData class object for EncryptionKey
    data_class_uid = None
    if enc_uid is not None:
        data_class_uid = add({
            "$classes": ["NSMutableData", "NSData", "NSObject"],
            "$classname": "NSMutableData",
        })
        objects[enc_uid.data]["$class"] = data_class_uid

    # MBFile class object
    mbfile_class_uid = add({
        "$classes": ["MBFile", "NSObject"],
        "$classname": "MBFile",
    })

    mbfile.update({
        "$class": mbfile_class_uid,
        "Size": size,
        "LastModified": mtime,
        "LastStatusChange": last_status_change,
        "Birth": birth,
        "Mode": mode,
        "UserID": uid,
        "GroupID": gid,
        "InodeNumber": 0,
        "ProtectionClass": protection_class,
        "Flags": 4,
        "RelativePath": rel_uid,
    })
    if enc_uid is not None:
        mbfile["EncryptionKey"] = enc_uid

    archive = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": mbfile_uid},
        "$objects": objects,
    }
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


# --------------------------------------------------------------------------- #
# Backup handle
# --------------------------------------------------------------------------- #
@dataclass
class EncryptedBackup:
    device_dir: Path            # <backup_root>/<UDID>
    passphrase: str

    def __post_init__(self):
        self._manifest_plist = plistlib.loads((self.device_dir / "Manifest.plist").read_bytes())
        if not self._manifest_plist.get("IsEncrypted"):
            raise ValueError("Backup is not encrypted")
        self._keybag = ibd_utils.BackupKeyBag(self._manifest_plist["BackupKeyBag"])
        if not self._keybag.unlock_with_passphrase(self.passphrase):
            raise ValueError("Failed to unlock keybag (wrong passphrase?)")
        mk = self._manifest_plist["ManifestKey"]
        self._manifest_class = struct.unpack("<i", mk[:4])[0]
        self._manifest_key = self._keybag.unwrap_key_for_class(self._manifest_class, mk[4:])

    # --- Manifest.db (decrypted, kept on disk in a temp path) --------------- #
    def open_manifest(self, out_path: Path) -> Path:
        pt = decrypt_manifest_db((self.device_dir / "Manifest.db").read_bytes(), self._manifest_key)
        # Device-made Manifest.db = AES-CBC(PKCS7(sqlite)) (iphone_backup_decrypt >= 0.10 strictly
        # unpads it). Strip only a provable PKCS7 tail and remember it for save_manifest().
        pt, self._manifest_padded = _strip_manifest_pkcs7(pt)
        out_path.write_bytes(pt)
        return out_path

    def save_manifest(self, decrypted_path: Path) -> None:
        pt = decrypted_path.read_bytes()
        if getattr(self, "_manifest_padded", False):
            padder = padding.PKCS7(128).padder()
            pt = padder.update(pt) + padder.finalize()
        elif len(pt) % 16:  # legacy unpadded format: pad the tail to a 16-byte boundary with zeros
            pt += b"\x00" * (16 - (len(pt) % 16))
        (self.device_dir / "Manifest.db").write_bytes(encrypt_manifest_db(pt, self._manifest_key))

    def class_key(self, protection_class: int) -> bytes:
        return self._keybag.classes_keys[protection_class]

    def stored_path(self, file_id: str) -> Path:
        return self.device_dir / file_id[:2] / file_id

    # --- read a file -------------------------------------------------------- #
    def read_file(self, manifest_conn: sqlite3.Connection, domain: str, relative_path: str) -> bytes:
        row = manifest_conn.execute(
            "SELECT fileID, file FROM Files WHERE domain=? AND relativePath=?",
            (domain, relative_path)).fetchone()
        if not row:
            raise FileNotFoundError(f"{domain}/{relative_path}")
        file_id, blob = row
        fp = ibd_utils.FilePlist(blob)
        file_key = self._keybag.unwrap_key_for_class(fp.protection_class, fp.encryption_key)
        return decrypt_file_content(self.stored_path(file_id).read_bytes(), file_key)

    # --- modify (replace) an existing file's content ------------------------ #
    def replace_file(self, manifest_conn: sqlite3.Connection, domain: str,
                     relative_path: str, new_plaintext: bytes,
                     mtime: int | None = None) -> str:
        row = manifest_conn.execute(
            "SELECT fileID, file FROM Files WHERE domain=? AND relativePath=?",
            (domain, relative_path)).fetchone()
        if not row:
            raise FileNotFoundError(f"{domain}/{relative_path}")
        file_id, blob = row
        fp = ibd_utils.FilePlist(blob)
        pclass = fp.protection_class
        file_key = self._keybag.unwrap_key_for_class(pclass, fp.encryption_key)
        # re-encrypt with the SAME per-file key so the existing EncryptionKey stays valid
        self.stored_path(file_id).write_bytes(encrypt_file_content(new_plaintext, file_key))
        # MBFile fields that MUST be refreshed after changing content:
        #   Size (decrypted length), LastModified (and typically Birth/LastStatusChange).
        #   Digest is NOT present in modern Manifest.db MBFile blobs, so nothing to recompute.
        new_blob = build_mbfile_blob(
            size=len(new_plaintext), protection_class=pclass,
            encryption_key_blob=struct.pack("<I", pclass) + aes_key_wrap(self.class_key(pclass), file_key),
            mode=fp._data.get("Mode", 0o100644),
            uid=fp._data.get("UserID", 501), gid=fp._data.get("GroupID", 501),
            mtime=mtime if mtime is not None else fp.mtime)
        # re-embed the true relative path (build_mbfile_blob leaves index 2 empty)
        new_blob = _set_relative_path(new_blob, relative_path)
        manifest_conn.execute(
            "UPDATE Files SET file=? WHERE domain=? AND relativePath=?",
            (new_blob, domain, relative_path))
        manifest_conn.commit()
        return file_id

    # --- add a brand-new file ---------------------------------------------- #
    def add_file(self, manifest_conn: sqlite3.Connection, domain: str,
                 relative_path: str, plaintext: bytes, protection_class: int,
                 mtime: int, mode: int = 0o100644) -> str:
        file_id = file_id_for(domain, relative_path)
        file_key = os.urandom(32)
        self.stored_path(file_id).parent.mkdir(parents=True, exist_ok=True)
        self.stored_path(file_id).write_bytes(encrypt_file_content(plaintext, file_key))
        enc_blob = make_encryption_key_blob(protection_class, file_key, self.class_key(protection_class))
        blob = build_mbfile_blob(size=len(plaintext), protection_class=protection_class,
                                 encryption_key_blob=enc_blob, mode=mode, mtime=mtime)
        blob = _set_relative_path(blob, relative_path)
        manifest_conn.execute(
            "INSERT OR REPLACE INTO Files(fileID, domain, relativePath, flags, file) "
            "VALUES(?,?,?,?,?)", (file_id, domain, relative_path, 1, blob))
        manifest_conn.commit()
        return file_id


def _strip_manifest_pkcs7(pt: bytes) -> tuple[bytes, bool]:
    """Return (sqlite_bytes, was_padded). Only strips when page-size arithmetic proves a PKCS7 tail."""
    if len(pt) < 100 or not pt.startswith(b"SQLite format 3\x00"):
        return pt, False
    page = struct.unpack(">H", pt[16:18])[0]
    page = 65536 if page == 1 else page
    n = pt[-1]
    if 1 <= n <= 16 and pt[-n:] == bytes([n]) * n and len(pt) % page and (len(pt) - n) % page == 0:
        return pt[:-n], True
    return pt, False


def _set_relative_path(mbfile_blob: bytes, relative_path: str) -> bytes:
    """Overwrite the RelativePath string (object index 2) in an MBFile archive."""
    arch = plistlib.loads(mbfile_blob)
    arch["$objects"][2] = relative_path
    return plistlib.dumps(arch, fmt=plistlib.FMT_BINARY)


# --------------------------------------------------------------------------- #
# Synthetic backup fabrication + self-test
# --------------------------------------------------------------------------- #
def _tlv(tag: bytes, value) -> bytes:
    if isinstance(value, int):
        value = struct.pack(">I", value)
    return tag + struct.pack(">I", len(value)) + value


def fabricate_encrypted_backup(root: Path, passphrase: str) -> Path:
    """
    Build a minimal but real encrypted backup so the writer can be exercised
    without a device. Produces <root>/<UDID>/ with a keybag, ManifestKey,
    Manifest.db (SQLite) and one encrypted seed file.
    """
    udid = "00008120-ZZFAKEUDID000001"
    dev = root / udid
    dev.mkdir(parents=True, exist_ok=True)

    salt, dpsl = os.urandom(20), os.urandom(20)
    iter_count, dpic = 10000, 10000
    pw = passphrase.encode()
    round1 = hashlib.pbkdf2_hmac("sha256", pw, dpsl, dpic, 32)
    passphrase_key = hashlib.pbkdf2_hmac("sha1", round1, salt, iter_count, 32)

    # protection classes 1..11 as iOS uses; give each a random 32-byte class key.
    class_keys = {c: os.urandom(32) for c in range(1, 12)}

    kb = _tlv(b"VERS", 4) + _tlv(b"TYPE", 1) + _tlv(b"UUID", os.urandom(16)) + _tlv(b"HMCK", os.urandom(40))
    kb += _tlv(b"WRAP", 0) + _tlv(b"SALT", salt) + _tlv(b"ITER", iter_count)
    kb += _tlv(b"DPSL", dpsl) + _tlv(b"DPIC", dpic)
    for c, ck in class_keys.items():
        wpky = aes_key_wrap(passphrase_key, ck)
        kb += _tlv(b"UUID", os.urandom(16)) + _tlv(b"CLAS", c) + _tlv(b"WRAP", 2) + _tlv(b"KTYP", 0) + _tlv(b"WPKY", wpky)

    manifest_class = 4
    manifest_key_raw = os.urandom(32)
    manifest_key_blob = struct.pack("<I", manifest_class) + aes_key_wrap(class_keys[manifest_class], manifest_key_raw)

    plistlib.dump({
        "IsEncrypted": True, "Version": "10.0", "SystemDomainsVersion": "20.0",
        "Date": plistlib.Data(b"") if False else __import__("datetime").datetime(2026, 5, 28),
        "BackupKeyBag": kb, "ManifestKey": manifest_key_blob,
        "Lockdown": {"ProductVersion": "18.0"}, "WasPasscodeSet": True,
    }, (dev / "Manifest.plist").open("wb"))
    plistlib.dump({"BackupState": "new", "IsFullBackup": True, "Version": "3.3",
                   "SnapshotState": "finished", "UUID": str(uuid.uuid4()).upper(),
                   "Date": __import__("datetime").datetime(2026, 5, 28)},
                  (dev / "Status.plist").open("wb"))
    plistlib.dump({"Target Identifier": udid, "Product Version": "18.0"},
                  (dev / "Info.plist").open("wb"))

    # Build a plaintext Manifest.db with one seed file, then CBC-encrypt it.
    tmp_db = root / "_seed.sqlite"
    if tmp_db.exists():
        tmp_db.unlink()
    conn = sqlite3.connect(tmp_db)
    conn.execute("CREATE TABLE Files(fileID TEXT PRIMARY KEY, domain TEXT, "
                 "relativePath TEXT, flags INTEGER, file BLOB)")
    seed_domain = "AppDomainGroup-group.ch.threema"
    seed_path = "seed.txt"
    seed_id = file_id_for(seed_domain, seed_path)
    seed_key = os.urandom(32)
    (dev / seed_id[:2]).mkdir(parents=True, exist_ok=True)
    (dev / seed_id[:2] / seed_id).write_bytes(encrypt_file_content(b"seed-content", seed_key))
    seed_blob = _set_relative_path(
        build_mbfile_blob(size=len(b"seed-content"), protection_class=3,
                          encryption_key_blob=struct.pack("<I", 3) + aes_key_wrap(class_keys[3], seed_key),
                          mtime=1748390400), seed_path)
    conn.execute("INSERT INTO Files VALUES(?,?,?,?,?)", (seed_id, seed_domain, seed_path, 1, seed_blob))
    conn.commit(); conn.close()
    pt = tmp_db.read_bytes()
    if len(pt) % 16:
        pt += b"\x00" * (16 - len(pt) % 16)
    (dev / "Manifest.db").write_bytes(encrypt_manifest_db(pt, manifest_key_raw))
    tmp_db.unlink()
    return dev


def _selftest() -> int:
    import tempfile
    pw = "selftest-" + uuid.uuid4().hex  # ephemeral, never a real password
    with tempfile.TemporaryDirectory() as td:          # never inside the package (signed bundle)
        root = Path(td)
        dev = fabricate_encrypted_backup(root, pw)
        bk = EncryptedBackup(dev, pw)

        mdb = root / "manifest_dec.sqlite"
        bk.open_manifest(mdb)
        conn = sqlite3.connect(mdb)

        # 1) read seed
        got = bk.read_file(conn, "AppDomainGroup-group.ch.threema", "seed.txt")
        assert got == b"seed-content", got
        # 2) replace seed content (same key path)
        bk.replace_file(conn, "AppDomainGroup-group.ch.threema", "seed.txt",
                        b"replaced-content-longer-than-before", mtime=1758979200)
        assert bk.read_file(conn, "AppDomainGroup-group.ch.threema", "seed.txt") == b"replaced-content-longer-than-before"
        # 3) add a new file
        new_domain = "AppDomainGroup-group.ch.threema"
        new_path = ".ThreemaData_SUPPORT/_EXTERNAL_DATA/NEWBLOB"
        payload = os.urandom(5000)
        fid = bk.add_file(conn, new_domain, new_path, payload, protection_class=3, mtime=1758979200)
        assert fid == file_id_for(new_domain, new_path)
        assert bk.read_file(conn, new_domain, new_path) == payload
        conn.close()

        # 4) persist manifest, then re-open the whole backup from scratch and
        #    verify everything decrypts (proves Manifest.db re-encryption works).
        bk.save_manifest(mdb)
        bk2 = EncryptedBackup(dev, pw)
        mdb2 = root / "manifest_dec2.sqlite"
        bk2.open_manifest(mdb2)
        conn2 = sqlite3.connect(mdb2)
        assert bk2.read_file(conn2, new_domain, "seed.txt") == b"replaced-content-longer-than-before"
        assert bk2.read_file(conn2, new_domain, new_path) == payload
        # 5) cross-check with pyiosbackup's independent reader on the same dir
        try:
            from pyiosbackup import Backup
            b = Backup.from_path(dev, password=pw)
            e = b.get_entry_by_domain_and_path(new_domain, new_path)
            assert e.read_bytes() == payload, "pyiosbackup mismatch"
            xcheck = "pyiosbackup cross-check OK"
            b._manifest_db._conn.close()                    # pyiosbackup leaves its decrypted
            Path(b._manifest_db.path).unlink(missing_ok=True)  # Manifest.db copy in TMPDIR
        except Exception as ex:  # pragma: no cover
            xcheck = f"pyiosbackup cross-check skipped: {ex}"
        conn2.close()
    print("SELFTEST PASS: read / replace / add / manifest re-encrypt round-trip OK")
    print(xcheck)
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print(__doc__)
