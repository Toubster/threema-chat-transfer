# SPDX-License-Identifier: AGPL-3.0-or-later
"""
prepare (S13): build and freeze the ONE restore payload from the PRE backup. Owner: coreA. Nothing on the iPhone
changes; no simulator (DESIGN §19). Session paths: devtools/CONTRACT-REQUESTS.md ("session paths").

  extract  freshness of the PRE backup (E_GUARD_FRESHNESS), lib.backup_pipeline extract -> work/extract (reused when
           work/extract/.backup_id == engine.json pre.backup_id), STOP checks of the extract report: encrypted,
           keychain, Threema group container, AppSetupState 40 / no setup marker, retention off, store integrity,
           store model in compat/threema-ios.json (E_BACKUP_ENCRYPTION_OFF, E_THREEMA_MISSING, E_THREEMA_NOT_SET_UP,
           E_THREEMA_RETENTION, E_THREEMA_MODEL_UNKNOWN, E_GUARD_SET_INTEGRITY sub=source)
  import   threema-import (bundled binary) android/normalized.sqlite -> work/store_out with the momd of exactly that
           model; duplicate 1:1 chats in the iPhone store -> E_IMPORT_DUPLICATE_CHAT
  count    lib.verify_import (independent re-read + Core Data re-open without migration) -> checks verify_import,
           coredata_open
  build    lib.backup_pipeline restoreset --store-out -> work/restoreset/<UDID>/ (APFS clone; Threema + complete
           Home/CameraRoll/Keyboard, nothing else; there is no partial payload)
  verify   lib.backup_pipeline verify --restoreset --source <PRE> --against work/store_out, then freeze the set
           (chmod -R a-w); E_GUARD_SET_INTEGRITY sub=structure|verify
engine.json prepared {finished_at, fresh_until, payload_bytes, set_sha256, pre_backup_id}; phase prepared.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from tmcore.cli import Context, StepResult
from tmcore.lib import backup_pipeline as bp
from tmcore.lib import verify_import as vi
from tmcore.protocol import Cancelled, EngineError, utc_now

FRESH_LIMIT_MIN = 60
SPACE_FACTOR = 1.5
PREFS_REL = f"{bp.GROUP_DOMAIN}/Library/Preferences/group.ch.threema.plist"
PHASES = ("extract", "import", "count", "build", "verify")


def _parse_ts(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(t: _dt.datetime) -> str:
    return t.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _writable(root: Path) -> None:
    for dirpath, _dirs, files in os.walk(root):
        with contextlib.suppress(OSError):
            os.chmod(dirpath, os.stat(dirpath).st_mode | stat.S_IRWXU)
        for fn in files:
            fp = os.path.join(dirpath, fn)
            with contextlib.suppress(OSError):
                st = os.lstat(fp)
                if not stat.S_ISLNK(st.st_mode):
                    os.chmod(fp, st.st_mode | stat.S_IWUSR)


def _remove(p: Path) -> None:
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.exists():
        _writable(p)
        shutil.rmtree(p)


def _freeze(root: Path) -> None:
    """chmod -R a-w (files and directories), as the proof of concept froze verified restore sets."""
    for dirpath, dirs, files in os.walk(root, topdown=False):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            st = os.lstat(fp)
            if not stat.S_ISLNK(st.st_mode):
                os.chmod(fp, stat.S_IMODE(st.st_mode) & ~0o222)
        os.chmod(dirpath, stat.S_IMODE(os.stat(dirpath).st_mode) & ~0o222)


def _pre_backup_dir(session) -> Path | None:
    root = session.path("ios/pre")
    devs = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")] if root.is_dir() else []
    return devs[0] if len(devs) == 1 else None


def _bp(cmd: str, password: str, **kw) -> tuple[int, dict | None]:
    """backup_pipeline in-process; PipelineError/ValueError propagate (mapped by the caller)."""
    return bp.run(cmd, password=password, **kw)


# ------------------------------------------------------------------------------------------------ extract
def _extract_checks(proto, rep: dict, ctx: Context) -> str:
    """STOP checks of the PRE extract (proof of concept: migrate.sh check_extract). Returns the model id."""
    backup = rep.get("backup") or {}
    gc = rep.get("group_container") or {}
    gp = gc.get("group_prefs") or {}
    store = rep.get("store") or {}
    md = store.get("metadata") or {}
    app = rep.get("threema_app") or {}
    app_version = app.get("CFBundleShortVersionString")
    app_version = app_version if isinstance(app_version, str) and app_version[:1].isdigit() else None
    errors = rep.get("errors") or []

    def fail(code: str, **data):
        proto.check("extract", "fail", code)
        raise EngineError(code, **data)

    if backup.get("encrypted") is not True:
        fail("E_BACKUP_ENCRYPTION_OFF")
    if not (rep.get("keychain") or {}).get("present"):
        fail("E_GUARD_SET_INTEGRITY", sub="source")
    if not gc.get("db_present") or any("not in backup" in str(e) for e in errors):
        fail("E_THREEMA_MISSING")
    if gc.get("app_setup_not_completed_marker") or gp.get("AppSetupState") != 40:
        fail("E_THREEMA_NOT_SET_UP")
    if gp.get("retention_active"):
        fail("E_THREEMA_RETENTION")
    if store.get("integrity_check") != "ok" or errors or rep.get("source_unchanged") is not True:
        fail("E_GUARD_SET_INTEGRITY", sub="source")
    model_id = md.get("model_id")
    if not model_id or not ctx.momd(model_id).is_dir():
        proto.check("extract", "pass")
        proto.check("threema_model", "fail", "E_THREEMA_MODEL_UNKNOWN")
        raise EngineError("E_THREEMA_MODEL_UNKNOWN", app_version=app_version)
    proto.check("extract", "pass", files=(rep.get("threema_domains") or {}).get(bp.GROUP_DOMAIN, {}).get("files", 0))
    proto.check("threema_model", "pass", model=model_id)
    return model_id


def _extract(ctx: Context, password: str, pre_dev: Path, backup_id: str) -> tuple[dict, str]:
    proto, session = ctx.proto, ctx.session
    out = session.path("work/extract")
    stamp = out / ".backup_id"
    rep_path = out / "report.json"
    reuse = stamp.is_file() and stamp.read_text().strip() == backup_id and rep_path.is_file()
    if not reuse:
        _remove(out)
        try:
            rc, _ = _bp("extract", password, backup_udid_dir=str(pre_dev), out_dir=str(out))
        except ValueError:                                  # keybag does not open: wrong password
            proto.check("password", "fail", "E_BACKUP_PASSWORD")
            raise EngineError("E_BACKUP_PASSWORD", attempt=1, max=1) from None
        except bp.PipelineError:
            if rep_path.is_file() and (json.loads(rep_path.read_text()).get("error") == "backup not encrypted"):
                proto.check("extract", "fail", "E_BACKUP_ENCRYPTION_OFF")
                raise EngineError("E_BACKUP_ENCRYPTION_OFF") from None
            proto.check("extract", "fail", "E_GUARD_SET_INTEGRITY")
            raise EngineError("E_GUARD_SET_INTEGRITY", sub="source") from None
        fd = os.open(stamp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(backup_id + "\n")
    rep = json.loads(rep_path.read_text())
    return rep, _extract_checks(proto, rep, ctx)


# ------------------------------------------------------------------------------------------------ import
def _run_importer(ctx: Context, argv: list[str]) -> int:
    """threema-import as a child process. stdout (a counts-only JSON) is discarded, stderr goes to the debug log
    (redacted); SIGTERM outside critical stops the child and ends with E_CANCELLED."""
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         text=True, errors="replace")
    try:
        while True:
            try:
                p.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if ctx.proto.cancel_requested:
                    p.terminate()
                    p.wait(timeout=30)
                    raise Cancelled()
    finally:
        err = p.stderr.read() if p.stderr else ""
        for line in err.splitlines()[-20:]:
            print("[importer] " + line, file=sys.stderr)
    return p.returncode


def _import(ctx: Context, model_id: str) -> dict:
    proto, session = ctx.proto, ctx.session
    importer = ctx.importer
    if not importer.is_file() or not os.access(importer, os.X_OK):
        proto.check("import", "fail", "E_INTERNAL")
        raise EngineError("E_INTERNAL", sub="importer_missing")
    store_out = session.path("work/store_out")
    _remove(store_out)
    report = session.path("work/import-report.json")
    report.unlink(missing_ok=True)
    extract = session.path("work/extract")
    argv = [str(importer), "--normalized", str(session.path("android/normalized.sqlite")),
            "--work-dir", str(session.path("android")), "--store-in", str(extract / "store"),
            "--store-out", str(store_out), "--momd", str(ctx.momd(model_id)), "--fill-missing-avatars",
            "--report", str(report)]
    if (extract / PREFS_REL).is_file():
        argv += ["--app-prefs", str(extract / PREFS_REL)]
    rc = _run_importer(ctx, argv)
    rep = json.loads(report.read_text()) if report.is_file() else {}
    info = rep if isinstance(rep, dict) else {}
    dups = (((info.get("entities") or {}).get("store") or {}).get("details") or {}).get("duplicate_1to1_conversations", 0)
    if rc != 0:
        code = info.get("error_code")
        if code == "duplicate_1to1" or dups:
            proto.check("duplicate_chat", "fail", "E_IMPORT_DUPLICATE_CHAT", chats=int(dups or 0))
            raise EngineError("E_IMPORT_DUPLICATE_CHAT", chats=int(dups or 0))
        if code == "model_incompatible":
            proto.check("import", "fail", "E_THREEMA_MODEL_UNKNOWN")
            raise EngineError("E_THREEMA_MODEL_UNKNOWN", app_version=None)
        proto.check("import", "fail", "E_INTERNAL")
        raise EngineError("E_INTERNAL", sub="import")
    proto.check("duplicate_chat", "pass", chats=0)
    ents = info.get("entities") or {}
    msgs = ents.get("messages") or {}
    media = ents.get("media") or {}
    counts = {"messages": int(msgs.get("inserted", 0)), "skipped": int(msgs.get("skipped_total", 0)),
              "media": int(media.get("inserted", 0)), "existing": int(msgs.get("existing", 0))}
    proto.check("import", "pass", messages=counts["messages"], media=counts["media"], skipped=counts["skipped"])
    return counts


def _verify_import(ctx: Context, model_id: str) -> dict:
    proto, session = ctx.proto, ctx.session
    report = session.path("work/verify-import.json")
    argv = ["--normalized", str(session.path("android/normalized.sqlite")), "--work-dir", str(session.path("android")),
            "--store", str(session.path("work/store_out")), "--store-in", str(session.path("work/extract/store")),
            "--momd", str(ctx.momd(model_id)), "--importer", str(ctx.importer), "--report", str(report)]
    rc = vi.main(argv, quiet=True)
    rep = json.loads(report.read_text()) if report.is_file() else {}
    failures = rep.get("failures") or {}
    n_ok = sum((rep.get("checks_passed") or {}).values())
    core_fail = "coredata_reopen_without_migration" in failures or "coredata_model_compatible" in failures
    proto.check("coredata_open", "fail" if core_fail else "pass", "E_INTERNAL" if core_fail else None)
    if rc != 0 or failures:
        proto.check("verify_import", "fail", "E_INTERNAL", checks=n_ok, failures=sum(failures.values()))
        raise EngineError("E_INTERNAL", sub="coredata_open" if core_fail else "verify_import")
    proto.check("verify_import", "pass", checks=n_ok, failures=0)
    return {"verify_checks": n_ok}


# ------------------------------------------------------------------------------------------------ build + verify
def _build(ctx: Context, password: str, pre_dev: Path) -> tuple[Path, dict]:
    proto, session = ctx.proto, ctx.session
    root = session.path("work/restoreset")
    _remove(root)
    root.mkdir(mode=0o700, parents=True)
    try:
        rc, summ = _bp("restoreset", password, backup_udid_dir=str(pre_dev), out_root=str(root),
                       store_out=str(session.path("work/store_out")))
    except (bp.PipelineError, ValueError):
        proto.check("restoreset", "fail", "E_GUARD_SET_INTEGRITY")
        raise EngineError("E_GUARD_SET_INTEGRITY", sub="layout") from None
    summ = summ or {}
    if rc != 0 or summ.get("result") != "OK":
        proto.check("restoreset", "fail", "E_GUARD_SET_INTEGRITY")
        raise EngineError("E_GUARD_SET_INTEGRITY", sub="structure")
    counts = {k: int(summ.get(k) or 0) for k in ("home_rows", "cameraroll_rows", "keyboard_rows", "threema_rows",
                                                "payload_bytes")}
    proto.check("restoreset", "pass", None, **counts)
    return root / pre_dev.name, counts


def _verify_set(ctx: Context, password: str, set_dev: Path, pre_dev: Path) -> str:
    proto, session = ctx.proto, ctx.session
    try:
        rc, summ = _bp("verify", password, backup_udid_dir=str(set_dev), source=str(pre_dev), restoreset=True,
                       against=str(session.path("work/store_out")))
    except (bp.PipelineError, ValueError):
        rc, summ = 2, None
    if rc != 0 or (summ or {}).get("result") != "PASS":
        proto.check("verify_restoreset", "fail", "E_GUARD_SET_INTEGRITY")
        raise EngineError("E_GUARD_SET_INTEGRITY", sub="verify")
    proto.check("verify_restoreset", "pass", files=int(((summ or {}).get("threema_files_decrypted") or 0)))
    marker = set_dev / bp.RESTORESET_MARKER
    set_sha = marker.read_text().strip()
    _freeze(set_dev.parent)
    proto.check("freeze", "pass")
    return set_sha


# ------------------------------------------------------------------------------------------------ step
def prepare(ctx: Context) -> StepResult:
    proto, session = ctx.proto, ctx.session
    st = session.engine_state()
    pre, android = st.get("pre"), st.get("android")
    if not android or not session.path("android/normalized.sqlite").is_file():
        raise EngineError("E_PROTOCOL", sub="no_android")
    pre_dev = _pre_backup_dir(session)
    if not pre or not pre.get("backup_id") or pre_dev is None:
        raise EngineError("E_PROTOCOL", sub="no_pre_backup")
    finished = _parse_ts(pre.get("finished_at"))
    # only a PRE backup whose checks all passed (password, airplane, Threema, identity, photos) is ever prepared:
    # `backup` sets fresh_until and password_ok only then (REVIEW m1; restore re-checks it as well)
    fresh_until = _parse_ts(pre.get("fresh_until"))
    if fresh_until is None or pre.get("password_ok") is not True or finished is None:
        raise EngineError("E_PROTOCOL", sub="pre_checks_failed")
    password = ctx.secrets.require("backup_password")
    bp.use_tmp(session.tmp)

    def freshness() -> None:
        now = _dt.datetime.now(_dt.timezone.utc)
        age = int((now - finished).total_seconds() // 60) if finished else FRESH_LIMIT_MIN + 1
        if now > fresh_until:
            proto.check("freshness", "fail", "E_GUARD_FRESHNESS", age_min=age, limit_min=FRESH_LIMIT_MIN)
            with session.update_engine() as w:
                w["prepared"] = None
            raise EngineError("E_GUARD_FRESHNESS", age_min=age, limit_min=FRESH_LIMIT_MIN)
        proto.check("freshness", "pass", age_min=max(age, 0), limit_min=FRESH_LIMIT_MIN)

    with session.update_engine() as w:
        w["prepared"] = None                          # whatever was prepared before is gone from here on
    proto.phase("extract", 1, len(PHASES))
    freshness()
    _rep, model_id = _extract(ctx, password, pre_dev, pre["backup_id"])
    proto.check_cancel()

    proto.phase("import", 2, len(PHASES))
    imp = _import(ctx, model_id)
    proto.check_cancel()

    proto.phase("count", 3, len(PHASES))
    ver = _verify_import(ctx, model_id)
    proto.check_cancel()

    proto.phase("build", 4, len(PHASES))
    set_dev, set_counts = _build(ctx, password, pre_dev)
    proto.check_cancel()

    proto.phase("verify", 5, len(PHASES))
    set_sha = _verify_set(ctx, password, set_dev, pre_dev)
    freshness()

    payload = set_counts["payload_bytes"]
    data = {"messages": imp["messages"], "media": imp["media"], "skipped": imp["skipped"], "payload_bytes": payload,
            "iphone_required_bytes": int(math.ceil(SPACE_FACTOR * payload)), "fresh_until": _iso(fresh_until)}
    with session.update_engine() as w:
        w["prepared"] = {"finished_at": utc_now(), "fresh_until": _iso(fresh_until), "payload_bytes": payload,
                         "set_sha256": set_sha, "pre_backup_id": pre["backup_id"]}
        session.advance_phase(w, "prepared")
    session.write_report("prepare", ctx.cmd, "R_OK", counts={**imp, **ver, **set_counts,
                                                              "iphone_required_bytes": data["iphone_required_bytes"],
                                                              "duration_s": int(time.monotonic() - _T0)})
    return StepResult(data=data)


_T0 = time.monotonic()
