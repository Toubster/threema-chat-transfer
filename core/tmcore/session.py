# SPDX-License-Identifier: AGPL-3.0-or-later
"""
session.py -- the session folder (DESIGN §5.8).

    ~/Library/Application Support/Chat Transfer for Threema/sessions/<uuid>/      0700, Time Machine exclusion, not indexed, APFS
      session.json   app state (schema session.v1)         -- written by the APP only; the engine only reads it
      engine.json    engine state (session.v1#/$defs/engine) -- written by the ENGINE only (atomic, locked)
      android/  ios/pre/  ios/post/  work/{extract,store_out,restoreset,tmp}/  reports/  logs/  diag/

Shared API:
    s = Session.open(path)              # every command except version/selftest/host-check (validates 0700 + schema)
    s = Session.create(base_dir)        # tests / demo; the app normally creates the folder
    s.path("android/normalized.sqlite") # absolute path inside the session (refuses escapes)
    s.key(path)                         # "android/normalized.sqlite" -- the only form a path may take in events
    with s.update_engine() as st: st["phase"] = "prepared"      # read-modify-write engine.json under a lock
    s.hasher                            # HMAC hasher with the session salt (tmcore.hashing)
    s.write_report("prepare", "prepare", "R_OK", counts={...})  # reports/<kind>.json, schema report.v1, 0600
    s.advance_phase(st, "android_done") # never moves the phase backwards (inside update_engine)
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import fcntl
import json
import os
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Iterator

from . import hashing
from .protocol import EngineError

SUBDIRS = ("android", "ios", "ios/pre", "ios/post", "work", "work/extract", "work/store_out", "work/restoreset",
           "work/tmp", "reports", "logs", "diag")
SESSION_SCHEMA = "session.v1"
ENGINE_SCHEMA = "engine.v1"
# engine.json phases in order (session.v1 #/$defs/engine phase)
PHASES = ("new", "host_checked", "android_done", "iphone_prepared", "pre_backup_done", "prepared", "restore_sent",
          "restore_finished", "post_backup_done", "postcheck_done", "rollback_sent", "closed")
REPORT_KINDS = ("host", "android_inspect", "android_normalize", "backup", "prepare", "import", "verify_import",
                "restoreset", "restore", "postcheck", "rollback", "cleanup")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SessionError(EngineError):
    """Session folder missing, wrong owner/mode or wrong schema: an invocation error (E_PROTOCOL, exit 2)."""

    def __init__(self, sub: str):
        super().__init__("E_PROTOCOL", sub=sub)


class Session:
    def __init__(self, root: Path):
        self.root = root
        self._hasher: hashing.SessionHasher | None = None

    # ---------------------------------------------------------------------------------------------- open/create
    @classmethod
    def open(cls, root: str | os.PathLike, *, require_session_json: bool = True) -> "Session":
        root = Path(root)
        try:
            st = root.lstat()
        except FileNotFoundError:
            raise SessionError("session_missing") from None
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
            raise SessionError("session_not_a_directory")
        if st.st_uid != os.getuid():
            raise SessionError("session_owner")
        if stat.S_IMODE(st.st_mode) != 0o700:
            raise SessionError("session_mode")
        s = cls(root.resolve())
        if require_session_json:
            data = s._read_json("session.json")
            if data is None or data.get("schema") != SESSION_SCHEMA:
                raise SessionError("session_schema")
        for d in SUBDIRS:
            (s.root / d).mkdir(mode=0o700, parents=True, exist_ok=True)
        return s

    @classmethod
    def create(cls, base_dir: str | os.PathLike, *, app_version: str = "0.0.0", locale: str = "de",
               exclude_from_backup: bool = True) -> "Session":
        """Create a new session folder with a minimal session.json (tests, demo, CLI use)."""
        base = Path(base_dir)
        base.mkdir(parents=True, exist_ok=True)
        sid = str(uuid.uuid4())
        root = base / sid
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        for d in SUBDIRS:
            (root / d).mkdir(mode=0o700, parents=True, exist_ok=True)
        (root / ".metadata_never_index").touch(mode=0o600)
        if exclude_from_backup and os.environ.get("TMCORE_NO_TMUTIL") != "1":
            subprocess.run(["/usr/bin/tmutil", "addexclusion", str(root)], capture_output=True, check=False)
        now = _now()
        s = cls(root.resolve())
        s._write_json("session.json", {"schema": SESSION_SCHEMA, "session_id": sid, "created_at": now,
                                       "updated_at": now, "app_version": app_version, "locale": locale,
                                       "sidebar_phase": "start", "screen": "S00", "answers": {}})
        return s

    # ---------------------------------------------------------------------------------------------- paths
    def path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if p != self.root and self.root not in p.parents:
            raise EngineError("E_INTERNAL", sub="path_escape")
        return p

    def key(self, path: str | os.PathLike) -> str:
        """Session-relative key for events (e.g. 'android/missing-senders.json')."""
        p = Path(path).resolve()
        return p.relative_to(self.root).as_posix()

    @property
    def tmp(self) -> Path:
        return self.root / "work" / "tmp"

    # ---------------------------------------------------------------------------------------------- json files
    def _read_json(self, name: str) -> dict | None:
        p = self.root / name
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError:
            raise SessionError(f"{name.split('.')[0]}_unreadable") from None

    def _write_json(self, name: str, data: dict) -> None:
        p = self.root / name
        tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def app_state(self) -> dict:
        """session.json (read-only for the engine)."""
        return self._read_json("session.json") or {}

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        fd = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def engine_state(self) -> dict:
        st = self._read_json("engine.json")
        if st is None:
            st = self._new_engine_state()
        return st

    def _new_engine_state(self) -> dict:
        now = _now()
        from . import __version__
        return {"schema": ENGINE_SCHEMA, "session_id": self.app_state().get("session_id") or self.root.name,
                "created_at": now, "updated_at": now, "engine_version": __version__, "phase": "new",
                "hash_salt_hex": hashing.new_salt().hex(), "device": None, "pre": None, "post": None,
                "prepared": None, "restore": None, "restore_sent_at": None, "postcheck": None,
                "rollback_used": False, "history": []}

    @contextlib.contextmanager
    def update_engine(self) -> Iterator[dict]:
        """Locked read-modify-write of engine.json; written atomically (0600) when the block ends without error."""
        with self.lock():
            st = self.engine_state()
            yield st
            st["updated_at"] = _now()
            self._write_json("engine.json", st)

    @staticmethod
    def advance_phase(st: dict, phase: str) -> None:
        """Set engine.json phase, but never backwards (a re-run of an earlier step keeps the later phase)."""
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        cur = st.get("phase", "new")
        if cur not in PHASES or PHASES.index(phase) > PHASES.index(cur):
            st["phase"] = phase

    # ---------------------------------------------------------------------------------------------- reports
    def write_report(self, kind: str, cmd: str, code: str, *, counts: dict | None = None,
                     checks: list | None = None, codes: list | None = None, data: dict | None = None) -> Path:
        """reports/<kind>.json (schema report.v1: counts, check results, codes -- no names, IDs, paths, free text).
        Values are checked with the protocol's safe-value rules before anything is written."""
        from . import __version__
        from .protocol import ProtocolViolation, check_safe
        if kind not in REPORT_KINDS:
            raise ValueError(f"unknown report kind {kind!r}")
        rep: dict = {"schema": "report.v1", "kind": kind, "cmd": cmd, "created_at": _now(),
                     "engine_version": __version__, "code": code,
                     "counts": {k: v for k, v in (counts or {}).items()
                                if isinstance(v, (int, float)) and not isinstance(v, bool)}}
        if checks:
            rep["checks"] = [{k: c[k] for k in ("id", "status", "code", "data") if c.get(k) is not None}
                             for c in checks]
        if codes:
            rep["codes"] = sorted(set(codes))
        if data:
            rep["data"] = data
        try:
            check_safe({k: v for k, v in rep.items() if k not in ("schema", "cmd")})
        except ProtocolViolation:
            raise EngineError("E_INTERNAL", sub="report_unsafe") from None
        self._write_json(f"reports/{kind}.json", rep)
        return self.path(f"reports/{kind}.json")

    # ---------------------------------------------------------------------------------------------- hashing
    @property
    def hasher(self) -> hashing.SessionHasher:
        if self._hasher is None:
            st = self.engine_state()
            if not (self.root / "engine.json").exists():
                with self.update_engine() as w:
                    st = w
            self._hasher = hashing.SessionHasher(bytes.fromhex(st["hash_salt_hex"]))
        return self._hasher
