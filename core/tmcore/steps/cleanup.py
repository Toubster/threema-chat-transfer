# SPDX-License-Identifier: AGPL-3.0-or-later
"""
cleanup --what work|pre|post|all (S20, discard). Owner: coreA.

  work   the readable chat copies: android/ (normalized.sqlite, media) and work/ (extract, store_out, restore set)
  pre    ios/pre  (the encrypted safety copy of the iPhone)
  post   ios/post (the control backup)
  all    all of the above and diag/
session.json, engine.json, reports/ (counts only) and logs/ stay; the app removes the folder itself when the user
discards the session. Frozen trees (chmod a-w) are made writable first. Result: freed_bytes, what.

Refused (E_PROTOCOL sub=cleanup_blocked) while the data is still needed:
  * a restore was sent and no postcheck verdict exists yet (the gate compares PRE/POST and re-reads the import)
  * PRE while R1 is still possible: verdict threema_only, rollback not used, PRE <= 6 h old (DESIGN §8.5)
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import os
import shutil
import stat
from pathlib import Path

from tmcore.cli import Context, StepResult
from tmcore.protocol import EngineError
from tmcore.session import SUBDIRS

ROLLBACK_LIMIT = _dt.timedelta(hours=6)
TARGETS = {"work": ("android", "work"), "pre": ("ios/pre",), "post": ("ios/post",),
           "all": ("android", "work", "ios/pre", "ios/post", "diag")}
PRE_RESTORE_PHASES = ("new", "host_checked", "android_done", "iphone_prepared", "pre_backup_done", "prepared")


def _size(p: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(p):
        for fn in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(dirpath, fn)).st_size
    return total


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


def _empty(p: Path) -> int:
    """Delete everything below p (p itself stays, 0700). Returns the bytes freed."""
    if not p.exists():
        p.mkdir(mode=0o700, parents=True, exist_ok=True)
        return 0
    freed = _size(p)
    _writable(p)
    for child in list(p.iterdir()):
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    os.chmod(p, 0o700)
    return freed


def _parse(s: str | None) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def _blocked(st: dict, what: str) -> bool:
    sent = st.get("restore_sent_at")
    verdict = (st.get("postcheck") or {}).get("verdict")
    if sent and (not verdict or verdict == "needs_answer"):
        return True
    if what in ("pre", "all") and verdict == "threema_only" and not st.get("rollback_used"):
        finished = _parse((st.get("pre") or {}).get("finished_at"))
        if finished and _dt.datetime.now(_dt.timezone.utc) - finished <= ROLLBACK_LIMIT:
            return True
    return False


def cleanup(ctx: Context) -> StepResult:
    proto, session = ctx.proto, ctx.session
    what = ctx.args.what
    proto.phase("delete", 1, 1)
    st = session.engine_state()
    if _blocked(st, what):
        raise EngineError("E_PROTOCOL", sub="cleanup_blocked")
    targets = TARGETS[what]
    freed = 0
    for i, key in enumerate(targets, 1):
        proto.check_cancel()
        freed += _empty(session.path(key))
        proto.progress("delete", i, len(targets), unit="items")
    for d in SUBDIRS:                            # the folder layout stays complete (work/tmp, work/restoreset, ...)
        session.path(d).mkdir(mode=0o700, parents=True, exist_ok=True)
    with session.update_engine() as w:
        if "android" in targets:
            w.pop("android", None)
            w["prepared"] = None
        if "work" in targets:
            w["prepared"] = None
        if "ios/pre" in targets:
            w["pre"] = None
            w["prepared"] = None
        if "ios/post" in targets:
            w["post"] = None
        cur = w.get("phase")
        if cur in PRE_RESTORE_PHASES and what != "post":  # before a restore the phase can only fall back to
            left = ("prepared" if w.get("prepared") else "pre_backup_done" if w.get("pre")   # what is left
                    else "android_done" if w.get("android") else "new")
            if PRE_RESTORE_PHASES.index(left) < PRE_RESTORE_PHASES.index(cur):
                w["phase"] = left
    session.write_report("cleanup", ctx.cmd, "R_OK", counts={"freed_bytes": freed, "targets": len(targets)})
    return StepResult(data={"freed_bytes": freed, "what": what})
