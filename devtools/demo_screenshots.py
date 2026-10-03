#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
demo_screenshots.py -- the REAL app in demo mode, end to end, and the app screenshots of the user guide
(DESIGN §13.1 level 7, §15). Owner: integrator. No device, no network, synthetic data only.

    python3 devtools/demo_screenshots.py                       # every scenario of docs/images/screenshots.json, DE + EN
    python3 devtools/demo_screenshots.py --scenarios happy find_my_on --langs de
    python3 devtools/demo_screenshots.py --install             # + marker tm-demo=1, copy to docs/images/app/<lang>/,
                                                               #   allowlist block "demo screenshots"
    python3 devtools/demo_screenshots.py --engine mock         # MockEngine replay instead of the virtual iPhone
    python3 devtools/demo_screenshots.py --all-scenarios       # every recording end to end (27 scenarios)

For each scenario and language the signed app (default build/dist/Chat Transfer for Threema.app, `make app`) starts with
TM_ENGINE=fake:<scenario>: the bundled tmcore runs every command on the virtual iPhone (tmcore --fake-device, no
socket at all) under the app's sandbox-exec profile. The app's demo autopilot (app/Sources/System/DemoAutopilot.swift)
plays the user directives of app/Tests/Scenarios/<scenario>.jsonl and saves the window of every wanted screen. A
scenario with a `relaunch` marker (app_crash_after_send) is started a second time on the same sessions folder. No UI
automation permission is needed; the window is rendered in-process (DEMO watermark included).

Checks per run: the autopilot ended on the scenario's expected screen, every wanted screenshot exists, the app bundle
is byte-identical afterwards (tree digest), the engine logs show no network attempt (no E_NETWORK_BLOCKED, no
netguard refusal in debug.log), and the canary scan (scripts/canary_scan.py) finds nothing in the engine output.
Output: build/demo/<lang>/<id>.png and build/demo/report.json (counts and screen ids only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIST = REPO / "docs" / "images" / "screenshots.json"
SCEN = REPO / "app" / "Tests" / "Scenarios"
RELAUNCH = 75


def rmtree(path: Path) -> None:
    """Sessions contain frozen (read-only) backups and restore sets: make them writable, then remove."""
    if not path.exists():
        return
    subprocess.run(["chmod", "-R", "u+w", str(path)], check=False, capture_output=True)
    shutil.rmtree(path)


def tree_digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            h.update(b"L" + p.relative_to(root).as_posix().encode() + os.readlink(p).encode())
        elif p.is_file():
            h.update(b"F" + p.relative_to(root).as_posix().encode() + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def android_files() -> dict[str, list[str]]:
    """The synthetic Android backups of the e2e flow (fixtures/gen_android_backup.py, cached in build/test-cache)."""
    sys.path.insert(0, str(REPO / "core"))
    from tests.e2e import flow  # noqa: PLC0415 -- test tree on purpose: one generator for e2e and demo
    kinds = ("full", "two_backups", "incomplete", "format_new", "wrong_password")
    return {k: [str(p) for p in flow.android_files(k)] for k in kinds}


def scenario_android_kind(name: str) -> str:
    sys.path.insert(0, str(REPO / "core"))
    from tmcore.fake import scenario as S  # noqa: PLC0415
    return S.load(name).flow.get("android", "full")


def wanted(lst: dict, scenarios: list[str] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for it in lst["groups"]["app"]["items"]:
        out.setdefault(it["scenario"], []).append(it["id"])
    if scenarios:
        out = {s: out.get(s, []) for s in scenarios}
    return out


def launch(app: Path, env: dict, timeout: float) -> int:
    exe = app / "Contents" / "MacOS" / app.stem
    # no window-state restoration: a run that was stopped must not leave the next start without its window
    p = subprocess.Popen([str(exe), "-ApplePersistenceIgnoreState", "YES"], env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        return p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
        return -1


def engine_output_problems(sessions: Path) -> list[str]:
    """Network attempts in the engine logs of a run (events + redacted debug.log)."""
    probs: list[str] = []
    for f in sessions.rglob("logs/events.jsonl"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"E_NETWORK_BLOCKED"' in line:
                probs.append("E_NETWORK_BLOCKED in events")
    for f in sessions.rglob("logs/debug.log"):
        t = f.read_text(encoding="utf-8", errors="replace")
        if "network access is blocked" in t or "NetworkBlocked" in t:
            probs.append("netguard refusal in debug.log")
    return probs


def canary_scan(sessions: Path, py: str) -> int:
    p = subprocess.run([py, str(REPO / "scripts" / "canary_scan.py"), "--canaries", str(REPO / "fixtures" / "canaries.json"),
                        str(sessions)], capture_output=True, text=True, timeout=110)
    return p.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", type=Path, default=REPO / "build" / "dist" / "Chat Transfer for Threema.app")
    ap.add_argument("--out", type=Path, default=REPO / "build" / "demo")
    ap.add_argument("--langs", nargs="+", default=["de", "en"])
    ap.add_argument("--scenarios", nargs="+")
    ap.add_argument("--all-scenarios", action="store_true",
                    help="every recording of app/Tests/Scenarios (end-to-end check; screenshots only where listed)")
    ap.add_argument("--engine", choices=("fake", "mock"), default="fake")
    ap.add_argument("--resources", type=Path, help="staged Resources/ with the engine (TM_DEMO_RESOURCES), for an "
                                                    "app built without packaging (e.g. the Debug build)")
    ap.add_argument("--size", default="940x680", help="window content size of the screenshots")
    ap.add_argument("--timeout", type=float, default=300)
    ap.add_argument("--install", action="store_true", help="mark, copy to docs/images/app/<lang>/, allowlist")
    a = ap.parse_args(argv)

    app = a.app.resolve()
    if not (app / "Contents" / "MacOS" / app.stem).is_file():
        print(f"demo_screenshots: no app at {app.name} (run `make app`)", file=sys.stderr)
        return 2
    lst = json.loads(LIST.read_text(encoding="utf-8"))
    if a.all_scenarios:
        a.scenarios = sorted(p.stem for p in SCEN.glob("*.jsonl"))
    plan = wanted(lst, a.scenarios)
    files = android_files() if a.engine == "fake" else {}
    before = tree_digest(app)
    out = a.out.resolve()
    work = out / "work"
    rmtree(work)
    report: dict = {"engine": a.engine, "runs": [], "bundle_unchanged": None}
    failures = 0
    for lang in a.langs:
        shots = out / lang
        shots.mkdir(parents=True, exist_ok=True)
        for scenario, ids in plan.items():
            if not (SCEN / f"{scenario}.jsonl").is_file():
                report["runs"].append({"scenario": scenario, "lang": lang, "error": "no recording"})
                failures += 1
                continue
            run_dir = work / f"{lang}-{scenario}"
            sessions = run_dir / "sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            rep = run_dir / "report.json"
            env = {"HOME": os.environ.get("HOME", ""), "USER": os.environ.get("USER", ""),
                   "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(run_dir),
                   "TM_ENGINE": f"{a.engine}:{scenario}", "TM_DEMO": "1", "TM_LANG": lang, "TM_APPEARANCE": "light",
                   "TM_MOCK_SPEED": "fast", "TM_SESSIONS_DIR": str(sessions), "TM_AUTOPILOT": "1",
                   "TM_AUTOPILOT_REPORT": str(rep), "TM_SCREENSHOT_DIR": str(shots),
                   "TM_SCREENSHOT_IDS": ",".join(ids), "TM_SCREENSHOT_SIZE": a.size}
            if a.engine == "fake":
                env["TM_DEMO_FIXTURES"] = str(REPO / "fixtures")
                if a.resources:
                    env["TM_DEMO_RESOURCES"] = str(a.resources.resolve())
                env["TM_AUTOPILOT_ANDROID_FILES"] = json.dumps(files[scenario_android_kind(scenario)])
            t0 = time.monotonic()
            rc = launch(app, env, a.timeout)
            starts = 1
            if rc == RELAUNCH:
                env["TM_AUTOPILOT_AFTER_RELAUNCH"] = "1"
                env["TM_MOCK_AFTER_RELAUNCH"] = "1"         # MockEngine: continue after the relaunch marker
                rc = launch(app, env, a.timeout)
                starts = 2
            r = json.loads(rep.read_text(encoding="utf-8")) if rep.is_file() else {}
            probs = engine_output_problems(sessions) if a.engine == "fake" else []
            canary_rc = canary_scan(sessions, sys.executable) if a.engine == "fake" else 0
            expect = None
            for line in (SCEN / f"{scenario}.jsonl").read_text(encoding="utf-8").splitlines()[:1]:
                expect = json.loads(line).get("expect_screen")
            end = r.get("end_screen")
            end_ok = end == expect or (expect == "S21" and (end or "").startswith(("S21-", "S22")))
            ok = rc == 0 and end_ok and not r.get("error") and not r.get("missing") and not probs and canary_rc == 0
            failures += 0 if ok else 1
            report["runs"].append({"scenario": scenario, "lang": lang, "exit": rc, "starts": starts,
                                   "seconds": round(time.monotonic() - t0, 1), "expect": expect, "end": end,
                                   "captured": r.get("captured", []), "missing": r.get("missing", ids),
                                   "error": r.get("error"), "network": probs, "canary_scan_rc": canary_rc, "ok": ok})
            print(f"{lang} {scenario:24s} {'ok ' if ok else 'BAD'} exit={rc} end={end} expect={expect} "
                  f"shots={len(r.get('captured', []))}/{len(ids)} {r.get('error') or ''}", flush=True)
    report["bundle_unchanged"] = tree_digest(app) == before
    if not report["bundle_unchanged"]:
        failures += 1
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"demo_screenshots: {len(report['runs'])} runs, {failures} problem(s), bundle unchanged: "
          f"{report['bundle_unchanged']}", flush=True)
    if a.install and failures == 0:
        pngs = []
        for lang in a.langs:
            dest = REPO / "docs" / "images" / "app" / lang
            dest.mkdir(parents=True, exist_ok=True)
            for f in sorted((out / lang).glob("*.png")):
                shutil.copy2(f, dest / f.name)
                pngs.append(dest / f.name)
        py = sys.executable
        subprocess.run([py, str(REPO / ".github" / "scripts" / "mark_png.py"), "--marker", "tm-demo=1", *map(str, pngs)],
                       check=True)
        all_pngs = sorted((REPO / "docs" / "images" / "app").glob("*/*.png"))
        subprocess.run([py, str(REPO / ".github" / "scripts" / "allowlist_block.py"), "--block", "demo screenshots",
                        *map(str, all_pngs)], check=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
