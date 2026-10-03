#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
synthetic_prepare_run.py -- the REAL engine of the app bundle (no --fake-device) on synthetic data only, as the app
starts it: `<App>/Contents/Resources/core/python/bin/python3 -I -B -m tmcore <cmd>` under the app's own
sandbox-exec profile (engine-sandbox.sb), the complete LiveEngine environment, secrets as one stdin line. Owner:
integrator. No device: the iPhone part is replaced by a synthetic encrypted PRE backup (fixtures/gen_ios_backup.py)
announced in engine.json exactly as `backup --role pre` leaves it (devtools/CONTRACT-REQUESTS.md "session paths").

    python3 devtools/synthetic_prepare_run.py [--app build/dist/Chat Transfer for Threema.app] [--out build/synthetic-run]

    version -> selftest -> android-inspect (two synthetic Android backups) -> android-normalize (text + media plan)
    -> [synthetic PRE backup] -> prepare (extract, import, verify_import, restore set, verify --restoreset, freeze)
    -> session-status -> diag-report, then an independent `backup_pipeline verify --restoreset --source --against`
    of the frozen set with the bundled interpreter.

Checks: every stdout line is a valid events.v1 stream with exactly one result and the catalog exit code; no
E_NETWORK_BLOCKED and no netguard refusal anywhere; the restore set is frozen and its marker equals
engine.json prepared.set_sha256; the canary scan finds nothing in events/logs/reports/session/diag; the password is in
no file of the session; the app bundle is byte-identical afterwards. Prints counts and OK/FAIL only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core"))

from tmcore.session import Session  # noqa: E402



class Run:
    def __init__(self, app: Path, session: Session):
        self.res = app / "Contents" / "Resources"
        self.py = self.res / "core" / "python" / "bin" / "python3"
        self.profile = self.res / "engine-sandbox.sb"
        self.s = session
        self.problems: list[str] = []
        import jsonschema
        from referencing import Registry, Resource
        reg = Registry()
        schema = REPO / "core" / "schema"
        ev = json.loads((schema / "events.v1.json").read_text())
        for name in ("events.v1.json", "session.v1.json", "report.v1.json", "compat.v1.json"):
            s = json.loads((schema / name).read_text())
            reg = reg.with_resource(s["$id"], Resource.from_contents(s))
        self.validator = jsonschema.Draft202012Validator(ev, registry=reg)
        self.catalog = json.loads((schema / "codes.v1.json").read_text())["codes"]

    def env(self) -> dict:
        tmp = self.s.root / "work" / "tmp"
        tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
        return {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "LANG": "C.UTF-8", "TMPDIR": str(tmp),
                "TMCORE_PROTOCOL": "1", "TMCORE_RESOURCES": str(self.res)}          # = LiveEngine.environment

    def argv(self, args: list[str]) -> list[str]:
        return ["/usr/bin/sandbox-exec", "-f", str(self.profile), str(self.py), *args]

    def tmcore(self, cmd: str, *args: str, secrets: dict | None = None, session: bool = True) -> dict:
        argv = ["-I", "-B", "-m", "tmcore", cmd] + (["--session", str(self.s.root)] if session else []) + list(args)
        stdin = b""
        if secrets is not None:
            argv.append("--secrets-stdin")
            stdin = json.dumps(secrets).encode() + b"\n"
        p = subprocess.run(self.argv(argv), input=stdin, capture_output=True, env=self.env(), timeout=110,
                           cwd=self.s.root)
        logs = self.s.root / "logs"
        with open(logs / "events.jsonl", "ab") as f:
            f.write(p.stdout)
        with open(logs / "debug.log", "ab") as f:
            f.write(p.stderr)
        evs = [json.loads(x) for x in p.stdout.decode("utf-8").splitlines()]
        self._validate(cmd, evs, p.returncode)
        res = evs[-1] if evs else {"ok": False, "code": "NO_RESULT", "data": {}}
        print(f"  {cmd:18s} {'ok ' if res.get('ok') else 'ERR'} {res.get('code')}", flush=True)
        res["_events"] = evs
        return res

    def _validate(self, cmd: str, evs: list[dict], rc: int) -> None:
        if not evs or evs[0].get("type") != "hello" or evs[-1].get("type") != "result":
            self.problems.append(f"{cmd}: stream without hello/result")
            return
        if [e["type"] for e in evs].count("result") != 1:
            self.problems.append(f"{cmd}: not exactly one result")
        if [e["seq"] for e in evs] != list(range(1, len(evs) + 1)):
            self.problems.append(f"{cmd}: seq gaps")
        for e in evs:
            if list(self.validator.iter_errors(e)):
                self.problems.append(f"{cmd}: event {e.get('type')} violates events.v1")
            if e.get("code") == "E_NETWORK_BLOCKED":
                self.problems.append(f"{cmd}: E_NETWORK_BLOCKED")
        res = evs[-1]
        want = 0 if res["ok"] else self.catalog.get(res["code"], {}).get("exit")
        if rc != want:
            self.problems.append(f"{cmd}: exit {rc} != {want}")


def iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def freeze(root: Path) -> None:
    for dirpath, _d, files in os.walk(root, topdown=False):
        for fn in files:
            p = os.path.join(dirpath, fn)
            os.chmod(p, stat.S_IMODE(os.lstat(p).st_mode) & ~0o222)
        os.chmod(dirpath, stat.S_IMODE(os.stat(dirpath).st_mode) & ~0o222)


def write_pre_backup(session: Session, password: str, c: dict) -> None:
    """The PRE backup as `backup --role pre` leaves it: ios/pre/<UDID>/ (frozen) + engine.json pre. The iPhone side
    carries the canary values (contact, message, device name, serial, phone, UDID), so the scan below proves that none
    of them leaves the session data."""
    os.environ.setdefault("TMCORE_FIXTURE_CACHE", str(REPO / ".build" / "fixtures"))
    sys.path.insert(0, str(REPO / "fixtures"))
    import gen_ios_backup as g
    spec = {"own": "ZZFIXN01",
            "contacts": [{"identity": c["threema_id"], "publicKey": "71" * 32, "firstName": c["contact_first_name"],
                          "lastName": c["contact_last_name"]}],
            "groups": [], "oneToOne": [c["threema_id"]],
            "messages": [{"chat": f"contact:{c['threema_id']}", "id": "7a7a7a7a00000001", "text": c["message_text"],
                          "dateMs": 1787000000000, "isOwn": False}]}
    img = g.build_image(variant="store", store_dir=g.v56_store(spec))
    info = g.write_backup(img, session.path("ios/pre"), password, udid=c["udid"], device_name=c["device_name"],
                          serial=c["serial"], phone=c["phone"], build="24A437")
    freeze(Path(info["device_dir"]))
    finished = dt.datetime.now(dt.timezone.utc)
    with session.update_engine() as st:
        st["pre"] = {"backup_id": str(uuid.uuid4()), "finished_at": iso(finished),
                     "fresh_until": iso(finished + dt.timedelta(minutes=60)), "ios_build": "24A437",
                     "password_ok": True, "frozen": True}
        st["device"] = session.hasher.h(c["udid"])
        Session.advance_phase(st, "pre_backup_done")


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            h.update(p.relative_to(root).as_posix().encode() + os.readlink(p).encode())
        elif p.is_file():
            h.update(p.relative_to(root).as_posix().encode() + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", type=Path, default=REPO / "build" / "dist" / "Chat Transfer for Threema.app")
    ap.add_argument("--out", type=Path, default=REPO / "build" / "synthetic-run")
    a = ap.parse_args(argv)
    app = a.app.resolve()
    out = a.out.resolve()
    if out.exists():
        subprocess.run(["chmod", "-R", "u+w", str(out)], check=False)
        shutil.rmtree(out)
    out.mkdir(parents=True)
    before = tree_digest(app)
    c = json.loads((REPO / "fixtures" / "canaries.json").read_text())                 # synthetic canary values
    c["udid"] = "-".join(c["udid_parts"])
    pw = c["password"]
    udid = c["udid"]

    print("android fixture (fixtures/gen_android_backup.py --split)", flush=True)
    fx = out / "android-input"
    p = subprocess.run([sys.executable, str(REPO / "fixtures" / "gen_android_backup.py"), "--out", str(fx), "--split"],
                       capture_output=True, text=True, timeout=110)
    if p.returncode != 0:
        print("FAIL android fixture", file=sys.stderr)
        return 1
    text, media = fx / "fixture-text-backup.zip", fx / "fixture-media-backup.zip"

    session = Session.create(out / "sessions", app_version="0.3.0-dev", locale="de")
    r = Run(app, session)
    print("engine of the bundle, sandbox-exec engine-sandbox.sb, no --fake-device:", flush=True)
    v = r.tmcore("version", session=False)
    r.tmcore("selftest", session=False)
    ins = r.tmcore("android-inspect", str(text), str(media))
    plan = json.dumps({"text_ref": ins["data"].get("text_ref"), "media_refs": ins["data"].get("media_refs")})
    norm = r.tmcore("android-normalize", "--plan", plan, str(text), str(media),
                    secrets={"android_passwords": {"0": pw, "1": pw}})
    write_pre_backup(session, pw, c)
    prep = r.tmcore("prepare", secrets={"backup_password": pw})
    stat_ = r.tmcore("session-status")
    diag = r.tmcore("diag-report")

    checks: dict[str, bool] = {
        "version reports the bundle": v.get("ok") and v["data"].get("engine_version") == v["data"].get("importer_version"),
        "inspect plan text_plus_media": ins.get("ok") and ins["data"].get("plan") == "text_plus_media",
        "normalize ok": bool(norm.get("ok")),
        "prepare ok": bool(prep.get("ok")),
        "resume at S14": stat_.get("ok") and stat_["data"].get("resume_at") == "S14",
        "diag report written": bool(diag.get("ok")),
    }
    prep_checks = {e["id"]: e["status"] for e in prep.get("_events", []) if e["type"] == "check"}
    for cid in ("extract", "threema_model", "import", "coredata_open", "verify_import", "restoreset",
                "verify_restoreset", "freeze"):
        checks[f"prepare check {cid}"] = prep_checks.get(cid) == "pass"

    eng = json.loads(session.path("engine.json").read_text())
    set_dev = session.path(f"work/restoreset/{udid}")
    marker = set_dev / ".RESTORESET_OK"
    checks["set marker = prepared.set_sha256"] = marker.is_file() and \
        marker.read_text().strip() == (eng.get("prepared") or {}).get("set_sha256")
    frozen = all(not os.stat(d).st_mode & 0o222 for d, _s, _f in os.walk(set_dev))
    checks["restore set frozen"] = frozen

    # independent verify of the frozen set with the bundled interpreter (password on stdin, never argv/file)
    vr = out / "verify-restoreset.json"
    p = subprocess.run(r.argv(["-I", "-B", "-m", "tmcore.lib.backup_pipeline", "verify", str(set_dev), "--restoreset",
                               "--source", str(session.path(f"ios/pre/{udid}")),
                               "--against", str(session.path("work/store_out")), "--report", str(vr)]),
                       input=(pw + "\n").encode(), capture_output=True, env=r.env(), timeout=110)
    rep = json.loads(vr.read_text()) if vr.is_file() else {}
    checks["independent verify --restoreset PASS"] = p.returncode == 0 and rep.get("result") == "PASS"
    sc = rep.get("source_comparison") or {}
    print(f"  verify --restoreset: {rep.get('result')}, domains {sorted((sc.get('domains') or {}).keys())}", flush=True)

    # privacy: canary scan of the engine output, password nowhere in the session
    cs = subprocess.run([sys.executable, str(REPO / "scripts" / "canary_scan.py"), "--canaries",
                         str(REPO / "fixtures" / "canaries.json"), str(session.root)], capture_output=True, text=True)
    checks["canary scan clean"] = cs.returncode == 0
    needles = [pw.encode(), pw.encode("utf-16-le")]
    hit = False
    for dirpath, _d, files in os.walk(session.root):
        for fn in files:
            fp = Path(dirpath) / fn
            if fp.is_symlink():
                continue
            data = fp.read_bytes()
            if any(n in data for n in needles):
                hit = True
    checks["password in no session file"] = not hit
    debug = session.path("logs/debug.log").read_text(encoding="utf-8", errors="replace")
    checks["no netguard refusal in debug.log"] = "network access is blocked" not in debug
    checks["bundle unchanged"] = tree_digest(app) == before
    for msg in r.problems:
        checks[f"stream: {msg}"] = False

    d = norm.get("data", {})
    print(f"  counts: chats {d.get('chats')}, messages {d.get('messages')}, media {d.get('media_present')}/"
          f"{d.get('media_total')}; prepare messages {prep.get('data', {}).get('messages')}, payload "
          f"{prep.get('data', {}).get('payload_bytes')} bytes", flush=True)
    bad = [k for k, ok in checks.items() if not ok]
    for k, ok in checks.items():
        print(f"{'OK  ' if ok else 'FAIL'} {k}")
    (out / "result.json").write_text(json.dumps({"checks": checks}, indent=1) + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
