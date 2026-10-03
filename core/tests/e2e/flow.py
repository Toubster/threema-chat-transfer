# SPDX-License-Identifier: AGPL-3.0-or-later
"""
flow.py -- one wizard run against `tmcore --fake-device <scenario>`, driven the way the app drives the engine
(DESIGN §8.3, §13.3). Owner: coreB. Used by the e2e tests (core/tests/e2e/test_scenarios.py) and by the scenario
recorder (devtools/record_scenarios.py), which writes the mock scenarios app/Tests/Scenarios/<name>.jsonl.

    f = Flow("happy", base_dir, importer=path)      # base_dir: an empty temporary folder
    f.run()                                          # every tmcore process, as the app would start it
    f.lines                                          # mock header, user/invoke/exit directives, events (raw)
    f.end_screen, f.runs[-1].result

What the app does between the commands is written as {"mock":"user"} lines. Secrets go to stdin only (one JSON
line); the recorded invoke lines name the secret fields, never the values. Passwords are the canary password of
fixtures/canaries.json (DESIGN §10.2), so the privacy scan proves they never leak.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
REPO = CORE.parent
os.environ.setdefault("TMCORE_NO_TMUTIL", "1")      # test sessions: no Time Machine exclusion calls (slow)
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from tests import support  # noqa: E402
from tmcore.fake import scenario as S  # noqa: E402
from tmcore.session import Session  # noqa: E402

CATALOG = json.loads((CORE / "schema" / "codes.v1.json").read_text(encoding="utf-8"))["codes"]
WRONG_PW = "ZZ-falsches-Passwort-0"
TIMEOUT_S = 600
WATCH_TIMEOUT_S = 30
RESTORE_RECOVERY = {"E_GUARD_FRESHNESS": "F-FRESHNESS", "E_GUARD_DCIM_CHANGED": "F-DCIM"}
RED = ("setup_full", "data_keychain", "data", "threema_only", "restore_state")


@dataclasses.dataclass
class Run:
    cmd: str
    argv: list[str]
    rc: int
    events: list[dict]
    crash: bool = False

    @property
    def result(self) -> dict | None:
        return self.events[-1] if self.events and self.events[-1].get("type") == "result" else None

    @property
    def ok(self) -> bool:
        return bool(self.result and self.result["ok"])

    @property
    def code(self) -> str | None:
        return self.result["code"] if self.result else None

    @property
    def data(self) -> dict:
        return (self.result or {}).get("data") or {}


# ------------------------------------------------------------------------------------------------ android files
def android_cache() -> Path:
    """Synthetic Android backups (fixtures/gen_android_backup.py), built once per generator version."""
    gen = REPO / "fixtures" / "gen_android_backup.py"
    import hashlib
    tag = hashlib.sha256(gen.read_bytes() + (REPO / "fixtures" / "canaries.json").read_bytes()).hexdigest()[:12]
    root = support.CACHE / f"android-{tag}"
    with support._lock("android"):  # noqa: SLF001 -- shared cache lock of tests/support
        if not (root / "v28" / "fixture-backup.zip").is_file():
            mod = support.android_fixture()
            tmp = root.with_name(root.name + f".tmp{os.getpid()}")
            shutil.rmtree(tmp, ignore_errors=True)
            mod.generate(str(tmp / "v27"), split=True)
            mod.generate(str(tmp / "v28"), format_version="28")
            src = tmp / "v27" / "fixture-backup.zip"
            data = src.read_bytes()
            (tmp / "incomplete").mkdir(parents=True)
            # an interrupted Android backup: Threema writes INCOMPLETE-<name> and the zip has no central directory
            (tmp / "incomplete" / "INCOMPLETE-threema-backup_zz_1.zip").write_bytes(data[: len(data) // 3])
            shutil.rmtree(root, ignore_errors=True)
            os.replace(tmp, root)
    return root


def android_files(kind: str) -> list[Path]:
    root = android_cache()
    return {"full": [root / "v27" / "fixture-backup.zip"],
            "wrong_password": [root / "v27" / "fixture-backup.zip"],
            "two_backups": [root / "v27" / "fixture-text-backup.zip", root / "v27" / "fixture-media-backup.zip"],
            "incomplete": [root / "incomplete" / "INCOMPLETE-threema-backup_zz_1.zip"],
            "format_new": [root / "v28" / "fixture-backup.zip"]}[kind]


# ------------------------------------------------------------------------------------------------ the flow
class Flow:
    def __init__(self, name: str, base_dir: Path, *, importer: Path | None = None, lang: str = "de"):
        self.name = name
        self.sc = S.load(name)
        self.base = Path(base_dir)
        self.workdir = self.base / "sessions"
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.logs = self.base / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.session = Session.create(self.workdir, app_version="0.3.0-dev", locale=lang)
        self.env = support.tmcore_env(TMCORE_IMPORTER=str(importer or support.importer()))
        self.canary_pw = support.canaries()["password"]
        self.lines: list[dict] = [{"mock": "scenario", "name": name, "protocol": 1, "lang": lang,
                                   "expect_screen": self.sc.expect_screen, "summary": self.sc.summary,
                                   "draft": False}]
        self.runs: list[Run] = []
        self.end_screen: str | None = None
        self.placeholders: dict[str, str] = {str(self.session.root): "<session>", str(self.workdir): "<workdir>"}
        self.backup_password: str | None = None

    # ---------------------------------------------------------------------------------------------- primitives
    def user(self, screen: str, answer: str) -> None:
        self.lines.append({"mock": "user", "screen": screen, "answer": answer})

    def _argv(self, cmd: str, args: list[str], secrets: dict | None, session: bool, files: list[Path]) -> list[str]:
        argv = [cmd]
        if session:
            argv += ["--session", str(self.session.root)]
        argv += args
        if secrets is not None:
            argv.append("--secrets-stdin")
        return argv + [str(f) for f in files]

    def _record_args(self, argv: list[str]) -> list[str]:
        out = []
        for a in argv[1:]:
            out.append(self.placeholders.get(a, a))
        return out

    def invoke(self, cmd: str, args: list[str] | None = None, *, secrets: dict | None = None, session: bool = True,
               files: list[Path] | None = None) -> Run:
        argv = self._argv(cmd, list(args or []), secrets, session, list(files or []))
        self.lines.append({"mock": "invoke", "cmd": cmd, "args": self._record_args(argv),
                           "secrets": sorted(secrets) if secrets else []})
        full = [sys.executable, "-E", "-s", "-B", "-m", "tmcore", *argv, "--fake-device", self.name]
        stdin = (json.dumps(secrets) + "\n").encode() if secrets is not None else b""
        log = self.logs / f"{len(self.runs) + 1:02d}-{cmd}.log"
        with open(log, "wb") as err:
            p = subprocess.run(full, cwd=CORE, input=stdin, stdout=subprocess.PIPE, stderr=err, env=self.env,
                               timeout=TIMEOUT_S)
        return self._finish(cmd, argv, p.returncode, p.stdout.decode("utf-8"))

    def _finish(self, cmd: str, argv: list[str], rc: int, out: str) -> Run:
        events = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
        self.lines.extend(events)
        crash = rc < 0
        self.lines.append({"mock": "exit", "code": rc, **({"crash": True} if crash else {})})
        run = Run(cmd, argv, rc, events, crash)
        self.runs.append(run)
        return run

    def device_watch(self) -> Run:
        """S03: stream until the iPhone is ready, then SIGTERM (the normal end of device-watch)."""
        argv = self._argv("device-watch", [], None, True, [])
        self.lines.append({"mock": "invoke", "cmd": "device-watch", "args": self._record_args(argv), "secrets": []})
        full = [sys.executable, "-E", "-s", "-B", "-m", "tmcore", *argv, "--fake-device", self.name]
        log = self.logs / f"{len(self.runs) + 1:02d}-device-watch.log"
        with open(log, "wb") as err:
            p = subprocess.Popen(full, cwd=CORE, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                                 env=self.env)
            lines: list[str] = []
            ready = threading.Event()

            def reader():
                for raw in p.stdout:
                    s = raw.decode("utf-8")
                    lines.append(s)
                    try:
                        e = json.loads(s)
                    except ValueError:
                        continue
                    if e.get("type") == "device" and e.get("state") == "ready":
                        ready.set()
                ready.set()
            t = threading.Thread(target=reader, daemon=True)
            t.start()
            ready.wait(WATCH_TIMEOUT_S)
            time.sleep(0.3)
            p.send_signal(signal.SIGTERM)
            rc = p.wait(timeout=WATCH_TIMEOUT_S)
            t.join(timeout=5)
        return self._finish("device-watch", argv, rc, "".join(lines))

    def end(self, screen: str) -> None:
        self.end_screen = screen

    def end_for(self, run: Run) -> None:
        code = run.code
        self.end_screen = (CATALOG.get(code) or {}).get("screen") or "F-INTERNAL" if code else "F-INTERNAL"

    # ---------------------------------------------------------------------------------------------- the wizard
    def run(self) -> "Flow":
        fl = self.sc.flow
        self.user("S00", "get_started")
        self.user("S01", "accepted")
        r = self.invoke("host-check", ["--workdir", str(self.workdir)], session=False)
        if not r.ok:
            return self.end_for(r) or self
        self.device_watch()
        st = self.invoke("device-status")
        if not st.ok:
            return self.end_for(st) or self
        ios_ok = st.data.get("compat") == "verified"
        self.user("S03", "continue" if ios_ok else "prepare_android")
        self.user("S04", "done")
        files = android_files(fl["android"])
        for i, f in enumerate(files):
            self.placeholders[str(f)] = f"<android-backup-{i}>"
        r = self.invoke("android-inspect", files=files)
        if not r.ok:
            return self.end_for(r) or self
        plan = {"text_ref": r.data.get("text_ref"), "media_refs": r.data.get("media_refs") or []}
        self.user("S05", "password_entered")
        pws = {str(i): self.canary_pw for i in range(len(files))}
        if fl["android"] == "wrong_password":
            r = self.invoke("android-normalize", ["--plan", json.dumps(plan, separators=(",", ":"))],
                            secrets={"android_passwords": {**pws, "0": WRONG_PW}}, files=files)
            if r.code != "E_ANDROID_PASSWORD":
                return self.end_for(r) or self
            self.user("S05", "password_entered")
        r = self.invoke("android-normalize", ["--plan", json.dumps(plan, separators=(",", ":"))],
                        secrets={"android_passwords": pws}, files=files)
        if not r.ok:
            return self.end_for(r) or self
        if self.sc.expect_screen == "S07":
            return self.end("S07") or self
        self.user("S07", "all_checked")
        st = self.invoke("device-status")
        if not st.ok:
            return self.end_for(st) or self
        if not ios_ok or st.data.get("compat") != "verified":
            return self.end("F-IOS-UNKNOWN") or self
        self.user("S08", "all_checked")
        self.user("S09", "icloud")
        if st.data.get("encryption") == "off":
            # S09 "Weiter": the app reads the encryption state again -- a Finder backup for the safety net turns it
            # on with the user's own password (REVIEW B1)
            st = self.invoke("device-status")
            if not st.ok:
                return self.end_for(st) or self
        if st.data.get("encryption") == "off":
            self.user("S10a", "password_confirmed")
            r = self.invoke("encryption-enable", secrets={"new_backup_password": self.canary_pw})
            if not r.ok:
                return self.end_for(r) or self
            right = self.canary_pw
        else:
            self.user("S10b", "password_entered")
            from tmcore.fake.device import device_password
            right = device_password(self.sc)
        first = WRONG_PW if fl["backup_password"] == "wrong_then_right" else right
        self.user("S11", "all_checked")
        r = self.invoke("backup", ["--role", "pre"], secrets={"backup_password": first})
        if r.code == "E_BACKUP_PASSWORD" and first != right:
            self.user("F-PW-WRONG", "password_entered")
            r = self.invoke("backup", ["--role", "pre"], secrets={"backup_password": right})
        if not r.ok:
            return self.end_for(r) or self
        self.backup_password = right
        if not self._transfer():
            return self
        return self._check()

    def _secret(self) -> dict:
        return {"backup_password": self.backup_password}

    def _transfer(self) -> bool:
        for _attempt in range(3):
            r = self.invoke("prepare", secrets=self._secret())
            if not r.ok:
                self.end_for(r)
                return False
            self.user("S14", "transfer_now")
            r = self.invoke("restore", secrets=self._secret())
            if r.crash:
                self.lines.append({"mock": "relaunch"})
                s = self.invoke("session-status")
                if s.data.get("resume_at") != "S16":
                    self.end_for(s)
                    return False
                self.user("S23", "continue")
                return True
            if r.code in RESTORE_RECOVERY:
                self.user(RESTORE_RECOVERY[r.code], "new_backup")
                b = self.invoke("backup", ["--role", "pre"], secrets=self._secret())
                if not b.ok:
                    self.end_for(b)
                    return False
                continue
            if r.code == "E_RESTORE_INTERRUPTED":
                self.user("F-RESTORE-MID", "iphone_restarted")
                return True
            if not r.ok:
                self.end_for(r)
                return False
            return True
        self.end("F-INTERNAL")
        return False

    def _postcheck(self, first: bool = True) -> Run | None:
        answer = self.sc.flow["buddy_answer"]
        self.user("S16", answer)
        if answer == "full_setup":
            # the iPhone sits in the full Setup Assistant: no S17 (Threema cannot be opened), no POST backup; the
            # answer alone is setup_full and needs no password (REVIEW M2)
            return self.invoke("postcheck", ["--buddy-answer", answer])
        threema = self.sc.flow["threema_answer"] if first else "ok"
        self.user("S17", "old_chats_present" if threema == "ok" else "problem")
        b = self.invoke("backup", ["--role", "post"], secrets=self._secret())
        if not b.ok:
            self.end_for(b)
            return None
        return self.invoke("postcheck", ["--buddy-answer", answer, "--threema-answer", threema],
                           secrets=self._secret())

    def _check(self) -> "Flow":
        p = self._postcheck()
        if p is None:
            return self
        if not p.ok:
            return self.end_for(p) or self
        verdict = p.data.get("verdict")
        if verdict == "threema_only" and "rollback" in self.sc.flow["after_postcheck"]:
            self.user("S21", "reset_threema")
            r = self.invoke("rollback-threema", secrets=self._secret())
            if not r.ok:
                return self.end_for(r) or self
            p = self._postcheck(first=False)
            if p is None:
                return self
            if not p.ok:
                return self.end_for(p) or self
            verdict = p.data.get("verdict")
        if verdict in RED:
            return self.end("S21") or self
        if verdict == "needs_answer":
            return self.end("S16") or self
        self.user("S19", "continue")
        c = self.invoke("cleanup", ["--what", "work"])
        return (self.end("S20") if c.ok else self.end_for(c)) or self

    def prepare_only(self) -> "Flow":
        """The shortest path to a prepared session (guard tests): Android, encryption, PRE backup, prepare."""
        st = self.invoke("device-status")
        files = android_files("full")
        r = self.invoke("android-inspect", files=files)
        plan = {"text_ref": r.data.get("text_ref"), "media_refs": r.data.get("media_refs") or []}
        r = self.invoke("android-normalize", ["--plan", json.dumps(plan, separators=(",", ":"))],
                        secrets={"android_passwords": {str(i): self.canary_pw for i in range(len(files))}},
                        files=files)
        assert r.ok, r.code
        if st.data.get("encryption") == "off":
            assert self.invoke("encryption-enable", secrets={"new_backup_password": self.canary_pw}).ok
            self.backup_password = self.canary_pw
        else:
            from tmcore.fake.device import device_password
            self.backup_password = device_password(self.sc)
        r = self.invoke("backup", ["--role", "pre"], secrets=self._secret())
        assert r.ok, r.code
        r = self.invoke("prepare", secrets=self._secret())
        assert r.ok, r.code
        return self

    # ---------------------------------------------------------------------------------------------- summary
    def final_verdict(self) -> str | None:
        for r in reversed(self.runs):
            if r.cmd == "postcheck" and r.ok:
                return r.data.get("verdict")
        return None
