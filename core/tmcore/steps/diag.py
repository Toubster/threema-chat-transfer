# SPDX-License-Identifier: AGPL-3.0-or-later
"""
diag-report (S21 "Diagnosebericht speichern"): one JSON file diag/diag-<utc>.json (0600, schema report.v1 kind diag).
Owner: coreA. Never uploaded; the app shows a preview before saving it somewhere else.

Content (DESIGN §10.1): the events of logs/events.jsonl (the app's stdout copy), the counts-only reports/*.json,
versions, compat stage of the iOS build, verdict, session phase and the codes seen. Every value is re-checked with the
protocol's safe-value rules (anything else is dropped, never "cleaned") and every "h:" hash is re-salted with a
fresh salt, so two reports cannot be linked through their hashes. debug.log is never read.
"""
from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path

from tmcore import __version__, hashing
from tmcore.cli import Context, StepResult, _pmd3_version
from tmcore.protocol import ProtocolViolation, check_safe, is_safe_string, utc_now

MAX_EVENTS = 20000
VERDICTS = ("setup_full", "data_keychain", "data", "threema_only", "restore_state", "needs_answer", "ok_with_notes",
            "ok")
PHASES = ("new", "host_checked", "android_done", "iphone_prepared", "pre_backup_done", "prepared", "restore_sent",
          "restore_finished", "post_backup_done", "postcheck_done", "rollback_sent", "closed")
ENVELOPE = ("v", "seq", "ts", "cmd", "type")


def _resalt(value, hasher: hashing.SessionHasher):
    if isinstance(value, str):
        return hasher.rehash(value) if value.startswith(hashing.PREFIX) else value
    if isinstance(value, list):
        return [_resalt(v, hasher) for v in value]
    if isinstance(value, dict):
        return {k: _resalt(v, hasher) for k, v in value.items()}
    return value


def _safe_event(ev) -> bool:
    if not isinstance(ev, dict) or any(k not in ev for k in ENVELOPE) or ev.get("v") != 1:
        return False
    if not isinstance(ev.get("cmd"), str) or not all(c.isalnum() or c == "-" for c in ev["cmd"]):
        return False
    try:
        check_safe({k: v for k, v in ev.items() if k != "cmd"})
    except ProtocolViolation:
        return False
    return True


def _events(session) -> tuple[list, int]:
    p = session.path("logs/events.jsonl")
    out: list[dict] = []
    dropped = 0
    if not p.is_file():
        return out, 0
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                dropped += 1
                continue
            if _safe_event(ev):
                out.append(ev)
            else:
                dropped += 1
    if len(out) > MAX_EVENTS:
        dropped += len(out) - MAX_EVENTS
        out = out[-MAX_EVENTS:]
    return out, dropped


def _reports(session) -> tuple[list, int]:
    out, dropped = [], 0
    for p in sorted(session.path("reports").glob("*.json")):
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
            if rep.get("schema") != "report.v1" or rep.get("kind") == "diag":
                raise ValueError
            check_safe({k: v for k, v in rep.items() if k not in ("schema", "cmd")})
            out.append(rep)
        except (OSError, ValueError, ProtocolViolation, AttributeError):
            dropped += 1
    return out, dropped


def _compat_stage(ctx: Context, build: str | None) -> str:
    if not build:
        return "none"
    try:
        for b in ctx.compat("ios").get("builds", []):
            if b.get("build") == build:
                s = b.get("status")
                return {"static-checked": "static_checked"}.get(s, s) if s in ("verified", "static-checked",
                                                                                "static_checked", "unknown",
                                                                                "blocked") else "unknown"
    except (OSError, ValueError):
        pass
    return "unknown"


def _version_or_none(v) -> str | None:
    return v if isinstance(v, str) and is_safe_string(v) and v[:1].isdigit() else None


def diag_report(ctx: Context) -> StepResult:
    proto, session = ctx.proto, ctx.session
    proto.phase("collect", 1, 1)
    st = session.engine_state()
    app = session.app_state()
    hasher = hashing.SessionHasher(hashing.new_salt())          # fresh salt per report, never stored

    events, ev_dropped = _events(session)
    reports, rep_dropped = _reports(session)
    pre = st.get("pre") or {}
    post = st.get("post") or {}
    ios_build = pre.get("ios_build") or post.get("ios_build")
    ios_build = ios_build if isinstance(ios_build, str) and is_safe_string(ios_build) else None
    product = st.get("device_product_type")
    product = product if isinstance(product, str) and is_safe_string(product) else None
    verdict = (st.get("postcheck") or {}).get("verdict")
    codes = sorted({str(h["code"]) for h in st.get("history") or [] if isinstance(h, dict)
                    and isinstance(h.get("code"), str) and is_safe_string(h["code"])})
    threema_model = None
    for r in reports:
        if r.get("kind") == "backup":
            m = (r.get("data") or {}).get("threema_model")
            threema_model = m if isinstance(m, str) and is_safe_string(m) else threema_model

    report = {
        "schema": "report.v1", "kind": "diag", "cmd": "diag-report", "created_at": utc_now(),
        "engine_version": __version__, "code": "R_OK",
        "counts": {"events": len(events), "events_dropped": ev_dropped, "reports": len(reports),
                   "reports_dropped": rep_dropped, "history": len(st.get("history") or [])},
        "codes": codes, "salt": "fresh",
        "versions": {"app": _version_or_none(app.get("app_version")) or "0", "engine": __version__,
                     "importer": __version__, "pymobiledevice3": _pmd3_version(),
                     "macos": _version_or_none(platform.mac_ver()[0]) or "0",
                     "ios": _version_or_none(pre.get("ios_version")), "ios_build": ios_build,
                     "product_type": product, "threema_model": threema_model},
        "compat": _compat_stage(ctx, ios_build),
        "verdict": verdict if verdict in VERDICTS else None,
        "session_phase": st.get("phase") if st.get("phase") in PHASES else "new",
        "events": _resalt(events, hasher),
        "reports": _resalt(reports, hasher),
    }
    stem = "diag-" + time.strftime("%Y%m%dt%H%M%Sz", time.gmtime())
    name = stem + ".json"
    n = 1
    while session.path("diag/" + name).exists():            # two reports in the same second: never overwrite
        n += 1
        name = f"{stem}-{n}.json"
    path = session.path("diag/" + name)
    tmp = path.with_name(f".{name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    proto.progress("collect", 1, 1, unit="items")
    return StepResult(data={"file": "diag/" + name, "bytes": Path(path).stat().st_size})
