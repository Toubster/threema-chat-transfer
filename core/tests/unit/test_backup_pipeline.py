# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for tmcore.lib.backup_pipeline (extract / restoreset --store-out / verify) against a realistic synthetic
ENCRYPTED backup (fixtures/gen_ios_backup.py, stores from tests/support). No device, synthetic data only.
Ported from the proof of concept: `inject` and `trim` are gone from the product (DESIGN §4.3: no partial payload,
no overrides), so the inject checks run against the Threema part of a `restoreset --store-out` build (same inject
code path), and the trim tests are dropped. Passwords go to the CLI on stdin, never in a file.
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

CORE = support.CORE
PLUGIN = "AppDomainPlugin-ch.threema.iapp.ThreemaNotificationExtension"


@pytest.fixture(scope="module")
def bf():
    return support.ios_fixture()


@pytest.fixture(scope="module")
def n_ext(run):
    """external-data files in the fixture backup (the proof of concept's private sample store had exactly 1)"""
    return sum(1 for (dom, rel) in run["fx"]["plain"] if dom == bp.GROUP_DOMAIN and rel.startswith(bp.EXT + "/"))


# --------------------------------------------------------------------------- helpers
def cli(*args, pw, tmp, timeout=110):
    """The maintainer CLI as a subprocess: password = one line on stdin, temp files under `tmp`."""
    env = support.tmcore_env(TMPDIR=str(tmp))
    r = subprocess.run([sys.executable, "-m", "tmcore.lib.backup_pipeline", *[str(a) for a in args]],
                       input=pw + "\n", capture_output=True, text=True, timeout=timeout, cwd=CORE, env=env)
    return r.returncode, r.stdout, r.stderr


def jout(stdout: str) -> dict:
    return json.loads(stdout)


def tree_hash(d: Path) -> dict:
    out = {}
    for p in sorted(d.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(d))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def manifest_rows(dev: Path, pw: str) -> dict:
    bk = rw.EncryptedBackup(dev, pw)
    ms = bp.ManifestSession(bk)
    try:
        return {(r.domain, r.rel): r for r in ms.rows()}, ms.padded
    finally:
        ms.close()


def modify_store(store: Path, *, add_external=True, touch_conversation=True, dangling=False) -> str | None:
    """Mimic the importer: new FileData row with an external blob, conversation lastUpdate bump."""
    u = None
    c = sqlite3.connect(store / bp.DB)
    if add_external or dangling:
        u = str(uuid.uuid4()).upper()
        if not dangling:
            ext = store / bp.EXT
            ext.mkdir(parents=True, exist_ok=True)
            (ext / u).write_bytes(os.urandom(70001))
        e, m = c.execute("SELECT Z_ENT, Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME='FileData'").fetchone()
        c.execute("INSERT INTO ZFILEDATA(Z_PK, Z_ENT, Z_OPT, ZDATA) VALUES (?,?,1,?)",
                  (m + 1, e, b"\x02" + u.encode() + b"\x00"))
        c.execute("UPDATE Z_PRIMARYKEY SET Z_MAX=? WHERE Z_NAME='FileData'", (m + 1,))
    if touch_conversation:
        c.execute("UPDATE ZCONVERSATION SET ZLASTUPDATE = coalesce(ZLASTUPDATE, 0) + 1")
    c.commit()
    c.close()
    assert not (store / bp.WAL).exists() or (store / bp.WAL).stat().st_size == 0
    return u


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def run(bf, tmp_path_factory):
    d = tmp_path_factory.mktemp("backup-pipeline")
    os.chmod(d, 0o700)
    pw = "t-" + secrets.token_hex(12)
    (d / "tmp").mkdir(mode=0o700)
    fx = bf.fabricate_realistic_backup(d / "orig", pw)
    yield {"dir": d, "pw": pw, "tmp": d / "tmp", "fx": fx, "dev": fx["device_dir"], "udid": fx["udid"]}


@pytest.fixture(scope="module")
def chain(run):
    d, dev, pw, udid, tmp = run["dir"], run["dev"], run["pw"], run["udid"], run["tmp"]
    res = {"before": tree_hash(dev)}
    res["extract"] = cli("extract", dev, d / "extract", pw=pw, tmp=tmp)
    store_out = d / "store_out"
    shutil.copytree(d / "extract" / "store", store_out)
    res["new_ext"] = modify_store(store_out)
    res["store_out"] = store_out
    res["inject"] = cli("restoreset", dev, d / "injected", "--store-out", store_out, pw=pw, tmp=tmp)
    res["inj"] = d / "injected" / udid
    res["verify1"] = cli("verify", res["inj"], "--against", store_out, "--report", d / "verify1.json", pw=pw, tmp=tmp)
    res["verify0"] = cli("verify", dev, pw=pw, tmp=tmp)
    res["after"] = tree_hash(dev)
    return res


# --------------------------------------------------------------------------- extract
def test_extract_report(run, chain):
    rc, out, err = chain["extract"]
    assert rc == 0, err + out
    rep = json.loads((run["dir"] / "extract" / "report.json").read_text())
    assert rep["backup"]["encrypted"] is True
    assert rep["backup"]["ios_version"] == "27.0"
    assert rep["threema_app"]["CFBundleVersion"] == "74051"
    assert rep["threema_app"]["CFBundleShortVersionString"] == "7.4"
    assert rep["keychain"]["present"] is True
    gp = rep["group_container"]["group_prefs"]
    assert gp["AppSetupState"] == 40 and gp["KeepMessagesDays"] == -1 and gp["retention_active"] is False
    assert rep["group_container"]["wal_size"] > 0            # live WAL in the fixture
    assert rep["store"]["integrity_check"] == "ok"
    assert rep["store"]["metadata"]["model_matches_v56_reference"] is True
    assert rep["store"]["missing_external_refs"] == 0
    # the only warning: threema-fs.db is excluded from iOS backups (review restoresafety M2)
    assert rep["group_container"]["threema_fs_db_in_backup"] is False
    assert len(rep["warnings"]) == 1 and "threema-fs.db" in rep["warnings"][0] and rep["errors"] == []
    assert rep["source_unchanged"] is True
    assert set(rep["threema_domains"]) == {bp.APP_DOMAIN, bp.GROUP_DOMAIN,
                                           "AppDomainPlugin-ch.threema.iapp.ThreemaNotificationExtension"}


def test_extract_files_and_manifest(run, chain):
    out = run["dir"] / "extract"
    man = json.loads((out / "manifest.json").read_text())["entries"]
    plain = run["fx"]["plain"]
    grp_files = [e for e in man if e["domain"] == bp.GROUP_DOMAIN and e["flags"] == 1]
    assert grp_files and all(e["decrypted"] for e in grp_files)
    for e in grp_files:
        data = (out / e["extractedPath"]).read_bytes()
        assert data == plain[(e["domain"], e["relativePath"])]
        assert hashlib.sha256(data).hexdigest() == e["sha256"]
    app_files = [e for e in man if e["domain"] == bp.APP_DOMAIN and e["flags"] == 1]
    assert app_files and not any(e["decrypted"] for e in app_files)
    for e in man:
        for k in ("domain", "relativePath", "flags", "size", "protectionClass", "mode"):
            assert k in e
    assert not (out / bp.APP_DOMAIN).exists()                 # app sandbox only listed


def test_extract_store_is_wal_folded(run, chain):
    out = run["dir"] / "extract"
    raw = out / bp.GROUP_DOMAIN / bp.DB
    store = out / "store" / bp.DB
    q = "SELECT ZJOBTITLE FROM ZCONTACT"
    raw_vals = sqlite3.connect(f"file:{raw}?immutable=1", uri=True).execute(q).fetchall()
    folded = sqlite3.connect(f"file:{store}?immutable=1", uri=True).execute(q).fetchall()
    assert ("wal-marker",) not in raw_vals                    # change only lived in the WAL
    assert ("wal-marker",) in folded                          # ...and was folded into store/
    assert not (out / "store" / bp.WAL).exists() and not (out / "store" / bp.SHM).exists()
    assert (out / "store" / bp.EXT).is_dir()


# --------------------------------------------------------------------------- source safety
def test_original_backup_untouched(chain):
    assert chain["before"] == chain["after"]


# --------------------------------------------------------------------------- inject
def test_inject_result(run, chain, n_ext):
    rc, out, err = chain["inject"]
    assert rc == 0, err + out
    rep = jout(out)
    assert rep["result"] == "OK" and rep["source_unchanged"] is True
    a = rep["inject_actions"]
    assert a["replaced"] == [bp.DB] and a["zeroed"] == [bp.WAL, bp.SHM]
    assert a["external_added"] == 1 and a["external_unchanged"] == n_ext and a["added_dirs"] == []
    full = json.loads((run["dir"] / "injected" / f"{run['udid']}.restoreset.json").read_text())
    cmpr = full["inject_validation"]["comparison_with_device_store"]
    assert cmpr["model_identical"] and cmpr["schema_identical"] and cmpr["lost_rows"] == {}
    assert set(cmpr["modified_existing_rows"]) <= {"ZCONVERSATION"}


def test_inject_manifest_rows(run, chain):
    orig, orig_padded = manifest_rows(run["dev"], run["pw"])
    new, new_padded = manifest_rows(chain["inj"], run["pw"])
    assert orig_padded and new_padded                          # device format (PKCS7) preserved
    orig = {k: r for k, r in orig.items() if bp.is_restoreset_domain(k[0])}    # the set keeps only these
    assert len(new) == len(orig) + 1
    db = new[(bp.GROUP_DOMAIN, bp.DB)].mb
    assert db.size == (chain["store_out"] / bp.DB).stat().st_size
    for n in (bp.WAL, bp.SHM):
        r = new[(bp.GROUP_DOMAIN, n)]
        assert r.flags == 1 and r.mb.size == 0
        # device format for empty files (real iOS 27 backup, pre-go review): keyed, class 3, 16-byte stored blob
        # (= one encrypted PKCS7 padding block) that decrypts to b""
        assert r.mb.enc_blob is not None and r.mb.key_class() == 3
        stored = chain["inj"] / r.file_id[:2] / r.file_id
        assert stored.stat().st_size == 16
        n, _ = bp.decrypt_to(stored, bp.file_key(rw.EncryptedBackup(chain["inj"], run["pw"]), r.mb))
        assert n == 0
    rel = f"{bp.EXT}/{chain['new_ext']}"
    r = new[(bp.GROUP_DOMAIN, rel)]
    assert r.flags == 1 and r.file_id == hashlib.sha1(f"{bp.GROUP_DOMAIN}-{rel}".encode()).hexdigest()
    assert r.mb.pclass == 3 and r.mb.key_class() == 3
    assert (r.mb.mode, r.mb.uid, r.mb.gid) == (db.mode, db.uid, db.gid)
    inodes = [x.mb.inode for (dom, _), x in new.items() if dom == bp.GROUP_DOMAIN]
    assert len(inodes) == len(set(inodes))
    # every row outside the group domain is byte-identical (metadata + blob)
    for key, o in orig.items():
        if key[0] == bp.GROUP_DOMAIN:
            continue
        assert new[key].blob == o.blob and new[key].flags == o.flags
        if o.flags == 1:
            assert ((chain["inj"] / o.file_id[:2] / o.file_id).read_bytes()
                    == (run["dev"] / o.file_id[:2] / o.file_id).read_bytes())


def test_verify_injected_passes(chain, n_ext):
    rc, out, err = chain["verify1"]
    assert rc == 0, err + out
    rep = jout(out)
    assert rep["result"] == "PASS" and rep["readers_agree_on_manifest"] is True
    assert rep["against_store"] == {"db": True, "external_checked": n_ext + 1, "external_mismatch": 0,
                                    "external_missing": 0}
    assert rep["wal_size"] == 0 and rep["shm_size"] == 0
    assert rep["db"]["integrity_check"] == "ok" and rep["db"]["missing_external_refs"] == 0


def test_verify_original_passes_with_live_file_warning(chain):
    rc, out, err = chain["verify0"]
    assert rc == 0, err + out
    rep = jout(out)
    # HomeDomain sms.db-wal "live" file + the zero-length HomeDomain file stored as a 0-byte blob (device format)
    assert rep["manifest"]["blob_size_mismatch"] == 2
    assert rep["errors"] == []


# --------------------------------------------------------------------------- fresh device variant
def test_fresh_variant_adds_dirs_and_wal_rows(run, bf):
    d = run["dir"] / "fresh"
    fx = bf.fabricate_realistic_backup(d / "orig", run["pw"], variant="fresh")
    dev = fx["device_dir"]
    rc, out, err = cli("extract", dev, d / "extract", pw=run["pw"], tmp=run["tmp"])
    assert rc == 0, err + out
    rep = json.loads((d / "extract" / "report.json").read_text())
    assert rep["group_container"]["app_setup_not_completed_marker"] is True
    assert rep["group_container"]["group_prefs"]["retention_active"] is True
    assert any("KeepMessagesDays" in w for w in rep["warnings"])
    assert rep["group_container"]["wal_size"] is None
    store_out = d / "store_out"
    shutil.copytree(d / "extract" / "store", store_out)
    u = modify_store(store_out, touch_conversation=False)
    rc, out, err = cli("restoreset", dev, d / "injected", "--store-out", store_out, pw=run["pw"], tmp=run["tmp"])
    assert rc == 0, err + out
    rep = jout(out)
    assert rep["inject_actions"]["added_dirs"] == [bp.SUPPORT, bp.EXT]
    assert rep["inject_actions"]["added_files"] == 3           # -wal, -shm, external
    rows, _ = manifest_rows(d / "injected" / fx["udid"], run["pw"])
    lib = rows[(bp.GROUP_DOMAIN, "Library")].mb               # sibling directory row
    for rel in (bp.SUPPORT, bp.EXT):
        r = rows[(bp.GROUP_DOMAIN, rel)]
        assert r.flags == 2 and r.mb.enc_blob is None and r.mb.size == 0
        assert (r.mb.mode, r.mb.uid, r.mb.gid, r.mb.pclass) == (lib.mode, lib.uid, lib.gid, lib.pclass)
    assert rows[(bp.GROUP_DOMAIN, f"{bp.EXT}/{u}")].flags == 1
    for n in (bp.WAL, bp.SHM):     # keyed empty files, as the real iOS 27 backup stores them
        r = rows[(bp.GROUP_DOMAIN, n)]
        assert r.mb.size == 0 and r.mb.enc_blob is not None and r.mb.key_class() == 3
        assert (d / "injected" / fx["udid"] / r.file_id[:2] / r.file_id).stat().st_size == 16
    rc, out, err = cli("verify", d / "injected" / fx["udid"], "--against", store_out, pw=run["pw"], tmp=run["tmp"])
    assert rc == 0, err + out
    assert rep["inject_actions"]["added_dirs"] == [bp.SUPPORT, bp.EXT]
    assert not json.loads((d / "extract" / "report.json").read_text())["group_container"]["threema_fs_db_in_backup"]


def test_inject_refuses_unknown_mbfile_key(run, chain):
    """review M4: a group-domain row with a key we do not understand (e.g. a content Digest) -> REFUSED."""
    t = run["dir"] / "digest-src" / run["udid"]
    shutil.copytree(run["dev"], t)
    bk = rw.EncryptedBackup(t, run["pw"])
    ms = bp.ManifestSession(bk)
    r = ms.get(bp.GROUP_DOMAIN, bp.DB)
    mb = r.mb
    mb.root["Digest"] = b"\x00" * 20
    ms.update_blob(r.file_id, mb.dumps())
    ms.commit_and_encrypt()
    ms.close()
    rc, out, err = cli("restoreset", t, run["dir"] / "digest-out", "--store-out", chain["store_out"], pw=run["pw"],
                       tmp=run["tmp"])
    assert rc == 1 and "REFUSED" in out and "Digest" in out, out
    rc, out, err = cli("verify", t, pw=run["pw"], tmp=run["tmp"])
    assert rc == 1 and "unknown keys" in out


# --------------------------------------------------------------------------- refusals
def _variant_store(run, chain, name):
    s = run["dir"] / f"store-{name}"
    shutil.copytree(chain["store_out"], s)
    return s


@pytest.mark.parametrize("case", ["nonempty_wal", "row_loss", "dangling_ref", "model_mismatch", "retyped"])
def test_inject_refuses_bad_store(run, chain, case):
    s = _variant_store(run, chain, case)
    if case == "nonempty_wal":
        (s / bp.WAL).write_bytes(os.urandom(4096))
    elif case == "row_loss":
        c = sqlite3.connect(s / bp.DB)
        c.execute("DELETE FROM ZMESSAGE WHERE Z_PK=(SELECT min(Z_PK) FROM ZMESSAGE)")
        c.commit()
        c.close()
    elif case == "dangling_ref":
        modify_store(s, add_external=False, touch_conversation=False, dangling=True)
    elif case == "model_mismatch":
        c = sqlite3.connect(s / bp.DB)
        md = plistlib.loads(c.execute("SELECT Z_PLIST FROM Z_METADATA").fetchone()[0])
        md["NSStoreModelVersionHashes"]["Contact"] = b"\x00" * 32
        c.execute("UPDATE Z_METADATA SET Z_PLIST=?", (plistlib.dumps(md, fmt=plistlib.FMT_BINARY),))
        c.commit()
        c.close()
    elif case == "retyped":
        c = sqlite3.connect(s / bp.DB)
        ents = [e for (e,) in c.execute("SELECT DISTINCT Z_ENT FROM ZMESSAGE")]
        other = next(e for e in range(15, 23) if e not in ents)
        c.execute("UPDATE ZMESSAGE SET Z_ENT=? WHERE Z_PK=(SELECT min(Z_PK) FROM ZMESSAGE)", (other,))
        c.commit()
        c.close()
    out_root = run["dir"] / f"inj-{case}"
    rc, out, err = cli("restoreset", run["dev"], out_root, "--store-out", s, pw=run["pw"], tmp=run["tmp"])
    assert rc == 1, (case, out, err)
    assert jout(out)["result"] == "REFUSED"
    assert not out_root.exists()


def test_inject_refuses_existing_output(run, chain):
    rc, out, err = cli("restoreset", run["dev"], run["dir"] / "injected", "--store-out", chain["store_out"],
                       pw=run["pw"], tmp=run["tmp"])
    assert rc == 2 and "already exists" in err


def test_inject_refuses_output_inside_source(run, chain):
    rc, out, err = cli("restoreset", run["dev"], run["dev"] / "sub", "--store-out", chain["store_out"],
                       pw=run["pw"], tmp=run["tmp"])
    assert rc == 2 and "overlap" in err


# --------------------------------------------------------------------------- tamper detection
@pytest.mark.parametrize("case", ["flip_db_byte", "missing_external_blob", "corrupt_shm_padding"])
def test_verify_detects_tampering(run, chain, case):
    t = run["dir"] / f"tamper-{case}" / run["udid"]
    shutil.copytree(chain["inj"], t)
    rows, _ = manifest_rows(t, run["pw"])
    if case == "flip_db_byte":
        r = rows[(bp.GROUP_DOMAIN, bp.DB)]
        p = t / r.file_id[:2] / r.file_id
        b = bytearray(p.read_bytes())
        b[len(b) // 2] ^= 0x55
        p.write_bytes(bytes(b))
    elif case == "missing_external_blob":
        r = rows[(bp.GROUP_DOMAIN, f"{bp.EXT}/{chain['new_ext']}")]
        (t / r.file_id[:2] / r.file_id).unlink()
    else:
        r = rows[(bp.GROUP_DOMAIN, bp.SHM)]
        p = t / r.file_id[:2] / r.file_id
        p.write_bytes(p.read_bytes()[:-16] + os.urandom(16))       # corrupt padding block
    rc, out, err = cli("verify", t, "--against", chain["store_out"], pw=run["pw"], tmp=run["tmp"])
    assert rc == 1, (case, out, err)
    assert jout(out)["result"] == "FAIL"


# --------------------------------------------------------------------------- secrets
def test_wrong_password(run, chain):
    wrong = "wrong-" + secrets.token_hex(6)
    rc, out, err = cli("extract", run["dev"], run["dir"] / "extract-bad", pw=wrong, tmp=run["tmp"])
    assert rc == 2 and wrong not in out + err
    rc, out, err = cli("verify", run["dev"], pw=wrong, tmp=run["tmp"])
    assert rc == 1 and wrong not in out + err


def test_no_password_on_stdin_is_refused(run):
    r = subprocess.run([sys.executable, "-m", "tmcore.lib.backup_pipeline", "verify", str(run["dev"])], input="",
                       capture_output=True, text=True, timeout=60, cwd=CORE, env=support.tmcore_env())
    assert r.returncode == 2 and "no password" in r.stderr


def test_no_password_file_or_override_left():
    subs = bp.build_parser()._subparsers._group_actions[0].choices          # noqa: SLF001
    assert set(subs) == {"extract", "verify", "restoreset"}                  # no trim, no inject
    helps = "".join(sp.format_help() for sp in subs.values())
    for gone in ("--password-file", "--allow", "--trimmed", "--waive", "--protection-class"):
        assert gone not in helps, gone


def test_password_never_written_or_printed(run, chain):
    pw = run["pw"].encode()
    outputs = "".join(o + e for k, v in chain.items() if isinstance(v, tuple) and len(v) == 3
                      for _, o, e in [v])
    assert run["pw"] not in outputs
    for p in run["dir"].rglob("*"):
        if p.is_file():
            assert pw not in p.read_bytes(), p
    ours = [p.name for p in run["tmp"].iterdir()
            if p.name.startswith(("manifest-", "inject-", "verify-")) or p.name.endswith("sqlite3")]
    assert not ours, f"temporary decrypted files left behind: {ours}"


def test_in_process_run_never_prints(run, capsys):
    rc, summary = bp.run("verify", password=run["pw"], backup_udid_dir=str(run["dev"]))
    assert rc == 0 and summary["result"] == "PASS"
    cap = capsys.readouterr()
    assert cap.out == ""


# --------------------------------------------------------------------------- unit bits
def test_manifest_padding_detection():
    page = 4096
    sqlite_bytes = b"SQLite format 3\x00" + page.to_bytes(2, "big") + b"\x00" * (page * 3 - 18)
    padded = sqlite_bytes + bytes([16]) * 16
    assert bp.strip_manifest_padding(padded) == (sqlite_bytes, True)
    assert bp.strip_manifest_padding(sqlite_bytes) == (sqlite_bytes, False)
    tricky = sqlite_bytes[:-1] + b"\x01"                      # unpadded file ending in 0x01: keep intact
    assert bp.strip_manifest_padding(tricky) == (tricky, False)
    key = os.urandom(32)
    ct = bp.encrypt_manifest_bytes(sqlite_bytes, key, True)
    assert len(ct) == len(sqlite_bytes) + 16
    assert bp.strip_manifest_padding(rw.decrypt_manifest_db(ct, key)) == (sqlite_bytes, True)


def test_mbfile_roundtrip_preserves_fields(bf):
    blob = bf.mbfile_blob(rel="a/b", mode=0o100600, size=5, pclass=3, inode=9, mtime=100,
                          enc_blob=b"\x03\x00\x00\x00" + os.urandom(40), xattrs={"k": b"v"})
    mb = bp.MBFile(blob)
    mb.set_fields(Size=7)
    mb2 = bp.MBFile(mb.dumps())
    assert mb2.size == 7 and mb2.mode == 0o100600 and mb2.relative_path == "a/b" and mb2.inode == 9
    assert "ExtendedAttributes" in mb2.root and mb2.enc_blob == mb.enc_blob


def test_iosbackup_rw_selftest_still_passes():
    r = subprocess.run([sys.executable, "-m", "tmcore.lib.iosbackup_rw", "--selftest"], cwd=CORE,
                       capture_output=True, text=True, timeout=60, env=support.tmcore_env())
    assert r.returncode == 0 and "SELFTEST PASS" in r.stdout
