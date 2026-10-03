# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Fixture E2E per scenario (DESIGN §13.1 level 6, §13.3): the real engine, synthetic data, the virtual iPhone.
Every scenario of core/tmcore/fake/scenarios/ runs as a whole wizard flow (core/tests/e2e/flow.py) and must
  * end on the screen and with the code (and verdict) the scenario expects,
  * produce a contract-valid stream per process (events.v1, hello first, gapless seq, one result last, exit code =
    catalog, balanced critical, device_modified consistent) -- checked with scripts/validate_schemas.py,
  * leak no canary value into events, logs, reports, session.json, engine.json (scripts/canary_scan.py),
  * never write a password anywhere (not even into the session's work files),
  * leave the virtual iPhone untouched whenever the engine says so.
Owner: coreB. No device; runs offline (CI runs it under sandbox-exec without network).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests import support
from tests.e2e.flow import WRONG_PW, Flow, android_files
from tmcore.fake import scenario as S

REPO = support.REPO
sys.path.insert(0, str(REPO / "devtools"))
sys.path.insert(0, str(REPO / "scripts"))
import canary_scan  # noqa: E402
import record_scenarios  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VS = _load("validate_schemas", REPO / "scripts" / "validate_schemas.py")


@pytest.fixture(scope="session")
def importer() -> Path:
    return support.importer()


def contract_errors(name: str, lines: list[dict], tmp: Path) -> list[str]:
    out = tmp / "rec"
    out.mkdir(exist_ok=True)
    p = record_scenarios.write(name, record_scenarios.canonical(lines), out)
    rep = VS.Report()
    registry, schemas = VS.build_registry()
    catalog = VS.load_json(VS.SCHEMA_DIR / "codes.v1.json")
    ev_validator = VS.validator(schemas["events.v1.json"], registry)
    repo, VS.REPO = VS.REPO, tmp                       # file names in messages are relative to REPO
    try:
        VS.check_scenario(rep, p, ev_validator, catalog, schemas["events.v1.json"])
    finally:
        VS.REPO = repo
    return rep.errors


def state_errors(f: Flow) -> list[str]:
    """reports/*.json against report.v1, engine.json against session.v1 #/$defs/engine."""
    registry, _schemas = VS.build_registry()
    rv = VS.validator({"$ref": "urn:threema-chat-transfer:schema:report:v1"}, registry)
    ev = VS.validator({"$ref": "urn:threema-chat-transfer:schema:session:v1#/$defs/engine"}, registry)
    errs = []
    for p in sorted((f.session.root / "reports").glob("*.json")):
        errs += [f"{p.name}: {e.message[:160]}" for e in rv.iter_errors(json.loads(p.read_text()))]
    errs += [f"engine.json: {e.message[:160]}" for e in ev.iter_errors(engine(f))]
    return errs


def fake_state(f: Flow) -> dict:
    return json.loads((f.session.root / "fake-iphone" / "state.json").read_text())


def engine(f: Flow) -> dict:
    return json.loads((f.session.root / "engine.json").read_text())


def password_anywhere(root: Path, pw: str) -> list[Path]:
    needles = [pw.encode("utf-8"), pw.encode("utf-16-le")]
    hits = []
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            data = p.read_bytes()
            if any(n in data for n in needles):
                hits.append(p)
    return hits


@pytest.mark.parametrize("name", S.names())
def test_scenario(name, tmp_path, importer):
    f = Flow(name, tmp_path, importer=importer).run()
    exp = f.sc.expect
    assert f.end_screen == f.sc.expect_screen, [(r.cmd, r.code) for r in f.runs]
    last = [r for r in f.runs if r.cmd == exp["cmd"]][-1]
    assert last.code == exp["code"], (last.cmd, last.code, last.events[-3:])
    if "verdict" in exp:
        assert f.final_verdict() == exp["verdict"]
    if "compat" in exp:
        assert last.data.get("compat") == exp["compat"]
    assert contract_errors(name, f.lines, tmp_path) == []
    assert state_errors(f) == []
    hits = canary_scan.scan([tmp_path], canary_scan.load_canaries(REPO / "fixtures" / "canaries.json"))
    assert hits == [], [(str(p.relative_to(tmp_path)), n) for p, n in hits]
    assert password_anywhere(tmp_path, support.canaries()["password"]) == []
    for r in f.runs:
        res = r.result
        if res is None:
            assert r.crash and r.cmd in ("restore", "rollback-threema")
            continue
        assert r.rc == (0 if res["ok"] else json.loads((REPO / "core" / "schema" / "codes.v1.json").read_text())
                        ["codes"][res["code"]]["exit"])
        crit = [e for e in r.events if e["type"] == "critical"]
        if not crit:
            assert res["device_modified"] == "no" or r.cmd == "encryption-enable"
    _scenario_specifics(name, f)


def _runs(f: Flow, cmd: str):
    return [r for r in f.runs if r.cmd == cmd]


def _scenario_specifics(name: str, f: Flow) -> None:
    st = fake_state(f) if (f.session.root / "fake-iphone" / "state.json").is_file() else {}
    eng = engine(f)
    restores = [r for r in f.runs if r.cmd in ("restore", "rollback-threema")]
    if f.end_screen != "S20" and not restores:
        assert st.get("restores", 0) == 0                     # nothing was ever applied to the iPhone
    if name == "first_backup_dropped":
        pre = _runs(f, "backup")[0]
        assert [e["reason"] for e in pre.events if e["type"] == "retry"] == ["W_BACKUP_RETRY"]
        assert st["backup_sessions"] >= 3                     # dropped + PRE + POST
    if name == "find_my_on":
        r = restores[-1]
        assert r.result["device_modified"] == "no" and r.data == {"source": "mberror_211"}
        assert any(e["type"] == "critical" for e in r.events)   # refused by the device after critical on
        assert eng["restore_sent_at"] is None and st["restores"] == 0
    if name == "wrong_backup_password":
        b1, b2 = _runs(f, "backup")[:2]
        assert b1.code == "E_BACKUP_PASSWORD" and b1.data == {"attempt": 1, "max": 5} and b1.result["retryable"]
        assert not [e for e in b2.events if e["type"] == "progress"]   # same backup, no second backup
    if name in ("freshness_expired", "dcim_changed"):
        assert [r.code for r in restores][0] in ("E_GUARD_FRESHNESS", "E_GUARD_DCIM_CHANGED")
        assert st["restores"] == 1 and len([r for r in _runs(f, "backup") if "pre" in r.argv]) == 2
    if name == "link_lost_after_send":
        r = restores[0]
        assert r.code == "E_RESTORE_INTERRUPTED" and r.result["device_modified"] == "unknown"
        assert r.data["last_progress"] < 100
    if name == "app_crash_after_send":
        assert restores[0].crash and restores[0].rc == -9
        assert _runs(f, "session-status")[0].data["resume_at"] == "S16"
    if name == "rollback_threema_ok":
        verdicts = [r.data.get("verdict") for r in _runs(f, "postcheck")]
        assert verdicts == ["threema_only", "ok"]
        assert st["restores"] == 2 and eng["rollback_used"] is True and eng["restore"]["kind"] == "rollback"
    if name == "happy":
        enc = _runs(f, "encryption-enable")[0]
        assert enc.result["device_modified"] == "yes" and [e["on"] for e in enc.events
                                                           if e["type"] == "critical"] == [True, False]
    if f.end_screen == "S20":
        assert st["restores"] >= 1 and eng["postcheck"]["verdict"] in ("ok", "ok_with_notes")
        rs = restores[-1]
        assert rs.crash or rs.result["device_modified"] in ("yes", "unknown")


def test_reset_all_settings_after_a_wrong_password_makes_a_new_pre_backup(tmp_path, importer):
    """S10b "Ich weiß es nicht" / F-PW-WRONG help: the user cannot find the stored backup password and does "Alle
    Einstellungen zurücksetzen" on the iPhone (Apple's way: the backup password is gone, encryption off); "Erneut
    prüfen" reads `device-status` again and S10a sets a new password. The PRE backup kept for another password attempt
    was made under the old password: the next PRE is a new backup, never a re-check of the old one with the new one."""
    f = Flow("wrong_backup_password", tmp_path, importer=importer)
    assert f.invoke("device-status").data["encryption"] == "on"
    files = android_files("full")
    r = f.invoke("android-inspect", files=files)
    plan = {"text_ref": r.data.get("text_ref"), "media_refs": r.data.get("media_refs") or []}
    r = f.invoke("android-normalize", ["--plan", json.dumps(plan, separators=(",", ":"))],
                 secrets={"android_passwords": {str(i): f.canary_pw for i in range(len(files))}}, files=files)
    assert r.ok, r.code
    b1 = f.invoke("backup", ["--role", "pre"], secrets={"backup_password": WRONG_PW})
    assert b1.code == "E_BACKUP_PASSWORD" and engine(f)["pre"]["password_ok"] is False
    # "Alle Einstellungen zurücksetzen": the iPhone forgets its backup password, encryption is off
    st = fake_state(f)
    st.update(encryption=False, secret=None)
    (f.session.root / "fake-iphone" / "state.json").write_text(json.dumps(st))
    assert f.invoke("device-status").data["encryption"] == "off"
    enc = f.invoke("encryption-enable", secrets={"new_backup_password": f.canary_pw})
    assert enc.ok and enc.data == {"encryption": "on", "changed": True}
    assert engine(f)["pre"] is None
    b2 = f.invoke("backup", ["--role", "pre"], secrets={"backup_password": f.canary_pw})
    assert b2.ok, (b2.code, b2.data)
    assert [e for e in b2.events if e["type"] == "progress"]      # a new backup, not the kept one
    assert fake_state(f)["backup_sessions"] == 2
    assert engine(f)["pre"]["password_ok"] is True
    assert contract_errors("wrong_backup_password", f.lines, tmp_path) == []
    assert state_errors(f) == []
    assert password_anywhere(tmp_path, f.canary_pw) == []
