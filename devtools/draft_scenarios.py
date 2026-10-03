#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
draft_scenarios.py -- hand-written DRAFT mock scenarios for the MockEngine (app/Tests/Scenarios/*.jsonl).
SUPERSEDED by devtools/record_scenarios.py (recordings of the real engine on the virtual iPhone); kept for the
history of the contract. It never overwrites a recording (header "draft": false).

These drafts exist so the app (P4) can be built against the frozen contract before the engine (P3) records real
scenarios. `make record-scenarios` (tmcore --fake-device, DESIGN §13.3) replaces every draft with a recording of
the same name; the contract job then compares drafts/recordings structurally. Values are the canonical example
values only (DESIGN §10.4): 18 chats, 4 groups, 12 345 messages, 1 234 media, 2 polls, 6.4 GB, iOS 27.0 (24A437),
Threema 7.4, hashed IDs.

File format (docs/ENGINE-PROTOCOL.md "Mock scenarios"):
  {"mock":"scenario", name, protocol, lang, expect_screen, summary, draft}   first line
  {"mock":"invoke", "cmd", "args", "secrets"}   a tmcore process starts (args never hold secrets)
  <events.v1 lines>                             stdout of that process, seq from 1
  {"mock":"exit", "code", "crash"?}             process exit (crash: no result, signal code)
  {"mock":"user", "screen", "answer"}           what the user does in the app (informational for UI tests)
  {"mock":"relaunch"}                           the app is quit and started again (resume, DESIGN §8.1)

    python3 devtools/draft_scenarios.py            # rewrite all drafts
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "app" / "Tests" / "Scenarios"
T0 = dt.datetime(2026, 1, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
ENGINE = "0.9.0-beta.1"
PMD3 = "11.19.4"
DEVICE = "h:5c0ffee1"
OWN_ID = "h:0a1b2c3d"
PRODUCT = "iPhone17,1"
GB = 1_000_000_000


class Clock:
    def __init__(self):
        self.t = T0

    def tick(self, seconds: float = 0.25) -> str:
        self.t += dt.timedelta(seconds=seconds)
        return self.ts()

    def ts(self, offset_s: float = 0) -> str:
        t = self.t + dt.timedelta(seconds=offset_s)
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Scenario:
    def __init__(self, name: str, expect_screen: str, summary: str, lang: str = "de"):
        self.lines: list[dict] = [{"mock": "scenario", "name": name, "protocol": 1, "lang": lang,
                                   "expect_screen": expect_screen, "summary": summary, "draft": True}]
        self.clock = Clock()
        self.name = name
        self._seq = 0
        self._cmd = None

    # -- process frame
    def invoke(self, cmd: str, args: list[str] | None = None, secrets: list[str] | None = None):
        self.lines.append({"mock": "invoke", "cmd": cmd, "args": args or [], "secrets": secrets or []})
        self._cmd, self._seq = cmd, 0
        self.ev("hello", engine_version=ENGINE, protocol=1, pymobiledevice3=PMD3, importer_version=ENGINE,
                models=["V56"], compat_digest="h:34ac0d10")

    def ev(self, type_: str, dt_s: float = 0.25, **fields):
        self._seq += 1
        self.lines.append({"v": 1, "seq": self._seq, "ts": self.clock.tick(dt_s), "cmd": self._cmd, "type": type_,
                           **fields})

    def result(self, ok: bool, code: str, data: dict, *, retryable: bool = False, device_modified: str = "no",
               exit_code: int | None = None, dt_s: float = 0.25):
        self.ev("result", dt_s, ok=ok, code=code, retryable=retryable, device_modified=device_modified, data=data)
        self.lines.append({"mock": "exit", "code": 0 if ok else (exit_code if exit_code is not None else 1)})

    def crash(self, signal_code: int = -9):
        self.lines.append({"mock": "exit", "code": signal_code, "crash": True})

    def user(self, screen: str, answer: str):
        self.lines.append({"mock": "user", "screen": screen, "answer": answer})

    def relaunch(self):
        self.lines.append({"mock": "relaunch"})

    def write(self):
        p = OUT / f"{self.name}.jsonl"
        if p.is_file():
            try:
                head = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
            except (ValueError, IndexError):
                head = {}
            if head.get("draft") is False:            # a recording (devtools/record_scenarios.py) is never replaced
                return None
        p.write_text("".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in self.lines),
                     encoding="utf-8")
        return p


# ------------------------------------------------------------------------------------------------ building blocks
def start(s: Scenario):
    s.user("S00", "get_started")
    s.user("S01", "accepted")
    s.invoke("host-check", ["--workdir", "<workdir>"])
    s.ev("phase", phase="checks", index=1, count=1)
    for cid in ("macos", "arch", "free_space", "fs_apfs", "power", "filevault"):
        s.ev("check", id=cid, status="pass")
    s.result(True, "R_OK", {"macos": "15.1", "arch": "arm64", "fs": "apfs", "free_bytes": 220 * GB,
                            "need_bytes": 40 * GB, "power": "ac", "battery_pct": None, "filevault": True})


def device_watch_ready(s: Scenario):
    s.invoke("device-watch", ["--session", "<session>"])
    s.ev("device", state="none")
    s.ev("prompt", 2.0, kind="unlock_device", active=True)
    s.ev("device", 1.0, state="locked", device=DEVICE, product_type=PRODUCT)
    s.ev("prompt", kind="unlock_device", active=False)
    s.ev("prompt", kind="trust_device", active=True)
    s.ev("device", 3.0, state="untrusted", device=DEVICE, product_type=PRODUCT)
    s.ev("prompt", 2.0, kind="trust_device", active=False)
    s.ev("device", state="ready", device=DEVICE, product_type=PRODUCT)
    s.result(True, "R_OK", {"events": 4})


def device_status(s: Scenario, *, build="24A437", compat="verified", find_my="off", threema_installed=True):
    s.invoke("device-status", ["--session", "<session>"])
    s.ev("phase", phase="read", index=1, count=1)
    s.ev("check", id="compat_ios", status="pass" if compat == "verified" else "fail",
         **({} if compat == "verified" else {"code": "E_IOS_UNKNOWN"}))
    s.ev("check", id="managed", status="pass")
    s.ev("check", id="threema_variant", status="pass" if threema_installed else "skip")
    s.ev("check", id="iphone_space", status="pass", data={"free_bytes": 48 * GB, "need_bytes": 10 * GB})
    s.ev("check", id="battery", status="pass", data={"battery_pct": 81, "charging": True})
    s.ev("check", id="findmy", status="pass" if find_my == "off" else "warn",
         **({} if find_my == "off" else {"code": "E_GUARD_FINDMY"}))
    s.ev("check", id="photos_limit", status="pass", data={"photos_bytes": 6 * GB, "limit_bytes": 20 * GB})
    s.result(True, "R_OK", {
        "device": DEVICE, "product_type": PRODUCT, "ios_version": "27.0", "ios_build": build, "compat": compat,
        "threema": {"installed": threema_installed, "variant": "regular" if threema_installed else "none",
                    "version": "7.4" if threema_installed else None},
        "encryption": "on", "find_my": find_my, "free_bytes": 48 * GB, "photos_bytes_estimate": 6 * GB,
        "battery_pct": 81, "charging": True, "managed": False})


def android(s: Scenario, *, wrong_password_first: bool = False):
    s.user("S04", "done")
    s.invoke("android-inspect", ["--session", "<session>", "<android-backup-0>"])
    s.ev("phase", phase="inspect", index=1, count=1)
    s.ev("check", id="android_file", status="pass", data={"ref": 0})
    s.ev("check", id="android_format", status="pass", data={"ref": 0, "format_version": 27})
    s.result(True, "R_OK", {"files": [{"ref": 0, "kind": "full", "format_version": 27,
                                       "created_at": s.clock.ts(-3600), "bytes": 6400000000, "has_media": True,
                                       "encrypted": True}],
                            "plan": "single", "text_ref": 0, "media_refs": [0]})
    if wrong_password_first:
        s.user("S05", "password_entered")
        s.invoke("android-normalize", ["--session", "<session>", "--plan", '{"text_ref":0,"media_refs":[0]}',
                                       "--secrets-stdin", "<android-backup-0>"], ["android_passwords"])
        s.ev("phase", phase="read", index=1, count=3)
        s.ev("check", 1.5, id="android_password", status="fail", code="E_ANDROID_PASSWORD", data={"ref": 0})
        s.result(False, "E_ANDROID_PASSWORD", {"ref": 0}, retryable=True, exit_code=1)
    s.user("S05", "password_entered")
    s.invoke("android-normalize", ["--session", "<session>", "--plan", '{"text_ref":0,"media_refs":[0]}',
                                   "--secrets-stdin", "<android-backup-0>"], ["android_passwords"])
    s.ev("phase", phase="read", index=1, count=3)
    s.ev("check", 1.5, id="android_password", status="pass", data={"ref": 0})
    s.ev("check", id="android_text", status="pass")
    s.ev("phase", phase="media", index=2, count=3)
    for done in (400, 800, 1234):
        s.ev("progress", 20.0, phase="media", pct=round(100 * done / 1234, 1), done=done, total=1234, unit="files")
    s.ev("phase", phase="verify", index=3, count=3)
    s.ev("check", id="duplicate_chat", status="pass")
    s.result(True, "R_OK", {"chats": 18, "groups": 4, "messages": 12345, "media_present": 1234, "media_total": 1234,
                            "polls": 2, "own_unsent_as_sent": 0, "missing_key_senders": 0,
                            "missing_key_messages": 0, "missing_key_groups": 0, "own_id": OWN_ID})


def prepare_iphone(s: Scenario):
    s.user("S07", "all_checked")
    device_status(s)                      # S08: Find My check on "Continue"
    s.user("S08", "all_checked")
    s.user("S09", "icloud")
    s.user("S10b", "password_entered")
    s.user("S11", "all_checked")


def backup_pre(s: Scenario, *, fail: str | None = None):
    s.invoke("backup", ["--session", "<session>", "--role", "pre", "--secrets-stdin"], ["backup_password"])
    s.ev("phase", phase="backup", index=1, count=2)
    s.ev("prompt", kind="passcode_on_device", active=True)
    s.ev("prompt", 5.0, kind="passcode_on_device", active=False)
    for pct in (10.0, 55.0, 100.0):
        s.ev("progress", 120.0, phase="backup", pct=pct, done=int(pct * 64_000_000), total=6_400_000_000,
             unit="bytes")
    s.ev("phase", phase="checks", index=2, count=2)
    s.ev("check", id="password", status="pass")
    s.ev("check", id="extract", status="pass")
    if fail == "airplane":
        s.ev("check", id="airplane", status="fail", code="E_GUARD_AIRPLANE")
        s.result(False, "E_GUARD_AIRPLANE", {}, retryable=True, exit_code=1)
        return
    s.ev("check", id="airplane", status="pass")
    for cid in ("threema_variant", "threema_setup", "threema_retention", "threema_model"):
        s.ev("check", id=cid, status="pass")
    if fail == "identity":
        s.ev("check", id="identity", status="fail", code="E_THREEMA_ID_MISMATCH")
        s.result(False, "E_THREEMA_ID_MISMATCH", {}, retryable=True, exit_code=1)
        return
    s.ev("check", id="identity", status="pass")
    s.ev("check", id="photos_limit", status="pass", data={"photos_bytes": 6 * GB, "limit_bytes": 20 * GB})
    s.result(True, "R_OK", {"role": "pre", "bytes": 6_400_000_000, "files": 12345, "password_ok": True,
                            "airplane": True, "threema": {"setup_ok": True, "retention_ok": True, "model": "V56",
                                                          "app_version": "7.4"},
                            "id_match": True, "photos_bytes": 6 * GB, "finished_at": s.clock.ts(),
                            "fresh_until": s.clock.ts(3600), "ios_build": "24A437"})


def prepare(s: Scenario):
    s.invoke("prepare", ["--session", "<session>", "--secrets-stdin"], ["backup_password"])
    steps = [("extract", "extract"), ("import", "import"), ("count", "verify_import"), ("build", "restoreset"),
             ("verify", "verify_restoreset")]
    for i, (ph, cid) in enumerate(steps, 1):
        s.ev("phase", 30.0, phase=ph, index=i, count=len(steps))
        if ph == "import":
            for done in (5000, 12345):
                s.ev("progress", 20.0, phase="import", pct=round(100 * done / 12345, 1), done=done, total=12345,
                     unit="items")
        s.ev("check", 10.0, id=cid, status="pass")
        if ph == "count":
            s.ev("check", id="coredata_open", status="pass")
    s.ev("check", id="freeze", status="pass")
    s.result(True, "R_OK", {"messages": 12345, "media": 1234, "skipped": 0, "payload_bytes": 6_400_000_000,
                            "iphone_required_bytes": 9_600_000_000, "fresh_until": s.clock.ts(3000)})


def restore(s: Scenario, *, outcome: str = "sent"):
    s.user("S14", "transfer_now")
    s.invoke("restore", ["--session", "<session>", "--secrets-stdin"], ["backup_password"])
    s.ev("phase", phase="guards", index=1, count=3)
    for cid in ("compat_ios", "device", "ios_unchanged"):
        s.ev("check", id=cid, status="pass")
    s.ev("check", id="freshness", status="pass", data={"age_min": 7, "limit_min": 60})
    s.ev("check", id="airplane", status="pass")
    s.ev("check", id="dcim_unchanged", status="pass", data={"files": 1234})
    s.ev("check", id="iphone_space", status="pass", data={"free_bytes": 48 * GB, "need_bytes": 9_600_000_000})
    s.ev("check", id="battery", status="pass", data={"battery_pct": 80, "charging": True})
    s.ev("check", id="findmy", status="pass")
    s.ev("check", 5.0, id="set_integrity", status="pass")
    s.ev("critical", on=True)
    s.ev("phase", phase="send", index=2, count=3)
    if outcome == "findmy_211":
        s.ev("critical", 3.0, on=False)
        s.result(False, "E_GUARD_FINDMY", {"source": "mberror_211"}, retryable=True, exit_code=1)
        return
    for pct in (12.5, 41.1, 77.0, 100.0):
        s.ev("progress", 45.0, phase="send", pct=pct, done=int(pct * 64_000_000), total=6_400_000_000, unit="bytes")
        if outcome == "crash" and pct == 77.0:
            s.crash()
            return
    s.ev("phase", phase="reboot", index=3, count=3)
    s.ev("critical", 20.0, on=False)
    s.result(True, "R_RESTORE_SENT_LINK_LOST", {"last_progress": 100, "finished_at": s.clock.ts()},
             device_modified="yes")


def after_restart(s: Scenario, answer: str = "account_only"):
    s.user("S16", answer)
    s.user("S17", "old_chats_present")


def backup_post_and_check(s: Scenario, *, verdict: str = "ok", answer: str = "account_only"):
    s.invoke("backup", ["--session", "<session>", "--role", "post", "--secrets-stdin"], ["backup_password"])
    s.ev("phase", phase="backup", index=1, count=2)
    s.ev("progress", 300.0, phase="backup", pct=100.0, done=6_400_000_000, total=6_400_000_000, unit="bytes")
    s.ev("phase", phase="checks", index=2, count=2)
    s.ev("check", id="password", status="pass")
    s.ev("check", id="extract", status="pass")
    s.result(True, "R_OK", {"role": "post", "bytes": 6_400_000_000, "files": 12345, "password_ok": True,
                            "airplane": True, "threema": {"setup_ok": True, "retention_ok": True, "model": "V56",
                                                          "app_version": "7.4"},
                            "photos_bytes": 6 * GB, "finished_at": s.clock.ts(), "fresh_until": None,
                            "ios_build": "24A437"})
    s.invoke("postcheck", ["--session", "<session>", "--buddy-answer", answer, "--secrets-stdin"],
             ["backup_password"])
    s.ev("phase", phase="threema", index=1, count=3)
    s.ev("check", id="post_after_restore", status="pass")
    s.ev("check", 20.0, id="p2_verify_import", status="pass")
    s.ev("phase", phase="compare", index=2, count=3)
    if verdict == "data":
        s.ev("check", 60.0, id="p3_payload", status="fail", data={"alerts": 1})
        s.ev("check", id="p4_strict", status="fail", data={"alerts": 2})
    else:
        s.ev("check", 60.0, id="p3_payload", status="pass")
        s.ev("check", id="p4_strict", status="pass")
    s.ev("check", id="purplebuddy", status="pass")
    s.ev("check", id="keychain_items", status="pass")
    s.ev("phase", phase="verdict", index=3, count=3)
    if verdict == "data":
        s.result(True, "R_OK", {"verdict": "data", "notes": [],
                                "areas": [{"area": "settings", "severity": "alert"},
                                          {"area": "sms_imessage", "severity": "alert"}],
                                "threema_ok": True})
    else:
        s.result(True, "R_OK", {"verdict": "ok", "notes": [], "areas": [], "threema_ok": True})


def cleanup(s: Scenario):
    s.user("S19", "continue")
    s.invoke("cleanup", ["--session", "<session>", "--what", "work"])
    s.ev("phase", phase="delete", index=1, count=1)
    s.result(True, "R_OK", {"freed_bytes": 12_800_000_000, "what": "work"})


def full_until_pre(s: Scenario, **android_kw):
    start(s)
    device_watch_ready(s)
    device_status(s)
    s.user("S03", "continue")
    android(s, **android_kw)
    prepare_iphone(s)


# ------------------------------------------------------------------------------------------------ scenarios
def build() -> list[Scenario]:
    out = []

    s = Scenario("happy", "S20", "full run, verdict ok, cleanup of the work folder")
    full_until_pre(s)
    backup_pre(s)
    prepare(s)
    restore(s)
    after_restart(s)
    backup_post_and_check(s)
    cleanup(s)
    out.append(s)

    s = Scenario("find_my_on", "F-FINDMY", "Find My still on: the device refuses the restore with MBError 211 "
                 "before staging; nothing changed")
    full_until_pre(s)
    backup_pre(s)
    prepare(s)
    restore(s, outcome="findmy_211")
    out.append(s)

    s = Scenario("airplane_off", "F-AIRPLANE", "PRE backup made with airplane mode off")
    full_until_pre(s)
    backup_pre(s, fail="airplane")
    out.append(s)

    s = Scenario("ios_unknown", "F-IOS-UNKNOWN", "iOS build not in compat/ios.json as verified; Android part may "
                 "still run")
    start(s)
    device_watch_ready(s)
    device_status(s, build="24B5070a", compat="unknown")
    out.append(s)

    s = Scenario("wrong_android_password", "S07", "first Android password wrong (inline S05), second right")
    start(s)
    device_watch_ready(s)
    device_status(s)
    s.user("S03", "continue")
    android(s, wrong_password_first=True)
    out.append(s)

    s = Scenario("id_mismatch", "F-THREEMA-ID", "Android backup belongs to a different Threema ID than the iPhone")
    full_until_pre(s)
    backup_pre(s, fail="identity")
    out.append(s)

    s = Scenario("data_fail", "S21", "postcheck verdict 'data' (something besides Threema changed) -> R2")
    full_until_pre(s)
    backup_pre(s)
    prepare(s)
    restore(s)
    after_restart(s)
    backup_post_and_check(s, verdict="data")
    out.append(s)

    s = Scenario("app_crash_after_send", "S16", "engine process dies during sending (no result after critical on); "
                 "on relaunch session-status resumes at S16, never 'start over'")
    full_until_pre(s)
    backup_pre(s)
    prepare(s)
    restore(s, outcome="crash")
    s.relaunch()
    s.invoke("session-status", ["--session", "<session>"])
    s.result(True, "R_OK", {"phase": "restore_sent", "resume_at": "S16", "restore_sent_at": s.clock.ts(-60),
                            "fresh_until": s.clock.ts(2400), "verdict": None})
    out.append(s)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for s in build():
        p = s.write()
        if p is None:
            print(f"kept {s.name}.jsonl (recorded from the engine; drafts never replace recordings)")
            continue
        print(f"wrote {p.relative_to(REPO)} ({len(s.lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
