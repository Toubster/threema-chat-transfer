# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Shared helpers for the core tests (owner: coreA). Synthetic data only, no device, no password files.

    importer()            -> path of threema-import: TMCORE_IMPORTER, else built from importer/Sources (cached)
    momd()                -> model/V56/ThreemaData.momd of this checkout
    empty_store()         -> dir with an EMPTY V56 ThreemaData.sqlite (created by Core Data, cached)
    sample_store()        -> dir with a V56 store holding the importer's synthetic mini fixture (+ _EXTERNAL_DATA)
    ios_fixture()         -> fixtures/gen_ios_backup.py as a module (stores injected if it has no own builder)
    android_fixture()     -> fixtures/gen_android_backup.py as a module
    run_tmcore(argv, ...) -> (rc, stdout, stderr) of `python -m tmcore` as the app starts it (no inherited PYTHON*)
    python_with(*mods)    -> an interpreter that can import mods: this one, else the staged bundle runtime, else None
    run_guarded(code,...) -> JSON dict printed by `code` in a fresh interpreter (cwd core/: this checkout's tmcore)

Everything that is built lands in <repo>/build/test-cache/ (git-ignored), keyed by the sha256 of its sources.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "core"
CACHE = REPO / "build" / "test-cache"
HERE = Path(__file__).resolve().parent


def momd() -> Path:
    return REPO / "model" / "V56" / "ThreemaData.momd"


def canaries() -> dict:
    return json.loads((REPO / "fixtures" / "canaries.json").read_text(encoding="utf-8"))


def canary_values() -> dict[str, str]:
    """Every canary string (DESIGN §10.2) by name; the UDID is joined from its parts."""
    c = canaries()
    out = {k: v for k, v in c.items() if isinstance(v, str) and k not in ("about", "udid_note")}
    out["udid"] = "-".join(c["udid_parts"])
    return out


@contextlib.contextmanager
def _lock(name: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    fd = os.open(CACHE / f".{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _digest(paths: list[Path], extra: str = "") -> str:
    h = hashlib.sha256(extra.encode())
    for p in sorted(paths):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


def _swift(name: str, sources: list[Path], flags: list[str]) -> Path:
    key = _digest(sources, " ".join(flags))
    out = CACHE / "bin" / f"{name}-{key}"
    with _lock(f"swift-{name}"):
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + f".tmp{os.getpid()}")
            p = subprocess.run(["xcrun", "swiftc", "-O", "-swift-version", "5", "-target", "arm64-apple-macosx14.0",
                                "-o", str(tmp), *map(str, sources), *flags], capture_output=True, text=True,
                               timeout=600)
            if p.returncode != 0:
                raise RuntimeError(f"swiftc {name} failed:\n{p.stderr[-3000:]}")
            os.replace(tmp, out)
    return out


def importer() -> Path:
    env = os.environ.get("TMCORE_IMPORTER")
    if env:
        return Path(env)
    srcs = sorted((REPO / "importer" / "Sources" / "ThreemaImport").glob("*.swift"))
    return _swift("threema-import", srcs, ["-framework", "CoreData", "-framework", "AVFoundation", "-framework",
                                           "ImageIO", "-framework", "CoreGraphics", "-lsqlite3"])


def mini_normalized_script() -> Path:
    """The importer's synthetic 'every message kind' normalized fixture (pack moves files; look in both places)."""
    for p in (REPO / "importer" / "Tests" / "Fixtures" / "make_mini_normalized.py",
              REPO / "importer" / "Tests" / "make_mini_normalized.py"):
        if p.is_file():
            return p
    raise FileNotFoundError("make_mini_normalized.py not found under importer/Tests")


def make_mini_normalized(out_dir: Path) -> Path:
    p = subprocess.run([sys.executable, str(mini_normalized_script()), str(out_dir)], capture_output=True, text=True,
                       timeout=300)
    if p.returncode != 0:
        raise RuntimeError("make_mini_normalized failed: " + p.stderr[-2000:])
    return out_dir


def _stores_dir() -> Path:
    key = _digest([HERE / "make_empty_store.swift", mini_normalized_script(),
                   *sorted((REPO / "importer" / "Sources" / "ThreemaImport").glob("*.swift")),
                   *sorted(momd().rglob("*.mom*"))])
    return CACHE / "stores" / key


def empty_store() -> Path:
    d = _stores_dir() / "empty"
    with _lock("stores"):
        if not (d / "ThreemaData.sqlite").is_file():
            tool = _swift("make_empty_store", [HERE / "make_empty_store.swift"], ["-framework", "CoreData"])
            tmp = d.with_name(f"empty.tmp{os.getpid()}")
            shutil.rmtree(tmp, ignore_errors=True)
            p = subprocess.run([str(tool), str(momd()), str(tmp)], capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                raise RuntimeError("make_empty_store failed: " + p.stderr[-2000:])
            os.replace(tmp, d)
    return d


def sample_store() -> Path:
    """The importer's synthetic mini fixture imported into the empty store: contacts, groups, messages, ballots and
    external data -- a realistic Safe-restored-and-used store for backup fixtures."""
    d = _stores_dir() / "sample"
    empty = empty_store()
    with _lock("stores"):
        if not (d / "ThreemaData.sqlite").is_file():
            work = d.with_name(f"mini.tmp{os.getpid()}")
            shutil.rmtree(work, ignore_errors=True)
            make_mini_normalized(work)
            tmp = d.with_name(f"sample.tmp{os.getpid()}")
            shutil.rmtree(tmp, ignore_errors=True)
            p = subprocess.run([str(importer()), "--normalized", str(work / "normalized.sqlite"), "--work-dir",
                                str(work), "--store-in", str(empty), "--store-out", str(tmp), "--momd", str(momd())],
                               capture_output=True, text=True, timeout=300)
            if p.returncode != 0:
                raise RuntimeError("importer (sample store) failed: " + p.stderr[-2000:])
            if not any((tmp / ".ThreemaData_SUPPORT" / "_EXTERNAL_DATA").glob("*")):
                raise RuntimeError("sample store has no _EXTERNAL_DATA (fixture changed?)")
            os.replace(tmp, d)
            shutil.rmtree(work, ignore_errors=True)
    return d


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def ios_fixture():
    """fixtures/gen_ios_backup.py (owner coreB) with the synthetic stores of this module. The fixture still imports
    the crypto helpers as the top-level module `iosbackup_rw`; that name is mapped to tmcore.lib.iosbackup_rw."""
    from tmcore.lib import iosbackup_rw
    sys.modules.setdefault("iosbackup_rw", iosbackup_rw)
    mod = sys.modules.get("gen_ios_backup") or _load("gen_ios_backup", REPO / "fixtures" / "gen_ios_backup.py")
    if not hasattr(mod, "v56_store"):          # older fixture: needs the two store directories injected
        mod.SAMPLE_STORE = sample_store()
        mod.EMPTY_STORE = empty_store()
    return mod


def android_fixture():
    return sys.modules.get("gen_android_backup") or _load("gen_android_backup",
                                                          REPO / "fixtures" / "gen_android_backup.py")


def tmcore_env(**extra: str) -> dict:
    """Environment as the app sets it (DESIGN §5.1): nothing PYTHON* inherited."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON") and not k.startswith("TMCORE")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", LANG="C.UTF-8", TMCORE_PROTOCOL="1",
               TMCORE_NO_TMUTIL="1")
    env.update(extra)
    return env


def run_tmcore(argv: list[str], stdin: bytes = b"", *, env: dict | None = None, timeout: int = 110):
    """`python3 -E -s -B -m tmcore ...` (bundle: -I -B with tmcore in the runtime's site-packages; here the working
    directory puts tmcore on sys.path)."""
    p = subprocess.run([sys.executable, "-E", "-s", "-B", "-m", "tmcore", *map(str, argv)], cwd=CORE, input=stdin,
                       capture_output=True, env=env if env is not None else tmcore_env(), timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8", "replace")


STAGED_PYTHON = REPO / "build" / "stage" / "Resources" / "core" / "python" / "bin" / "python3"


def python_with(*modules: str) -> str | None:
    """An interpreter that can import every module: the test interpreter, else the staged bundle runtime
    (packaging/build-core.sh: the pinned wheels incl. pymobiledevice3/urllib3), else None (caller skips). Only
    imports are probed -- nothing here talks to a device."""
    probe = "import importlib, sys\nfor m in sys.argv[1:]: importlib.import_module(m)"
    for py in (sys.executable, str(STAGED_PYTHON)):
        if not Path(py).is_file():
            continue
        p = subprocess.run([py, "-s", "-B", "-c", probe, *modules], cwd=REPO, capture_output=True,
                           env=tmcore_env(), timeout=60, check=False)
        if p.returncode == 0:
            return py
    return None


def run_guarded(code: str, *, python: str | None = None, timeout: int = 60) -> dict:
    """Run `code` in a fresh interpreter (the netguard audit hook cannot be removed: one process per case) with the
    working directory core/, so this checkout's tmcore wins over any copy in the runtime's site-packages. `code`
    prints one JSON object as its last stdout line."""
    p = subprocess.run([python or sys.executable, "-s", "-B", "-c", code], cwd=CORE, capture_output=True, text=True,
                       timeout=timeout, env=tmcore_env(), check=False)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])
