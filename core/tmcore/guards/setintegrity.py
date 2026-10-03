# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Guard 'set_integrity' (DESIGN §6.1, §7): the restore set the device will read is exactly what `prepare` built and
verified from THIS session's PRE backup. Ported from the proof of concept (tools/restore.py guards layout,
do_not_restore, marker, source, plists, structure, verify, unchanged) without any override.

    snap = check(ctx, set_root, pre_dev, password)        # SetSnapshot or GuardFailure(sub)
    unchanged(snap)                                       # sha256 of Manifest.db / marker / report right before send

Sub-codes (E_GUARD_SET_INTEGRITY data.sub): layout, marker, source, plists, structure, verify, unchanged.
Restore set = the 4 Threema domains (store injected, zero-length -wal/-shm) + the COMPLETE HomeDomain,
CameraRollDomain and KeyboardDomain, rows and blobs identical to the PRE backup; nothing else (never_partial).
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import plistlib
import sqlite3
from pathlib import Path

from tmcore.guards import GuardResult
from tmcore.steps.iphone import as_utc, bp_run, lib, parse_ts, quiet

FULL_DOMAINS = ("HomeDomain", "CameraRollDomain", "KeyboardDomain")
MARKER = ".RESTORESET_OK"
DO_NOT_RESTORE = "DO_NOT_RESTORE"
REPORT_SUFFIX = ".restoreset.json"
REPORT_DATE_TOLERANCE_S = 120
FLAG_FILE = 1


class GuardFailure(Exception):
    def __init__(self, sub: str):
        super().__init__(sub)
        self.sub = sub


@dataclasses.dataclass
class SetSnapshot:
    root: Path
    dev: Path
    udid: str
    report: dict
    lockdown: dict
    payload_bytes: int
    counts: dict
    hashes: dict
    noop: bool

    def result(self) -> GuardResult:
        return GuardResult("set_integrity", "pass", data={k: v for k, v in self.counts.items()})


def fail(sub: str) -> GuardResult:
    return GuardResult("set_integrity", "fail", "E_GUARD_SET_INTEGRITY", data={"sub": sub})


def _sha(p: Path) -> str:
    return lib("backup_pipeline").sha256_file(p)


def _plist(p: Path) -> dict:
    return plistlib.loads(p.read_bytes())


def hashes(root: Path, dev: Path, udid: str) -> dict:
    return {"manifest_db": _sha(dev / "Manifest.db"), "marker": _sha(dev / MARKER),
            "report": _sha(root / f"{udid}{REPORT_SUFFIX}")}


def _layout(root: Path, udid: str) -> Path:
    if not root.is_dir():
        raise GuardFailure("layout")
    devs = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "Manifest.plist").exists())
    if devs != [udid]:
        raise GuardFailure("layout")
    dev = root / udid
    for n in ("Info.plist", "Manifest.plist", "Status.plist", "Manifest.db"):
        p = dev / n
        if not p.is_file() or p.stat().st_size == 0:
            raise GuardFailure("layout")
    if (dev / "Snapshot").exists():
        raise GuardFailure("layout")
    # never a partial / diagnostic payload (there is no trim in the product; refuse its markers anyway)
    if any(p.exists() for p in (dev / DO_NOT_RESTORE, root / DO_NOT_RESTORE)) or any(root.glob("*.trim-report.json")):
        raise GuardFailure("layout")
    return dev


def _marker(root: Path, dev: Path, udid: str) -> dict:
    rep_p, mark_p = root / f"{udid}{REPORT_SUFFIX}", dev / MARKER
    if not rep_p.is_file() or not mark_p.is_file():
        raise GuardFailure("marker")
    words = mark_p.read_text(errors="replace").split()
    if not words or words[0].lower() != _sha(rep_p):
        raise GuardFailure("marker")
    try:
        rep = json.loads(rep_p.read_text())
    except (ValueError, OSError):
        raise GuardFailure("marker") from None
    if not isinstance(rep, dict) or rep.get("kind") != "restoreset" or rep.get("result") != "OK":
        raise GuardFailure("marker")
    need = ("source", "source_manifest_sha256", "noop", "domains", "home_rows", "cameraroll_rows", "keyboard_rows",
            "threema_rows", "payload_bytes", "source_backup_date", "lockdown")
    if any(k not in rep for k in need):
        raise GuardFailure("marker")
    if (rep.get("lockdown") or {}).get("UniqueDeviceID") != udid:
        raise GuardFailure("marker")
    if not isinstance(rep["noop"], bool) or not isinstance(rep["domains"], dict):
        raise GuardFailure("marker")
    return rep


def _source(rep: dict, pre_dev: Path, dev: Path) -> None:
    src = Path(str(rep["source"])).expanduser()
    try:
        if src.resolve() != pre_dev.resolve() or src.resolve() == dev.resolve():
            raise GuardFailure("source")
    except OSError:
        raise GuardFailure("source") from None
    if not (pre_dev / "Manifest.db").is_file():
        raise GuardFailure("source")
    if _sha(pre_dev / "Manifest.db") != str(rep["source_manifest_sha256"]).lower():
        raise GuardFailure("source")


def _plists(rep: dict, dev: Path, src: Path, udid: str) -> tuple[dict, _dt.datetime]:
    pm, sm = _plist(dev / "Manifest.plist"), _plist(src / "Manifest.plist")
    ps, ss = _plist(dev / "Status.plist"), _plist(src / "Status.plist")
    if not pm.get("IsEncrypted"):
        raise GuardFailure("plists")
    for k in ("Lockdown", "BackupKeyBag", "ManifestKey"):
        if pm.get(k) != sm.get(k):
            raise GuardFailure("plists")
    lk = pm.get("Lockdown") or {}
    if lk.get("UniqueDeviceID") != udid or not lk.get("BuildVersion") or not lk.get("ProductVersion"):
        raise GuardFailure("plists")
    rl = rep.get("lockdown") or {}
    if any(rl.get(k) != lk.get(k) for k in ("UniqueDeviceID", "BuildVersion", "ProductVersion")):
        raise GuardFailure("plists")
    if ps.get("SnapshotState") != "finished":
        raise GuardFailure("plists")
    sd = ss.get("Date")
    if not isinstance(sd, _dt.datetime) or ps.get("Date") != sd:
        raise GuardFailure("plists")
    info = _plist(dev / "Info.plist")
    if str(info.get("Target Identifier") or udid).lower() != udid.lower():
        raise GuardFailure("plists")
    rd, sdu = parse_ts(rep.get("source_backup_date")), as_utc(sd)
    if rd is None or sdu is None or abs((rd - sdu).total_seconds()) > REPORT_DATE_TOLERANCE_S:
        raise GuardFailure("plists")
    return {k: lk.get(k) for k in ("UniqueDeviceID", "BuildVersion", "ProductVersion")}, sdu


def _manifest_rows(dev: Path, manifest_key: bytes) -> list[tuple[str, str, str, int, bytes]]:
    bp, rw = lib("backup_pipeline"), lib("iosbackup_rw")
    pt = rw.decrypt_manifest_db((dev / "Manifest.db").read_bytes(), manifest_key)
    pt, _ = bp.strip_manifest_padding(pt)
    buf = bytearray(pt)
    if buf[18] == 2 or buf[19] == 2:
        buf[18] = buf[19] = 1
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(bytes(buf))
        return [(fid, dom, rel or "", int(fl or 0), bytes(b or b"")) for fid, dom, rel, fl, b in
                conn.execute("SELECT fileID, domain, relativePath, flags, file FROM Files")]
    finally:
        conn.close()


def _blob_size(dev: Path, fid: str) -> int | None:
    try:
        return (dev / fid[:2] / fid).stat().st_size
    except OSError:
        return None


def _structure(rep: dict, dev: Path, src: Path, password: str) -> tuple[int, dict]:
    bp, rw = lib("backup_pipeline"), lib("iosbackup_rw")
    try:
        bk = rw.EncryptedBackup(src, password)
    except ValueError:
        raise GuardFailure("structure") from None
    if _plist(dev / "Manifest.plist").get("ManifestKey") != bk._manifest_plist.get("ManifestKey"):
        raise GuardFailure("structure")
    try:
        prow = _manifest_rows(dev, bk._manifest_key)
        srow = _manifest_rows(src, bk._manifest_key)
    except (sqlite3.DatabaseError, ValueError, OSError):
        raise GuardFailure("structure") from None
    counts: dict[str, int] = {}
    for _f, dom, _r, _fl, _b in prow:
        counts[dom] = counts.get(dom, 0) + 1
    if any(not (d in FULL_DOMAINS or bp.is_threema_domain(d)) for d in counts):
        raise GuardFailure("structure")                     # anything outside Threema + Home/CameraRoll/Keyboard
    src_full = {r[0]: r for r in srow if r[1] in FULL_DOMAINS}
    pay_full = {r[0]: r for r in prow if r[1] in FULL_DOMAINS}
    if not any(r[1] == "HomeDomain" for r in srow):
        raise GuardFailure("structure")
    if src_full.keys() != pay_full.keys() or any(src_full[k] != pay_full[k] for k in src_full):
        raise GuardFailure("structure")                     # never partial: identical to the PRE backup
    for fid, r in pay_full.items():
        if r[3] == FLAG_FILE and _blob_size(dev, fid) != _blob_size(src, fid):
            raise GuardFailure("structure")
    total = 0
    for fid, _dom, _rel, fl, _b in prow:
        if fl == FLAG_FILE:
            s = _blob_size(dev, fid)
            if s is None:
                raise GuardFailure("structure")             # the device would abort mid-restore
            total += s
    grp = {rel: (fl, b) for _f, dom, rel, fl, b in prow if dom == bp.GROUP_DOMAIN}
    if bp.DB not in grp:
        raise GuardFailure("structure")
    for n in (bp.WAL, bp.SHM):
        if n not in grp or grp[n][0] != FLAG_FILE:
            raise GuardFailure("structure")                 # a live device WAL would be replayed onto the new DB
    home, cam, kbd = (counts.get(d, 0) for d in FULL_DOMAINS)
    threema = sum(n for d, n in counts.items() if bp.is_threema_domain(d))
    src_c = {d: sum(1 for r in srow if r[1] == d) for d in FULL_DOMAINS}
    if home == 0 or cam != src_c["CameraRollDomain"] or kbd != src_c["KeyboardDomain"]:
        raise GuardFailure("structure")
    if (rep.get("home_rows"), rep.get("cameraroll_rows"), rep.get("keyboard_rows"), rep.get("threema_rows")) != \
            (home, cam, kbd, threema):
        raise GuardFailure("structure")
    if {str(k): int(v) for k, v in (rep.get("domains") or {}).items()} != counts:
        raise GuardFailure("structure")
    payload = max(total, int(rep.get("payload_bytes") or 0))
    return payload, {"home_rows": home, "cameraroll_rows": cam, "keyboard_rows": kbd, "threema_rows": threema,
                     "payload_bytes": payload}


def check(ctx, set_root: Path, pre_dev: Path, password: str, *, run_verify: bool = True) -> SetSnapshot | GuardResult:
    """All set checks in fixed order; returns the snapshot for `unchanged`, or the failed GuardResult."""
    udid = pre_dev.name
    try:
        dev = _layout(set_root, udid)
        rep = _marker(set_root, dev, udid)
        _source(rep, pre_dev, dev)
        lockdown, _date = _plists(rep, dev, pre_dev, udid)
        with quiet():
            payload, counts = _structure(rep, dev, pre_dev, password)
        h = hashes(set_root, dev, udid)
        if run_verify:
            rc, _summary = bp_run(ctx, "verify", password, backup_udid_dir=str(dev), source=str(pre_dev),
                                  restoreset=True, against=None,
                                  report=str(ctx.session.path("work/tmp") / "restoreset-verify.json"))
            if rc != 0:
                raise GuardFailure("verify")
    except GuardFailure as e:
        return fail(e.sub)
    except (OSError, ValueError, plistlib.InvalidFileException, KeyError):
        return fail("layout")
    return SetSnapshot(set_root, dev, udid, rep, lockdown, payload, counts, h, bool(rep.get("noop")))


def unchanged(snap: SetSnapshot) -> GuardResult:
    """m9: the payload the device will read is still the one every guard checked (minutes may pass)."""
    try:
        if hashes(snap.root, snap.dev, snap.udid) == snap.hashes:
            return GuardResult("set_integrity", "pass", data={"sub": "unchanged"})
    except OSError:
        pass
    return fail("unchanged")
