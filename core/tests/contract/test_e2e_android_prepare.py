# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Fixture E2E of the Android/prepare path (DESIGN §13.1 levels 3, 6, 8; owner: coreA), with the real engine as the app
starts it (one `python -m tmcore` process per command, secrets as one stdin line) and synthetic data only:

    android-inspect -> android-normalize -> [PRE backup] -> prepare -> session-status -> diag-report -> cleanup

The PRE backup is written by fixtures/gen_ios_backup.py into ios/pre/<UDID>/ and announced in engine.json exactly as
`backup --role pre` does (devtools/CONTRACT-REQUESTS.md "session paths"); the device part itself is coreB's E2E.
Every process runs under `sandbox-exec (deny network*)` when macOS has it, and its stdout must be a valid event
stream. Afterwards the canary scan (DESIGN §10.2 point 4) looks for every canary value in events, debug log, reports,
session.json/engine.json and the diagnostic report (UTF-8, UTF-16, Base64); the password must not appear in ANY file
of the session or its temporary folder.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from tests import support
from tests.support import android_canary, canary
from tmcore import __version__
from tmcore.session import Session

SCHEMA = support.CORE / "schema"
CATALOG = json.loads((SCHEMA / "codes.v1.json").read_text())["codes"]
X_PHASES = json.loads((SCHEMA / "events.v1.json").read_text())["x-phases"]
SANDBOX = Path("/usr/bin/sandbox-exec")
NO_NETWORK = "(version 1) (allow default) (deny network*)"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Core Data importer needs macOS")


# ------------------------------------------------------------------------------------------------ helpers
@pytest.fixture(scope="module")
def schemas():
    reg = Registry()
    loaded = {}
    for name in ("events.v1.json", "session.v1.json", "report.v1.json", "compat.v1.json"):
        s = json.loads((SCHEMA / name).read_text())
        loaded[name] = s
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
    ev = jsonschema.Draft202012Validator(loaded["events.v1.json"], registry=reg)
    rep = jsonschema.Draft202012Validator(loaded["report.v1.json"], registry=reg)
    eng = jsonschema.Draft202012Validator({"$ref": "urn:threema-chat-transfer:schema:session:v1#/$defs/engine"}, registry=reg)
    return {"events": ev, "report": rep, "engine": eng}


@pytest.fixture(scope="module")
def importer():
    return support.importer()


class App:
    """What the app does around one tmcore process (DESIGN §5.1, §5.8): fixed environment, secrets on stdin, stdout
    copied to logs/events.jsonl, stderr to logs/debug.log."""

    def __init__(self, session: Session, importer: Path, schemas: dict):
        self.s = session
        self.importer = importer
        self.schemas = schemas
        self.outputs: list[bytes] = []

    def run(self, cmd: str, *args, secrets: dict | None = None) -> dict:
        argv = [sys.executable, "-E", "-s", "-B", "-m", "tmcore", cmd, "--session", str(self.s.root), *map(str, args)]
        stdin = b""
        if secrets is not None:
            argv.append("--secrets-stdin")
            stdin = json.dumps(secrets).encode() + b"\n"
        if SANDBOX.exists():
            argv = [str(SANDBOX), "-p", NO_NETWORK, *argv]
        env = support.tmcore_env(TMCORE_IMPORTER=str(self.importer), TMPDIR=str(self.s.tmp))
        p = subprocess.run(argv, cwd=support.CORE, input=stdin, capture_output=True, env=env, timeout=110)
        self.outputs += [p.stdout, p.stderr]
        with open(self.s.path("logs/events.jsonl"), "ab") as f:
            f.write(p.stdout)
        with open(self.s.path("logs/debug.log"), "ab") as f:
            f.write(p.stderr)
        evs = [json.loads(line) for line in p.stdout.decode("utf-8").splitlines()]
        validate_stream(self.schemas["events"], cmd, evs, p.returncode)
        res = evs[-1]
        res["_events"] = evs
        res["_stderr"] = p.stderr.decode("utf-8", "replace")
        return res

    def engine(self) -> dict:
        st = json.loads(self.s.path("engine.json").read_text())
        errs = list(self.schemas["engine"].iter_errors(st))
        assert not errs, jsonschema.exceptions.best_match(errs).message
        return st


def validate_stream(validator, cmd: str, evs: list[dict], rc: int) -> None:
    assert evs and evs[0]["type"] == "hello" and evs[0]["engine_version"] == __version__
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert [e["type"] for e in evs].count("result") == 1 and evs[-1]["type"] == "result"
    depth = 0
    for e in evs:
        errs = list(validator.iter_errors(e))
        assert not errs, (e["type"], jsonschema.exceptions.best_match(errs).message)
        assert e["cmd"] == cmd
        if e["type"] in ("phase", "progress"):
            assert e["phase"] in X_PHASES[cmd], (cmd, e["phase"])
        for key in ("code", "reason"):
            if isinstance(e.get(key), str):
                assert e[key] in CATALOG, e[key]
        if e["type"] == "critical":
            depth += 1 if e["on"] else -1
            assert depth in (0, 1)
    assert depth == 0
    res = evs[-1]
    assert rc == (0 if res["ok"] else CATALOG[res["code"]]["exit"])


def _iso(t: _dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _freeze(root: Path) -> None:
    for dirpath, _d, files in os.walk(root, topdown=False):
        for fn in files:
            p = os.path.join(dirpath, fn)
            os.chmod(p, stat.S_IMODE(os.lstat(p).st_mode) & ~0o222)
        os.chmod(dirpath, stat.S_IMODE(os.stat(dirpath).st_mode) & ~0o222)


def write_pre_backup(session: Session, password: str, *, udid: str, device_name: str = "Fixture iPhone",
                     serial: str = "FAKESERIAL01", phone: str | None = None, store_spec: dict | None = None,
                     age_min: int = 0) -> str:
    """The PRE backup as coreB's `backup --role pre` leaves it: ios/pre/<UDID>/ (frozen) + engine.json pre."""
    g = support.ios_fixture()
    spec = store_spec or {"own": "ZZFIXN01", "contacts": [{"identity": "ZZFIXR01", "publicKey": "5b" * 32,
                                                           "firstName": "Fixture"}],
                          "groups": [], "oneToOne": ["ZZFIXR01"],
                          "messages": [{"chat": "contact:ZZFIXR01", "id": "5b5b5b5b00000001", "text": "fixture text",
                                        "dateMs": 1788000000000, "isOwn": False}]}
    img = g.build_image(variant="store", store_dir=g.v56_store(spec))
    info = g.write_backup(img, session.path("ios/pre"), password, udid=udid, device_name=device_name, serial=serial,
                          phone=phone, build="24A437")
    _freeze(Path(info["device_dir"]))
    finished = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=age_min)
    backup_id = str(uuid.uuid4())
    with session.update_engine() as st:
        st["pre"] = {"backup_id": backup_id, "finished_at": _iso(finished),
                     "fresh_until": _iso(finished + _dt.timedelta(minutes=60)), "ios_build": "24A437",
                     "password_ok": True, "frozen": True}
        st["device"] = session.hasher.h(udid)
        Session.advance_phase(st, "pre_backup_done")
    return backup_id


def _writable_rm(p: Path) -> None:
    for dirpath, dirs, files in os.walk(p):
        os.chmod(dirpath, 0o700)
        for fn in files:
            fp = os.path.join(dirpath, fn)
            if not os.path.islink(fp):
                os.chmod(fp, 0o600)
    shutil.rmtree(p, ignore_errors=True)


@pytest.fixture()
def session(tmp_path):
    s = Session.create(tmp_path / "sessions")
    yield s
    _writable_rm(tmp_path / "sessions")


# ------------------------------------------------------------------------------------------------ tests
def test_two_android_backups_to_a_frozen_restore_set(tmp_path, session, importer, schemas):
    """S05 combination 'text from the newest, media from the older backup' -> S06 counts -> S13 -> S14."""
    pytest.importorskip("PIL", reason="fixtures/gen_android_backup.py renders its media with Pillow")
    if not shutil.which("ffmpeg"):
        pytest.skip("fixtures/gen_android_backup.py needs ffmpeg for its video/audio media")
    fx = tmp_path / "android"
    p = subprocess.run([sys.executable, str(support.REPO / "fixtures" / "gen_android_backup.py"), "--out", str(fx),
                        "--split"], capture_output=True, text=True, timeout=110)
    assert p.returncode == 0, p.stderr[-2000:]
    text, media = fx / "fixture-text-backup.zip", fx / "fixture-media-backup.zip"
    pw = support.canaries()["password"]
    app = App(session, importer, schemas)

    res = app.run("android-inspect", text, media)
    assert res["ok"] and res["data"]["plan"] == "text_plus_media"
    assert res["data"]["text_ref"] == 0 and res["data"]["media_refs"] == [1]

    plan = json.dumps({"text_ref": res["data"]["text_ref"], "media_refs": res["data"]["media_refs"]})
    wrong = app.run("android-normalize", "--plan", plan, text, media,
                    secrets={"android_passwords": {"0": pw, "1": pw + "x"}})
    assert wrong["code"] == "E_ANDROID_PASSWORD" and wrong["data"]["ref"] == 1 and wrong["retryable"]

    res = app.run("android-normalize", "--plan", plan, text, media, secrets={"android_passwords": {"0": pw, "1": pw}})
    assert res["ok"], res
    d = res["data"]
    assert d["messages"] > 0 and d["chats"] > 0 and 0 < d["media_present"] <= d["media_total"]
    assert d["own_id"].startswith("h:")
    notes = {e["code"] for e in res["_events"] if e["type"] == "note"}
    assert ("W_ANDROID_MEDIA_PARTIAL" in notes) == (d["media_present"] < d["media_total"])
    st = app.engine()
    assert st["phase"] == "android_done" and st["android"]["plan"] == "text_plus_media"
    assert app.run("session-status")["data"]["resume_at"] == "S07"

    write_pre_backup(session, pw, udid="00008150-ZZFAKEUDID000003")
    assert app.run("prepare")["code"] == "E_SECRETS_MISSING"
    res = app.run("prepare", secrets={"backup_password": pw})
    assert res["ok"], (res, res["_stderr"][-3000:])
    phases = [e["phase"] for e in res["_events"] if e["type"] == "phase"]
    assert phases == X_PHASES["prepare"]
    checks = {e["id"]: e["status"] for e in res["_events"] if e["type"] == "check"}
    for cid in ("freshness", "extract", "threema_model", "duplicate_chat", "import", "coredata_open", "verify_import",
                "restoreset", "verify_restoreset", "freeze"):
        assert checks[cid] == "pass", cid
    d = res["data"]
    assert d["messages"] > 0 and d["payload_bytes"] > 0 and d["iphone_required_bytes"] >= 1.5 * d["payload_bytes"]

    st = app.engine()
    assert st["phase"] == "prepared" and st["prepared"]["pre_backup_id"] == st["pre"]["backup_id"]
    set_dev = session.path("work/restoreset/00008150-ZZFAKEUDID000003")
    marker = set_dev / ".RESTORESET_OK"
    assert marker.read_text().strip() == st["prepared"]["set_sha256"]
    for dirpath, _dirs, files in os.walk(set_dev):              # frozen: chmod -R a-w
        assert not os.stat(dirpath).st_mode & 0o222
        for fn in files:
            fp = os.path.join(dirpath, fn)
            assert os.path.islink(fp) or not os.lstat(fp).st_mode & 0o222
    assert app.run("session-status")["data"]["resume_at"] == "S14"

    for rep in sorted(session.path("reports").glob("*.json")):  # counts only, schema report.v1
        errs = list(schemas["report"].iter_errors(json.loads(rep.read_text())))
        assert not errs, (rep.name, jsonschema.exceptions.best_match(errs).message)

    diag = app.run("diag-report")
    assert diag["ok"] and diag["data"]["file"].startswith("diag/")
    doc = json.loads(session.path(diag["data"]["file"]).read_text())
    errs = list(schemas["report"].iter_errors(doc))
    assert not errs, jsonschema.exceptions.best_match(errs).message
    own_hash = st["android"]["own_id"]
    assert own_hash not in json.dumps(doc)                       # fresh salt per report

    res = app.run("cleanup", "--what", "work")
    assert res["ok"] and res["data"]["freed_bytes"] > 0
    assert not any(session.path("work/restoreset").iterdir()) and not session.path("android/normalized.sqlite").exists()
    st = app.engine()
    assert st["prepared"] is None and st.get("android") is None and st["phase"] == "pre_backup_done"
    assert app.run("prepare", secrets={"backup_password": pw})["code"] == "E_PROTOCOL"   # Android part is gone


def test_prepare_refusals_keep_the_iphone_untouched(tmp_path, session, importer, schemas):
    pw = support.canaries()["password"]
    app = App(session, importer, schemas)
    backup = tmp_path / "threema-backup_1788000000000_1"
    android_canary.build(backup)
    assert app.run("android-normalize", "--plan", '{"text_ref":0,"media_refs":[]}', backup,
                   secrets={"android_passwords": {"0": pw}})["ok"]
    assert app.run("prepare", secrets={"backup_password": pw})["data"]["sub"] == "no_pre_backup"

    write_pre_backup(session, pw, udid="00008150-ZZFAKEUDID000004", age_min=61)        # older than 60 min
    res = app.run("prepare", secrets={"backup_password": pw})
    assert res["code"] == "E_GUARD_FRESHNESS" and res["device_modified"] == "no"
    assert CATALOG["E_GUARD_FRESHNESS"]["needs_new_backup"]
    assert app.run("session-status")["data"]["resume_at"] == "S11"

    _writable_rm(session.path("ios/pre"))
    session.path("ios/pre").mkdir(mode=0o700)
    write_pre_backup(session, pw, udid="00008150-ZZFAKEUDID000004")
    res = app.run("prepare", secrets={"backup_password": pw + "-wrong"})
    assert res["code"] == "E_BACKUP_PASSWORD" and not res["ok"]
    assert app.engine()["prepared"] is None
    assert not any(session.path("work/restoreset").iterdir())


def test_canaries_never_leave_the_session_data(tmp_path, session, importer, schemas):
    """DESIGN §10.2 point 4: 0 hits in events, debug log, reports, session.json/engine.json, diagnostic report;
    allowed: the fixture inputs and android/missing-senders.json. The password: nowhere in the session at all."""
    c = support.canaries()
    values = support.canary_values()
    pw = c["password"]
    app = App(session, importer, schemas)
    backup = tmp_path / "input" / "threema-backup_1788000000000_1"
    backup.parent.mkdir()
    built = android_canary.build(backup)

    res = app.run("android-inspect", backup)
    assert res["ok"] and res["data"]["plan"] == "single"
    res = app.run("android-normalize", "--plan", '{"text_ref":0,"media_refs":[]}', backup,
                  secrets={"android_passwords": {"0": pw}})
    assert res["ok"] and res["data"]["missing_key_senders"] == 1
    assert res["data"]["missing_senders_file"] == "android/missing-senders.json"
    missing = json.loads(session.path("android/missing-senders.json").read_text())
    assert missing["identities"] == built["missing"]
    assert stat.S_IMODE(session.path("android/missing-senders.json").stat().st_mode) == 0o600

    spec = {"own": built["own"],
            "contacts": [{"identity": c["threema_id"], "publicKey": "71" * 32, "firstName": c["contact_first_name"],
                          "lastName": c["contact_last_name"]}],
            "groups": [], "oneToOne": [c["threema_id"]],
            "messages": [{"chat": f"contact:{c['threema_id']}", "id": "7a7a7a7a00000001", "text": c["message_text"],
                          "dateMs": 1787000000000, "isOwn": False}]}
    write_pre_backup(session, pw, udid=values["udid"], device_name=c["device_name"], serial=c["serial"],
                     phone=c["phone"], store_spec=spec)
    res = app.run("prepare", secrets={"backup_password": pw})
    assert res["ok"], (res, res["_stderr"][-3000:])
    assert app.run("session-status")["ok"]
    assert app.run("diag-report")["ok"]

    # 1) everything the engine wrote to the app: stdout + stderr of every process
    for i, out in enumerate(app.outputs):
        assert canary.scan_bytes(out, values) == [], f"process output #{i}"
    # 2) events copy, debug log, reports, session.json, engine.json, diagnostic reports
    hits = canary.scan_tree(session.root, values,
                            only=("logs", "reports", "diag", "session.json", "engine.json"))
    assert hits == []
    # 3) the password: in no file of the session (also not in normalized.sqlite, work/, tmp/) and no password file
    pw_hits = canary.scan_tree(session.root, {"password": pw})
    assert pw_hits == []
    names = [f.lower() for _d, _s, files in os.walk(session.root) for f in files]
    assert not any("password" in n or n.endswith((".pw", ".pass")) for n in names)
    # 4) the canary IDs and names are really in the data (the scan would otherwise prove nothing)
    assert canary.scan_bytes(session.path("android/normalized.sqlite").read_bytes(),
                             {"threema_id": c["threema_id"], "contact_first_name": c["contact_first_name"]})
