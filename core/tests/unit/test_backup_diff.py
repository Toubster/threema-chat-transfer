# SPDX-License-Identifier: AGPL-3.0-or-later
"""
tmcore.lib.backup_diff on SYNTHETIC encrypted backups (fixtures/gen_ios_backup.py). No device, no real data, no
password files: the maintainer CLI gets the password as one stdin line (--password-stdin). Ported from the private
proof of concept; the waiver, --deep-paths and --show-app-names tests were replaced by "does not exist" tests
(DESIGN §4.3).
"""
from __future__ import annotations

import json
import os
import plistlib
import secrets
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import support
from tmcore.lib import backup_diff as bd
from tmcore.lib import backup_pipeline as bp
from tmcore.lib import iosbackup_rw as rw

pytest.importorskip("iphone_backup_decrypt")

SECRET_NAME = "Geheimordner Zzwitscher"            # personal-looking folder name that must never be printed
SECRET_FILE = "IMG_4711 Urlaub mit Zzwitscher.HEIC"
_PW = {"value": None}


def cli(*args, pw=None):
    """python -m tmcore.lib.backup_diff ...; with --password-stdin the password is the one stdin line."""
    stdin = ((pw or _PW["value"]) + "\n") if "--password-stdin" in args else ""
    r = subprocess.run([sys.executable, "-m", "tmcore.lib.backup_diff", *[str(a) for a in args]], cwd=support.CORE,
                       input=stdin, capture_output=True, text=True, timeout=120, env=support.tmcore_env())
    return r.returncode, r.stdout, r.stderr


def add_file(ms: bp.ManifestSession, dev: Path, domain: str, rel: str, data: bytes, *, inode: int, birth: int):
    key = os.urandom(32)
    enc = struct.pack("<I", 3) + rw.aes_key_wrap(ms.bk.class_key(3), key)
    fid = rw.file_id_for(domain, rel)
    bp.encrypt_to(bp.stored_path(dev, fid), key, data=data)
    blob = bp.build_mbfile(relative_path=rel, mode=0o100644, uid=501, gid=501, size=len(data), pclass=3,
                           mtime=birth, inode=inode, enc_blob=enc)
    ms.insert(fid, domain, rel, 1, blob)


def mutate(dev: Path, pw: str, *, remove_prefix: str | None, purple: dict | None, recreate: str | None,
           extra: list[tuple[str, str]] = (), drop_secret: bool = False):
    bk = rw.EncryptedBackup(dev, pw)
    ms = bp.ManifestSession(bk)
    try:
        now = int(time.time())
        if remove_prefix:
            ms.conn.execute("DELETE FROM Files WHERE domain='HomeDomain' AND relativePath LIKE ?",
                            (remove_prefix + "%",))
        if drop_secret:
            ms.conn.execute("DELETE FROM Files WHERE relativePath LIKE ?", (f"%{SECRET_NAME}%",))
        if recreate:
            r = ms.get("HomeDomain", recreate)
            mb = r.mb
            mb.set_fields(InodeNumber=mb.inode + 999, Birth=now)
            ms.update_blob(r.file_id, mb.dumps())
        if purple is not None:
            rel = "Library/Preferences/com.apple.purplebuddy.plist"
            old = ms.get("HomeDomain", rel)
            if old:
                ms.conn.execute("DELETE FROM Files WHERE fileID=?", (old.file_id,))
            add_file(ms, dev, "HomeDomain", rel, plistlib.dumps(purple, fmt=plistlib.FMT_BINARY),
                     inode=55555 + (1000 if old else 0), birth=now)
        for i, (dom, rel) in enumerate(extra):
            add_file(ms, dev, dom, rel, b"x" * 100, inode=77000 + i, birth=now)
        ms.commit_and_encrypt()
    finally:
        ms.close()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    d = tmp_path_factory.mktemp("backup-diff")
    os.chmod(d, 0o700)
    pw = "t-" + secrets.token_hex(12)
    _PW["value"] = pw
    bp.use_tmp(d / "tmp")
    fx = support.ios_fixture().fabricate_realistic_backup(d / "pre", pw)
    pre = fx["device_dir"]
    # PRE gets a purplebuddy plist, a populated DeviceRegistry and a personal-looking folder
    regs = [("HomeDomain", f"Library/DeviceRegistry/{i:02d}/pairing.plist") for i in range(30)]
    mutate(pre, pw, remove_prefix=None, recreate=None,
           purple={"SetupDone": True, "SetupState": "SetupUsingAssistant", "SetupLastExit": "x", "Name": "Kassiopeia"},
           extra=regs + [("AppDomain-com.example.other", f"Documents/{SECRET_NAME}/{SECRET_FILE}"),
                         ("AppDomainGroup-group.com.apple.FileProvider.LocalStorage",
                          f"File Provider Storage/{SECRET_NAME}/{SECRET_FILE}"),
                         ("HomeDomain", f"Library/Mobile Documents/com~apple~CloudDocs/{SECRET_NAME}/{SECRET_FILE}"),
                         ("HomeDomain", f"Documents/{SECRET_NAME}/{SECRET_FILE}")])

    def clone(name):
        dst = d / name / fx["udid"]
        dst.parent.mkdir(parents=True)
        shutil.copytree(pre, dst)
        bp.make_writable(dst)
        return dst

    same = clone("same")
    bad = clone("bad")
    mutate(bad, pw, remove_prefix="Library/DeviceRegistry", drop_secret=True, recreate="Library/Preferences/com.apple.springboard.plist",
           purple={"SetupDone": True, "SetupState": "RestoredFromiTunesBackup", "SetupLastExit": "y", "Name": "Kassiopeia"})
    yield {"d": d, "pw": pw, "pre": pre, "same": same, "bad": bad}


def test_identical_backups_pass_gate(env):
    rc, out, err = cli(env["pre"], env["same"], "--password-stdin", "--gate",
                       "--report", env["d"] / "same.json")
    assert rc == 0, out + err
    rep = json.loads((env["d"] / "same.json").read_text())
    assert rep["gate"] == "PASS" and not rep["alerts"]
    assert all(r["removed_files"] == 0 and r["recreated_files"] == 0 for r in rep["domains"])
    assert rep["purplebuddy"]["changed_keys"] == []


def test_collateral_damage_fails_gate(env):
    rc, out, err = cli(env["pre"], env["bad"], "--password-stdin", "--gate", "--alert-min", "5",
                       "--report", env["d"] / "bad.json")
    assert rc == 1, out + err
    rep = json.loads((env["d"] / "bad.json").read_text())
    sent = {s["id"]: s for s in rep["sentinels"]}
    assert sent["watch_pairing"]["verdict"] == "ALERT:wiped" and sent["watch_pairing"]["removed"] == 30
    home = next(r for r in rep["domains"] if r["domain"] == "HomeDomain")
    # 30 DeviceRegistry files + the 2 personal-looking HomeDomain files; springboard + purplebuddy plist rewritten
    assert home["removed_files"] == 32 and home["recreated_files"] == 2 and home["verdict"] == "ALERT:files_lost"
    assert rep["purplebuddy"]["alert"] is True
    assert {"SetupState", "SetupLastExit"} <= set(rep["purplebuddy"]["changed_keys"])


def test_without_gate_exit_zero(env):
    rc, _, _ = cli(env["pre"], env["bad"], "--password-stdin")
    assert rc == 0


def test_home_in_payload_is_never_masked(env):
    # the bad backup as "payload": HomeDomain is in the payload, but it is an IDENTITY domain (a restore set carries it
    # bit-identical to PRE) -> still judged like an outside domain, plus the payload != PRE alert
    rc, out, _ = cli(env["pre"], env["bad"], "--password-stdin", "--payload", env["bad"], "--gate",
                     "--alert-min", "5", "--report", env["d"] / "payload.json")
    assert rc == 1
    rep = json.loads((env["d"] / "payload.json").read_text())
    home = next(r for r in rep["domains"] if r["domain"] == "HomeDomain")
    assert home["role"] == "identity" and home["verdict"] == "ALERT:files_lost"
    assert {s["id"]: s for s in rep["sentinels"]}["watch_pairing"]["verdict"] == "ALERT:wiped"
    assert any(a.startswith("identity HomeDomain: payload != PRE") for a in rep["alerts"])
    assert rep["payload_landing"]["missing_in_post"] == 0


def _restoreset(env, name: str) -> Path:
    """backup_pipeline.py restoreset --noop of PRE (Threema + complete HomeDomain/CameraRollDomain/KeyboardDomain)."""
    out = env["d"] / name
    if not out.exists():
        rc, summ = bp.run("restoreset", password=env["pw"], backup_udid_dir=str(env["pre"]), out_root=str(out),
                          noop=True)
        assert rc == 0, summ
    return out


def test_restoreset_payload_unchanged_device_passes(env):
    rs = _restoreset(env, "rs-noop")
    rc, out, err = cli(env["pre"], env["same"], "--password-stdin", "--payload", rs, "--gate",
                       "--report", env["d"] / "rs-same.json")
    assert rc == 0, out + err
    rep = json.loads((env["d"] / "rs-same.json").read_text())
    assert rep["identity_domains"]["HomeDomain"]["different"] == 0
    assert rep["identity_domains"]["HomeDomain"]["payload_rows"] == rep["identity_domains"]["HomeDomain"]["pre_rows"]
    roles = {r["domain"]: r["role"] for r in rep["domains"]}
    assert all(v != "payload" for k, v in roles.items() if k in ("HomeDomain", "CameraRollDomain", "KeyboardDomain"))
    assert set(rep["identity_domains"]) == {"HomeDomain", "CameraRollDomain", "KeyboardDomain"}
    assert rep["identity_domains"]["KeyboardDomain"]["payload_rows"] == 5


def test_restoreset_payload_home_wipe_fails(env):
    """The 2026-10-01 damage pattern with a restore set as --payload: must FAIL (HomeDomain not exempt)."""
    rs = _restoreset(env, "rs-noop")
    rc, out, err = cli(env["pre"], env["bad"], "--password-stdin", "--payload", rs, "--gate",
                       "--alert-min", "5", "--report", env["d"] / "rs-bad.json")
    assert rc == 1, out + err
    rep = json.loads((env["d"] / "rs-bad.json").read_text())
    home = next(r for r in rep["domains"] if r["domain"] == "HomeDomain")
    assert home["role"] == "identity" and home["verdict"].startswith("ALERT")
    assert {s["id"]: s for s in rep["sentinels"]}["watch_pairing"]["verdict"] == "ALERT:wiped"
    assert not any(a.startswith("identity ") for a in rep["alerts"])       # payload == PRE: only real damage


def test_no_personal_data_in_output(env):
    rc, out, err = cli(env["pre"], env["bad"], "--password-stdin", "--report", env["d"] / "p.json",
                       "--depth", "4")
    blob = out + err + (env["d"] / "p.json").read_text()
    for s in ("Kassiopeia", "Geheimordner", "IMG_4711", "Urlaub", "pairing.plist", "com.example.other", env["pw"]):
        assert s not in blob, s
    assert "<x>" in blob or "Library" in blob


@pytest.mark.parametrize("opt", ["--deep-paths", "--show-app-names", "--waive", "--password-file"])
def test_removed_options_do_not_exist(env, opt):
    """DESIGN §4.3: no deep paths, no third-party app names, no waivers, no password files in the product."""
    rc, out, err = cli(env["pre"], env["same"], "--password-stdin", opt, *(["x"] if opt in ("--waive",
                                                                                        "--password-file") else []))
    assert rc == 2 and "unrecognized arguments" in err


def test_compare_refuses_waivers_deep_paths_and_app_names(env):
    pre = bd.Backup(env["pre"], env["pw"], "pre")
    post = bd.Backup(env["same"], env["pw"], "post")
    common = dict(payload=None, expect=list(bd.DEFAULT_EXPECT), marks=[], depth=2, content_re=None, baseline=None,
                  alert_min=20, alert_frac=0.10, top=25)
    for bad in ({"waivers": [{"alert_id": "sentinel:keyboard"}]}, {"deep": True}, {"show_apps": True}):
        with pytest.raises(ValueError):
            bd.compare(pre, post, **common, **bad)
    rep = bd.compare(pre, post, **common, show_apps=False, deep=False, waivers=None)   # the product values
    assert rep["gate"] == "PASS" and "waived" not in rep and "waivers" not in rep
    with pytest.raises(ValueError):
        bd.bucket("HomeDomain", "Library/x/y/z", 1, 4, False, True)


def test_wrong_password_exit_2(env):
    rc, out, err = cli(env["pre"], env["same"], "--password-stdin", pw="nope")
    assert rc == 2 and "ERROR" in err and "nope" not in out + err


def test_baseline_raises_threshold(env):
    cli(env["pre"], env["bad"], "--password-stdin", "--report", env["d"] / "base.json")
    rc, out, _ = cli(env["pre"], env["bad"], "--password-stdin", "--alert-min", "5",
                     "--baseline", env["d"] / "base.json", "--report", env["d"] / "b2.json")
    rep = json.loads((env["d"] / "b2.json").read_text())
    home = next(r for r in rep["domains"] if r["domain"] == "HomeDomain")
    assert home["allowed_removed"] >= 2 * 30
    assert home["verdict"] == "churn"



# --------------------------------------------------------------------------- reset detectors and benign classes
import sqlite3  # noqa: E402


def sqlite_bytes(tmp: Path, rows: int, blob: int = 0) -> bytes:
    """A real SQLite file with `rows` rows (optionally padded with random blobs to make it large)."""
    f = tmp / f"db-{rows}-{blob}-{time.time_ns()}.sqlite"
    c = sqlite3.connect(f)
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v BLOB)")
    c.executemany("INSERT INTO t (v) VALUES (?)", [(os.urandom(blob) if blob else b"x",) for _ in range(rows)])
    c.commit()
    c.execute("VACUUM")
    c.close()
    data = f.read_bytes()
    f.unlink()
    return data


def replace_file(ms: bp.ManifestSession, dev: Path, domain: str, rel: str, data: bytes, inode: int) -> None:
    old = ms.get(domain, rel)
    if old:
        ms.conn.execute("DELETE FROM Files WHERE fileID=?", (old.file_id,))
    add_file(ms, dev, domain, rel, data, inode=inode, birth=int(time.time()))


def pair(env, name: str, pre_fn, post_fn) -> tuple[Path, Path]:
    """PRE = env pre + pre_fn; POST = that PRE + post_fn (both full encrypted synthetic backups)."""
    out = []
    for side, fn, src in (("pre", pre_fn, env["pre"]), ("post", post_fn, None)):
        dst = env["d"] / f"{name}-{side}" / env["pre"].name
        dst.parent.mkdir(parents=True)
        shutil.copytree(src or out[0], dst)
        bp.make_writable(dst)
        if fn:
            ms = bp.ManifestSession(rw.EncryptedBackup(dst, env["pw"]))
            try:
                fn(ms, dst)
                ms.commit_and_encrypt()
            finally:
                ms.close()
        out.append(dst)
    return out[0], out[1]


def gate(env, pre: Path, post: Path, name: str) -> tuple[int, dict, str]:
    rc, out, err = cli(pre, post, "--password-stdin", "--gate", "--report", env["d"] / f"{name}.json")
    return rc, json.loads((env["d"] / f"{name}.json").read_text()), out + err


def test_inplace_db_reset_fails_although_no_file_is_removed(env):
    """M2: TCC/sms.db reset in place (same paths, new inode, fewer rows / tiny file) must FAIL on its own."""
    tmp = env["d"]
    big_sms, tcc = sqlite_bytes(tmp, 200, 1000), sqlite_bytes(tmp, 150)

    def pre_fn(ms, dev):
        replace_file(ms, dev, "HomeDomain", "Library/SMS/sms.db", big_sms, 81000)
        replace_file(ms, dev, "HomeDomain", "Library/TCC/TCC.db", tcc, 81001)

    def post_fn(ms, dev):
        replace_file(ms, dev, "HomeDomain", "Library/SMS/sms.db", sqlite_bytes(tmp, 0), 82000)
        replace_file(ms, dev, "HomeDomain", "Library/TCC/TCC.db", sqlite_bytes(tmp, 90), 82001)
    pre, post = pair(env, "inplace", pre_fn, post_fn)
    rc, rep, out = gate(env, pre, post, "inplace")
    assert rc == 1, out
    a = "\n".join(rep["alerts"])
    assert "db tcc: ALERT:rows_dropped" in a and "db messages: ALERT:rows_dropped" in a
    assert "sentinel messages_db: ALERT:collapsed" in a
    assert not [x for x in rep["alerts"] if "files_lost" in x or "wiped" in x]     # nothing was removed
    assert "sms.db" not in out and "TCC.db" not in out
    # the unchanged control of the same pair passes (no false alarm from the probes themselves)
    rc, rep, out = gate(env, pre, pre, "inplace-control")
    assert rc == 0, out


def test_keychain_item_loss_fails(env):
    """M1: keychain-backup.plist lost items (incident: genp -5, keys -4) -> ALERT; more items = ok."""
    def kc(genp, keys, cert):
        return plistlib.dumps({"genp": [{"v_Data": b"x"}] * genp, "inet": [], "cert": [{"v_Data": b"c"}] * cert,
                               "keys": [{"v_Data": b"k"}] * keys}, fmt=plistlib.FMT_BINARY)
    pre, post = pair(env, "kc", lambda ms, d: replace_file(ms, d, "KeychainDomain", "keychain-backup.plist",
                                                           kc(10, 4, 1), 83000),
                     lambda ms, d: replace_file(ms, d, "KeychainDomain", "keychain-backup.plist", kc(9, 4, 3), 83001))
    rc, rep, out = gate(env, pre, post, "kc")
    assert rc == 1, out
    assert rep["keychain_items"]["verdict"] == "ALERT:items_lost"
    assert any(x.startswith("keychain_items: ALERT:items_lost (genp 10 -> 9)") for x in rep["alerts"])
    rc, rep, out = gate(env, post, pre, "kc-more")       # 9 -> 10 genp, cert 3 -> 1 (cert not alerting)
    assert rc == 0 and rep["keychain_items"]["verdict"] == "ok", out


def test_small_areas_and_keyboard_are_watched(env):
    """M2/m1: Wallet passes (2 files), keyboard model (3 files) were below every threshold before."""
    def pre_fn(ms, dev):
        for i, rel in enumerate(("Library/Passes/a.pkpass/pass.json", "Library/Passes/b.pkpass/pass.json")):
            add_file(ms, dev, "HomeDomain", rel, b"p" * 300, inode=84000 + i, birth=1790000000)
        for i, n in enumerate(("dynamic-lexicon.dat", "lm.dat", "user_model_database.sqlite")):
            add_file(ms, dev, "KeyboardDomain", f"Library/Keyboard/{n}", os.urandom(70000 if i == 2 else 300),
                     inode=84100 + i, birth=1790000000)

    def post_fn(ms, dev):
        ms.conn.execute("DELETE FROM Files WHERE domain='HomeDomain' AND relativePath LIKE 'Library/Passes/%'")
        ms.conn.execute("DELETE FROM Files WHERE domain='KeyboardDomain' AND relativePath NOT LIKE '%user_model%'")
        replace_file(ms, dev, "KeyboardDomain", "Library/Keyboard/user_model_database.sqlite", b"\0" * 4096, 84200)
    pre, post = pair(env, "small", pre_fn, post_fn)
    rc, rep, out = gate(env, pre, post, "small")
    assert rc == 1, out
    a = "\n".join(rep["alerts"])
    assert "HomeDomain: ALERT:area_wiped" in a and "Library/Passes" in a
    assert "sentinel wallet_passes: ALERT:wiped" in a
    assert "sentinel keyboard: ALERT:files_lost" in a


def test_collapse_threshold(env):
    """M2: > --collapse-max files of a system domain shrinking from > 16 KiB to < 50 % -> ALERT; up to the
    threshold (control real2->real3 had 1 per domain) -> no alert."""
    names = [f"Library/Caches/blob{i}.bin" for i in range(5)]

    def pre_fn(ms, dev):
        for i, rel in enumerate(names):
            add_file(ms, dev, "HomeDomain", rel, os.urandom(40000), inode=85000 + i, birth=1790000000)

    def shrink(k):
        def fn(ms, dev):
            for i, rel in enumerate(names[:k]):
                replace_file(ms, dev, "HomeDomain", rel, os.urandom(1000), 85100 + i)
        return fn
    pre, post = pair(env, "coll3", pre_fn, shrink(3))
    rc, rep, out = gate(env, pre, post, "coll3")
    assert rc == 0, out
    pre, post = pair(env, "coll4", pre_fn, shrink(4))
    rc, rep, out = gate(env, pre, post, "coll4")
    assert rc == 1 and any("HomeDomain: ALERT:collapse (4 files" in x for x in rep["alerts"]), out


PB_REL = "Library/Preferences/com.apple.purplebuddy.plist"
PB_BASE = {"SetupDone": True, "SetupState": "SetupUsingAssistant", "SetupLastExit": "x", "Name": "Kassiopeia"}
PB_CANARY_PRE = PB_BASE | {"Locale": "en_US", "GuessedCountry": "US", "chronicle": {"n": 1},
                           "lastPrepareLaunchSentinel": 1}


@pytest.mark.parametrize("pre_purple,post_purple,cls,rc", [
    # Apple-account-only re-run, SetupDone true before and after -> PASS with note (canary 2026-10-01)
    (None, PB_BASE | {"SetupLastExit": "z", "AppleIDPB10Presented": True}, "apple_account_rerun", 0),
    # the real canary pattern: SetupLastExit + Locale REGION modifier + GuessedCountry + buddy bookkeeping
    (PB_CANARY_PRE, PB_CANARY_PRE | {"SetupLastExit": "z", "Locale": "en_US@rg=chzzzz", "GuessedCountry": "CH",
                                     "chronicle": {"n": 2}, "lastPrepareLaunchSentinel": 2}, "apple_account_rerun", 0),
    # same, but the locale BASE changed = language/region reset -> FAIL
    (PB_CANARY_PRE, PB_CANARY_PRE | {"SetupLastExit": "z", "Locale": "de_CH", "GuessedCountry": "CH"},
     "setup_reset", 1),
    # a same-length value change is invisible in the sanitized view ('<str 2>' both sides) -> raw compare catches it
    (PB_BASE | {"Language": "en"}, PB_BASE | {"Language": "de", "SetupLastExit": "z"}, "setup_reset", 1),
    # no SetupDone evidence -> still an alert (operator confirmation, runbook E.4 / migrate.sh exit 3)
    ({"SetupState": "SetupUsingAssistant", "SetupLastExit": "x"}, {"SetupState": "SetupUsingAssistant",
                                                                   "SetupLastExit": "z"}, "apple_account_rerun", 1),
    (None, PB_BASE | {"SetupDone": False, "SetupLastExit": "z"}, "setup_reset", 1),
    (None, PB_BASE | {"SetupState": "RestoredFromiTunesBackup", "SetupLastExit": "z"}, "restore_state", 1),
    (None, PB_BASE | {"SetupLastExit": "z", "UserChoseLanguage": True}, "setup_reset", 1),
])
def test_purplebuddy_class(env, pre_purple, post_purple, cls, rc):
    """M7 + canary 2026-10-01: tell an Apple-account-only re-run (SetupDone stays true) from a setup reset."""
    def put(obj, inode):
        return lambda ms, dev: replace_file(ms, dev, "HomeDomain", PB_REL, plistlib.dumps(obj, fmt=plistlib.FMT_BINARY),
                                            inode)
    name = f"pb-{cls}-{rc}-{abs(hash(json.dumps([pre_purple, post_purple], sort_keys=True))) % 10**8}"
    pre, post = pair(env, name, put(pre_purple, 85900) if pre_purple else None, put(post_purple, 86000))
    got_rc, rep, out = gate(env, pre, post, name)
    assert got_rc == rc and rep["purplebuddy"]["class"] == cls, out
    assert rep["alerts"] == [a for a in rep["alerts"] if a.startswith("purplebuddy: class=" + cls)]
    if rc == 0:
        assert rep["gate"] == "PASS" and [n["class"] for n in rep["notes"]] == ["apple_account_rerun"]
        assert rep["purplebuddy"]["setup_done_before_and_after"] is True
    else:
        assert len(rep["alerts"]) == 1 and not rep["notes"]
    for secret in ("Maria", "chzzzz", "en_US", "de_CH"):
        assert secret not in out


# --------------------------------------------------------------------------- canary 2026-10-01: benign classes
POSTER = "AppDomain-com.apple.PosterBoard"
CLOCK = "Library/Application Support/PRBPosterExtensionDataStore/1/Extensions/com.apple.ClockPoster.ClockPosterExtension"


def _uuid(i: int, gen: int) -> str:
    return f"{gen:08X}-0000-4000-8000-{i:012X}"


def test_poster_cache_regeneration_is_benign(env):
    """Canary: PosterBoard removed part of its files (ClockPoster plists) and wrote them again under new UUIDs, no file
    extension fewer -> PASS with note. Fewer files of one extension (as in the incident: fewer plists) or the same pattern
    in a NON-poster domain stays ALERT."""
    other = "AppDomain-com.apple.mobilenotes"

    def pre_fn(ms, dev):
        for dom in (POSTER, other):
            for i in range(30):
                add_file(ms, dev, dom, f"{CLOCK}/{_uuid(i, 1)}/configuration.plist", b"p" * 200, inode=87000 + i,
                         birth=1790000000)
            for i in range(3):
                add_file(ms, dev, dom, f"Library/Wallpaper/{i}.heic", b"h" * 300, inode=87100 + i, birth=1790000000)

    def regen(n_new):
        def fn(ms, dev):
            for dom in (POSTER, other):
                ms.conn.execute("DELETE FROM Files WHERE domain=? AND relativePath LIKE ? AND relativePath LIKE ?",
                                (dom, CLOCK + "/%", "%" + _uuid(0, 1)[:8] + "%"))
                for i in range(n_new):
                    add_file(ms, dev, dom, f"{CLOCK}/{_uuid(i, 2)}/configuration.plist", b"q" * 200,
                             inode=87200 + i, birth=int(time.time()))
        return fn
    pre, post = pair(env, "poster-ok", pre_fn, regen(31))
    rc, rep, out = gate(env, pre, post, "poster-ok")
    assert rc == 1, out                                    # the non-poster domain still alerts ...
    assert rep["alert_ids"] == ["domain:" + other], out    # ... and nothing else does
    assert {(n["id"], n["class"]) for n in rep["notes"]} == {(f"domain:{POSTER}", "poster_cache_regenerated"),
                                                             ("sentinel:wallpapers", "poster_cache_regenerated")}
    dom = next(r for r in rep["domains"] if r["domain"] == POSTER)
    assert dom["removed_files"] == 30 and dom["verdict"] == "note:poster_cache_regenerated"
    assert "configuration.plist" not in out and "plist" not in json.dumps(rep["notes"])
    pre, post = pair(env, "poster-short", pre_fn, regen(29))
    rc, rep, out = gate(env, pre, post, "poster-short")
    assert rc == 1 and f"domain:{POSTER}" in rep["alert_ids"] and "sentinel:wallpapers" in rep["alert_ids"], out
    assert not rep["notes"]


TOOLKIT = "Library/Shortcuts/ToolKit"


def test_shortcuts_catalogue_regeneration_is_benign(env):
    """Canary: the ToolKit tool catalogue Tools-prod.v79-<UUID>.sqlite (+ .lock) was rebuilt under a new UUID while
    Shortcuts.sqlite kept every row -> PASS with note. Losing anything else under Shortcuts/ (incident:
    CascadeBookmarkRegistry, ToolEmbeddingDatabase) or a collapsed/shrunk Shortcuts.sqlite stays ALERT."""
    tmp = env["d"]
    db40 = sqlite_bytes(tmp, 40)

    def pre_fn(ms, dev):
        replace_file(ms, dev, "HomeDomain", "Library/Shortcuts/Shortcuts.sqlite", db40, 88000)
        add_file(ms, dev, "HomeDomain", f"{TOOLKIT}/Tools-prod.v79-{_uuid(1, 1)}.sqlite", os.urandom(70000),
                 inode=88001, birth=1790000000)
        add_file(ms, dev, "HomeDomain", f"{TOOLKIT}/Tools-prod.v79-{_uuid(1, 1)}.sqlite.lock", b"", inode=88002,
                 birth=1790000000)
        add_file(ms, dev, "HomeDomain", f"{TOOLKIT}/CascadeBookmarkRegistry", b"c" * 300, inode=88003,
                 birth=1790000000)

    def post_fn(variant):
        def fn(ms, dev):
            ms.conn.execute("DELETE FROM Files WHERE domain='HomeDomain' AND relativePath LIKE ?",
                            (f"{TOOLKIT}/Tools-prod.v79-%",))
            add_file(ms, dev, "HomeDomain", f"{TOOLKIT}/Tools-prod.v79-{_uuid(1, 2)}.sqlite", os.urandom(69000),
                     inode=88101, birth=int(time.time()))
            add_file(ms, dev, "HomeDomain", f"{TOOLKIT}/Tools-prod.v79-{_uuid(1, 2)}.sqlite.lock", b"", inode=88102,
                     birth=int(time.time()))
            if variant == "registry_lost":
                ms.conn.execute("DELETE FROM Files WHERE domain='HomeDomain' AND relativePath=?",
                                (f"{TOOLKIT}/CascadeBookmarkRegistry",))
            elif variant == "db_reset":
                replace_file(ms, dev, "HomeDomain", "Library/Shortcuts/Shortcuts.sqlite", sqlite_bytes(tmp, 10),
                             88200)
        return fn
    pre, post = pair(env, "sc-ok", pre_fn, post_fn("ok"))
    rc, rep, out = gate(env, pre, post, "sc-ok")
    assert rc == 0, out
    assert [(n["id"], n["class"]) for n in rep["notes"]] == [("sentinel:shortcuts", "shortcuts_catalogue_regenerated")]
    assert {d["id"]: d for d in rep["db_rows"]}["shortcuts"]["pre_rows"] == 40
    assert "Tools-prod" not in out and "Cascade" not in out
    for variant, needle in (("registry_lost", "sentinel:shortcuts"), ("db_reset", "db:shortcuts")):
        pre, post = pair(env, f"sc-{variant}", pre_fn, post_fn(variant))
        rc, rep, out = gate(env, pre, post, f"sc-{variant}")
        assert rc == 1 and needle in rep["alert_ids"] and "sentinel:shortcuts" in rep["alert_ids"], (variant, out)
        assert not rep["notes"], variant


def cal_bytes(tmp: Path, counts: dict[str, int]) -> bytes:
    f = tmp / f"cal-{time.time_ns()}.sqlitedb"
    c = sqlite3.connect(f)
    for t, n in counts.items():
        c.execute(f'CREATE TABLE "{t}" (id INTEGER PRIMARY KEY, v TEXT)')
        c.executemany(f'INSERT INTO "{t}" (v) VALUES (?)', [("x",)] * n)
    c.commit()
    c.close()
    data = f.read_bytes()
    f.unlink()
    return data


def test_calendar_sync_table_drop_is_benign(env):
    """Canary: Calendar.sqlitedb lost > 25 % of its rows, but Store, Calendar and CalendarItem kept theirs (only the
    *Changes / sync tables were rebuilt after the Apple-account sign-in) -> PASS with note. A drop in a key table
    (as in the incident) stays ALERT."""
    tmp = env["d"]
    base = {"Store": 4, "Calendar": 9, "CalendarItem": 150, "CalendarItemChanges": 300, "AlarmChanges": 120}
    rel = "Library/Calendar/Calendar.sqlitedb"
    pre_fn = lambda ms, dev: replace_file(ms, dev, "HomeDomain", rel, cal_bytes(tmp, base), 89000)  # noqa: E731
    pre, post = pair(env, "cal-ok", pre_fn, lambda ms, dev: replace_file(
        ms, dev, "HomeDomain", rel, cal_bytes(tmp, base | {"CalendarItemChanges": 12, "AlarmChanges": 0}), 89001))
    rc, rep, out = gate(env, pre, post, "cal-ok")
    assert rc == 0, out
    cal = {d["id"]: d for d in rep["db_rows"]}["calendar"]
    assert cal["pre_rows"] == 583 and cal["post_rows"] == 175 and cal["verdict"] == "note:sync_tables_only"
    assert cal["key_tables"] == {"Store": [4, 4], "Calendar": [9, 9], "CalendarItem": [150, 150]}
    assert [n["class"] for n in rep["notes"]] == ["calendar_sync_tables"]
    pre, post = pair(env, "cal-bad", pre_fn, lambda ms, dev: replace_file(
        ms, dev, "HomeDomain", rel, cal_bytes(tmp, base | {"CalendarItem": 100, "CalendarItemChanges": 6,
                                                           "AlarmChanges": 0}), 89002))
    rc, rep, out = gate(env, pre, post, "cal-bad")
    assert rc == 1 and rep["alert_ids"] == ["db:calendar"], out
    assert {d["id"]: d for d in rep["db_rows"]}["calendar"]["verdict"] == "ALERT:rows_dropped"


# --------------------------------------------------------------------------- KeyboardDomain
KB_MODEL = "Library/Keyboard/user_model_database.sqlite"


def _kb_pre(ms, dev):
    add_file(ms, dev, "KeyboardDomain", KB_MODEL, os.urandom(200000), inode=89500, birth=1790000000)


def _kb_reset(ms, dev):
    replace_file(ms, dev, "KeyboardDomain", KB_MODEL, b"\0" * 4096, 89600)     # canary: the model collapsed to 4 KiB


def test_keyboard_domain_is_an_identity_domain_of_the_restore_set(env):
    """KeyboardDomain is carried complete by the restore set: with --payload <set> it is judged like an outside
    domain (role identity, sentinel active), so a keyboard reset FAILS the gate."""
    rs = _restoreset(env, "rs-noop")
    pre, post = pair(env, "kb-id", None, _kb_reset_from_fixture)
    rc, out, err = cli(pre, post, "--password-stdin", "--payload", rs, "--gate",
                       "--report", env["d"] / "kb-id.json")
    rep = json.loads((env["d"] / "kb-id.json").read_text())
    assert rc == 1, out + err
    assert rep["identity_domains"]["KeyboardDomain"]["different"] == 0
    assert {r["domain"]: r["role"] for r in rep["domains"]}["KeyboardDomain"] == "identity"
    assert "sentinel:keyboard" in rep["alert_ids"]


def _kb_reset_from_fixture(ms, dev):
    """both keyboard files of the fixture gone (incident pattern: emoji_adaptation.db + langlikelihood.dat removed)"""
    ms.conn.execute("DELETE FROM Files WHERE domain='KeyboardDomain' AND flags=1")
