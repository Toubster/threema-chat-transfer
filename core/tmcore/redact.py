# SPDX-License-Identifier: AGPL-3.0-or-later
"""
redact.py -- the debug log (stderr -> <session>/logs/debug.log) never holds personal data (DESIGN §10.2).

    r = Redactor(extra=[device_name, *secrets.values_for_redaction()])
    r("text")                                  # -> redacted text
    install_stderr(r)                          # wrap sys.stderr; foreign loggers (pymobiledevice3, importer) too
    where = exception_summary(exc)             # {"exc": "valueerror", "where": "backup_pipeline_py_812"}

Redacted: $HOME -> ~, other home paths, UDIDs (new + old), ECIDs, serial-like tokens after 'serial', e-mail
addresses, phone numbers, Threema-ID-shaped tokens, Threema Android backup file names, and every extra value
(device name from lockdown, passwords). Exceptions never print their message (it may contain data).
"""
from __future__ import annotations

import io
import os
import re
import sys
import traceback

_RULES = [
    # backup file names first: their epoch-millisecond part would otherwise look like a phone number
    (re.compile(r"threema-backup_[0-9A-Za-z_.\-]+"), "<android-backup>"),
    (re.compile(r"\b[0-9A-F]{8}-[0-9A-F]{16}\b", re.I), "<udid>"),
    (re.compile(r"\b[0-9a-f]{40}\b", re.I), "<hex40>"),
    (re.compile(r"(?i)\b(ecid|uniquechipid)\b(\W{0,4})(0x)?[0-9a-f]{6,20}\b"), r"\1\2<ecid>"),
    (re.compile(r"(?i)\b(serial(?:number| number)?)\b(\W{0,4})[A-Z0-9]{8,16}\b"), r"\1\2<serial>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"), "<email>"),
    # phone numbers (international and local formats); never ISO dates/timestamps such as 2026-10-01T16:23:24Z
    # (a match may not start inside a digit run, nor at a YYYY-MM-DD date)
    (re.compile(r"(?<![\d+])(?!\d{4}-\d{2}-\d{2}(?!\d))\+?\d[\d \-()]{7,}\d"), "<phone>"),
    (re.compile(r"(?<![0-9A-Za-z*])\*[0-9A-Z]{7}(?![0-9A-Za-z*])"), "<threema-id>"),
    (re.compile(r"(?<![0-9A-Za-z*])(?=[0-9A-Z]{8}(?![0-9A-Za-z*]))(?=[0-9A-Z]*[0-9])(?=[0-9A-Z]*[A-Z])[0-9A-Z]{8}"),
     "<threema-id>"),
    (re.compile(r"/" + r"(?:Users|home)/[^/\s\"']+"), "/<home>"),
]


class Redactor:
    def __init__(self, extra: list[str] | tuple[str, ...] = (), home: str | None = None):
        self.home = home if home is not None else os.path.expanduser("~")
        # longest first so a password containing a device name is removed completely
        self.extra = sorted({e for e in extra if e and len(e) >= 3}, key=len, reverse=True)

    def add(self, *values: str) -> None:
        self.extra = sorted(set(self.extra) | {v for v in values if v and len(v) >= 3}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for v in self.extra:
            text = text.replace(v, "<redacted>")
        if self.home and len(self.home) > 1:
            text = text.replace(self.home, "~")
        for rx, repl in _RULES:
            text = rx.sub(repl, text)
        return text


class _RedactingStream(io.TextIOBase):
    def __init__(self, inner, redactor: Redactor):
        self._inner = inner
        self._r = redactor
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._inner.write(self._r(line) + "\n")
        return len(s)

    def flush(self) -> None:
        if self._buf:
            self._inner.write(self._r(self._buf))
            self._buf = ""
        self._inner.flush()

    def isatty(self) -> bool:
        return False


def install_stderr(redactor: Redactor) -> None:
    """Route everything written to sys.stderr (incl. logging and warnings) through the redactor."""
    if isinstance(sys.stderr, _RedactingStream):
        sys.stderr._r = redactor  # type: ignore[attr-defined]
        return
    sys.stderr = _RedactingStream(sys.stderr, redactor)


def _token(s: str) -> str:
    t = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return (t or "x")[:47] if t[:1].isalpha() else ("x_" + t)[:47]


def exception_summary(exc: BaseException) -> dict:
    """Class and 'module_line' of the innermost frame -- never the message (DESIGN §10.2 point 3)."""
    tb = traceback.extract_tb(exc.__traceback__)
    where = "unknown"
    if tb:
        fr = tb[-1]
        where = f"{os.path.basename(fr.filename)}_{fr.lineno}"
    return {"exc": _token(type(exc).__name__), "where": _token(where)}
