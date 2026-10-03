# SPDX-License-Identifier: AGPL-3.0-or-later
"""
hashing.py -- hashed identifiers for events, reports and engine.json (DESIGN §5.2).

    h = SessionHasher(salt)            # salt: 32 random bytes per session (engine.json hash_salt_hex)
    h.h("<UDID or Threema ID>")        # -> "h:3f2a91c0"  (HMAC-SHA256, first 8 hex)
    h.same(a, b)                       # constant-time comparison of two raw values (identity guard, in memory)

Raw values never leave the process; only the "h:" form is written anywhere. Diagnostic reports re-salt with a fresh
salt per report (rehash), so two reports cannot be linked through their hashes.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import unicodedata

PREFIX = "h:"
LENGTH = 8


def new_salt() -> bytes:
    return os.urandom(32)


def _norm(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        return value
    return unicodedata.normalize("NFC", value.strip()).upper().encode("utf-8")


class SessionHasher:
    def __init__(self, salt: bytes):
        if len(salt) < 16:
            raise ValueError("salt too short")
        self._salt = bytes(salt)

    def digest(self, value: str | bytes) -> bytes:
        return hmac.new(self._salt, _norm(value), hashlib.sha256).digest()

    def h(self, value: str | bytes) -> str:
        """Hash for events: 'h:' + 8 hex. Case-insensitive for strings (UDIDs, Threema IDs)."""
        return PREFIX + self.digest(value).hex()[:LENGTH]

    def same(self, a: str | bytes, b: str | bytes) -> bool:
        return hmac.compare_digest(self.digest(a), self.digest(b))

    def rehash(self, hashed: str) -> str:
        """Re-salt an existing 'h:' value (diagnostic report: fresh salt per report)."""
        if not hashed.startswith(PREFIX):
            raise ValueError("not a hashed value")
        return self.h(hashed.encode("ascii"))


def bundle_sha256(data: bytes) -> str:
    """Digest of a PUBLIC bundle file (model, importer, compat) for 'version'/'selftest': 'sha256:<hex>'."""
    return "sha256:" + hashlib.sha256(data).hexdigest()
