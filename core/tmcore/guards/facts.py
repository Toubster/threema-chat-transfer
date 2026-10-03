# SPDX-License-Identifier: AGPL-3.0-or-later
"""
facts.py -- read guard facts from backups and extract reports, in memory only (owner: coreB).

    v = BackupView(dev, password)        # ValueError: the keybag does not open with this password
    v.status_date, v.lockdown, v.rows    # rows: [(file_id, domain, rel, flags, MBFile blob)]
    airplane(v)                          # True / False / None (missing or unreadable)
    dcim_rows(v)                         # [(path below Media/, size)]  for dcim_unchanged
    photos_bytes(v)                      # CameraRollDomain file bytes (MBFile Size)
    extract_facts(report)                # AppSetupState, retention, marker, integrity, model digest, app version
    iphone_identity(store_db)            # own Threema ID of the iPhone store (never leaves the process)
    android_identity(normalized_db)      # own Threema ID of the Android backup

Nothing read here is written anywhere or emitted; callers compare and emit codes/counts/hashes only.
"""
from __future__ import annotations

import plistlib
import re
import sqlite3
import urllib.parse
from pathlib import Path

from tmcore.steps.iphone import as_utc, lib

RADIOS = ("SystemPreferencesDomain", "SystemConfiguration/com.apple.radios.plist")
THREEMA_ID = re.compile(r"^[0-9A-Z*][0-9A-Z]{7}$")


class BackupView:
    def __init__(self, dev: Path, password: str):
        bp = lib("backup_pipeline")
        rw = lib("iosbackup_rw")
        self.dev = Path(dev)
        self.manifest = plistlib.loads((self.dev / "Manifest.plist").read_bytes())
        self.status = plistlib.loads((self.dev / "Status.plist").read_bytes())
        self.info = plistlib.loads((self.dev / "Info.plist").read_bytes()) if (self.dev / "Info.plist").exists() \
            else {}
        self.bk = rw.EncryptedBackup(self.dev, password)           # ValueError on a wrong password
        pt = rw.decrypt_manifest_db((self.dev / "Manifest.db").read_bytes(), self.bk._manifest_key)
        pt, _ = bp.strip_manifest_padding(pt)
        buf = bytearray(pt)
        if buf[18] == 2 or buf[19] == 2:
            buf[18] = buf[19] = 1
        conn = sqlite3.connect(":memory:")
        try:
            conn.deserialize(bytes(buf))
            self.rows = [(fid, dom, rel or "", int(fl or 0), bytes(b or b"")) for fid, dom, rel, fl, b in
                         conn.execute("SELECT fileID, domain, relativePath, flags, file FROM Files")]
        finally:
            conn.close()
            del buf
        self._bp, self._rw = bp, rw

    @property
    def status_date(self):
        return as_utc(self.status.get("Date"))

    @property
    def lockdown(self) -> dict:
        return dict(self.manifest.get("Lockdown") or {})

    @property
    def applications(self) -> set[str]:
        return set((self.manifest.get("Applications") or {}).keys())

    def mbfile(self, blob: bytes):
        return self._bp.MBFile(blob)

    def read(self, domain: str, rel: str) -> bytes | None:
        for fid, dom, r, fl, blob in self.rows:
            if (dom, r) == (domain, rel) and fl == 1:
                mb = self._bp.MBFile(blob)
                p = self._bp.stored_path(self.dev, fid)
                if mb.enc_blob is None or not p.is_file():
                    return b"" if mb.size == 0 else None
                return self._rw.decrypt_file_content(p.read_bytes(), self._bp.file_key(self.bk, mb))
        return None


def open_view(dev: Path, password: str) -> BackupView | None:
    """None when the keybag does not open (wrong password)."""
    try:
        return BackupView(dev, password)
    except ValueError:
        return None


def airplane(v: BackupView) -> bool | None:
    try:
        raw = v.read(*RADIOS)
        if raw is None:
            return None
        val = plistlib.loads(raw).get("AirplaneMode")
        return val if isinstance(val, bool) else None
    except Exception:  # noqa: BLE001 -- unreadable = not proven = refused by the guard
        return None


def dcim_rows(v: BackupView) -> list[tuple[str, int]]:
    out = []
    for _fid, dom, rel, fl, blob in v.rows:
        if dom == "CameraRollDomain" and fl == 1 and rel.startswith("Media/DCIM/"):
            out.append((rel[len("Media/"):], v.mbfile(blob).size))
    return out


def photos_bytes(v: BackupView) -> int:
    return sum(v.mbfile(b).size for _f, dom, _r, fl, b in v.rows if dom == "CameraRollDomain" and fl == 1)


def domain_rows(v: BackupView) -> dict[str, int]:
    out: dict[str, int] = {}
    for _f, dom, _r, _fl, _b in v.rows:
        out[dom] = out.get(dom, 0) + 1
    return out


def extract_facts(report: dict) -> dict:
    """Facts from backup_pipeline extract's report.json (counts/structure only)."""
    gc = report.get("group_container") or {}
    gp = gc.get("group_prefs") or {}
    st = report.get("store") or {}
    md = st.get("metadata") or {}
    app = report.get("threema_app") or {}
    short = app.get("CFBundleShortVersionString")
    if not (isinstance(short, str) and re.match(r"^[0-9]{1,4}(\.[0-9]{1,4}){0,3}$", short)):
        short = None
    return {"group_present": bool(gc) and bool(gc.get("db_present")),
            "app_setup_state": gp.get("AppSetupState"), "keep_messages_days": gp.get("KeepMessagesDays"),
            "setup_marker": bool(gc.get("app_setup_not_completed_marker")),
            "integrity_ok": st.get("integrity_check") == "ok",
            "model_hashes": md.get("model_hashes") if isinstance(md.get("model_hashes"), dict) else None,
            "app_version": short, "errors": len(report.get("errors") or []),
            "messages": int(((st.get("counts") or {}).get("ZMESSAGE")) or 0)}


def _ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro", uri=True)


def iphone_identity(store_db: Path) -> str | None:
    """Own Threema ID on the iPhone: Conversation.groupMyIdentity of the group conversations (GroupManager keeps it
    on the current ID). Exactly one well-formed value, else None (unreadable -> fail-closed)."""
    if not store_db.is_file():
        return None
    try:
        c = _ro(store_db)
        try:
            vals = {r[0] for r in c.execute("SELECT DISTINCT ZGROUPMYIDENTITY FROM ZCONVERSATION "
                                            "WHERE ZGROUPID IS NOT NULL AND ZGROUPMYIDENTITY IS NOT NULL")}
        finally:
            c.close()
    except sqlite3.DatabaseError:
        return None
    vals = {v.strip().upper() for v in vals if isinstance(v, str) and v.strip()}
    if len(vals) != 1:
        return None
    v = next(iter(vals))
    return v if THREEMA_ID.match(v) else None


def android_identity(normalized_db: Path) -> str | None:
    if not normalized_db.is_file():
        return None
    try:
        c = _ro(normalized_db)
        try:
            row = c.execute("SELECT value FROM meta WHERE key='own_identity'").fetchone()
        finally:
            c.close()
    except sqlite3.DatabaseError:
        return None
    v = (row[0] if row else None) or None
    v = v.strip().upper() if isinstance(v, str) else None
    return v if v and THREEMA_ID.match(v) else None


def store_counts(store_db: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    if not store_db.is_file():
        return out
    try:
        c = _ro(store_db)
        try:
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for t in ("ZMESSAGE", "ZCONVERSATION", "ZCONTACT", "ZGROUP"):
                if t in names:
                    out[t] = c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        finally:
            c.close()
    except sqlite3.DatabaseError:
        pass
    return out
