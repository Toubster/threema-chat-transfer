# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for `backup_pipeline restoreset` (THE restore payload: Threema + complete HomeDomain + CameraRollDomain +
KeyboardDomain) and `verify --restoreset --source` / `verify --source`, on SYNTHETIC encrypted backups
(fixtures/gen_ios_backup.py with the stores of tests/support). No device, no real data. Ported from the proof of
concept: the trim/inject based cases now build their partial or full-clone inputs directly (the product has neither
command, DESIGN §4.3); passwords go to the CLI on stdin.
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import secrets
import shutil
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytest.importorskip("iphone_backup_decrypt")
pytest.importorskip("pyiosbackup")

from tests import support  # noqa: E402
from tmcore.lib import backup_pipeline as bp  # noqa: E402
from tmcore.lib import iosbackup_rw as rw  # noqa: E402

bf = support.ios_fixture()
CORE = support.CORE
PLUGIN = "AppDomainPlugin-ch.threema.iapp.ThreemaNotificationExtension"
THREEMA = {bp.APP_DOMAIN, bp.GROUP_DOMAIN, PLUGIN}
SYSTEM = {"HomeDomain", "CameraRollDomain", "KeyboardDomain"}
EXPECTED = THREEMA | SYSTEM
# relative paths of the fixture's non-Threema rows that must never show up in any output (privacy: counts only)
NON_THREEMA_NAMES = ("TCC.db", "IMG_0001", "Accounts3", "AddressBook", "healthdb", "keychain-backup", "att0.bin")


# --------------------------------------------------------------------------- helpers
def cli(*args, timeout=110):
    """The maintainer CLI; password = one line on stdin (the module-level ENV holds it), temp files in ENV tmp."""
    env = support.tmcore_env(TMPDIR=str(ENV["tmp"]))
    r = subprocess.run([sys.executable, "-m", "tmcore.lib.backup_pipeline", *[str(a) for a in args]],
                       input=ENV["pw"] + "\n", capture_output=True, text=True, timeout=timeout, cwd=CORE, env=env)
    return r.returncode, r.stdout, r.stderr


ENV: dict = {}


def rows_of(dev: Path, pw: str) -> dict:
    ms = bp.ManifestSession(rw.EncryptedBackup(dev, pw))
    try:
        return {(r.domain, r.rel): r for r in ms.rows()}
    finally:
        ms.close()


def blob_bytes(dev: Path, fid: str) -> bytes:
    return (dev / fid[:2] / fid).read_bytes()


def tree_hash(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*"))
            if p.is_file()}


def add_external(store: Path) -> str:
    """Mimic the importer: new FileData row + its external blob."""
    u = str(uuid.uuid4()).upper()
    ext = store / bp.EXT
    ext.mkdir(parents=True, exist_ok=True)
    (ext / u).write_bytes(os.urandom(70001))
    c = sqlite3.connect(store / bp.DB)
    e, m = c.execute("SELECT Z_ENT, Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME='FileData'").fetchone()
    c.execute("INSERT INTO ZFILEDATA(Z_PK, Z_ENT, Z_OPT, ZDATA) VALUES (?,?,1,?)",
              (m + 1, e, b"\x02" + u.encode() + b"\x00"))
    c.execute("UPDATE Z_PRIMARYKEY SET Z_MAX=? WHERE Z_NAME='FileData'", (m + 1,))
    c.execute("UPDATE ZCONVERSATION SET ZLASTUPDATE = coalesce(ZLASTUPDATE, 0) + 1")
    c.commit()
    c.close()
    return u


def copy_set(env, which: str, name: str) -> Path:
    """Copy a built restore set (UDID dir incl. marker + its builder report) to a scratch root."""
    root = env["dir"] / f"tamper-{name}"
    root.mkdir()
    dst = root / env["udid"]
    shutil.copytree(env[which], dst)
    shutil.copy2(env[which].parent / f"{env['udid']}.restoreset.json", root / f"{env['udid']}.restoreset.json")
    return dst


def assert_private(text: str, env) -> None:
    assert env["pw"] not in text
    for n in NON_THREEMA_NAMES:
        assert n not in text, n


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("restoreset")
    os.chmod(d, 0o700)
    pw = "t-" + secrets.token_hex(12)
    (d / "tmp").mkdir(mode=0o700)
    ENV.update(pw=pw, tmp=d / "tmp")
    fx = bf.fabricate_realistic_backup(d / "orig", pw)
    dev, udid = fx["device_dir"], fx["udid"]
    e = {"dir": d, "pw": pw, "fx": fx, "dev": dev, "udid": udid, "outputs": [], "tmp": d / "tmp"}
    e["before"] = tree_hash(dev)
    rc, out, err = cli("extract", dev, d / "extract")
    assert rc == 0, out + err
    store = d / "store_out"
    shutil.copytree(d / "extract" / "store", store)
    e["new_ext"] = add_external(store)
    e["store"] = store
    for key, extra in (("final", ("--store-out", store)), ("noop", ("--noop",))):
        e[f"build_{key}"] = cli("restoreset", dev, d / key, *extra)
        e[key] = d / key / udid
    e["v_final"] = cli("verify", e["final"], "--source", dev, "--restoreset", "--against", store)
    e["v_noop"] = cli("verify", e["noop"], "--source", dev, "--restoreset")
    e["src_rows"] = rows_of(dev, pw)
    yield e


def partial_payload(env, name: str) -> Path:
    """What the proof of concept's `trim` produced (the product cannot build it any more): only the Threema
    domains, marked DO_NOT_RESTORE, no builder report next to it."""
    t = env["dir"] / name / env["udid"]
    shutil.copytree(env["noop"], t)
    bp.make_writable(t)
    (t / bp.RESTORESET_MARKER).unlink()
    ms = bp.ManifestSession(rw.EncryptedBackup(t, env["pw"]))
    try:
        gone = [r for r in ms.rows() if not bp.is_threema_domain(r.domain)]
        ms.conn.executemany("DELETE FROM Files WHERE fileID=?", [(r.file_id,) for r in gone])
        ms.commit_and_encrypt()
    finally:
        ms.close()
    for r in gone:
        if r.flags == 1:
            (t / r.file_id[:2] / r.file_id).unlink(missing_ok=True)
    bp.write_do_not_restore(t, "partial payload (test)")
    return t


# --------------------------------------------------------------------------- build
@pytest.mark.parametrize("which", ["final", "noop"])
def test_build_ok_and_report_contract(env, which):
    rc, out, err = env[f"build_{which}"]
    assert rc == 0, out + err
    assert_private(out + err, env)
    rp = env[which].parent / f"{env['udid']}.restoreset.json"
    rep = json.loads(rp.read_text())
    src = env["src_rows"]
    assert rep["kind"] == "restoreset" and rep["result"] == "OK" and rep["errors"] == []
    assert rep["noop"] is (which == "noop")
    assert rep["source"] == str(env["dev"])
    assert rep["source_manifest_sha256"] == hashlib.sha256((env["dev"] / "Manifest.db").read_bytes()).hexdigest()
    assert set(rep["domains"]) == EXPECTED
    assert rep["home_rows"] == sum(1 for dom, _ in src if dom == "HomeDomain") > 20
    assert rep["cameraroll_rows"] == sum(1 for dom, _ in src if dom == "CameraRollDomain") > 5
    assert rep["keyboard_rows"] == sum(1 for dom, _ in src if dom == "KeyboardDomain") == 5     # 3 dirs + 2 files
    assert rep["source_counts"]["KeyboardDomain"] == 5
    n_threema = sum(1 for dom, _ in src if dom in THREEMA)
    assert rep["threema_rows"] == (n_threema + 1 if which == "final" else n_threema)   # +1 = new external file
    assert rep["source_backup_date"] == "2026-09-28T06:00:00+00:00"
    assert rep["lockdown"] == {"UniqueDeviceID": env["udid"], "BuildVersion": "24A5260a", "ProductVersion": "27.0"}
    marker = env[which] / bp.RESTORESET_MARKER
    assert marker.read_text() == hashlib.sha256(rp.read_bytes()).hexdigest()
    on_disk = sum(p.stat().st_size for p in env[which].rglob("*") if p.is_file())
    assert rep["payload_bytes"] == on_disk - marker.stat().st_size
    assert rep["source_unchanged"] is True
    assert not (env[which] / bp.DO_NOT_RESTORE).exists()


def test_restoreset_contains_exactly_threema_home_cameraroll(env):
    for which in ("final", "noop"):
        rows = rows_of(env[which], env["pw"])
        assert {dom for dom, _ in rows} == EXPECTED, which
        for decoy in ("KeychainDomain", "MediaDomain", "HealthDomain", "SystemPreferencesDomain", "RootDomain",
                      "AppDomain-com.example.other", "AppDomainGroup-group.com.example.other",
                      "AppDomainGroup-group.ch.threema.work"):
            assert decoy in {dom for dom, _ in env["src_rows"]}       # the fixture really has it ...
            assert decoy not in {dom for dom, _ in rows}               # ... and the restore set does not
        blobs = {p.name for p in env[which].glob("??/*")}
        assert blobs == {r.file_id for r in rows.values() if r.flags == 1}, which   # no stray / missing blob


@pytest.mark.parametrize("which", ["final", "noop"])
def test_home_and_cameraroll_bit_identical(env, which):
    rows = rows_of(env[which], env["pw"])
    src = {k: r for k, r in env["src_rows"].items() if k[0] in SYSTEM}
    assert {k for k in rows if k[0] in SYSTEM} == set(src)
    digests = flags = 0
    for k, s in src.items():
        r = rows[k]
        assert (r.file_id, r.flags, r.blob) == (s.file_id, s.flags, s.blob), k
        if s.flags == 1:
            assert blob_bytes(env[which], r.file_id) == blob_bytes(env["dev"], s.file_id), k
            digests += "Digest" in s.mb.root
            flags += 1
    assert digests >= 10 and flags >= 15                               # Digest rows survive untouched
    zero = rows[("HomeDomain", "Library/Preferences/com.apple.zerolength.plist")]
    assert (env[which] / zero.file_id[:2] / zero.file_id).stat().st_size == 0     # 0-byte stored blob kept


def test_threema_noop_identical_and_final_injected(env):
    src = {k: r for k, r in env["src_rows"].items() if k[0] in THREEMA}
    noop = rows_of(env["noop"], env["pw"])
    assert {k for k in noop if k[0] in THREEMA} == set(src)
    for k, s in src.items():
        assert (noop[k].file_id, noop[k].flags, noop[k].blob) == (s.file_id, s.flags, s.blob), k
        if s.flags == 1:
            assert blob_bytes(env["noop"], s.file_id) == blob_bytes(env["dev"], s.file_id), k
    final = rows_of(env["final"], env["pw"])
    assert final[(bp.GROUP_DOMAIN, bp.DB)].blob != src[(bp.GROUP_DOMAIN, bp.DB)].blob
    assert final[(bp.GROUP_DOMAIN, bp.DB)].mb.size == (env["store"] / bp.DB).stat().st_size
    assert (bp.GROUP_DOMAIN, f"{bp.EXT}/{env['new_ext']}") in final
    for n in (bp.WAL, bp.SHM):
        assert final[(bp.GROUP_DOMAIN, n)].mb.size == 0
    # every non-managed Threema row (app sandbox, plugin, prefs) stays identical in the final set
    for k, s in src.items():
        if not (k[0] == bp.GROUP_DOMAIN and bp.is_managed_group_path(k[1])):
            assert final[k].blob == s.blob, k


def test_plists_consistent(env):
    srcm = plistlib.loads((env["dev"] / "Manifest.plist").read_bytes())
    srci = plistlib.loads((env["dev"] / "Info.plist").read_bytes())
    for which in ("final", "noop"):
        d = env[which]
        assert (d / "Status.plist").read_bytes() == (env["dev"] / "Status.plist").read_bytes()
        m = plistlib.loads((d / "Manifest.plist").read_bytes())
        i = plistlib.loads((d / "Info.plist").read_bytes())
        assert set(m["Applications"]) == {"ch.threema.iapp"}
        assert m["Applications"]["ch.threema.iapp"] == srcm["Applications"]["ch.threema.iapp"]
        assert {k: v for k, v in m.items() if k != "Applications"} == \
               {k: v for k, v in srcm.items() if k != "Applications"}     # Lockdown, keybag, ManifestKey ...
        assert "Applications" not in i
        assert i == {k: v for k, v in srci.items() if k != "Applications"}
        ms = bp.ManifestSession(rw.EncryptedBackup(d, env["pw"]))
        try:
            assert ms.padded is True                                     # device format (PKCS7) preserved
        finally:
            ms.close()


def test_noop_matches_pymobiledevice3_prune(env):
    """The restore set's Manifest.db rows == pymobiledevice3's own prune with the restore-set domains."""
    pytest.importorskip("pymobiledevice3")
    from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service as MB2
    pm = env["dir"] / "pmd3" / env["udid"]
    shutil.copytree(env["dev"], pm)
    bp.make_writable(pm)
    MB2.prune_backup_manifest(pm, MB2.regex_filter_callback(list(bp.THREEMA_REGEXES)
                                                            + [r"^HomeDomain/", r"^CameraRollDomain/",
                                                               r"^KeyboardDomain/"]),
                              password=env["pw"])
    ours = rows_of(env["noop"], env["pw"])
    theirs = rows_of(pm, env["pw"])
    assert {k: (r.file_id, r.flags, r.blob) for k, r in ours.items()} == \
           {k: (r.file_id, r.flags, r.blob) for k, r in theirs.items()}


def test_source_untouched(env):
    assert tree_hash(env["dev"]) == env["before"]


# --------------------------------------------------------------------------- verify
@pytest.mark.parametrize("which", ["final", "noop"])
def test_verify_restoreset_passes(env, which):
    rc, out, err = env[f"v_{which}"]
    assert rc == 0, out + err
    assert_private(out + err, env)
    rep = json.loads(out)
    assert rep["result"] == "PASS" and rep["errors"] == []
    sc = rep["source_comparison"]
    assert sc["scope"] == "restoreset" and sc["noop"] is (which == "noop")
    assert set(sc["domains"]) == EXPECTED
    assert sc["home_rows"] == sc["source_rows"]["HomeDomain"]
    assert sc["cameraroll_rows"] == sc["source_rows"]["CameraRollDomain"]
    assert sc["keyboard_rows"] == sc["source_rows"]["KeyboardDomain"] == 5
    if which == "noop":
        assert "changed_managed_rows" not in sc["stats"] and "added_managed_rows" not in sc["stats"]
    else:
        assert sc["stats"]["changed_managed_rows"] == 3 and sc["stats"]["added_managed_rows"] == 1
        assert rep["against_store"]["db"] is True
    assert rep["restoreset_report"]["marker"] is True
    vr = json.loads((env[which].parent / f"{env['udid']}.restoreset-verify.json").read_text())
    assert vr["result"] == "PASS" and vr["restoreset"] is True


def _tamper(env, case: str) -> Path:
    t = copy_set(env, "final", case)
    pw = env["pw"]
    rows = rows_of(t, pw)
    if case == "flip_home_byte":
        r = rows[("HomeDomain", "Library/TCC/TCC.db")]
        p = t / r.file_id[:2] / r.file_id
        b = bytearray(p.read_bytes())
        b[len(b) // 3] ^= 0x01
        p.write_bytes(bytes(b))
    elif case == "missing_cameraroll_row":
        r = rows[("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0001.HEIC")]
        ms = bp.ManifestSession(rw.EncryptedBackup(t, pw))
        ms.conn.execute("DELETE FROM Files WHERE fileID=?", (r.file_id,))
        ms.commit_and_encrypt()
        ms.close()
        (t / r.file_id[:2] / r.file_id).unlink()
    elif case == "missing_keyboard_row":
        r = rows[("KeyboardDomain", "Library/Keyboard/emoji_adaptation.db")]
        ms = bp.ManifestSession(rw.EncryptedBackup(t, pw))
        ms.conn.execute("DELETE FROM Files WHERE fileID=?", (r.file_id,))
        ms.commit_and_encrypt()
        ms.close()
        (t / r.file_id[:2] / r.file_id).unlink()
    elif case == "flip_keyboard_byte":
        r = rows[("KeyboardDomain", "Library/Keyboard/emoji_adaptation.db")]
        p = t / r.file_id[:2] / r.file_id
        b = bytearray(p.read_bytes())
        b[len(b) // 2] ^= 0x01
        p.write_bytes(bytes(b))
    elif case == "extra_keychain_row":
        s = env["src_rows"][("KeychainDomain", "keychain-backup.plist")]
        ms = bp.ManifestSession(rw.EncryptedBackup(t, pw))
        ms.insert(s.file_id, s.domain, s.rel, s.flags, s.blob)
        ms.commit_and_encrypt()
        ms.close()
        (t / s.file_id[:2]).mkdir(exist_ok=True)
        shutil.copy2(env["dev"] / s.file_id[:2] / s.file_id, t / s.file_id[:2] / s.file_id)
    elif case == "changed_home_mbfile":
        r = rows[("HomeDomain", "Library/Preferences/.GlobalPreferences.plist")]
        mb = r.mb
        mb.set_fields(LastModified=mb.mtime + 1)
        ms = bp.ManifestSession(rw.EncryptedBackup(t, pw))
        ms.update_blob(r.file_id, mb.dumps())
        ms.commit_and_encrypt()
        ms.close()
    elif case == "stray_blob":
        (t / "ff").mkdir(exist_ok=True)
        (t / "ff" / ("ff" * 20)).write_bytes(os.urandom(32))
    elif case == "report_edited":
        rp = t.parent / f"{env['udid']}.restoreset.json"
        rep = json.loads(rp.read_text())
        rep["payload_bytes"] += 1
        rp.write_text(json.dumps(rep))
    elif case == "marker_missing":
        (t / bp.RESTORESET_MARKER).unlink()
    elif case == "do_not_restore":
        (t / bp.DO_NOT_RESTORE).write_text("x\n")
    elif case == "info_plist_apps":
        shutil.copy2(env["dev"] / "Info.plist", t / "Info.plist")
    else:
        raise AssertionError(case)
    return t


@pytest.mark.parametrize("case, needle", [
    ("flip_home_byte", "stored blob(s) differ"),
    ("missing_cameraroll_row", "row(s) of the source missing"),
    ("missing_keyboard_row", "row(s) of the source missing"),
    ("flip_keyboard_byte", "stored blob(s) differ"),
    ("extra_keychain_row", "outside the restore set"),
    ("changed_home_mbfile", "row(s) differ from the source"),
    ("stray_blob", "not referenced"),
    ("report_edited", "does not match the report sha256"),
    ("marker_missing", "marker missing"),
    ("do_not_restore", "DO_NOT_RESTORE"),
    ("info_plist_apps", "Info.plist still has an Applications key"),
])
def test_verify_restoreset_detects(env, case, needle):
    t = _tamper(env, case)
    rc, out, err = cli("verify", t, "--source", env["dev"], "--restoreset",
                       "--against", env["store"])
    assert rc == 1, (case, out, err)
    rep = json.loads(out)
    assert rep["result"] == "FAIL"
    assert any(needle in e for e in rep["errors"]), (case, rep["errors"])
    assert_private(out + err, env)
    vr = json.loads((t.parent / f"{env['udid']}.restoreset-verify.json").read_text())
    assert vr["result"] == "FAIL"


def test_verify_restoreset_wrong_source(env):
    other = bf.fabricate_realistic_backup(env["dir"] / "other", env["pw"], udid=env["udid"])["device_dir"]
    rc, out, err = cli("verify", env["noop"], "--source", other, "--restoreset")
    assert rc == 1, out + err
    errs = json.loads(out)["errors"]
    assert any("not the backup this restore set was built from" in e for e in errs), errs
    assert any("differ from the source" in e for e in errs), errs


def test_verify_restoreset_needs_source(env):
    rc, out, err = cli("verify", env["noop"], "--restoreset")
    assert rc == 2 and "--source" in err


def test_verify_restoreset_rejects_partial_payload(env):
    t = partial_payload(env, "partial")
    rc, out, err = cli("verify", t, "--source", env["dev"], "--restoreset")
    assert rc == 1
    errs = json.loads(out)["errors"]
    assert any("DO_NOT_RESTORE" in e for e in errs) and any("builder report missing" in e for e in errs)
    assert any("row(s) of the source missing" in e for e in errs)      # HomeDomain/CameraRollDomain absent


def test_verify_source_full_clone(env):
    """verify --source without --restoreset (a full clone): all non-Threema rows/blobs identical; one flipped
    Keychain blob byte is found."""
    clone = env["dir"] / "clone" / env["udid"]
    shutil.copytree(env["dev"], clone)
    rc, out, err = cli("verify", clone, "--source", env["dev"])
    assert rc == 0, out + err
    sc = json.loads(out)["source_comparison"]
    assert sc["scope"] == "full" and "KeychainDomain" in sc["domains"] and "MediaDomain" in sc["domains"]
    r = rows_of(clone, env["pw"])[("KeychainDomain", "keychain-backup.plist")]
    p = clone / r.file_id[:2] / r.file_id
    b = bytearray(p.read_bytes())
    b[5] ^= 0x80
    p.write_bytes(bytes(b))
    rc, out, err = cli("verify", clone, "--source", env["dev"])
    assert rc == 1 and any("stored blob(s) differ" in e for e in json.loads(out)["errors"])


# --------------------------------------------------------------------------- refusals
def test_cli_needs_exactly_one_of_store_out_or_noop(env):
    rc, out, err = cli("restoreset", env["dev"], env["dir"] / "x1")
    assert rc == 2 and "--store-out" in err and not (env["dir"] / "x1").exists()
    rc, out, err = cli("restoreset", env["dev"], env["dir"] / "x2",
                       "--noop", "--store-out", env["store"])
    assert rc == 2 and not (env["dir"] / "x2").exists()


def test_refuses_existing_output(env):
    rc, out, err = cli("restoreset", env["dev"], env["dir"] / "noop", "--noop")
    assert rc == 2 and "already exists" in err


def test_refuses_marked_or_partial_source(env):
    # a restore set / a trim output as SOURCE -> refused by marker
    rc, out, err = cli("restoreset", env["noop"], env["dir"] / "x3", "--noop")
    assert rc == 2 and bp.RESTORESET_MARKER in err
    t = partial_payload(env, "partial-src")
    rc, out, err = cli("restoreset", t, env["dir"] / "x4", "--noop")
    assert rc == 2 and bp.DO_NOT_RESTORE in err
    # ... and even without the marker: a Threema-only backup is not a full backup
    (t / bp.DO_NOT_RESTORE).unlink()
    rc, out, err = cli("restoreset", t, env["dir"] / "x4", "--noop")
    assert rc == 1 and json.loads(out)["result"] == "REFUSED"
    assert any("FULL backup" in e for e in json.loads(out)["errors"])
    assert not (env["dir"] / "x4").exists()


def test_refuses_bad_store(env):
    s = env["dir"] / "store-rowloss"
    shutil.copytree(env["store"], s)
    c = sqlite3.connect(s / bp.DB)
    c.execute("DELETE FROM ZMESSAGE WHERE Z_PK=(SELECT min(Z_PK) FROM ZMESSAGE)")
    c.commit()
    c.close()
    rc, out, err = cli("restoreset", env["dev"], env["dir"] / "x5", "--store-out", s)
    assert rc == 1 and json.loads(out)["result"] == "REFUSED", out + err
    assert not (env["dir"] / "x5").exists()
    assert not (env["dir"] / "x5" / f"{env['udid']}.restoreset.json").exists()


def test_refuses_source_with_missing_system_blob(env):
    src = env["dir"] / "src-missing" / env["udid"]
    shutil.copytree(env["dev"], src)
    r = env["src_rows"][("CameraRollDomain", "Media/PhotoData/Photos.sqlite")]
    (src / r.file_id[:2] / r.file_id).unlink()
    rc, out, err = cli("restoreset", src, env["dir"] / "x6", "--noop")
    assert rc == 2 and "no blob" in err
    assert not (env["dir"] / "x6" / env["udid"]).exists()


# --------------------------------------------------------------------------- secrets
def test_password_never_written_or_printed(env):
    pw = env["pw"].encode()
    for k, v in env.items():
        if isinstance(v, tuple) and len(v) == 3:
            assert env["pw"] not in v[1] + v[2], k
    for p in env["dir"].rglob("*"):
        if p.is_file():
            assert pw not in p.read_bytes(), p
    left = [p.name for p in env["tmp"].iterdir() if p.name.startswith(("manifest-", "inject-", "verify-"))]
    assert not left, f"temporary decrypted files left behind: {left}"



# --------------------------------------------------------------------------- review-fix.md M4 (2026-10-01)
def test_noop_adds_empty_wal_shm_when_source_has_none(env):
    """Real iOS 27 backups carry NO -wal/-shm rows. A --noop set (canary/rollback) must still carry zero-length
    ones, so the overlay restore replaces a live device WAL (e.g. of the imported store) instead of leaving it next
    to the restored original DB. Every other Threema row stays identical."""
    d = env["dir"] / "fresh-src"
    fx = bf.fabricate_realistic_backup(d, env["pw"], variant="fresh")
    src = fx["device_dir"]
    src_rows = rows_of(src, env["pw"])
    assert (bp.GROUP_DOMAIN, bp.WAL) not in src_rows and (bp.GROUP_DOMAIN, bp.SHM) not in src_rows
    rc, out, err = cli("restoreset", src, env["dir"] / "fresh-noop", "--noop")
    assert rc == 0, out + err
    dev = env["dir"] / "fresh-noop" / src.name
    rep = json.loads((dev.parent / f"{src.name}.restoreset.json").read_text())
    assert rep["noop_added_empty"] == [bp.WAL, bp.SHM]
    rows = rows_of(dev, env["pw"])
    for n in (bp.WAL, bp.SHM):
        r = rows[(bp.GROUP_DOMAIN, n)]
        assert r.flags == 1 and r.mb.size == 0 and len(blob_bytes(dev, r.file_id)) == 16
    for k, s in src_rows.items():
        if k[0] in THREEMA:
            assert (rows[k].file_id, rows[k].flags, rows[k].blob) == (s.file_id, s.flags, s.blob), k
    rc, out, err = cli("verify", dev, "--source", src, "--restoreset")
    assert rc == 0, out[-3000:] + err[-2000:]
    assert '"added_empty_wal_shm": 2' in out


def test_verify_rejects_noop_set_without_wal_shm(env):
    """A noop set built by the OLD builder (no -wal/-shm rows) must no longer verify."""
    dev = copy_set(env, "noop", "nowal")
    ms = bp.ManifestSession(rw.EncryptedBackup(dev, env["pw"]))
    try:
        gone = [r.file_id for r in ms.rows(bp.GROUP_DOMAIN) if r.rel in (bp.WAL, bp.SHM)]
        ms.conn.executemany("DELETE FROM Files WHERE fileID=?", [(f,) for f in gone])
        ms.commit_and_encrypt()
    finally:
        ms.close()
    for f in gone:
        (dev / f[:2] / f).unlink()
    rc, out, err = cli("verify", dev, "--source", env["dev"], "--restoreset")
    assert rc == 1
    assert "has no ThreemaData.sqlite-wal file row" in out


# --------------------------------------------------------------------------- canary 2026-10-01: KeyboardDomain
def test_keyboard_domain_is_complete_and_identical(env):
    """Canary 2026-10-01 (iOS 27.0 24A437): KeyboardDomain was NOT in the set and lost emoji_adaptation.db, its learned
    model collapsed to 4 KiB. It is now carried complete and bit-identical (rows + blobs)."""
    for which in ("final", "noop"):
        rows = rows_of(env[which], env["pw"])
        src = {k: r for k, r in env["src_rows"].items() if k[0] == "KeyboardDomain"}
        assert len(src) == 5 and {k for k in rows if k[0] == "KeyboardDomain"} == set(src), which
        for k, s in src.items():
            assert (rows[k].file_id, rows[k].flags, rows[k].blob) == (s.file_id, s.flags, s.blob), k
            if s.flags == 1:
                assert blob_bytes(env[which], s.file_id) == blob_bytes(env["dev"], s.file_id), k


def test_verify_rejects_set_built_without_keyboard_domain(env):
    """A set built by the builder BEFORE KeyboardDomain joined (all KeyboardDomain rows absent, report without
    keyboard_rows) must no longer verify -- it would reset the keyboard learning again."""
    dev = copy_set(env, "noop", "nokbd")
    ms = bp.ManifestSession(rw.EncryptedBackup(dev, env["pw"]))
    try:
        gone = [(r.file_id, r.flags) for r in ms.rows("KeyboardDomain")]
        ms.conn.execute("DELETE FROM Files WHERE domain='KeyboardDomain'")
        ms.commit_and_encrypt()
    finally:
        ms.close()
    for f, fl in gone:
        if fl == 1:
            (dev / f[:2] / f).unlink()
    rp = dev.parent / f"{env['udid']}.restoreset.json"
    rep = json.loads(rp.read_text())
    for k in ("keyboard_rows",):
        rep.pop(k, None)
    rep["domains"].pop("KeyboardDomain", None)
    rp.write_text(json.dumps(rep))
    (dev / bp.RESTORESET_MARKER).write_text(hashlib.sha256(rp.read_bytes()).hexdigest())   # consistent old report
    rc, out, err = cli("verify", dev, "--source", env["dev"], "--restoreset")
    assert rc == 1, out[-2000:] + err[-1000:]
    errs = json.loads(out)["errors"]
    assert any("row(s) of the source missing" in e for e in errs), errs
    assert any("builder report keyboard_rows != actual" in e for e in errs), errs     # old report has none
    assert_private(out + err, env)


def test_builder_warns_when_source_has_no_keyboard_domain(env):
    """A source without KeyboardDomain rows (e.g. a device that never used the keyboard) still builds, with a
    warning, keyboard_rows = 0."""
    src = env["dir"] / "src-nokbd" / env["udid"]
    shutil.copytree(env["dev"], src)
    bp.make_writable(src)
    ms = bp.ManifestSession(rw.EncryptedBackup(src, env["pw"]))
    try:
        gone = [(r.file_id, r.flags) for r in ms.rows("KeyboardDomain")]
        ms.conn.execute("DELETE FROM Files WHERE domain='KeyboardDomain'")
        ms.commit_and_encrypt()
    finally:
        ms.close()
    for f, fl in gone:
        if fl == 1:
            (src / f[:2] / f).unlink()
    rc, out, err = cli("restoreset", src, env["dir"] / "x-nokbd", "--noop")
    assert rc == 0, out + err
    rep = json.loads(out)
    assert rep["keyboard_rows"] == 0 and "KeyboardDomain" not in rep["domains"]
    assert any("no KeyboardDomain rows" in w for w in rep["warnings"])
