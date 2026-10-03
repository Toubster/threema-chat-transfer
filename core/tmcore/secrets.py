# SPDX-License-Identifier: AGPL-3.0-or-later
"""
secrets.py -- passwords arrive ONLY on stdin (DESIGN §5.1, §9).

With --secrets-stdin the app writes exactly ONE JSON line, then closes stdin:
    {"backup_password":"…","new_backup_password":"…","android_passwords":{"0":"…","1":"…"}}
Unused fields are absent. Without --secrets-stdin stdin is /dev/null and no secret is available.

    s = read_stdin()                    # or Secrets.empty()
    s.require("backup_password")        # -> str, or EngineError(E_SECRETS_MISSING)
    s.android(0)                        # password for the Android file with argv index 0
    s.wipe()                            # drop references when the step is done

Values live in memory only: never in argv, environment, files, logs, reports or events. repr() is redacted.
"""
from __future__ import annotations

import json
import sys
from typing import BinaryIO

from .protocol import EngineError

FIELDS = ("backup_password", "new_backup_password", "android_passwords")
MAX_STDIN = 64 * 1024


class Secrets:
    __slots__ = ("_values",)

    def __init__(self, values: dict | None = None):
        self._values = dict(values or {})

    @classmethod
    def empty(cls) -> "Secrets":
        return cls()

    def has(self, name: str) -> bool:
        return bool(self._values.get(name))

    def require(self, name: str) -> str:
        v = self._values.get(name)
        if not isinstance(v, str) or not v:
            raise EngineError("E_SECRETS_MISSING", sub=name if name in FIELDS else "unknown")
        return v

    def android(self, ref: int) -> str:
        pw = (self._values.get("android_passwords") or {}).get(str(ref))
        if not isinstance(pw, str) or not pw:
            raise EngineError("E_SECRETS_MISSING", sub="android_passwords")
        return pw

    def values_for_redaction(self) -> list[str]:
        """All secret strings (for the stderr redactor) -- never logged themselves."""
        out = [v for k, v in self._values.items() if isinstance(v, str) and v]
        out += [v for v in (self._values.get("android_passwords") or {}).values() if isinstance(v, str) and v]
        return out

    def wipe(self) -> None:
        self._values.clear()

    def __repr__(self) -> str:
        return f"Secrets({sorted(k for k, v in self._values.items() if v)} redacted)"

    __str__ = __repr__


def parse_line(raw: bytes) -> Secrets:
    if len(raw) > MAX_STDIN:
        raise EngineError("E_PROTOCOL", sub="secrets_too_long")
    text = raw.decode("utf-8")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) != 1:
        raise EngineError("E_PROTOCOL", sub="secrets_not_one_line")
    try:
        obj = json.loads(lines[0])
    except ValueError:
        raise EngineError("E_PROTOCOL", sub="secrets_not_json") from None
    if not isinstance(obj, dict) or any(k not in FIELDS for k in obj):
        raise EngineError("E_PROTOCOL", sub="secrets_unknown_field")
    for k in ("backup_password", "new_backup_password"):
        if k in obj and not isinstance(obj[k], str):
            raise EngineError("E_PROTOCOL", sub="secrets_type")
    ap = obj.get("android_passwords")
    if ap is not None and (not isinstance(ap, dict)
                           or any(not (isinstance(k, str) and k.isdigit() and isinstance(v, str))
                                  for k, v in ap.items())):
        raise EngineError("E_PROTOCOL", sub="secrets_type")
    return Secrets(obj)


def read_stdin(stream: BinaryIO | None = None) -> Secrets:
    """Read exactly one line from stdin and require EOF afterwards."""
    stream = stream if stream is not None else sys.stdin.buffer
    raw = stream.read(MAX_STDIN + 1)
    return parse_line(raw)
