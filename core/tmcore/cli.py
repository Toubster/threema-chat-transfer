# SPDX-License-Identifier: AGPL-3.0-or-later
"""
cli.py -- `python3 -I -B -m tmcore <command> --session <dir> [options] [--secrets-stdin] [--fake-device <scenario>]`
(DESIGN §5.1, §5.4). One process per command. This module owns the process frame; steps only compute.

Frame of every command:
  0. stdout isolation (process entry only): the event stream gets a private duplicate of fd 1, and fd 1 itself is
     pointed at stderr -- a stray print(), a C library or an inherited child stdout can never corrupt the stream
  1. parse argv (never secrets, never personal data except user-chosen Android file paths)
  2. stderr redaction + network guard (fake device: not even AF_UNIX) + SIGTERM -> cancel at safe points
  3. 'hello'
  4. open the session (all commands except version/selftest/host-check; 0700 + schema session.v1); temporary files
     of the process go to <session>/work/tmp
  5. secrets from stdin (only with --secrets-stdin; exactly one JSON line)
  6. run the step: steps/<module>.<function>(ctx) -> StepResult, or raise EngineError / Cancelled (SIGTERM is only
     acted on at the step's safe points, proto.check_cancel(); a step that returned keeps its result)
  7. exactly one 'result'; exit code from the code catalog (0 ok, 1 refused, 2 program, 3 device, 4 cancelled).
     A network violation anywhere in the process (also one a library swallowed) ends as E_NETWORK_BLOCKED; a
     SystemExit or any other exception inside a step ends as E_INTERNAL (class + module_line, never the message).

No overrides: there is no --allow-*, --force, --waive or similar option, in no command (DESIGN §6.1, §19).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__, netguard, protocol as P, redact
from .protocol import Cancelled, EngineError, ProtocolViolation
from .secrets import Secrets, read_stdin
from .session import Session

NO_SESSION, SESSION = "none", "required"


@dataclasses.dataclass(frozen=True)
class CommandSpec:
    module: str                 # tmcore.steps.<module>
    function: str               # function in that module: (ctx) -> StepResult
    session: str                # NO_SESSION | SESSION
    device: str                 # "-", "read", "write" (DESIGN §5.4 'Gerät')
    secrets: tuple[str, ...]    # secrets the step will require (informational; the step calls ctx.secrets.require)


COMMANDS: dict[str, CommandSpec] = {
    "version":           CommandSpec("status", "version", NO_SESSION, "-", ()),
    "selftest":          CommandSpec("status", "selftest", NO_SESSION, "-", ()),
    "host-check":        CommandSpec("host", "host_check", NO_SESSION, "-", ()),
    "android-inspect":   CommandSpec("android", "inspect", SESSION, "-", ()),
    "android-normalize": CommandSpec("android", "normalize", SESSION, "-", ("android_passwords",)),
    "device-watch":      CommandSpec("device", "watch", SESSION, "read", ()),
    "device-status":     CommandSpec("device", "status", SESSION, "read", ()),
    "encryption-enable": CommandSpec("encryption", "enable", SESSION, "write", ("new_backup_password",)),
    "backup":            CommandSpec("backup", "backup", SESSION, "read", ("backup_password",)),
    "prepare":           CommandSpec("prepare", "prepare", SESSION, "-", ("backup_password",)),
    "restore":           CommandSpec("restore", "restore", SESSION, "write", ("backup_password",)),
    "postcheck":         CommandSpec("postcheck", "postcheck", SESSION, "read", ("backup_password",)),
    "rollback-threema":  CommandSpec("rollback", "rollback_threema", SESSION, "write", ("backup_password",)),
    "session-status":    CommandSpec("status", "session_status", SESSION, "-", ()),
    "diag-report":       CommandSpec("diag", "diag_report", SESSION, "-", ()),
    "cleanup":           CommandSpec("cleanup", "cleanup", SESSION, "-", ()),
}


@dataclasses.dataclass
class StepResult:
    """What a step returns on success. data: numbers/enums/hashes only (checked by the protocol)."""
    data: dict = dataclasses.field(default_factory=dict)
    code: str = "R_OK"
    device_modified: str | None = None      # None -> catalog default ("no" for dynamic codes)
    retryable: bool | None = None


@dataclasses.dataclass
class Context:
    cmd: str
    args: argparse.Namespace
    proto: P.Protocol
    resources: Path
    # None only for the commands without a session (version, selftest, host-check); main() opens the session
    # before any other step runs, so steps may use ctx.session directly
    session: Session = None  # type: ignore[assignment]
    secrets: Secrets = dataclasses.field(default_factory=Secrets.empty)
    fake_device: str | None = None
    redactor: redact.Redactor | None = None

    @property
    def compat_dir(self) -> Path:
        return self.resources / "compat"

    @property
    def models_dir(self) -> Path:
        bundled = self.resources / "models"
        return bundled if bundled.exists() else self.resources / "model"

    def compat(self, name: str) -> dict:
        return json.loads((self.compat_dir / f"{name}.json").read_text(encoding="utf-8"))

    def momd(self, model_id: str) -> Path:
        """Compiled Core Data model of a compat model id (bundle: models/<id>/, repository: model/<id>/)."""
        return self.models_dir / model_id / "ThreemaData.momd"

    @property
    def importer(self) -> Path:
        """threema-import: <Resources>/bin/threema-import; TMCORE_IMPORTER for development builds and tests."""
        env = os.environ.get("TMCORE_IMPORTER")
        return Path(env) if env else self.resources / "bin" / "threema-import"


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # never print usage with argv echo to stdout
        raise UsageError(message)


def resources_dir() -> Path:
    """Bundle: <App>/Contents/Resources (TMCORE_RESOURCES). Repo: the repository root (core/..)."""
    env = os.environ.get("TMCORE_RESOURCES")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    common = _Parser(add_help=False)
    common.add_argument("--session", metavar="DIR", help="session folder (DESIGN §5.8)")
    common.add_argument("--secrets-stdin", action="store_true", help="read exactly one JSON line of secrets")
    common.add_argument("--fake-device", metavar="SCENARIO", help="virtual iPhone (CI, demo); blocks AF_UNIX")

    p = _Parser(prog="tmcore", description="Chat Transfer for Threema engine (protocol v1). Output: JSON lines on stdout.")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help_)

    add("version", "versions and bundle digests")
    add("selftest", "import all modules, verify bundle digests, network guard active")
    hc = add("host-check", "macOS, arch, APFS, free space, power, FileVault")
    hc.add_argument("--workdir", metavar="DIR", help="folder that will hold the sessions")
    ai = add("android-inspect", "classify Threema Android backup files")
    ai.add_argument("files", nargs="+", metavar="FILE")
    an = add("android-normalize", "decrypt + normalize the chosen Android backup(s)")
    an.add_argument("--plan", required=True, metavar="JSON", help='e.g. {"text_ref":0,"media_refs":[1]}')
    an.add_argument("files", nargs="+", metavar="FILE")
    add("device-watch", "stream device events until SIGTERM (read only)")
    ds = add("device-status", "read-only device facts")
    ds.add_argument("--expect", metavar="HASH", help="device hash the session belongs to")
    add("encryption-enable", "turn on backup encryption (writes one device setting)")
    bk = add("backup", "encrypted full backup + checks")
    bk.add_argument("--role", required=True, choices=("pre", "post"))
    add("prepare", "extract, import, verify, restore set, freeze")
    add("restore", "all guards, then exactly one restore of the frozen set")
    pc = add("postcheck", "gate v2 on the post backup -> verdict")
    pc.add_argument("--buddy-answer", choices=("account_only", "full_setup", "none"))
    pc.add_argument("--threema-answer", choices=("ok", "problem"), help="S17: are the old chats there?")
    add("rollback-threema", "R1: no-op set from the PRE backup (<= 6 h, only after final, only threema_only)")
    add("session-status", "where to resume (DESIGN §8.1)")
    add("diag-report", "diagnostic report (codes and counts only, fresh salt)")
    cl = add("cleanup", "delete session data")
    cl.add_argument("--what", required=True, choices=("work", "pre", "post", "all"))
    return p


# ------------------------------------------------------------------------------------------------ hello
def _pmd3_version() -> str:
    try:
        from importlib.metadata import version
        return version("pymobiledevice3")
    except Exception:  # noqa: BLE001 -- not installed in the skeleton / CI without device deps
        return "0"


def model_ids(resources: Path) -> list[str]:
    try:
        data = json.loads((resources / "compat" / "threema-ios.json").read_text(encoding="utf-8"))
        return [m["id"] for m in data.get("models", [])]
    except (OSError, ValueError, KeyError):
        return []


def compat_digest(resources: Path) -> str:
    h = hashlib.sha256()
    for name in ("ios.json", "threema-ios.json", "android.json"):
        p = resources / "compat" / name
        h.update(name.encode() + b"\0" + (p.read_bytes() if p.exists() else b"") + b"\0")
    return "h:" + h.hexdigest()[:8]


def send_hello(proto: P.Protocol, resources: Path, fake: bool) -> None:
    proto.hello(engine_version=__version__, pymobiledevice3=_pmd3_version(), importer_version=__version__,
                models=model_ids(resources), compat_digest=compat_digest(resources), fake_device=fake)


# ------------------------------------------------------------------------------------------------ main
def _safe_data(data: dict) -> dict:
    """EngineError data that would violate the protocol is dropped, not leaked."""
    try:
        P.check_safe(data)
        return dict(data)
    except ProtocolViolation:
        return {"sub": "data_dropped"}


def _record_history(ctx: Context) -> None:
    if ctx.session is None or ctx.proto._result is None:  # noqa: SLF001
        return
    try:
        with ctx.session.update_engine() as st:
            st.setdefault("history", []).append({"cmd": ctx.cmd, "at": ctx.proto._result["ts"],  # noqa: SLF001
                                                 "code": ctx.proto._result["code"],  # noqa: SLF001
                                                 "exit": ctx.proto.exit_code or 0})
            st["history"] = st["history"][-500:]
    except Exception:  # noqa: BLE001 -- history is best effort, the result is already out
        pass


def resolve(spec: CommandSpec) -> Callable[[Context], StepResult]:
    mod = importlib.import_module(f"tmcore.steps.{spec.module}")
    return getattr(mod, spec.function)


def isolate_stdout() -> TextIO:
    """Process entry only: keep a private, non-inheritable duplicate of fd 1 for the event stream and point fd 1 (and
    sys.stdout) at stderr. Nothing but tmcore.protocol can then write to the app's stdout pipe."""
    sys.stdout.flush()
    fd = os.dup(1)
    os.set_inheritable(fd, False)
    os.dup2(2, 1)
    out = os.fdopen(fd, "w", encoding="utf-8", newline="\n", buffering=1)
    sys.stdout = sys.stderr
    return out


def known_identities(session: Session, limit: int = 20000) -> list[str]:
    """Threema IDs of this session (own, contacts, group members/creators, senders of android/normalized.sqlite),
    so the stderr redactor also removes IDs the generic pattern cannot recognise (8 letters without a digit)."""
    db_path = session.root / "android" / "normalized.sqlite"
    if not db_path.is_file():
        return []
    import sqlite3
    ids: set[str] = set()
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for sql in ("SELECT value FROM meta WHERE key = 'own_identity'", "SELECT identity FROM contacts",
                        "SELECT DISTINCT sender FROM messages WHERE sender IS NOT NULL"):
                try:
                    ids.update(r[0] for r in db.execute(sql).fetchmany(limit) if isinstance(r[0], str))
                except sqlite3.Error:
                    continue
        finally:
            db.close()
    except sqlite3.Error:
        return []
    return sorted(i for i in ids if len(i) == 8)[:limit]


def _use_session_tmp(session: Session) -> None:
    """Temporary files of this process (tempfile, pyiosbackup, iphone_backup_decrypt) stay in the session."""
    tmp = session.tmp
    tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp)
    os.environ["TMPDIR"] = str(tmp)


def main(argv: list[str] | None = None, *, out: TextIO | None = None) -> int:
    """One tmcore process. `out`: the event stream (default sys.stdout; __main__ passes isolate_stdout())."""
    argv = list(sys.argv[1:] if argv is None else argv)
    known_cmd = argv[0] if argv and argv[0] in COMMANDS else None
    resources = resources_dir()
    try:
        args = build_parser().parse_args(argv)
    except UsageError:
        if known_cmd is None:
            print("tmcore: unknown or missing command", file=sys.stderr)
            return P.EXIT_PROGRAM
        proto = P.start(known_cmd, out=out)
        send_hello(proto, resources, fake="--fake-device" in argv)
        return proto.result(False, "E_PROTOCOL", {"sub": "usage"})
    except SystemExit as e:   # --help
        return int(e.code or 0)

    spec = COMMANDS[args.cmd]
    redactor = redact.Redactor()
    redact.install_stderr(redactor)
    if out is not None:                          # isolated stream: stray print()s go to the REDACTED stderr
        sys.stdout = sys.stderr
    netguard.install(allow_unix=not args.fake_device)
    proto = P.start(args.cmd, out=out)
    proto.install_sigterm()
    ctx = Context(cmd=args.cmd, args=args, proto=proto, resources=resources, fake_device=args.fake_device,
                  redactor=redactor)
    try:
        send_hello(proto, resources, fake=bool(args.fake_device))
        if spec.session == SESSION:
            if not args.session:
                raise EngineError("E_PROTOCOL", sub="session_missing")
            ctx.session = Session.open(args.session)
            _use_session_tmp(ctx.session)
            redactor.add(*known_identities(ctx.session))
        if args.secrets_stdin:
            ctx.secrets = read_stdin()
            redactor.add(*ctx.secrets.values_for_redaction())
        step = resolve(spec)
        proto.check_cancel()
        res = step(ctx)
        if netguard.violations():
            raise netguard.NetworkBlocked("network access was attempted")
        # no cancel check here: a step that returned has done its work (e.g. a restore that was sent while SIGTERM
        # was deferred inside critical); reporting E_CANCELLED afterwards would be untrue. Steps poll
        # proto.check_cancel() at their safe points.
        return proto.result(True, res.code, res.data, retryable=res.retryable, device_modified=res.device_modified)
    except EngineError as e:
        if netguard.violations():
            return _finish_error(proto, "E_NETWORK_BLOCKED", {"sub": "violation"},
                                 device_modified="unknown" if proto.critical_seen else None)
        dm = e.device_modified or ("unknown" if proto.critical_seen else None)
        return _finish_error(proto, e.code, _safe_data(e.data), retryable=e.retryable, device_modified=dm)
    except Cancelled:
        return _finish_error(proto, "E_CANCELLED", {})
    except netguard.NetworkBlocked as e:
        return _finish_error(proto, "E_NETWORK_BLOCKED", {"sub": "violation", **redact.exception_summary(e)},
                             device_modified="unknown" if proto.critical_seen else None)
    except NotImplementedError as e:
        return _finish_error(proto, "E_INTERNAL", {"sub": "not_implemented", **redact.exception_summary(e)})
    except ProtocolViolation as e:
        return _finish_error(proto, "E_PROTOCOL", {"sub": "violation", **redact.exception_summary(e)},
                             device_modified="unknown" if proto.critical_seen else None)
    except (Exception, SystemExit) as e:  # noqa: BLE001 -- the engine catches everything (DESIGN §10.2 point 3)
        code = "E_NETWORK_BLOCKED" if netguard.violations() else "E_INTERNAL"
        return _finish_error(proto, code, redact.exception_summary(e),
                             device_modified="unknown" if proto.critical_seen else None)
    finally:
        ctx.secrets.wipe()
        _record_history(ctx)


def _finish_error(proto: P.Protocol, code: str, data: dict, *, retryable: bool | None = None,
                  device_modified: str | None = None) -> int:
    if proto.has_result:                       # result already out (should not happen): keep its exit code
        return proto.exit_code if proto.exit_code is not None else P.EXIT_PROGRAM
    if proto._seq == 0:                        # noqa: SLF001 -- hello failed: still produce a valid stream
        try:
            send_hello(proto, resources_dir(), fake=False)
        except Exception:  # noqa: BLE001
            pass
    try:
        return proto.result(False, code, data, retryable=retryable, device_modified=device_modified)
    except ProtocolViolation:
        return proto.result(False, "E_INTERNAL", {"sub": "result_rejected"}, device_modified=device_modified)


def run_module(argv: list[str] | None = None) -> Any:  # pragma: no cover - entry point helper
    sys.exit(main(argv))
