# SPDX-License-Identifier: AGPL-3.0-or-later
"""
version / selftest / session-status. Owner: coreA (devtools/OWNERSHIP.md).

selftest (phases imports, bundle, netguard; checks modules, bundle_hashes, netguard):
  imports  every tmcore module and the runtime libraries; pymobiledevice3 is required in the bundle
           (TMCORE_RESOURCES set) and optional in a source checkout
  bundle   compat lists parse, every compat model has its .momd, the importer answers `--version` with the engine
           version (bundle: required), and -- when the bundle carries one -- every digest of
           <Resources>/bundle-manifest.json matches ({"schema": 1, "files": {"<relative path>": "<sha256 hex>"}},
           written by packaging/tools/bundle_manifest.py from stage-resources.sh and again after sign-adhoc.sh)
  netguard an AF_INET socket and a DNS lookup are refused
  Any failure -> E_INTERNAL sub=modules|bundle|netguard.

session-status: phase, resume_at (DESIGN §8.1), restore_sent_at, fresh_until, verdict -- engine.json only, read only.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import importlib
import json
import os
import platform
import pkgutil
import subprocess
from pathlib import Path

from tmcore import PROTOCOL, __version__, netguard
from tmcore.cli import Context, StepResult, _pmd3_version, compat_digest, model_ids
from tmcore.protocol import EngineError

RUNTIME_LIBS = ("pyzipper", "cryptography", "iphone_backup_decrypt", "pyiosbackup", "construct")
DEVICE_LIBS = ("pymobiledevice3",)
RED = ("setup_full", "data_keychain", "data", "threema_only", "restore_state")


def _tree_sha256(root: Path) -> str | None:
    """sha256 over the sorted (relative path, file sha256) list of a directory, e.g. a .momd bundle."""
    if not root.is_dir():
        return None
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return "sha256:" + h.hexdigest()


def version(ctx: Context) -> StepResult:
    models = model_ids(ctx.resources)
    data: dict = {"engine_version": __version__, "protocol": PROTOCOL, "pymobiledevice3": _pmd3_version(),
                  "importer_version": __version__, "python": platform.python_version(), "models": models,
                  "compat_digest": compat_digest(ctx.resources)}
    model_sha = [{"model": m, "sha256": d} for m in models
                 if (d := _tree_sha256(ctx.models_dir / m / "ThreemaData.momd"))]
    if model_sha:
        data["model_sha256"] = model_sha
    importer = ctx.resources / "bin" / "threema-import"
    if importer.is_file():
        data["importer_sha256"] = "sha256:" + hashlib.sha256(importer.read_bytes()).hexdigest()
    h = hashlib.sha256()
    for name in ("ios.json", "threema-ios.json", "android.json"):
        p = ctx.compat_dir / name
        if p.exists():
            h.update(p.read_bytes())
    data["compat_sha256"] = "sha256:" + h.hexdigest()
    return StepResult(data=data)


# ------------------------------------------------------------------------------------------------ selftest
def _bundle_mode() -> bool:
    return bool(os.environ.get("TMCORE_RESOURCES"))


def _import_all() -> tuple[int, list[str]]:
    import tmcore
    ok, bad = 0, []
    names = ["tmcore"] + [m.name for m in pkgutil.walk_packages(tmcore.__path__, "tmcore.")
                          if ".tests" not in m.name and not m.name.endswith("__main__")]
    libs = list(RUNTIME_LIBS) + (list(DEVICE_LIBS) if _bundle_mode() else [])
    for name in names + libs:
        try:
            importlib.import_module(name)
            ok += 1
        except Exception:  # noqa: BLE001 -- counted, never printed with its message
            bad.append(name)
    return ok, bad


def _bundle(ctx: Context) -> tuple[bool, dict]:
    facts = {"models": 0, "files": 0, "importer": False}
    try:
        for name in ("ios", "threema-ios", "android"):
            ctx.compat(name)
        models = model_ids(ctx.resources)
    except (OSError, ValueError):
        return False, facts
    for m in models:
        if not ctx.momd(m).is_dir():
            return False, facts
        facts["models"] += 1
    importer = ctx.importer
    if importer.is_file():
        try:
            p = subprocess.run([str(importer), "--version"], capture_output=True, text=True, timeout=30)
            v = json.loads(p.stdout or "{}").get("importer_version") if p.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            v = None
        facts["importer"] = v == __version__
        if not facts["importer"]:
            return False, facts
    elif _bundle_mode():
        return False, facts
    manifest = ctx.resources / "bundle-manifest.json"
    if manifest.is_file():
        try:
            files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        except (OSError, ValueError, KeyError):
            return False, facts
        for rel, digest in files.items():
            p = (ctx.resources / rel).resolve()
            if ctx.resources.resolve() not in p.parents or not p.is_file():
                return False, facts
            if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                return False, facts
            facts["files"] += 1
    elif _bundle_mode():
        return False, facts
    return True, facts


def selftest(ctx: Context) -> StepResult:
    proto = ctx.proto
    proto.phase("imports", 1, 3)
    n_ok, bad = _import_all()
    proto.check("modules", "fail" if bad else "pass", "E_INTERNAL" if bad else None, modules=n_ok, failed=len(bad))
    proto.phase("bundle", 2, 3)
    bundle_ok, facts = _bundle(ctx)
    proto.check("bundle_hashes", "pass" if bundle_ok else "fail", None if bundle_ok else "E_INTERNAL", **facts)
    proto.phase("netguard", 3, 3)
    guard_ok = netguard.is_active() and netguard.self_test()
    proto.check("netguard", "pass" if guard_ok else "fail", None if guard_ok else "E_INTERNAL",
                unix=netguard.allows_unix())
    if bad:
        raise EngineError("E_INTERNAL", sub="modules")
    if not bundle_ok:
        raise EngineError("E_INTERNAL", sub="bundle")
    if not guard_ok:
        raise EngineError("E_INTERNAL", sub="netguard")
    return StepResult(data={"modules_ok": n_ok, "bundle_ok": True, "netguard": True,
                            "python": platform.python_version()})


# ------------------------------------------------------------------------------------------------ session-status
def _ts(s: str | None) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def resume_screen(st: dict, now: _dt.datetime | None = None) -> str:
    """DESIGN §8.1 resume table. Once a restore was sent (also without a result after critical) it is S16 until a
    postcheck newer than that restore exists -- never 'start over'."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    post = st.get("postcheck") or {}
    sent = _ts(st.get("restore_sent_at"))
    verdict = post.get("verdict")
    post_at = _ts(post.get("at"))
    if sent and (not verdict or verdict == "needs_answer" or (post_at and post_at < sent)):
        return "S16"
    if verdict:
        if verdict in RED:
            return "S21"
        if verdict == "needs_answer":
            return "S16"
        return "S20"
    if st.get("phase") == "closed":
        return "S20"
    pre = st.get("pre") or {}
    if pre.get("backup_id"):
        prepared = st.get("prepared") or {}
        fresh = _ts(prepared.get("fresh_until") or pre.get("fresh_until"))
        if fresh is None or now > fresh:
            return "S11"
        if prepared.get("pre_backup_id") == pre.get("backup_id"):
            return "S14"
        return "S13"
    if st.get("android"):
        return "S07"
    return "S00"


def session_status(ctx: Context) -> StepResult:
    st = ctx.session.engine_state()
    prepared, pre = st.get("prepared") or {}, st.get("pre") or {}
    fresh = prepared.get("fresh_until") or pre.get("fresh_until")
    verdict = (st.get("postcheck") or {}).get("verdict")
    return StepResult(data={"phase": st.get("phase") or "new", "resume_at": resume_screen(st),
                            "restore_sent_at": st.get("restore_sent_at"), "fresh_until": fresh, "verdict": verdict})
