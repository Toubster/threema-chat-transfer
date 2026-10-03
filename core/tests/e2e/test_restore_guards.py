# SPDX-License-Identifier: AGPL-3.0-or-later
"""
restore / rollback-threema / postcheck guards on REAL artifacts (a prepared session on the virtual iPhone): every
refusal happens before a single byte goes to the device, and none of them can be switched off. Owner: coreB.
The session state is changed directly in these tests only (the shipped engine has no option for any of it).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests import support
from tests.e2e.flow import Flow
from tmcore.fake import device as D
from tmcore.steps import iphone as I


@pytest.fixture(scope="session")
def importer() -> Path:
    return support.importer()


def prepared(tmp_path: Path, importer: Path, scenario: str = "happy_with_notes") -> Flow:
    return Flow(scenario, tmp_path, importer=importer).prepare_only()


def edit_engine(f: Flow, fn) -> None:
    with f.session.update_engine() as st:
        fn(st)


def restore(f: Flow, cmd: str = "restore"):
    return f.invoke(cmd, secrets=f._secret())   # noqa: SLF001


def restores_applied(f: Flow) -> int:
    return json.loads((f.session.root / "fake-iphone" / "state.json").read_text())["restores"]


def shift(ts: str, minutes: int) -> str:
    import datetime as dt
    t = I.parse_ts(ts) - dt.timedelta(minutes=minutes)
    return I.iso(t)


def nothing_sent(r) -> None:
    assert not [e for e in r.events if e["type"] == "critical"], "refused only before the critical section"
    assert r.result["device_modified"] == "no" and r.rc == 1


def test_freshness_61_min_refused_before_anything(tmp_path, importer):
    f = prepared(tmp_path, importer)
    edit_engine(f, lambda st: st["pre"].update(started_at=shift(st["pre"]["started_at"], 61)))
    r = restore(f)
    assert r.code == "E_GUARD_FRESHNESS" and r.data["limit_min"] == 60 and r.data["age_min"] >= 61
    nothing_sent(r)
    assert restores_applied(f) == 0 and f.session.engine_state()["restore_sent_at"] is None


def test_other_device_and_changed_ios_build(tmp_path, importer):
    f = prepared(tmp_path, importer)
    edit_engine(f, lambda st: st.update(device="h:00000000"))
    r = restore(f)
    assert r.code == "E_DEV_OTHER"
    nothing_sent(r)
    f2 = prepared(tmp_path / "b", importer)
    edit_engine(f2, lambda st: st["pre"].update(ios_build="24A001"))
    r = restore(f2)
    assert r.code == "E_IOS_CHANGED"
    nothing_sent(r)


@pytest.mark.parametrize("tamper,sub", [("marker", "marker"), ("do_not_restore", "layout"),
                                        ("manifest", "marker"), ("report", "marker"), ("blob", "structure")])
def test_set_integrity(tmp_path, importer, tamper, sub):
    f = prepared(tmp_path, importer)
    root = f.session.path("work/restoreset")
    I.make_writable(root)
    dev = next(p for p in root.iterdir() if p.is_dir())
    if tamper == "marker":
        (dev / ".RESTORESET_OK").unlink()
    elif tamper == "do_not_restore":
        (dev / "DO_NOT_RESTORE").write_text("x")
    elif tamper == "manifest":
        # a different payload with the old report: the marker/report no longer describe this Manifest.db
        rep_p = root / f"{dev.name}.restoreset.json"
        rep = json.loads(rep_p.read_text())
        rep["home_rows"] += 1
        rep_p.write_text(json.dumps(rep))
    elif tamper == "report":
        (root / f"{dev.name}.restoreset.json").write_text("{}")
    elif tamper == "blob":
        blobs = sorted(p for p in dev.glob("??/*") if p.is_file())
        blobs[0].unlink()
    r = restore(f)
    assert (r.code, r.data.get("sub")) == ("E_GUARD_SET_INTEGRITY", sub)
    nothing_sent(r)


def test_not_prepared_and_one_exposition(tmp_path, importer):
    f = prepared(tmp_path, importer)
    edit_engine(f, lambda st: st["prepared"].update(pre_backup_id="00000000-0000-4000-8000-000000000000"))
    r = restore(f)
    assert (r.code, r.data.get("sub")) == ("E_PROTOCOL", "not_prepared")
    f2 = prepared(tmp_path / "b", importer)
    assert restore(f2).code == "R_RESTORE_SENT_LINK_LOST"
    r = restore(f2)
    assert (r.code, r.data.get("sub")) == ("E_PROTOCOL", "restore_already_sent")
    assert restores_applied(f2) == 1


def test_dcim_changed_right_before_the_send(tmp_path, importer):
    f = Flow("dcim_changed", tmp_path, importer=importer).prepare_only()
    r = restore(f)
    assert r.code == "E_GUARD_DCIM_CHANGED" and r.result["retryable"] is True
    nothing_sent(r)
    ev = [e for e in r.events if e["type"] == "check" and e["id"] == "dcim_unchanged"][0]
    assert ev["data"]["added"] == 1


def test_airplane_rechecked_from_the_pre_backup(tmp_path, importer):
    """The PRE check already refuses airplane off; restore re-reads it (a PRE kept from elsewhere would fail)."""
    f = Flow("airplane_off", tmp_path, importer=importer)
    with pytest.raises(AssertionError):
        f.prepare_only()
    assert f.runs[-1].code == "E_GUARD_AIRPLANE"


SPY = r"""
import io, json, sys
from tmcore import cli
from tmcore.fake import device as D
seen = {}
orig = D.VirtualIPhone.restore
def spy(self, src_root, source, password, options, progress):
    seen.update(options)
    return orig(self, src_root, source, password, options, progress)
D.VirtualIPhone.restore = spy
out = io.StringIO()
rc = cli.main(sys.argv[1:], out=out)
sys.__stdout__.write(json.dumps({"rc": rc, "seen": seen, "last": json.loads(out.getvalue().splitlines()[-1])}) + "\n")
"""


def test_restore_options_are_the_proven_ones_end_to_end(tmp_path, importer):
    """The call that reaches the (virtual) device carries exactly the proven option set. Own process: the network
    guard of a fake run (no AF_UNIX either) cannot be removed again."""
    import subprocess
    import sys
    f = prepared(tmp_path, importer)
    p = subprocess.run([sys.executable, "-E", "-s", "-B", "-c", SPY, "restore", "--session", str(f.session.root),
                        "--secrets-stdin", "--fake-device", f.name], cwd=support.CORE, env=f.env,
                       input=(json.dumps(f._secret()) + "\n").encode(), capture_output=True, timeout=120)  # noqa: SLF001
    assert p.stdout, p.stderr.decode()[-2000:]
    res = json.loads(p.stdout.decode().splitlines()[-1])
    assert res["rc"] == 0 and res["seen"] == I.RESTORE_OPTIONS
    assert res["last"]["code"] == "R_RESTORE_SENT_LINK_LOST"


def test_partial_payload_turns_the_gate_red(tmp_path, importer):
    """Counter-test (DESIGN §13.2): if a payload without HomeDomain had reached the iPhone, the gate must be red."""
    f = prepared(tmp_path, importer)
    assert restore(f).code == "R_RESTORE_SENT_LINK_LOST"
    ph = D.VirtualIPhone(f.session.root, f.name)
    img = ph.image()
    partial = {k: e for k, e in img.entries.items() if k[0] in ("CameraRollDomain", "KeyboardDomain")}
    ph._apply(img, partial)                                                 # noqa: SLF001
    ph.save_image(img)
    assert f.invoke("backup", ["--role", "post"], secrets=f._secret()).ok   # noqa: SLF001
    r = f.invoke("postcheck", ["--buddy-answer", "account_only"], secrets=f._secret())   # noqa: SLF001
    assert r.ok and r.data["verdict"] in ("data", "setup_full")
    assert any(a["severity"] == "alert" for a in r.data["areas"])


def test_post_backup_must_be_newer_than_the_restore(tmp_path, importer):
    f = prepared(tmp_path, importer)
    assert restore(f).code == "R_RESTORE_SENT_LINK_LOST"
    assert f.invoke("backup", ["--role", "post"], secrets=f._secret()).ok   # noqa: SLF001
    edit_engine(f, lambda st: st["post"].update(started_at=shift(st["restore"]["finished_at"], 1)))
    r = f.invoke("postcheck", ["--buddy-answer", "account_only"], secrets=f._secret())   # noqa: SLF001
    assert r.code == "E_POST_TOO_EARLY"


def test_needs_answer_without_buddy_answer(tmp_path, importer):
    f = prepared(tmp_path, importer)                          # happy_with_notes: Setup Assistant re-ran
    assert restore(f).code == "R_RESTORE_SENT_LINK_LOST"
    assert f.invoke("backup", ["--role", "post"], secrets=f._secret()).ok   # noqa: SLF001
    r = f.invoke("postcheck", secrets=f._secret())            # noqa: SLF001
    assert r.ok and r.data["verdict"] == "needs_answer"
    r = f.invoke("postcheck", ["--buddy-answer", "account_only"], secrets=f._secret())   # noqa: SLF001
    assert r.data["verdict"] == "ok_with_notes" and "N_APPLE_ACCOUNT_RERUN" in r.data["notes"]


def test_rollback_not_allowed_after_green_and_window(tmp_path, importer):
    f = prepared(tmp_path, importer)
    assert restore(f).code == "R_RESTORE_SENT_LINK_LOST"
    assert f.invoke("backup", ["--role", "post"], secrets=f._secret()).ok   # noqa: SLF001
    assert f.invoke("postcheck", ["--buddy-answer", "account_only"], secrets=f._secret()).ok   # noqa: SLF001
    r = restore(f, "rollback-threema")
    assert (r.code, r.data) == ("E_GUARD_ROLLBACK_NOT_ALLOWED", {"verdict": "ok_with_notes"})
    nothing_sent(r)
    edit_engine(f, lambda st: st["postcheck"].update(verdict="threema_only"))
    edit_engine(f, lambda st: st["pre"].update(started_at=shift(st["pre"]["started_at"], 6 * 60 + 1)))
    r = restore(f, "rollback-threema")
    assert r.code == "E_GUARD_ROLLBACK_WINDOW" and r.data["limit_min"] == 360
    nothing_sent(r)
    assert restores_applied(f) == 1


def test_no_override_option_exists():
    from tmcore import cli
    p = cli.build_parser()
    for cmd in ("restore", "rollback-threema", "postcheck", "backup"):
        sub = p._subparsers._group_actions[0].choices[cmd]                  # noqa: SLF001
        opts = {o for a in sub._actions for o in a.option_strings}          # noqa: SLF001
        assert not [o for o in opts if any(w in o for w in ("allow", "force", "waive", "skip", "stale", "trim"))]


def test_fake_home_is_inside_the_session(tmp_path, importer):
    f = prepared(tmp_path, importer)
    assert (f.session.root / "fake-iphone" / "state.json").is_file()
    assert oct(os.stat(f.session.root / "fake-iphone").st_mode & 0o777) == "0o700"
