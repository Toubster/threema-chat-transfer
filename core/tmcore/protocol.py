# SPDX-License-Identifier: AGPL-3.0-or-later
"""
protocol.py -- the stdout event stream of one tmcore process (protocol v1, DESIGN §5.2-§5.6).

Shared API (every step, guard and library adapter uses only this to talk to the app):

    from tmcore import protocol as P
    P.emit("check", id="freshness", status="pass", data={"age_min": 7, "limit_min": 60})
    P.phase("send", 2, 3)
    P.progress("send", done, total, unit="bytes")
    with P.critical():                      # 'critical' on ... off; SIGTERM is deferred inside
        ...
    raise P.EngineError("E_GUARD_FRESHNESS", age_min=71, limit_min=60)   # -> exactly one result, exit code

Rules enforced here (not by convention):
  * envelope v/seq/ts/cmd/type on every line, seq gapless from 1, ts RFC 3339 UTC with milliseconds;
  * one JSON object per line, UTF-8, <= 64 KiB, flushed after every line; stdout carries nothing else;
  * every string value must be a "safe string" (enum token, code, h:-hash, timestamp, version, iOS build, model
    identifier, screen ID, session file key, sha256) -- free text, paths, names raise ProtocolViolation before
    anything is written (DESIGN §5.2);
  * exactly one 'result', always the last event; emitting after it raises;
  * exit code derived from the result code via core/schema/codes.v1.json (DESIGN §5.6).
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import signal
import sys
import threading
from pathlib import Path
from typing import Any, Iterator, TextIO

PROTOCOL = 1
MAX_LINE = 64 * 1024
EVENT_TYPES = ("hello", "phase", "progress", "check", "prompt", "retry", "device", "critical", "note", "result")

EXIT_OK, EXIT_REFUSED, EXIT_PROGRAM, EXIT_DEVICE, EXIT_CANCELLED = 0, 1, 2, 3, 4

_SAFE_PATTERNS = [re.compile(p) for p in (
    r"^[a-z][a-z0-9_]{0,47}$",                                         # enum / token
    r"^[EWNR]_[A-Z0-9_]{2,60}$",                                       # code
    r"^h:[0-9a-f]{8}$",                                                # hashed id
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$",  # timestamp
    r"^[0-9]{1,4}(\.[0-9]{1,4}){0,3}([-+.][0-9A-Za-z.]{1,20})?$",      # public version
    r"^[0-9]{2}[A-Z][0-9]{1,5}[a-z]?$",                                # iOS build
    r"^(iPhone|iPad|iPod)[0-9]{1,3},[0-9]{1,2}$",                      # device model identifier
    r"^V[0-9]{1,3}$",                                                  # Threema model id
    r"^(S[0-9]{2}[a-z]?|F-[A-Z0-9-]{2,40})$",                          # screen id
    r"^(android|ios|work|reports|logs|diag)/[a-z0-9_.-]{1,60}(/[a-z0-9_.-]{1,60}){0,3}$",  # session file key
    r"^sha256:[0-9a-f]{64}$",                                          # public bundle digest
)]
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CODE = re.compile(r"^[EWNR]_[A-Z0-9_]{2,60}$")


class ProtocolViolation(Exception):
    """A step tried to emit something the contract forbids (free text, unknown type, second result ...)."""


class EngineError(Exception):
    """Raised by steps/guards to end the command with an E_ code. data: numbers/enums only."""

    def __init__(self, code: str, *, retryable: bool | None = None, device_modified: str | None = None, **data):
        if not code.startswith("E_"):
            raise ValueError("EngineError needs an E_ code")
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.device_modified = device_modified
        self.data = data


class Cancelled(Exception):
    """SIGTERM outside 'critical' reached a safe point -> E_CANCELLED, exit 4."""


# ------------------------------------------------------------------------------------------------ code catalog
def _schema_dir() -> Path:
    env = os.environ.get("TMCORE_SCHEMA_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[1] / "schema"


_CATALOG: dict | None = None


def catalog() -> dict:
    """codes.v1.json -> {code: entry}. Loaded once per process."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = json.loads((_schema_dir() / "codes.v1.json").read_text(encoding="utf-8"))["codes"]
    return _CATALOG


def exit_code_for(ok: bool, code: str) -> int:
    if ok:
        return EXIT_OK
    entry = catalog().get(code)
    return entry["exit"] if entry else EXIT_PROGRAM


# ------------------------------------------------------------------------------------------------ safety checks
def is_safe_string(s: str) -> bool:
    return any(p.match(s) for p in _SAFE_PATTERNS)


def check_safe(value: Any, where: str = "data", depth: int = 0) -> None:
    """Raise ProtocolViolation unless value is made of numbers, booleans, null and safe strings only."""
    if depth > 8:
        raise ProtocolViolation(f"{where}: nesting too deep")
    if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise ProtocolViolation(f"{where}: NaN/inf")
        return
    if isinstance(value, str):
        if not is_safe_string(value):
            raise ProtocolViolation(f"{where}: string is not a safe value (free text, path or name?)")
        return
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            check_safe(v, f"{where}[{i}]", depth + 1)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or not _KEY.match(k):
                raise ProtocolViolation(f"{where}: key is not snake_case")
            check_safe(v, f"{where}.{k}", depth + 1)
        return
    raise ProtocolViolation(f"{where}: unsupported type {type(value).__name__}")


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ------------------------------------------------------------------------------------------------ the stream
class Protocol:
    """One per process. Thread-safe (device-watch and progress callbacks may emit from other threads)."""

    def __init__(self, cmd: str, out: TextIO | None = None, clock=utc_now):
        self.cmd = cmd
        self._out = out if out is not None else sys.stdout
        self._clock = clock
        self._seq = 0
        self._lock = threading.RLock()
        self._result: dict | None = None
        self._critical = False
        self._critical_seen = False
        self._cancel_requested = False
        self.exit_code: int | None = None
        self.on_emit = None          # optional hook(event_dict), e.g. copy to <session>/logs/events.jsonl

    # -- state
    @property
    def has_result(self) -> bool:
        return self._result is not None

    @property
    def in_critical(self) -> bool:
        return self._critical

    @property
    def critical_seen(self) -> bool:
        return self._critical_seen

    # -- core
    def emit(self, event_type: str, **fields: Any) -> dict:
        if event_type not in EVENT_TYPES:
            raise ProtocolViolation(f"unknown event type {event_type!r}")
        for k in ("v", "seq", "ts", "cmd", "type"):
            if k in fields:
                raise ProtocolViolation(f"envelope field {k!r} is set by the protocol")
        for k, v in fields.items():
            if not _KEY.match(k):
                raise ProtocolViolation(f"field name {k!r} is not snake_case")
            check_safe(v, k)
        with self._lock:
            if self._result is not None:
                raise ProtocolViolation("event after the result")
            if event_type == "result" and self._critical:
                raise ProtocolViolation("result while critical is on (close critical first)")
            self._seq += 1
            ev = {"v": PROTOCOL, "seq": self._seq, "ts": self._clock(), "cmd": self.cmd, "type": event_type, **fields}
            line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(line.encode("utf-8")) > MAX_LINE:
                self._seq -= 1
                raise ProtocolViolation("event line longer than 64 KiB")
            self._out.write(line + "\n")
            self._out.flush()
            if event_type == "result":
                self._result = ev
            if self.on_emit is not None:
                try:
                    self.on_emit(ev)
                except Exception:  # noqa: BLE001 -- a log copy must never break the stream
                    pass
            return ev

    # -- typed helpers
    def hello(self, *, engine_version: str, pymobiledevice3: str, importer_version: str, models: list[str],
              compat_digest: str, fake_device: bool = False) -> dict:
        if self._seq != 0:
            raise ProtocolViolation("hello must be the first event")
        fields = dict(engine_version=engine_version, protocol=PROTOCOL, pymobiledevice3=pymobiledevice3,
                      importer_version=importer_version, models=list(models), compat_digest=compat_digest)
        if fake_device:
            fields["fake_device"] = True
        return self.emit("hello", **fields)

    def phase(self, name: str, index: int, count: int) -> dict:
        return self.emit("phase", phase=name, index=index, count=count)

    def progress(self, phase: str, done: int, total: int, unit: str = "bytes", eta_s: int | None = None) -> dict:
        pct = round(100.0 * done / total, 1) if total else 0.0
        fields: dict[str, Any] = dict(phase=phase, pct=min(100.0, max(0.0, pct)), done=int(done), total=int(total),
                                      unit=unit)
        if eta_s is not None:
            fields["eta_s"] = int(eta_s)
        return self.emit("progress", **fields)

    def check(self, check_id: str, status: str, code: str | None = None, **data: Any) -> dict:
        fields: dict[str, Any] = dict(id=check_id, status=status)
        if code is not None:
            fields["code"] = code
        if data:
            fields["data"] = data
        return self.emit("check", **fields)

    def note(self, code: str, **data: Any) -> dict:
        if not code.startswith(("W_", "N_")):
            raise ProtocolViolation("note needs a W_ or N_ code")
        return self.emit("note", code=code, **({"data": data} if data else {}))

    def retry(self, reason: str, attempt: int, max_attempts: int, wait_s: float) -> dict:
        return self.emit("retry", reason=reason, attempt=attempt, max=max_attempts, wait_s=wait_s)

    def prompt(self, kind: str, active: bool) -> dict:
        return self.emit("prompt", kind=kind, active=active)

    def device(self, state: str, device_hash: str | None = None, product_type: str | None = None) -> dict:
        fields: dict[str, Any] = {"state": state}
        if device_hash:
            fields["device"] = device_hash
        if product_type:
            fields["product_type"] = product_type
        return self.emit("device", **fields)

    @contextlib.contextmanager
    def critical(self) -> Iterator[None]:
        """critical on: the app locks Cancel/Quit, SIGTERM is deferred. Always closed, also on exceptions."""
        with self._lock:
            if self._critical:
                raise ProtocolViolation("critical is already on")
            self.emit("critical", on=True)
            self._critical = True
            self._critical_seen = True
        try:
            yield
        finally:
            with self._lock:
                self._critical = False
                if self._result is None:
                    self.emit("critical", on=False)

    def result(self, ok: bool, code: str, data: dict | None = None, *, retryable: bool | None = None,
               device_modified: str | None = None) -> int:
        """The one and only result. Returns the process exit code."""
        if not _CODE.match(code) or code[0] not in ("R", "E"):
            raise ProtocolViolation("result code must be R_ or E_")
        if ok != code.startswith("R_"):
            raise ProtocolViolation("ok=true needs an R_ code, ok=false an E_ code")
        entry = catalog().get(code)
        if entry is None:
            raise ProtocolViolation(f"code {code} is not in codes.v1.json")
        if retryable is None:
            retryable = bool(entry["retryable"])
        if device_modified is None:
            dm = entry["device_modified"]
            device_modified = dm if dm != "dynamic" else "no"
        self.emit("result", ok=ok, code=code, retryable=retryable, device_modified=device_modified,
                  data=dict(data or {}))
        self.exit_code = exit_code_for(ok, code)
        return self.exit_code

    # -- cancellation (SIGTERM)
    def request_cancel(self) -> None:
        self._cancel_requested = True

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def consume_cancel(self) -> bool:
        """For stream commands that END on SIGTERM by design (device-watch): returns whether SIGTERM arrived and
        clears it, so the command can finish with its normal result instead of E_CANCELLED."""
        with self._lock:
            was, self._cancel_requested = self._cancel_requested, False
        return was

    def check_cancel(self) -> None:
        """Call at safe points. Raises Cancelled when SIGTERM arrived and critical is off."""
        if self._cancel_requested and not self._critical:
            raise Cancelled()

    def install_sigterm(self) -> None:
        def handler(_signum, _frame):
            self.request_cancel()   # never interrupts a critical section; steps poll check_cancel()
        signal.signal(signal.SIGTERM, handler)


# ------------------------------------------------------------------------------------------------ module API
_current: Protocol | None = None


def start(cmd: str, out: TextIO | None = None, clock=utc_now) -> Protocol:
    global _current
    _current = Protocol(cmd, out=out, clock=clock)
    return _current


def current() -> Protocol:
    if _current is None:
        raise ProtocolViolation("protocol not started (tmcore.cli sets it up)")
    return _current


def emit(event_type: str, **fields: Any) -> dict:
    """Emit one event on the current process stream (envelope added here)."""
    return current().emit(event_type, **fields)


def phase(name: str, index: int, count: int) -> dict:
    return current().phase(name, index, count)


def progress(phase_name: str, done: int, total: int, unit: str = "bytes", eta_s: int | None = None) -> dict:
    return current().progress(phase_name, done, total, unit, eta_s)


def check(check_id: str, status: str, code: str | None = None, **data: Any) -> dict:
    return current().check(check_id, status, code, **data)


def note(code: str, **data: Any) -> dict:
    return current().note(code, **data)


def retry(reason: str, attempt: int, max_attempts: int, wait_s: float) -> dict:
    return current().retry(reason, attempt, max_attempts, wait_s)


def prompt(kind: str, active: bool) -> dict:
    return current().prompt(kind, active)


def critical():
    return current().critical()


def check_cancel() -> None:
    current().check_cancel()
