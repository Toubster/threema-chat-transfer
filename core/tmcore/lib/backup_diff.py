#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
backup_diff.py -- side-effect detector for iOS restores (tmcore.lib; gate v2 P.3/P.4 of `postcheck`). Compares two
iOS (MobileBackup2) backups of the SAME device, one taken BEFORE and one AFTER a restore (+ reboot), domain by
domain, and says which domains OUTSIDE the restore payload lost / rewrote files. Never talks to a device. Never
prints file names, message texts or identities. Ported from the private proof of concept; behaviour unchanged except
the product rules of DESIGN §4.3: no waivers (--waive), no --deep-paths, no --show-app-names, no password files.

In-process API (tmcore postcheck):
    pre, post = Backup(pre_dev, password, "pre"), Backup(post_dev, password, "post")
    rep = compare(pre, post, payload=Backup(set_dev, password, "payload"), expect=list(DEFAULT_EXPECT), marks=[...],
                  depth=2, content_re=None, baseline=None, alert_min=20, alert_frac=0.10, top=25, collapse_max=3)
Which harmless note classes a user may get is decided per iOS build from compat/ios.json `expected_notes`
(tmcore.verdict); this module only reports the classes with their evidence.

Maintainer CLI (password = ONE line on stdin with --password-stdin, never argv, never a file):
  python3 -m tmcore.lib.backup_diff PRE POST [--password-stdin] [--payload PAYLOAD]
        [--expect REGEX ...] [--mark LABEL=ISO8601 ...] [--content REGEX] [--baseline REPORT.json]
        [--depth N] [--report FILE] [--gate] [--top N] [--tmp-dir DIR]

  PRE / POST / PAYLOAD   <backup_root>/<UDID> (or a backup root that contains exactly one UDID directory).
                         Encrypted (--password-stdin) or unencrypted backups; PRE and POST must be FULL backups.
  --payload              the backup that was restored (restore set or trimmed payload): its domains are "payload"
                         domains (changes expected there) and its files are checked for having landed in POST (same
                         Size). EXCEPTION, the IDENTITY domains HomeDomain + CameraRollDomain + KeyboardDomain: a
                         restore set carries them bit-identical to PRE, so they keep the OUTSIDE rules (thresholds +
                         sentinels, role "identity") and their payload rows must equal PRE (else ALERT: payload not
                         built from PRE).
  --expect REGEX         extra domains where changes are expected (default: the three Threema domain patterns).
  --mark LABEL=TIME      extra time marks (e.g. restore_start=2026-01-01T09:00:00Z); entries added/rewritten
                         after PRE are bucketed by Birth/LastModified between consecutive marks. PRE/POST backup dates
                         are always marks. Naive times are local time.
  --content REGEX        for domains matching REGEX also decrypt both sides IN MEMORY and compare sha256 of every file
                         present on both sides (detects content changes with identical metadata). Nothing is written.
  --baseline REPORT      report of a CONTROL run (pre/post backups WITHOUT a restore in between, same reboot): per
                         domain, removals up to 2x the control's removals (+ the minimum) count as normal churn.
  --gate                 exit 1 when an ALERT is raised (domain outside payload lost files, sentinel area damaged,
                         com.apple.purplebuddy.plist setup keys changed: SetupState/RestoreState/SetupLastExit/
                         SetupDone = Setup Assistant ran again -- except class apple_account_rerun with SetupDone
                         true before and after, see "Benign classes"); exit 0 otherwise (notes are printed but do
                         not fail the gate). Without --gate: exit 0.
  --collapse-max N       per system domain (outside/identity): ALERT when more than N files > 16 KiB shrank below
                         50 % (default 3; a control pair stayed well below, the incident far above).

Reset detectors beyond file removal (after a restore-set restore EVERY HomeDomain
file is 'recreated', so inode/Birth say nothing -- these look at size and content structure, in memory, counts only):
  area_wiped      system domain (outside/identity): a Library/<x> or Media/<x> area with >= 2 files PRE, 0 POST
  collapse        system domain: > --collapse-max files shrank from > 16 KiB to < 50 %
  sentinel        any sentinel area with >= 1 file PRE and 0 POST (wiped), >= 25 % removed (files_lost), or a
                  sentinel file >= 64 KiB that shrank below 10 % (collapsed; not photos_library: thumbnails churn)
  db_rows         total row count of TCC / Messages / Accounts / CallHistory / Calendar databases dropped >= 25 %
                  (PRE >= 20 rows); control pairs stay well below, the incident was far above
  keychain_items  keychain-backup.plist item count per class (genp/inet/keys) decreased (incident: genp and keys dropped)
  identity        HomeDomain/CameraRollDomain/KeyboardDomain removals allowed = --alert-min only (no 10 % share)

Benign classes (no-op canary, iOS 27.0 24A437; docs/RESTORE-MECHANISM.md): each turns ONE specific alert into
PASS-with-note ("notes" in the report, NOTE lines in the summary) only while its evidence holds; otherwise the
alert stays. The incident real3 -> real3-post fails every one of these conditions.
  poster_cache_regenerated   PosterBoard domain / wallpapers sentinel lost files, but for EVERY file extension the
                             POST count is >= PRE and the total did not shrink (ClockPoster/GalleryCache rewritten
                             under new UUIDs); never for wiped/area_wiped/collapse
  shortcuts_catalogue_regenerated  shortcuts sentinel lost files, but every removed file is the ToolKit tool
                             catalogue (Library/Shortcuts/ToolKit/Tools-*.sqlite[-wal|-shm|.lock]), POST has at least
                             as many catalogue files, Shortcuts.sqlite is present, not collapsed (>= 50 % of its size)
                             and its row total did not drop (db probe 'shortcuts')
  calendar_sync_tables       Calendar.sqlitedb row total dropped >= 25 %, but its key tables Store / Calendar /
                             CalendarItem each have POST >= PRE rows (only change-tracking/sync tables were rebuilt
                             after the Apple-account sign-in)
  apple_account_rerun        purplebuddy: SetupDone true before AND after, and the only keys whose (raw, in-memory)
                             values changed are SetupLastExit, *Presented, the Setup Assistant bookkeeping keys
                             (chronicle, lastPrepareLaunchSentinel, GuessedCountry) and Locale with only its region
                             modifier (@rg=) changed -> the Setup Assistant re-ran for the Apple account only. Without
                             SetupDone=true on both sides the class still alerts; the user's answer after the
                             restart decides (postcheck --buddy-answer).
  --depth N              path-prefix depth of the (sanitized) bucket breakdown in the report (default and strict
                         maximum 2: 2nd component only below Library/ or Media/).

Per entry, keyed by (domain, relativePath): removed | added | recreated (inode or Birth changed: deleted and written
again, also what atomic plist saves look like) | modified (same inode, Size/LastModified changed) | meta (mode,
protection class, owner) | unchanged. Files, directories and symlinks are counted separately; verdicts use files.

Privacy: output = domain names (Apple domains in clear, third-party bundle ids always hashed as app#xxxxxxxx),
directory-prefix buckets whose components are sanitized (UUID/hex/number/@/non-ASCII -> placeholders, never the
file name itself), counts, timestamps and, for com.apple.purplebuddy.plist only, key names with numbers/booleans and
state-like strings. The decrypted Manifest.db lives in memory only (sqlite3 deserialize); the password arrives in
process (or as one stdin line) and is never printed.

Exit codes: 0 ok / no gate, 1 gate failed, 2 usage or read error.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import hashlib
import json
import plistlib
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from . import backup_pipeline as bp  # quiets the library loggers; temp files under bp.TMP_ROOT (use_tmp)
    from . import iosbackup_rw as rw
except ImportError:  # pragma: no cover -- loaded as a top-level module
    import backup_pipeline as bp  # type: ignore[no-redef]
    import iosbackup_rw as rw  # type: ignore[no-redef]

FLAG_FILE, FLAG_DIR, FLAG_LINK = 1, 2, 4
KIND = {FLAG_FILE: "files", FLAG_DIR: "dirs", FLAG_LINK: "links"}
STATUSES = ("removed", "added", "recreated", "modified", "meta", "unchanged")
CONTAINER_KINDS = ("AppDomainGroup-", "AppDomainPlugin-", "AppDomain-", "SysSharedContainerDomain-",
                   "SysContainerDomain-")
APPLE_IDENT_PREFIXES = ("com.apple.", "group.com.apple.", "systemgroup.com.apple.")
DEFAULT_EXPECT = bp.THREEMA_REGEXES  # applied to "<domain>/"
# Domains a restore set carries COMPLETE and bit-identical to its source (= PRE) backup (docs/RESTORE-MECHANISM.md, iOS 27
# deletes whatever of them is missing from the payload; KeyboardDomain since the canary 2026-10-01, where its
# learned model was reset while it was absent from the set). In the payload they must NOT be exempt: role "identity"
# = judged exactly like "outside" (thresholds + sentinels), plus payload rows == PRE rows.
IDENTITY_DOMAINS = frozenset({"HomeDomain", "CameraRollDomain", "KeyboardDomain"})

# Areas whose loss the user notices immediately (incident 2026-10-01, iOS 27.0 24A437). Regexes on (domain, path).
SENTINELS = (
    ("watch_pairing", "Apple Watch pairing (NanoRegistry / DeviceRegistry)", r"^HomeDomain$", r"^Library/DeviceRegistry(/|$)"),
    ("preferences", "System and Apple-app preferences", r"^HomeDomain$", r"^Library/Preferences(/|$)"),
    ("notification_settings", "Notification settings (BulletinBoard / UserNotifications)", r"^HomeDomain$",
     r"^Library/(BulletinBoard|UserNotifications)(/|$)"),
    ("privacy_permissions", "App privacy permissions (TCC)", r"^HomeDomain$", r"^Library/TCC(/|$)"),
    ("springboard", "Home Screen layout / SpringBoard", r"^HomeDomain$", r"^Library/SpringBoard(/|$)"),
    ("wallpapers", "Wallpapers / Lock Screens (PosterBoard)", r"^(AppDomain|AppDomainGroup|AppDomainPlugin)-.*[Pp]oster", r""),
    ("shortcuts", "Shortcuts", r"^(HomeDomain|AppDomain-com\.apple\.shortcuts|AppDomainGroup-group\.com\.apple\.shortcuts)$",
     r"(^|/)Shortcuts(/|$)"),
    ("local_photos", "Local photos (DCIM)", r"^(CameraRollDomain|MediaDomain)$", r"^Media/DCIM(/|$)"),
    ("photos_library", "Photos library database", r"^(CameraRollDomain|MediaDomain)$", r"^Media/PhotoData(/|$)"),
    ("sms_attachments", "Messages attachments", r"^(MediaDomain|HomeDomain)$", r"^Library/SMS/Attachments(/|$)"),
    ("messages_db", "Messages database", r"^HomeDomain$", r"^Library/SMS/sms\.db$"),
    ("keychain", "Keychain backup", r"^KeychainDomain$", r""),
    ("health", "Health", r"^HealthDomain$", r""),
    ("wifi_network", "Wi-Fi / network settings", r"^(SystemPreferencesDomain|WirelessDomain)$", r""),
)
# smaller areas added after the adversarial review: all removed or reset in the incident
SENTINELS += (
    ("keyboard", "Keyboard dictionary / learned words", r"^KeyboardDomain$", r""),
    ("accounts", "Accounts database", r"^HomeDomain$", r"^Library/Accounts(/|$)"),
    ("wallet_passes", "Wallet passes", r"^HomeDomain$", r"^Library/Passes(/|$)"),
    ("call_history", "Call history", r"^HomeDomain$", r"^Library/CallHistory(DB|Transactions)(/|$)"),
    ("calendar", "Calendar", r"^HomeDomain$", r"^Library/Calendar(/|$)"),
    ("identity_services", "iMessage / FaceTime registration (IdentityServices)", r"^HomeDomain$",
     r"^Library/IdentityServices(/|$)"),
)
NO_COLLAPSE_SENTINELS = frozenset({"photos_library"})     # thumbnails/derivatives shrink legitimately (control: 19 %)
SENTINEL_COLLAPSE_MIN, SENTINEL_COLLAPSE_FRAC = 64 * 1024, 0.10
COLLAPSE_MIN, COLLAPSE_FRAC = 16 * 1024, 0.50
COLLAPSE_SKIP_SUFFIXES = ("-wal", "-shm", "-journal")
BUCKET_WIPE_MIN = 2
# databases whose row totals are compared (in memory, counts only); ids keep file names out of the output
DB_PROBES = (
    ("tcc", "App privacy permissions (TCC)", "HomeDomain", "Library/TCC/TCC.db"),
    ("messages", "Messages database", "HomeDomain", "Library/SMS/sms.db"),
    ("accounts", "Accounts database", "HomeDomain", "Library/Accounts/Accounts3.sqlite"),
    ("call_history", "Call history", "HomeDomain", "Library/CallHistoryDB/CallHistory.storedata"),
    ("calendar", "Calendar", "HomeDomain", "Library/Calendar/Calendar.sqlitedb"),
    # canary 2026-10-01: rows unchanged (control pair unchanged, incident dropped); evidence for the class
    # shortcuts_catalogue_regenerated
    ("shortcuts", "Shortcuts database", "HomeDomain", "Library/Shortcuts/Shortcuts.sqlite"),
)
DB_DROP_FRAC, DB_MIN_ROWS = 0.25, 20
# per database: tables that hold the user's data; when only OTHER tables shrank (change tracking / sync state rebuilt
# after the Apple-account sign-in) a total-row drop is benign. Canary: Calendar total rows dropped while Store,
# Calendar and CalendarItem kept every row; the incident dropped rows in all three.
DB_KEY_TABLES = {"calendar": ("Store", "Calendar", "CalendarItem")}
# benign regeneration classes (no-op canary on iOS 27.0 24A437)
POSTER_DOMAIN_RE = re.compile(r"^(AppDomain|AppDomainGroup|AppDomainPlugin)-(group\.)?com\.apple\.[A-Za-z0-9.]*[Pp]oster")
SHORTCUTS_CATALOGUE_RE = re.compile(r"^Library/Shortcuts/ToolKit/Tools-[A-Za-z0-9._-]+\.sqlite(-wal|-shm|\.lock)?$")
SHORTCUTS_DB_KEY = ("HomeDomain", "Library/Shortcuts/Shortcuts.sqlite")
KEYCHAIN_KEY = ("KeychainDomain", "keychain-backup.plist")
KEYCHAIN_CLASSES = ("genp", "inet", "cert", "keys")
KEYCHAIN_ALERT_CLASSES = ("genp", "inet", "keys")
PROBE_KEYS = frozenset({KEYCHAIN_KEY} | {(d, r) for _i, _l, d, r in DB_PROBES})
# purplebuddy keys the Setup Assistant changes when it only re-runs for the Apple account after a USB restore
# (protocol §3: RestoredFromiTunesBackup on EVERY drive restore). Anything else (language, analytics, SetupDone) =
# setup reset.
PB_RERUN_KEY_RE = re.compile(r"^(SetupLastExit|[A-Za-z0-9]+Presented)$")
# Setup Assistant bookkeeping that moves on EVERY buddy run (changed in the canary AND in the incident, so they never
# decide a class on their own); GuessedCountry = the region guess that goes with a Locale region change
PB_BOOKKEEPING_KEYS = frozenset({"chronicle", "lastPrepareLaunchSentinel", "GuessedCountry"})
PB_LOCALE_KEYS = frozenset({"Locale"})          # rerun key only when just the @rg= region modifier changed
PURPLEBUDDY_SUFFIX = "Preferences/com.apple.purplebuddy.plist"
PB_STATE_KEY_RE = re.compile(r"(State|Version|Build|Exit|Step)s?$")
PB_STATE_VAL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
UUID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
HEX_RE = re.compile(r"^[0-9A-Fa-f]{8,}$")
NUM_RE = re.compile(r"^[0-9][0-9._-]*$")
SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9 ._+~-]{1,48}$")
PRIVATE_TREE_RE = re.compile(r"FileProvider|LocalStorage|CloudDocs|Mobile Documents|Documents", re.I)


class DiffError(Exception):
    pass


@dataclass(slots=True)
class Entry:
    file_id: str
    flags: int
    size: int
    mtime: int
    birth: int
    inode: int
    mode: int
    pclass: int
    uid: int
    gid: int


# --------------------------------------------------------------------------- reading
def device_dir(p: str) -> Path:
    d = Path(p).expanduser()
    if (d / "Manifest.db").is_file():
        return d
    subs = [s for s in d.iterdir() if s.is_dir() and (s / "Manifest.db").is_file()] if d.is_dir() else []
    if len(subs) == 1:
        return subs[0]
    raise DiffError(f"{p}: not a backup directory (no Manifest.db, {len(subs)} candidate sub-directories)")


def _date(v) -> _dt.datetime | None:
    if isinstance(v, _dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc)   # plistlib dates are UTC
    return None


class Backup:
    def __init__(self, dev: Path, password: str | None, label: str):
        self.dev, self.label = dev, label
        self.manifest = plistlib.loads((dev / "Manifest.plist").read_bytes())
        self.status = plistlib.loads((dev / "Status.plist").read_bytes()) if (dev / "Status.plist").exists() else {}
        self.info = plistlib.loads((dev / "Info.plist").read_bytes()) if (dev / "Info.plist").exists() else {}
        self.encrypted = bool(self.manifest.get("IsEncrypted"))
        self.bk = None
        mdb = (dev / "Manifest.db").read_bytes()
        self.manifest_sha256 = hashlib.sha256(mdb).hexdigest()     # identity of this backup (report meta)
        if self.encrypted:
            if not password:
                raise DiffError(f"{label}: backup is encrypted, a password is required")
            try:
                self.bk = rw.EncryptedBackup(dev, password)
            except ValueError as e:
                raise DiffError(f"{label}: {e}") from None
            pt = rw.decrypt_manifest_db(mdb, self.bk._manifest_key)
            pt, _ = bp.strip_manifest_padding(pt)
        else:
            pt = mdb
        del mdb
        if not pt.startswith(b"SQLite format 3\x00"):
            raise DiffError(f"{label}: Manifest.db did not decrypt to SQLite (wrong password?)")
        pt = bytearray(pt)
        if pt[18] == 2 or pt[19] == 2:      # WAL header -> rollback, so the in-memory copy opens without a -wal file
            pt[18] = pt[19] = 1
        self.conn = sqlite3.connect(":memory:")
        self.conn.deserialize(bytes(pt))
        del pt
        self.entries: dict[tuple[str, str], Entry] = {}
        self._blobs: dict[tuple[str, str], bytes] = {}
        for fid, dom, rel, flags, blob in self.conn.execute(
                "SELECT fileID, domain, relativePath, flags, file FROM Files"):
            rel = rel or ""
            try:
                mb = bp.MBFile(blob)
                e = Entry(fid, int(flags or 0), mb.size, mb.mtime, mb.i("Birth"), mb.inode, mb.mode, mb.pclass,
                          mb.uid, mb.gid)
            except Exception:  # noqa: BLE001 -- malformed MBFile: keep the row, metadata unknown
                e = Entry(fid, int(flags or 0), -1, 0, 0, 0, 0, 0, 0, 0)
            self.entries[(dom, rel)] = e
            if rel.endswith(PURPLEBUDDY_SUFFIX) or (dom, rel) in PROBE_KEYS:
                self._blobs[(dom, rel)] = blob
        self.conn.close()

    @property
    def date(self) -> _dt.datetime | None:
        return _date(self.status.get("Date")) or _date(self.manifest.get("Date"))

    def meta(self) -> dict:
        lk = self.manifest.get("Lockdown") or {}
        doms = {d for d, _ in self.entries}
        return {"date": self.date.isoformat() if self.date else None,
                "ios": lk.get("ProductVersion"), "build": lk.get("BuildVersion"), "product": lk.get("ProductType"),
                "encrypted": self.encrypted, "status_version": self.status.get("Version"),
                "is_full_backup_flag": self.status.get("IsFullBackup"),
                "manifest_applications": len(self.manifest.get("Applications") or {}),
                "info_applications": len(self.info.get("Applications") or {}),
                "entries": len(self.entries), "domains": len(doms), "manifest_sha256": self.manifest_sha256}

    def read_plain(self, key: tuple[str, str]) -> bytes | None:
        """Decrypt one file into memory (purplebuddy, keychain item counts, DB row counts; never written)."""
        e = self.entries.get(key)
        if e is None or e.flags != FLAG_FILE:
            return None
        src = bp.stored_path(self.dev, e.file_id)
        if not src.is_file():
            return None
        if not self.encrypted:
            return src.read_bytes()
        blob = self._blobs.get(key)
        if blob is None:
            return None
        mb = bp.MBFile(blob)
        if not mb.enc_blob:
            return None
        k = bp.file_key(self.bk, mb)
        dec = bp._cbc(k).decryptor()
        unpad = bp.padding.PKCS7(128).unpadder()
        data = src.read_bytes()
        return unpad.update(dec.update(data) + dec.finalize()) + unpad.finalize()

    def content_hash(self, key: tuple[str, str], blob: bytes) -> str | None:
        e = self.entries[key]
        src = bp.stored_path(self.dev, e.file_id)
        if not src.is_file():
            return None
        if not self.encrypted:
            return bp.sha256_file(src)
        mb = bp.MBFile(blob)
        if not mb.enc_blob:
            return None if e.size else hashlib.sha256(b"").hexdigest()
        return bp.decrypt_to(src, bp.file_key(self.bk, mb), None)[1]


def load_blobs(b: Backup, keys: set[tuple[str, str]]) -> dict[tuple[str, str], bytes]:
    """Re-read MBFile blobs for selected rows (for --content), from a fresh in-memory Manifest copy."""
    if not keys:
        return {}
    if b.encrypted:
        pt = rw.decrypt_manifest_db((b.dev / "Manifest.db").read_bytes(), b.bk._manifest_key)
        pt, _ = bp.strip_manifest_padding(pt)
    else:
        pt = (b.dev / "Manifest.db").read_bytes()
    pt = bytearray(pt)
    if pt[18] == 2 or pt[19] == 2:
        pt[18] = pt[19] = 1
    c = sqlite3.connect(":memory:")
    c.deserialize(bytes(pt))
    out = {}
    doms = {d for d, _ in keys}
    for dom in doms:
        for rel, blob in c.execute("SELECT relativePath, file FROM Files WHERE domain = ?", (dom,)):
            k = (dom, rel or "")
            if k in keys:
                out[k] = blob
    c.close()
    return out


def keychain_counts(b: Backup) -> dict | None:
    """Item count per class of keychain-backup.plist (decrypted in memory; item contents stay encrypted and are
    never looked at). None = no keychain row."""
    if KEYCHAIN_KEY not in b.entries:
        return None
    try:
        raw = b.read_plain(KEYCHAIN_KEY)
        kc = plistlib.loads(raw) if raw is not None else None
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    if not isinstance(kc, dict):
        return {"error": "unreadable"}
    return {k: len(kc[k]) for k in KEYCHAIN_CLASSES if isinstance(kc.get(k), list)}


def db_rows(b: Backup, key: tuple[str, str]) -> int | str | None:
    """Total row count over all ordinary tables of one SQLite file of the backup (in memory). None = no row."""
    t = db_tables(b, key)
    return sum(t.values()) if isinstance(t, dict) else t


def db_tables(b: Backup, key: tuple[str, str]) -> dict[str, int] | str | None:
    """Row count per ordinary table of one SQLite file of the backup (in memory; table names are schema names, no
    content). None = no row; str = unreadable/not_sqlite."""
    if key not in b.entries:
        return None
    try:
        raw = b.read_plain(key)
    except Exception as e:  # noqa: BLE001
        return f"unreadable:{type(e).__name__}"
    if raw is None:
        return "unreadable"
    if not raw.startswith(b"SQLite format 3\x00"):
        return "not_sqlite"
    buf = bytearray(raw)
    del raw
    if buf[18] == 2 or buf[19] == 2:
        buf[18] = buf[19] = 1
    c = sqlite3.connect(":memory:")
    try:
        c.deserialize(bytes(buf))
        del buf
        out: dict[str, int] = {}
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            try:
                out[t] = c.execute('SELECT count(*) FROM "%s"' % t.replace('"', '""')).fetchone()[0]
            except sqlite3.DatabaseError:       # e.g. virtual table of a module this sqlite lacks
                continue
        return out
    except sqlite3.DatabaseError as e:
        return f"unreadable:{type(e).__name__}"
    finally:
        c.close()


# --------------------------------------------------------------------------- labelling (privacy)
def split_container(domain: str) -> tuple[str, str] | None:
    for k in CONTAINER_KINDS:
        if domain.startswith(k):
            return k, domain[len(k):]
    return None


def is_third_party(domain: str) -> bool:
    sc = split_container(domain)
    if sc is None or bp.is_threema_domain(domain):
        return False
    kind, ident = sc
    return kind.startswith("App") and not ident.startswith(APPLE_IDENT_PREFIXES)


def domain_label(domain: str, show_apps: bool = False) -> str:
    if show_apps:
        raise ValueError("app names are not part of the product (DESIGN §4.3)")
    if not is_third_party(domain):
        return domain
    kind, ident = split_container(domain)
    return f"{kind}app#{hashlib.sha256(ident.encode()).hexdigest()[:8]}"


def sanitize_component(c: str) -> str:
    if UUID_RE.match(c):
        return "<uuid>"
    if HEX_RE.match(c):
        return "<hex>"
    if NUM_RE.match(c):
        return "<n>"
    if "@" in c or not SAFE_COMPONENT_RE.match(c):
        return "<x>"
    return c


def bucket(domain: str, rel: str, flags: int, depth: int, show_apps: bool, deep: bool = False) -> str:
    """Directory prefix of an entry, never its file name. Strict default: at most 2 components and the 2nd only
    below Library/ or Media/ (system/app-defined names; users cannot create folders there). Folders users CAN name
    (app Documents/, Files-app storage, iCloud Drive) are cut at depth 1. There is no way to lift this."""
    parts = [p for p in rel.split("/") if p] if rel else []
    dirparts = parts if flags == FLAG_DIR else parts[:-1]
    if deep or show_apps:
        raise ValueError("deep paths / app names are not part of the product (DESIGN §4.3)")
    d = min(depth, 2)
    if is_third_party(domain):
        d = min(d, 1)
    if PRIVATE_TREE_RE.search(domain) or not dirparts or dirparts[0] not in ("Library", "Media"):
        d = min(d, 1)
    comps = [sanitize_component(c) for c in dirparts[:d]]
    return "/".join(comps) or "."


# --------------------------------------------------------------------------- comparison
def classify(a: Entry | None, b: Entry | None) -> str:
    if a is None:
        return "added"
    if b is None:
        return "removed"
    if a.inode != b.inode or a.birth != b.birth:
        return "recreated"
    if a.size != b.size or a.mtime != b.mtime:
        return "modified"
    if (a.mode, a.pclass, a.uid, a.gid) != (b.mode, b.pclass, b.uid, b.gid):
        return "meta"
    return "unchanged"


def parse_mark(s: str) -> tuple[str, _dt.datetime]:
    if "=" not in s:
        raise DiffError(f"--mark needs LABEL=ISO8601, got {s!r}")
    label, t = s.split("=", 1)
    try:
        dt = _dt.datetime.fromisoformat(t)
    except ValueError:
        raise DiffError(f"--mark {label}: bad time {t!r}") from None
    if dt.tzinfo is None:
        dt = dt.astimezone()          # naive = local time
    return label, dt


def interval_labels(marks: list[tuple[str, _dt.datetime]]):
    marks = sorted(marks, key=lambda m: m[1])
    edges = [(m[1].timestamp(), m[0]) for m in marks]

    def lab(ts: int) -> str:
        if not ts:
            return "no-time"
        prev = "start"
        for t, name in edges:
            if ts < t:
                return f"{prev}..{name}"
            prev = name
        return f"after {prev}"
    return lab


def sanitize_pb(v, depth: int = 0, key: str = ""):
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if isinstance(v, _dt.datetime):
        return _date(v).isoformat()
    if isinstance(v, (bytes, bytearray)):
        return f"<data {len(v)}>"
    if isinstance(v, str):
        if PB_STATE_KEY_RE.search(key) and PB_STATE_VAL_RE.match(v) and not v.isdigit():
            return v
        return f"<str {len(v)}>"
    if isinstance(v, dict):
        if depth >= 2:
            return f"<dict {len(v)}>"
        return {str(k): sanitize_pb(x, depth + 1, str(k)) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, list):
        return f"<list {len(v)}>"
    return f"<{type(v).__name__}>"


def _locale_region_only(a, b) -> bool:
    """True when two ICU locale ids differ ONLY in the region modifier (en_US -> en_US@rg=chzzzz): the Setup
    Assistant sets it after the Apple-account sign-in; a language/country reset changes the base (incident:
    the base changed). Values are compared in memory and never printed."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False

    def split(s: str):
        base, _, kw = s.partition("@")
        kws = {}
        for part in kw.split(";") if kw else []:
            k, sep, v = part.partition("=")
            if not sep:
                return None
            kws[k.strip().lower()] = v
        kws.pop("rg", None)
        return base, kws
    x, y = split(a), split(b)
    return x is not None and y is not None and x == y and a != b


def purplebuddy(pre: Backup, post: Backup, show_apps: bool) -> dict:
    keys = sorted({k for k in list(pre.entries) + list(post.entries) if k[1].endswith(PURPLEBUDDY_SUFFIX)
                   and (pre.entries.get(k) or post.entries.get(k)).flags == FLAG_FILE})
    out = {"files": [], "changed_keys": [], "raw_changed_keys": [], "alert": False, "notes": [], "pass_note": None}
    legacy_alert = False
    setup_done = {"pre": [], "post": []}
    locale_region_only: list[bool] = []
    for k in keys:
        rec = {"domain": domain_label(k[0], show_apps), "path": k[1] if k[1].startswith("Library/") else "<x>"}
        vals, raws = {}, {}
        for side, b in (("pre", pre), ("post", post)):
            try:
                raw = b.read_plain(k)
                obj = plistlib.loads(raw) if raw is not None else None
                raws[side] = obj if isinstance(obj, dict) else {}
                vals[side] = sanitize_pb(obj) if obj is not None else None
            except Exception as e:  # noqa: BLE001
                raws[side] = {}
                vals[side] = f"<unreadable: {type(e).__name__}>"
        rec.update(vals)
        a = vals.get("pre") if isinstance(vals.get("pre"), dict) else {}
        z = vals.get("post") if isinstance(vals.get("post"), dict) else {}
        ch = sorted(x for x in set(a) | set(z) if a.get(x) != z.get(x))
        rec["changed"] = ch
        # raw comparison (in memory, key NAMES only leave this function): a same-length change such as
        # Language en -> de is invisible in the sanitized view ('<str 2>' both sides)
        ra, rz = raws["pre"], raws["post"]
        rch = sorted(str(x) for x in set(ra) | set(rz) if ra.get(x) != rz.get(x))
        rec["raw_changed"] = rch
        out["raw_changed_keys"] += rch
        for x in rch:
            if x in PB_LOCALE_KEYS:
                locale_region_only.append(_locale_region_only(ra.get(x), rz.get(x)))
        for side, d in (("pre", ra), ("post", rz)):
            setup_done[side].append(d.get("SetupDone"))
        for x in ch:
            out["changed_keys"].append(x)
            # SetupLastExit moves whenever Setup Assistant (buddy) ran again -- seen 2026-10-01 after the partial
            # restore although the plist's SetupState stayed 'SetupUsingAssistant' (lockdown said RestoredFromiTunesBackup)
            if re.search(r"(Setup|Restore)State|SetupLastExit|SetupDone|SetupFinishedAllSteps", x) \
                    or "Restored" in str(z.get(x, "")):
                legacy_alert = True
        out["files"].append(rec)
    if not keys:
        out["notes"].append("no com.apple.purplebuddy.plist in either backup (check lockdown domain "
                            "com.apple.purplebuddy on the device instead)")
    # class (refined after the no-op canary): apple_account_rerun = only SetupLastExit /
    # *Presented / buddy bookkeeping / the Locale REGION modifier changed (Setup Assistant re-ran for the Apple account,
    # expected after every USB restore); restore_state = additionally SetupState/RestoreState now 'Restored...'
    # (protocol §3: set on every drive restore); setup_reset = anything else (language, analytics, SetupDone ... =
    # the plist was reset, the incident pattern). Decided on the RAW changed keys.
    def rerun_key(x: str) -> bool:
        if PB_RERUN_KEY_RE.match(x) or x in PB_BOOKKEEPING_KEYS:
            return True
        return x in PB_LOCALE_KEYS and bool(locale_region_only) and all(locale_region_only)
    changed = sorted(set(out["raw_changed_keys"]) | set(out["changed_keys"]))
    other = [k for k in changed if not rerun_key(k)]
    post_vals: dict = {}
    for f in out["files"]:
        if isinstance(f.get("post"), dict):
            post_vals.update(f["post"])
    restore_marks = [k for k in other if re.fullmatch(r"(Setup|Restore)State", k)
                     and "Restored" in str(post_vals.get(k, ""))]
    if not changed:
        out["class"] = "unchanged"
    elif not other:
        out["class"] = "apple_account_rerun"
    elif len(restore_marks) == len(other):
        out["class"] = "restore_state"
    else:
        out["class"] = "setup_reset"
    out["non_rerun_keys"] = other
    out["locale_region_only"] = (all(locale_region_only) if locale_region_only else None)
    done_both = bool(setup_done["pre"]) and all(v is True for v in setup_done["pre"] + setup_done["post"])
    out["setup_done_before_and_after"] = done_both
    if out["class"] == "apple_account_rerun" and done_both:
        # PASS with note: SetupDone proves the setup itself was not reset; everything that moved is explained by an
        # Apple-account-only re-run (canary 2026-10-01: SetupLastExit, Locale region, GuessedCountry, bookkeeping)
        out["alert"] = False
        out["pass_note"] = ("purplebuddy: class=apple_account_rerun, SetupDone true before and after, changed only "
                            + ", ".join(changed) + " -> Setup Assistant re-ran for the Apple account only")
    elif out["class"] in ("setup_reset", "restore_state"):
        out["alert"] = True
    else:
        out["alert"] = legacy_alert       # apple_account_rerun WITHOUT SetupDone proof: operator confirms (E.4)
    return out


def file_ext(rel: str) -> str:
    """Lower-case extension of the file name (counted per extension for the poster class, never printed)."""
    n = rel.rsplit("/", 1)[-1]
    return n.rsplit(".", 1)[-1].lower() if "." in n.lstrip(".") else "<none>"


def compare(pre: Backup, post: Backup, *, payload: Backup | None, expect: list[str], marks, depth: int,
            content_re: str | None, baseline: dict | None, alert_min: int, alert_frac: float, top: int,
            collapse_max: int = 3, show_apps: bool = False, deep: bool = False, waivers: list | None = None) -> dict:
    """show_apps / deep / waivers: accepted only with their product values (False, False, None/empty) so existing
    callers keep working; anything else is refused -- the product has no waivers, deep paths or app names."""
    if show_apps or deep or waivers:
        raise ValueError("waivers, deep paths and app names are not part of the product (DESIGN §4.3)")
    expect_res = [re.compile(x) for x in expect]
    payload_domains = {d for d, _ in payload.entries} if payload else set()
    identity_domains = payload_domains & IDENTITY_DOMAINS
    exempt_domains = payload_domains - identity_domains      # changes expected there

    def role(dom: str) -> str:
        if dom in identity_domains:
            return "identity"
        if dom in payload_domains:
            return "payload"
        if any(r.search(dom + "/") for r in expect_res):
            return "expected"
        return "outside"

    lab = interval_labels(marks)
    doms: dict[str, dict] = {}
    keys = set(pre.entries) | set(post.entries)
    content_keys: set = set()
    content_dom = re.compile(content_re) if content_re else None
    for k in keys:
        a, b = pre.entries.get(k), post.entries.get(k)
        st = classify(a, b)
        e = b or a
        kind = KIND.get(e.flags, "other")
        d = doms.setdefault(k[0], {"pre": collections.Counter(), "post": collections.Counter(),
                                   "status": collections.defaultdict(collections.Counter),
                                   "buckets": collections.defaultdict(collections.Counter),
                                   "when": collections.defaultdict(collections.Counter),
                                   "area_pre": collections.Counter(), "area_post": collections.Counter(),
                                   "collapsed": collections.Counter(),
                                   "ext_pre": collections.Counter(), "ext_post": collections.Counter()})
        if a:
            d["pre"][KIND.get(a.flags, "other")] += 1
        if b:
            d["post"][KIND.get(b.flags, "other")] += 1
        if split_container(k[0]) is None:          # system domain: per-area file counts + size collapse
            area = bucket(k[0], k[1], FLAG_FILE, 2, show_apps)
            if a and a.flags == FLAG_FILE:
                d["area_pre"][area] += 1
            if b and b.flags == FLAG_FILE:
                d["area_post"][area] += 1
            if (a and b and a.flags == FLAG_FILE and b.flags == FLAG_FILE and a.size > COLLAPSE_MIN
                    and 0 <= b.size < COLLAPSE_FRAC * a.size and not k[1].endswith(COLLAPSE_SKIP_SUFFIXES)):
                d["collapsed"][area] += 1
        elif POSTER_DOMAIN_RE.search(k[0]):          # class poster_cache_regenerated: files per extension
            if a and a.flags == FLAG_FILE:
                d["ext_pre"][file_ext(k[1])] += 1
            if b and b.flags == FLAG_FILE:
                d["ext_post"][file_ext(k[1])] += 1
        d["status"][kind][st] += 1
        if st != "unchanged":
            d["buckets"][st][bucket(k[0], k[1], e.flags, depth, show_apps, deep)] += 1
        if st in ("added", "recreated"):
            d["when"][st][lab(b.birth)] += 1
        elif st == "modified":
            d["when"][st][lab(b.mtime)] += 1
        if content_dom and a and b and a.flags == FLAG_FILE and b.flags == FLAG_FILE \
                and content_dom.search(k[0]):
            content_keys.add(k)

    # --- optional content comparison (in memory) ---
    content = {}
    if content_keys:
        ba, bb = load_blobs(pre, content_keys), load_blobs(post, content_keys)
        for k in sorted(content_keys):
            c = content.setdefault(k[0], collections.Counter())
            try:
                ha, hb = pre.content_hash(k, ba[k]), post.content_hash(k, bb[k])
            except Exception:  # noqa: BLE001
                c["unreadable"] += 1
                continue
            c["same" if ha == hb and ha is not None else ("unreadable" if None in (ha, hb) else "different")] += 1

    alerts: list[tuple[str, str]] = []          # (stable alert id, text)
    notes: list[dict] = []                      # benign classes: PASS with note

    def note(aid: str, cls: str, text: str) -> None:
        notes.append({"id": aid, "class": cls, "text": text})

    # --- per-domain verdicts ---
    base = (baseline or {}).get("domains_raw", {})
    rows = []
    for dom, d in doms.items():
        f = d["status"].get("files", collections.Counter())
        pre_f, post_f = d["pre"]["files"], d["post"]["files"]
        r = role(dom)
        removed = f["removed"]
        # identity domains come back bit-identical from the restore set: only daemon churn is normal there, no
        # 10 % share (HomeDomain would otherwise allow a three-digit number of lost files)
        allowed = alert_min if r == "identity" else max(alert_min, int(alert_frac * pre_f))
        if dom in base:
            allowed = max(allowed, 2 * int(base[dom].get("removed_files", 0)) + alert_min)
        verdict = "ok"
        lbl = domain_label(dom, show_apps)
        base_dom = base.get(dom, {})
        areas_wiped = sorted(x for x, n in d["area_pre"].items() if n >= BUCKET_WIPE_MIN and not d["area_post"][x]
                             and x not in set(base_dom.get("areas_wiped", [])))      # same in the control = normal
        collapsed = sum(d["collapsed"].values())
        collapse_allowed = collapse_max + 2 * int(base_dom.get("collapsed_files", 0))
        extra_alerts: list[tuple[str, str]] = []
        if r in ("outside", "identity"):
            if pre_f >= 5 and post_f == 0:
                verdict = "ALERT:wiped"
            elif removed > allowed:
                verdict = "ALERT:files_lost"
            elif removed or f["recreated"] or f["added"]:
                verdict = "churn"
            if areas_wiped:
                extra_alerts.append((f"area_wiped:{lbl}", f"{lbl}: ALERT:area_wiped ({len(areas_wiped)} area(s) with "
                                     f">= {BUCKET_WIPE_MIN} files emptied: {', '.join(areas_wiped[:6])})"))
            if collapsed > collapse_allowed:
                extra_alerts.append((f"collapse:{lbl}", f"{lbl}: ALERT:collapse ({collapsed} files > 16 KiB shrank "
                                     f"below 50 %, allowed {collapse_allowed}; areas "
                                     f"{dict(d['collapsed'].most_common(4))})"))
            if (verdict == "ALERT:files_lost" and not extra_alerts and POSTER_DOMAIN_RE.search(dom)
                    and post_f >= pre_f and all(d["ext_post"][x] >= n for x, n in d["ext_pre"].items())):
                # canary 2026-10-01: ClockPoster/GalleryCache plists rewritten under new UUIDs (some removed, more
                # added, no extension fewer); incident: fewer 'plist' and 'atx' files = stays ALERT
                verdict = "note:poster_cache_regenerated"
                note(f"domain:{lbl}", "poster_cache_regenerated",
                     f"{lbl}: removed {removed}/{pre_f} files (allowed {allowed}), but no file extension has fewer "
                     f"files ({len(d['ext_pre'])} extensions, {pre_f} -> {post_f} files): poster/gallery caches "
                     f"regenerated")
            if extra_alerts and not verdict.startswith("ALERT"):
                verdict = "ALERT:" + extra_alerts[0][1].split("ALERT:", 1)[1].split(" ", 1)[0]
        else:
            verdict = r
        row = {"domain": lbl, "role": r, "pre_files": pre_f, "post_files": post_f,
               "removed_files": removed, "added_files": f["added"], "recreated_files": f["recreated"],
               "modified_files": f["modified"], "meta_files": f["meta"], "unchanged_files": f["unchanged"],
               "pre_dirs": d["pre"]["dirs"], "post_dirs": d["post"]["dirs"],
               "removed_dirs": d["status"].get("dirs", collections.Counter())["removed"],
               "allowed_removed": allowed if r in ("outside", "identity") else None, "verdict": verdict,
               "areas_wiped": areas_wiped if r in ("outside", "identity") else [],
               "collapsed_files": collapsed,
               "buckets": {s: dict(c.most_common(top)) for s, c in d["buckets"].items()},
               "when": {s: dict(c) for s, c in d["when"].items()}}
        if dom in content:
            row["content"] = dict(content[dom])
        rows.append(row)
        if verdict in ("ALERT:wiped", "ALERT:files_lost"):
            alerts.append((f"domain:{lbl}", f"{lbl}: {verdict} (removed {removed}/{pre_f} files, allowed {allowed})"))
        alerts += extra_alerts
    rows.sort(key=lambda x: (-x["removed_files"], -x["recreated_files"], x["domain"]))

    # --- content structure of reset-prone databases (in memory, row counts only; computed before the sentinels
    # because the shortcuts class needs it, reported after them) ---
    dbr, db_alerts = [], []
    for pid, label, ddom, drel in DB_PROBES:
        if ddom in exempt_domains:
            continue
        ta, tb = db_tables(pre, (ddom, drel)), db_tables(post, (ddom, drel))
        ra = sum(ta.values()) if isinstance(ta, dict) else ta
        rb = sum(tb.values()) if isinstance(tb, dict) else tb
        if ra is None and rb is None:
            continue
        v = "ok"
        if isinstance(ra, int) and ra >= DB_MIN_ROWS:
            if rb is None:
                v = "ALERT:missing"
            elif isinstance(rb, int) and rb < (1 - DB_DROP_FRAC) * ra:
                v = "ALERT:rows_dropped"
            elif not isinstance(rb, int):
                v = "unreadable"
        rec = {"id": pid, "label": label, "pre_rows": ra, "post_rows": rb, "verdict": v}
        kt = DB_KEY_TABLES.get(pid)
        if kt and isinstance(ta, dict) and isinstance(tb, dict):
            rec["key_tables"] = {t: [ta.get(t), tb.get(t)] for t in kt}
            if v == "ALERT:rows_dropped" and all(isinstance(x, int) and isinstance(y, int) and y >= x
                                                 for x, y in rec["key_tables"].values()):
                v = rec["verdict"] = "note:sync_tables_only"
                note(f"db:{pid}", f"{pid}_sync_tables",
                     f"db {pid}: rows {ra} -> {rb}, but {'/'.join(kt)} rows equal or higher ("
                     + ", ".join(f"{t} {x} -> {y}" for t, (x, y) in rec["key_tables"].items())
                     + "): only change-tracking/sync tables were rebuilt")
        dbr.append(rec)
        if v.startswith("ALERT"):
            db_alerts.append((f"db:{pid}", f"db {pid}: {v} (rows {ra} -> {rb}, allowed drop "
                                           f"{int(DB_DROP_FRAC * 100)} %)"))
    db_by_id = {x["id"]: x for x in dbr}

    # --- sentinels ---
    sent = []
    for sid, label, dre, pre_re in SENTINELS:
        dr, pr = re.compile(dre), re.compile(pre_re)
        c = collections.Counter()
        ext_pre, ext_post = collections.Counter(), collections.Counter()
        removed_rels: list[str] = []           # in memory only (shortcuts class), never printed
        cat_pre = cat_post = 0
        for k in keys:
            if not dr.search(k[0]) or not pr.search(k[1]):
                continue
            a, b = pre.entries.get(k), post.entries.get(k)
            e = b or a
            if e.flags != FLAG_FILE:
                continue
            c["pre"] += a is not None
            c["post"] += b is not None
            st = classify(a, b)
            c[st] += 1
            if (sid not in NO_COLLAPSE_SENTINELS and a is not None and b is not None and b.flags == FLAG_FILE
                    and a.size >= SENTINEL_COLLAPSE_MIN and 0 <= b.size < SENTINEL_COLLAPSE_FRAC * a.size):
                c["collapsed"] += 1
            if sid == "wallpapers":
                ext_pre[file_ext(k[1])] += a is not None
                ext_post[file_ext(k[1])] += b is not None
            elif sid == "shortcuts":
                if st == "removed":
                    removed_rels.append(k[1] if k[0] == SHORTCUTS_DB_KEY[0] else "")
                if k[0] == SHORTCUTS_DB_KEY[0] and SHORTCUTS_CATALOGUE_RE.match(k[1]):
                    cat_pre += a is not None
                    cat_post += b is not None
        if not c:
            continue
        in_payload = any(dr.search(x) for x in exempt_domains)
        v = "ok"
        if not in_payload and c["pre"]:
            if c["post"] == 0:
                v = "ALERT:wiped"
            elif c["pre"] >= 3 and c["removed"] / c["pre"] >= 0.25:
                v = "ALERT:files_lost"
            elif c["collapsed"]:
                v = "ALERT:collapsed"   # e.g. the Messages DB 17.8 MB -> 4 KB, keyboard model 3.5 MB -> 4 KB
            elif c["removed"]:
                v = "churn"
            elif c["recreated"]:
                v = "rewritten"     # same files, new inode/Birth: content may have been reset -> see db_rows
        if v == "ALERT:files_lost" and not c["collapsed"]:
            if (sid == "wallpapers" and c["post"] >= c["pre"]
                    and all(ext_post[x] >= n for x, n in ext_pre.items())):
                v = "note:poster_cache_regenerated"
                note(f"sentinel:{sid}", "poster_cache_regenerated",
                     f"sentinel {sid}: {c['removed']}/{c['pre']} files removed, but no file extension has fewer "
                     f"files ({c['pre']} -> {c['post']}): poster/gallery caches regenerated")
            elif sid == "shortcuts":
                sa, sb = pre.entries.get(SHORTCUTS_DB_KEY), post.entries.get(SHORTCUTS_DB_KEY)
                dbv = (db_by_id.get("shortcuts") or {}).get("verdict", "ok")
                if (removed_rels and all(SHORTCUTS_CATALOGUE_RE.match(x) for x in removed_rels)
                        and cat_post >= cat_pre and sa is not None and sb is not None
                        and sa.flags == FLAG_FILE and sb.flags == FLAG_FILE and sb.size >= COLLAPSE_FRAC * sa.size
                        and not dbv.startswith("ALERT")):
                    v = "note:shortcuts_catalogue_regenerated"
                    note(f"sentinel:{sid}", "shortcuts_catalogue_regenerated",
                         f"sentinel {sid}: {c['removed']}/{c['pre']} files removed, all of them the ToolKit tool "
                         f"catalogue ({cat_pre} -> {cat_post} catalogue files); Shortcuts.sqlite present, size "
                         f"{sa.size} -> {sb.size}, rows {(db_by_id.get('shortcuts') or {}).get('pre_rows')} -> "
                         f"{(db_by_id.get('shortcuts') or {}).get('post_rows')}: catalogue regenerated")
        sent.append({"id": sid, "label": label, "pre": c["pre"], "post": c["post"], "removed": c["removed"],
                     "added": c["added"], "recreated": c["recreated"], "modified": c["modified"],
                     "collapsed": c["collapsed"], "verdict": v})
        if v.startswith("ALERT"):
            alerts.append((f"sentinel:{sid}", f"sentinel {sid}: {v} ({c['removed']}/{c['pre']} files removed, "
                                              f"{c['collapsed']} collapsed)"))
    alerts += db_alerts

    # --- keychain item counts (the incident lost genp 5 / keys 4 while KeychainDomain was
    # never in the payload; a restore set cannot bring keychain items back) ---
    kca, kcb = keychain_counts(pre), keychain_counts(post)
    kc = {"pre": kca, "post": kcb, "verdict": "n/a"}
    if isinstance(kca, dict) and "error" not in kca and isinstance(kcb, dict) and "error" not in kcb:
        lost = {k: (kca[k], kcb.get(k, 0)) for k in KEYCHAIN_ALERT_CLASSES if k in kca and kcb.get(k, 0) < kca[k]}
        kc["verdict"] = "ALERT:items_lost" if lost else "ok"
        if lost:
            alerts.append(("keychain_items", "keychain_items: ALERT:items_lost (" + ", ".join(
                f"{k} {x} -> {y}" for k, (x, y) in sorted(lost.items())) + ")"))
    elif kca is not None or kcb is not None:
        kc["verdict"] = "unreadable"

    # --- payload landing ---
    landed = None
    if payload:
        lc = collections.Counter()
        for k, pe in payload.entries.items():
            if pe.flags != FLAG_FILE:
                continue
            b = post.entries.get(k)
            lc["payload_files"] += 1
            if b is None:
                lc["missing_in_post"] += 1
            elif b.size == pe.size:
                lc["present_same_size"] += 1
            else:
                lc["present_other_size"] += 1     # normal for files the app rewrote after launch (DB, WAL, prefs)
        landed = {k: lc[k] for k in ("payload_files", "present_same_size", "present_other_size", "missing_in_post")}

    # --- identity domains: the payload must carry them exactly as in PRE (restore set built from PRE) ---
    identity = {}
    for dom in sorted(identity_domains):
        pk = {k for k in payload.entries if k[0] == dom}
        sk = {k for k in pre.entries if k[0] == dom}
        diff = {"payload_rows": len(pk), "pre_rows": len(sk), "missing_in_payload": len(sk - pk),
                "extra_in_payload": len(pk - sk),
                "different": sum(1 for k in pk & sk if payload.entries[k] != pre.entries[k])}
        identity[dom] = diff
        if diff["missing_in_payload"] or diff["extra_in_payload"] or diff["different"]:
            alerts.append((f"identity:{dom}", f"identity {dom}: payload != PRE (missing {diff['missing_in_payload']}, "
                                              f"extra {diff['extra_in_payload']}, different {diff['different']}) -> "
                                              f"restore set not built from PRE, the gate is not meaningful"))

    pb = purplebuddy(pre, post, show_apps)
    if pb["alert"]:
        keys_txt = [k for k in pb["changed_keys"] if re.search(r"Setup|Restore", k)]
        extra_txt = [k for k in pb["non_rerun_keys"] if k not in keys_txt]
        alerts.append(("purplebuddy", f"purplebuddy: class={pb['class']} setup keys changed (" + ", ".join(keys_txt)
                       + ")" + (f", other keys changed ({', '.join(extra_txt[:12])})" if extra_txt else "")
                       + " -> Setup Assistant ran again"))
    elif pb.get("pass_note"):
        note("purplebuddy", "apple_account_rerun", pb["pass_note"])


    totals = collections.Counter()
    for r in rows:
        if r["role"] in ("outside", "identity"):
            for s in ("removed_files", "added_files", "recreated_files", "modified_files"):
                totals[s] += r[s]
    return {
        "tool": "backup_diff.py", "generated": bp.now_iso(),
        "pre": pre.meta(), "post": post.meta(), "payload": payload.meta() if payload else None,
        "payload_domains": sorted(domain_label(x, show_apps) for x in payload_domains),
        "identity_domains": identity,
        "marks": [{"label": m[0], "time": m[1].isoformat()} for m in sorted(marks, key=lambda m: m[1])],
        "thresholds": {"alert_min_files": alert_min, "alert_frac": alert_frac, "identity_allowed": alert_min,
                       "collapse_max": collapse_max, "area_wipe_min": BUCKET_WIPE_MIN,
                       "db_drop_frac": DB_DROP_FRAC, "baseline": bool(baseline)},
        "outside_totals": dict(totals),
        "alerts": [t for _i, t in alerts], "alert_ids": [i for i, _t in alerts],
        "gate": "FAIL" if alerts else "PASS",
        "notes": notes,
        "sentinels": sent, "db_rows": dbr, "keychain_items": kc, "purplebuddy": pb, "payload_landing": landed,
        "unchanged_domains": sum(1 for r in rows if r["verdict"] == "ok"),
        "domains": [r for r in rows if r["verdict"] != "ok"],     # changed / payload / expected domains only
        # raw per-domain removal counts for --baseline (third-party domains keyed by their hashed label)
        "domains_raw": {(dom if not is_third_party(dom) else domain_label(dom, False)):
                        {"removed_files": doms[dom]["status"].get("files", collections.Counter())["removed"],
                         "areas_wiped": sorted(x for x, n in doms[dom]["area_pre"].items()
                                               if n >= BUCKET_WIPE_MIN and not doms[dom]["area_post"][x]),
                         "collapsed_files": sum(doms[dom]["collapsed"].values())}
                        for dom in doms},
    }


# --------------------------------------------------------------------------- output
def print_summary(rep: dict, top: int) -> None:
    p, q = rep["pre"], rep["post"]
    print(f"PRE : {p['date']}  iOS {p['ios']} ({p['build']})  entries {p['entries']}  domains {p['domains']}")
    print(f"POST: {q['date']}  iOS {q['ios']} ({q['build']})  entries {q['entries']}  domains {q['domains']}")
    if rep["payload"]:
        print(f"PAYLOAD domains: {len(rep['payload_domains'])}  landing: {rep['payload_landing']}")
        for dom, v in (rep.get("identity_domains") or {}).items():
            print(f"IDENTITY {dom} (judged as outside): payload rows {v['payload_rows']} vs PRE {v['pre_rows']}, "
                  f"missing {v['missing_in_payload']} extra {v['extra_in_payload']} different {v['different']}")
    t = rep["outside_totals"]
    print(f"OUTSIDE PAYLOAD totals (files): removed {t.get('removed_files', 0)}  added {t.get('added_files', 0)}  "
          f"recreated {t.get('recreated_files', 0)}  modified {t.get('modified_files', 0)}")
    hdr = f"{'domain':58} {'role':8} {'pre':>6} {'post':>6} {'rm':>6} {'add':>5} {'recr':>5} {'mod':>5}  verdict"
    print("\nDOMAINS (files; sorted by removed):")
    print(hdr)
    shown = [r for r in rep["domains"] if r["verdict"] not in ("ok",)][:top]
    for r in shown:
        print(f"{r['domain'][:58]:58} {r['role'][:8]:8} {r['pre_files']:6} {r['post_files']:6} {r['removed_files']:6} "
              f"{r['added_files']:5} {r['recreated_files']:5} {r['modified_files']:5}  {r['verdict']}")
    rest = len([r for r in rep["domains"] if r["verdict"] != "ok"]) - len(shown)
    if rest > 0:
        print(f"... {rest} more changed domains in the report")
    print("\nSENTINELS:")
    for s in rep["sentinels"]:
        print(f"  {s['id']:22} pre {s['pre']:6} post {s['post']:6} removed {s['removed']:6} "
              f"recreated {s['recreated']:5}  {s['verdict']}")
    if rep.get("db_rows"):
        print("\nDATABASES (row totals):")
        for x in rep["db_rows"]:
            kt = x.get("key_tables")
            kts = ("  key tables " + ", ".join(f"{t} {a}->{b}" for t, (a, b) in kt.items())) if kt else ""
            print(f"  {x['id']:22} pre {x['pre_rows']!s:>8} post {x['post_rows']!s:>8}  {x['verdict']}{kts}")
    kc = rep.get("keychain_items") or {}
    print("\nKEYCHAIN items:", kc.get("pre"), "->", kc.get("post"), kc.get("verdict"))
    pb = rep["purplebuddy"]
    print("\nPURPLEBUDDY:", f"class {pb.get('class')};", "changed keys " + ", ".join(pb["changed_keys"]) if pb["changed_keys"] else "unchanged",
          *pb["notes"])
    if pb.get("raw_changed_keys"):
        print(f"  raw changed keys: {', '.join(sorted(set(pb['raw_changed_keys'])))}; SetupDone true before and "
              f"after: {pb.get('setup_done_before_and_after')}; Locale region-only: {pb.get('locale_region_only')}")
    for f in pb["files"]:
        for side in ("pre", "post"):
            v = f.get(side)
            if isinstance(v, dict):
                sel = {k: v[k] for k in v if re.search(r"State|Restore|Setup", k)}
                print(f"  {side:4} {f['domain']}: {json.dumps(sel, default=str)[:300]}")
    extra = []
    if rep.get("notes"):
        extra.append(f"{len(rep['notes'])} note(s)")
    print(f"\nGATE: {rep['gate']}" + (f" ({', '.join(extra)})" if extra else ""))
    for a in rep["alerts"]:
        print("  ALERT", a)
    for n in rep.get("notes") or []:
        print(f"  NOTE [{n['class']}] {n['text']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pre")
    ap.add_argument("post")
    ap.add_argument("--password-stdin", action="store_true", help="read the backup password as ONE stdin line")
    ap.add_argument("--payload")
    ap.add_argument("--expect", action="append", default=None)
    ap.add_argument("--mark", action="append", default=[])
    ap.add_argument("--content")
    ap.add_argument("--baseline")
    ap.add_argument("--depth", type=int, default=2, help="bucket depth; capped at 2 (no deep paths)")
    ap.add_argument("--report")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--alert-min", type=int, default=20)
    ap.add_argument("--alert-frac", type=float, default=0.10)
    ap.add_argument("--collapse-max", type=int, default=3)
    ap.add_argument("--tmp-dir", help="temp dir for library temp files (default: the system temp dir)")
    a = ap.parse_args(argv)
    try:
        if a.tmp_dir:
            bp.use_tmp(Path(a.tmp_dir).expanduser())
        pw = bp.read_password_line() if a.password_stdin else None
        pre = Backup(device_dir(a.pre), pw, "pre")
        post = Backup(device_dir(a.post), pw, "post")
        if pre.dev.name != post.dev.name:
            print(f"WARNING: different UDID directories ({pre.dev.name} vs {post.dev.name})", file=sys.stderr)
        payload = Backup(device_dir(a.payload), pw, "payload") if a.payload else None
        del pw
        marks = [("pre_backup", pre.date), ("post_backup", post.date)]
        marks = [m for m in marks if m[1]] + [parse_mark(m) for m in a.mark]
        baseline = json.loads(Path(a.baseline).read_text()) if a.baseline else None
        rep = compare(pre, post, payload=payload, expect=a.expect if a.expect is not None else list(DEFAULT_EXPECT),
                      marks=marks, depth=a.depth, content_re=a.content, baseline=baseline, alert_min=a.alert_min,
                      alert_frac=a.alert_frac, top=a.top, collapse_max=a.collapse_max)
    except (DiffError, bp.PipelineError, OSError, sqlite3.DatabaseError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if a.report:
        bp.write_json(Path(a.report), rep)
    print_summary(rep, a.top)
    return 1 if (a.gate and rep["alerts"]) else 0


if __name__ == "__main__":
    sys.exit(main())
