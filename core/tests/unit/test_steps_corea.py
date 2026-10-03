# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Steps of coreA in-process (owner: coreA): host-check decisions, the resume table of session-status (DESIGN §8.1),
cleanup refusals, diag-report re-salting and filtering, android-inspect plans and the android-normalize error mapping.
The probes of host-check are replaced; nothing reads the real machine, nothing touches a device.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import shutil
from pathlib import Path

import pytest

from tests import support
from tests.support import android_canary
from tmcore import protocol as P
from tmcore.cli import Context, resources_dir
from tmcore.protocol import EngineError
from tmcore.secrets import Secrets
from tmcore.session import Session
from tmcore.steps import android, cleanup, diag, host, status

CANARY = support.canaries()
UTC = _dt.timezone.utc


def make_ctx(cmd: str, session: Session | None = None, secrets: dict | None = None, **args) -> Context:
    buf = io.StringIO()
    proto = P.start(cmd, out=buf)
    ctx = Context(cmd=cmd, args=argparse.Namespace(**args), proto=proto, resources=resources_dir(), session=session,
                  secrets=Secrets(secrets or {}))
    ctx._buf = buf          # type: ignore[attr-defined]
    return ctx


def events(ctx) -> list[dict]:
    return [json.loads(line) for line in ctx._buf.getvalue().splitlines()]


def iso(t: _dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


# ------------------------------------------------------------------------------------------------ host-check
@pytest.fixture()
def probes(monkeypatch):
    state = {"mac": "15.4", "arch": "arm64", "fs": "apfs", "free": 200 * 10**9, "power": ("ac", None), "fv": True}
    monkeypatch.setattr(host, "macos_version", lambda: state["mac"])
    monkeypatch.setattr(host, "arch", lambda: state["arch"])
    monkeypatch.setattr(host, "fs_type", lambda p: state["fs"])
    monkeypatch.setattr(host, "free_bytes", lambda p: state["free"])
    monkeypatch.setattr(host, "power", lambda: state["power"])
    monkeypatch.setattr(host, "filevault", lambda: state["fv"])
    return state


def test_host_check_all_green(probes, tmp_path):
    ctx = make_ctx("host-check", workdir=str(tmp_path / "not" / "yet"))
    res = host.host_check(ctx)
    assert res.data["macos"] == "15.4" and res.data["fs"] == "apfs" and res.data["filevault"] is True
    checks = [(e["id"], e["status"]) for e in events(ctx) if e["type"] == "check"]
    assert checks == [("macos", "pass"), ("arch", "pass"), ("fs_apfs", "pass"), ("free_space", "pass"),
                      ("power", "pass"), ("filevault", "pass")]


@pytest.mark.parametrize("change,code", [
    ({"mac": "13.6"}, "E_HOST_MACOS_OLD"),
    ({"arch": "x86_64"}, "E_HOST_ARCH"),
    ({"fs": "exfat"}, "E_HOST_FS_NOT_APFS"),
    ({"free": 10 * 10**9}, "E_HOST_SPACE"),
    ({"power": ("battery", 80)}, "E_HOST_POWER"),
])
def test_host_check_refusals(probes, tmp_path, change, code):
    probes.update(change)
    ctx = make_ctx("host-check", workdir=str(tmp_path))
    with pytest.raises(EngineError) as e:
        host.host_check(ctx)
    assert e.value.code == code
    assert len([ev for ev in events(ctx) if ev["type"] == "check"]) == 6       # every row is shown first


def test_host_check_first_failure_wins_and_notes(probes, tmp_path):
    probes.update({"fs": "hfs", "power": ("battery", 20), "fv": None, "free": 40 * 10**9})
    ctx = make_ctx("host-check", workdir=str(tmp_path))
    with pytest.raises(EngineError) as e:
        host.host_check(ctx)
    assert e.value.code == "E_HOST_FS_NOT_APFS"
    notes = {ev["code"] for ev in events(ctx) if ev["type"] == "note"}
    assert notes == {"W_FILEVAULT_OFF", "W_LOW_HOST_SPACE_MARGIN"}


def test_host_check_fake_device_reports_the_virtual_mac(probes, tmp_path):
    """--fake-device: a fixed virtual Mac (recordings, demo screenshots); the real machine is never read."""
    probes.update({"mac": "13.6", "arch": "x86_64", "fs": "exfat", "free": 1, "power": ("battery", 5), "fv": False})
    ctx = make_ctx("host-check", workdir=str(tmp_path))
    ctx.fake_device = "happy"
    res = host.host_check(ctx)
    assert res.data == {"macos": "15.1", "arch": "arm64", "fs": "apfs", "free_bytes": 220_000_000_000,
                        "need_bytes": host.NEED_BYTES, "power": "ac", "battery_pct": None, "filevault": True}
    assert all(e["status"] == "pass" for e in events(ctx) if e["type"] == "check")


def test_host_probes_fail_soft(tmp_path):
    assert host.fs_type(tmp_path / "missing") in ("apfs", "other", "hfs")
    assert host.free_bytes(tmp_path / "missing") == 0
    assert host._version_tuple("14.0") >= host.MIN_MACOS > host._version_tuple("13.7.1")


# ------------------------------------------------------------------------------------------------ session-status
NOW = _dt.datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def st(**kw) -> dict:
    base = {"phase": "new", "pre": None, "prepared": None, "restore_sent_at": None, "postcheck": None}
    base.update(kw)
    return base


PRE_OPEN = {"backup_id": "b1", "finished_at": iso(NOW - _dt.timedelta(minutes=10)),
            "fresh_until": iso(NOW + _dt.timedelta(minutes=50))}
PRE_EXPIRED = {"backup_id": "b1", "finished_at": iso(NOW - _dt.timedelta(minutes=70)),
               "fresh_until": iso(NOW - _dt.timedelta(minutes=10))}


@pytest.mark.parametrize("state,screen", [
    (st(), "S00"),
    (st(phase="android_done", android={"own_id": "h:00000000"}), "S07"),
    (st(phase="pre_backup_done", android={}, pre=PRE_OPEN), "S13"),
    (st(phase="prepared", pre=PRE_OPEN, prepared={"pre_backup_id": "b1", "fresh_until": PRE_OPEN["fresh_until"]}),
     "S14"),
    (st(phase="prepared", pre=PRE_OPEN, prepared={"pre_backup_id": "old", "fresh_until": PRE_OPEN["fresh_until"]}),
     "S13"),
    (st(phase="pre_backup_done", pre=PRE_EXPIRED), "S11"),
    (st(phase="prepared", pre=PRE_EXPIRED, prepared={"pre_backup_id": "b1", "fresh_until": PRE_EXPIRED["fresh_until"]}),
     "S11"),
    # restore sent (also: the result is missing after critical) and no postcheck: always S16, never start over
    (st(phase="restore_sent", pre=PRE_EXPIRED, restore_sent_at=iso(NOW)), "S16"),
    (st(phase="post_backup_done", restore_sent_at=iso(NOW), postcheck={"verdict": "needs_answer", "at": iso(NOW)}),
     "S16"),
    (st(phase="postcheck_done", restore_sent_at=iso(NOW - _dt.timedelta(hours=1)),
        postcheck={"verdict": "ok", "at": iso(NOW)}), "S20"),
    (st(phase="postcheck_done", restore_sent_at=iso(NOW - _dt.timedelta(hours=1)),
        postcheck={"verdict": "ok_with_notes", "at": iso(NOW)}), "S20"),
    (st(phase="postcheck_done", restore_sent_at=iso(NOW - _dt.timedelta(hours=1)),
        postcheck={"verdict": "threema_only", "at": iso(NOW)}), "S21"),
    (st(phase="postcheck_done", restore_sent_at=iso(NOW - _dt.timedelta(hours=1)),
        postcheck={"verdict": "data_keychain", "at": iso(NOW)}), "S21"),
    # a rollback was sent after a red postcheck: the old verdict is older than the new send -> S16 again
    (st(phase="rollback_sent", restore_sent_at=iso(NOW), postcheck={"verdict": "threema_only",
                                                                    "at": iso(NOW - _dt.timedelta(minutes=30))}),
     "S16"),
])
def test_resume_table(state, screen):
    assert status.resume_screen(state, now=NOW) == screen


def test_session_status_reads_engine_json_only(tmp_path):
    s = Session.create(tmp_path)
    with s.update_engine() as w:
        w["pre"] = PRE_OPEN
        w["phase"] = "pre_backup_done"
    ctx = make_ctx("session-status", session=s)
    res = status.session_status(ctx)
    assert res.data["phase"] == "pre_backup_done" and res.data["fresh_until"] == PRE_OPEN["fresh_until"]
    P.check_safe(res.data)


# ------------------------------------------------------------------------------------------------ cleanup
def _sess_with_data(tmp_path) -> Session:
    s = Session.create(tmp_path)
    for key in ("android/normalized.sqlite", "work/store_out/x", "ios/pre/DEV/Manifest.db", "ios/post/DEV/a",
                "diag/diag-1.json"):
        p = s.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 100)
    frozen = s.path("work/restoreset/DEV")
    frozen.mkdir(parents=True)
    (frozen / "f").write_bytes(b"y" * 50)
    os.chmod(frozen / "f", 0o444)
    os.chmod(frozen, 0o555)
    return s


def test_cleanup_work_removes_readable_copies_and_keeps_backups(tmp_path):
    s = _sess_with_data(tmp_path)
    with s.update_engine() as w:
        w.update(phase="prepared", android={"normalized_at": iso(NOW), "own_id": "h:00000000"}, pre=PRE_OPEN,
                 prepared={"pre_backup_id": "b1"})
    res = cleanup.cleanup(make_ctx("cleanup", session=s, what="work"))
    assert res.data == {"freed_bytes": 250, "what": "work"}
    assert not s.path("android/normalized.sqlite").exists() and s.path("ios/pre/DEV/Manifest.db").exists()
    assert s.path("work/tmp").is_dir() and s.path("work/restoreset").is_dir()
    w = s.engine_state()
    assert w["phase"] == "pre_backup_done" and w["prepared"] is None and "android" not in w


def test_cleanup_is_refused_while_the_data_is_still_needed(tmp_path):
    s = _sess_with_data(tmp_path)
    with s.update_engine() as w:
        w.update(phase="restore_sent", restore_sent_at=iso(_dt.datetime.now(UTC)))
    for what in ("work", "pre", "post", "all"):
        with pytest.raises(EngineError) as e:
            cleanup.cleanup(make_ctx("cleanup", session=s, what=what))
        assert e.value.data["sub"] == "cleanup_blocked"
    now = _dt.datetime.now(UTC)
    with s.update_engine() as w:                                 # R1 still possible: keep PRE, work may go
        w.update(phase="postcheck_done", pre={"backup_id": "b1", "finished_at": iso(now - _dt.timedelta(hours=1))},
                 postcheck={"verdict": "threema_only", "at": iso(now)}, rollback_used=False)
    with pytest.raises(EngineError):
        cleanup.cleanup(make_ctx("cleanup", session=s, what="pre"))
    assert cleanup.cleanup(make_ctx("cleanup", session=s, what="work")).data["freed_bytes"] > 0
    with s.update_engine() as w:
        w["rollback_used"] = True
    assert cleanup.cleanup(make_ctx("cleanup", session=s, what="all")).data["what"] == "all"
    assert s.engine_state()["phase"] == "postcheck_done"         # after a restore the phase never falls back


# ------------------------------------------------------------------------------------------------ diag-report
def test_diag_report_resalts_and_drops_unsafe_values(tmp_path):
    s = Session.create(tmp_path)
    dev = s.hasher.h("00008150-ZZFAKEUDID000009")
    with s.update_engine() as w:
        w.update(phase="prepared", device=dev, pre={"backup_id": "b1", "finished_at": iso(NOW), "ios_build": "24A437",
                                                    "ios_version": "27.0"},
                 history=[{"cmd": "prepare", "at": iso(NOW), "code": "R_OK", "exit": 0}])
    good = {"v": 1, "seq": 1, "ts": iso(NOW), "cmd": "device-status", "type": "device", "state": "ready",
            "device": dev}
    bad_name = dict(good, seq=2, state=CANARY["device_name"])
    bad_path = {"v": 1, "seq": 3, "ts": iso(NOW), "cmd": "prepare", "type": "note", "code": "W_USB2_SLOW",
                "data": {"file": "/" + "Users/someone/x"}}
    with open(s.path("logs/events.jsonl"), "w") as f:
        for ev in (good, bad_name, bad_path):
            f.write(json.dumps(ev) + "\n")
        f.write("not json\n")
    s.write_report("prepare", "prepare", "R_OK", counts={"messages": 12345})
    (s.path("reports") / "evil.json").write_text(json.dumps({"schema": "report.v1", "kind": "prepare",
                                                            "data": {"name": CANARY["contact_first_name"]}}))
    (s.path("logs") / "debug.log").write_text(f"{CANARY['email']} {CANARY['password']}\n")
    res = diag.diag_report(make_ctx("diag-report", session=s))
    text = s.path(res.data["file"]).read_text()
    doc = json.loads(text)
    assert doc["counts"]["events"] == 1 and doc["counts"]["events_dropped"] == 3
    assert doc["counts"]["reports"] == 1 and doc["counts"]["reports_dropped"] == 1
    assert dev not in text and doc["events"][0]["device"].startswith("h:")
    assert doc["versions"]["ios_build"] == "24A437" and doc["compat"] in ("unknown", "verified")
    assert doc["codes"] == ["R_OK"]
    for v in (CANARY["device_name"], CANARY["contact_first_name"], CANARY["email"], CANARY["password"], "someone"):
        assert v not in text
    assert oct(s.path(res.data["file"]).stat().st_mode & 0o777) == "0o600"
    second = diag.diag_report(make_ctx("diag-report", session=s))           # same second: a new file anyway
    assert second.data["file"] != res.data["file"] and s.path(res.data["file"]).is_file()
    assert json.loads(s.path(second.data["file"]).read_text())["events"][0]["device"] != doc["events"][0]["device"]


# ------------------------------------------------------------------------------------------------ android
def test_inspect_plans(tmp_path):
    s = Session.create(tmp_path / "s")
    full = tmp_path / "threema-backup_1790000000000_1"
    android_canary.build(full)
    media_only = tmp_path / "threema-backup_1780000000000_1"
    import pyzipper
    with pyzipper.AESZipFile(media_only, "w", encryption=pyzipper.WZ_AES) as z:
        z.setpassword(b"x")
        z.writestr("settings", b'"version","27"\n')
        z.writestr("message_media_00000000-0000-4000-8000-000000000001", b"\xff\xd8\xff")
    incomplete = tmp_path / "INCOMPLETE-threema-backup_1791000000000_1"
    incomplete.write_bytes(b"PK\x03\x04" + b"\0" * 10)
    junk = tmp_path / "notes.txt"
    junk.write_text("hello")

    res = android.inspect(make_ctx("android-inspect", session=s, files=[str(full)]))
    assert res.data["plan"] == "single" and res.data["text_ref"] == 0 and res.data["media_refs"] == []
    res = android.inspect(make_ctx("android-inspect", session=s, files=[str(media_only), str(full)]))
    assert res.data["plan"] == "text_plus_media" and res.data["text_ref"] == 1 and res.data["media_refs"] == [0]
    assert [f["kind"] for f in res.data["files"]] == ["media", "text"]
    with pytest.raises(EngineError) as e:
        android.inspect(make_ctx("android-inspect", session=s, files=[str(media_only)]))
    assert e.value.code == "E_ANDROID_NO_TEXT"
    with pytest.raises(EngineError) as e:
        android.inspect(make_ctx("android-inspect", session=s, files=[str(incomplete), str(junk)]))
    assert e.value.code == "E_ANDROID_INCOMPLETE" and e.value.data["ref"] == 0
    res = android.inspect(make_ctx("android-inspect", session=s, files=[str(junk)]))
    assert res.data["plan"] == "none"
    for f in res.data["files"]:
        P.check_safe(f)


def _normalize(tmp_path, files, plan, passwords):
    s = Session.create(tmp_path / "s")
    ctx = make_ctx("android-normalize", session=s, secrets={"android_passwords": passwords}, files=list(map(str, files)),
                   plan=json.dumps(plan))
    return s, ctx


def test_normalize_error_mapping(tmp_path, monkeypatch):
    pw = CANARY["password"]
    b = tmp_path / "threema-backup_1790000000000_1"
    android_canary.build(b)
    s, ctx = _normalize(tmp_path, [b], {"text_ref": 0, "media_refs": []}, {"0": pw + "-wrong"})
    with pytest.raises(EngineError) as e:
        android.normalize(ctx)
    assert e.value.code == "E_ANDROID_PASSWORD" and e.value.data == {"ref": 0}
    assert not s.path("android/normalized.sqlite").exists()

    for version, code in ((28, "E_ANDROID_FORMAT_NEW"), (26, "E_ANDROID_FORMAT_UNVERIFIED")):
        monkeypatch.setattr(android.an, "read_format_version", lambda *a, v=version: v)
        s, ctx = _normalize(tmp_path / str(version), [b], {"text_ref": 0, "media_refs": []}, {"0": pw})
        with pytest.raises(EngineError) as e:
            android.normalize(ctx)
        assert e.value.code == code and e.value.data["format_version"] == version
    monkeypatch.undo()

    for plan in ({"text_ref": 1, "media_refs": []}, {"text_ref": 0, "media_refs": [0, 0]}, {"text_ref": "0"},
                 {"text_ref": 0, "media_refs": [], "password": "x"}):
        s, ctx = _normalize(tmp_path / "p", [b], plan, {"0": pw})
        with pytest.raises(EngineError) as e:
            android.normalize(ctx)
        assert e.value.code == "E_PROTOCOL" and e.value.data["sub"] == "plan"
        shutil.rmtree(tmp_path / "p")

    s, ctx = _normalize(tmp_path / "nopw", [b], {"text_ref": 0, "media_refs": []}, {})
    with pytest.raises(EngineError) as e:
        android.normalize(ctx)
    assert e.value.code == "E_SECRETS_MISSING"


def test_normalize_ok_writes_only_hashes_to_engine_json(tmp_path):
    pw = CANARY["password"]
    b = tmp_path / "threema-backup_1790000000000_1"
    built = android_canary.build(b)
    s, ctx = _normalize(tmp_path, [b], {"text_ref": 0, "media_refs": []}, {"0": pw})
    res = android.normalize(ctx)
    P.check_safe(res.data)
    eng = Path(s.path("engine.json")).read_text()
    assert built["own"] not in eng and CANARY["threema_id"] not in eng
    assert json.loads(eng)["android"]["own_id"] == s.hasher.h(built["own"])
    assert res.data["missing_key_senders"] == 1 and res.data["chats"] == 1 and res.data["groups"] == 1
    rep = json.loads(s.path("reports/android_normalize.json").read_text())
    assert rep["codes"] == ["W_MISSING_KEY_SENDERS"] and rep["counts"]["format_version"] == 27


def test_known_identities_feed_the_redactor(tmp_path):
    """IDs without a digit (ZZCANARY) escape the generic pattern; the session's own IDs are redacted by value."""
    from tmcore import redact
    from tmcore.cli import known_identities
    pw = CANARY["password"]
    b = tmp_path / "threema-backup_1790000000000_1"
    built = android_canary.build(b)
    s, ctx = _normalize(tmp_path, [b], {"text_ref": 0, "media_refs": []}, {"0": pw})
    android.normalize(ctx)
    ids = known_identities(s)
    assert {built["own"], CANARY["threema_id"], *built["missing"]} <= set(ids)
    r = redact.Redactor()
    r.add(*ids)
    assert CANARY["threema_id"] not in r(f"opened chat {CANARY['threema_id']}")
    assert known_identities(Session.create(tmp_path / "empty")) == []
