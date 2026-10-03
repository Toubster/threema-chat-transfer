# SPDX-License-Identifier: AGPL-3.0-or-later
"""
netguard.py -- tmcore never opens a network connection (DESIGN §2.5, §10.1).

    netguard.install()                  # real device: only AF_UNIX connections to usbmuxd (/var/run/usbmuxd)
    netguard.install(allow_unix=False)  # --fake-device: no connection at all, a fake run can never reach a device

Mechanism: a CPython audit hook (`sys.addaudithook`). It sees every socket operation of the process --
also from C extensions and code that imports `_socket` directly -- and it cannot be removed once installed:

  socket.__new__                     only AF_UNIX (family -1 = adopting an existing fd is checked on connect/send)
  socket.connect / connect_ex        AF_UNIX to the usbmuxd socket only (and only without --fake-device)
  socket.bind                        refused (tmcore never listens)
  socket.sendto / sendmsg            refused unless the socket is AF_UNIX (no datagrams to addresses)
  socket.getaddrinfo, gethostbyname(_ex), gethostbyaddr, getnameinfo
                                     refused (no DNS)

`socket.socketpair()` (asyncio, multiprocessing) stays possible: it is local IPC and cannot reach anything.
Every refusal raises NetworkBlocked. Refused connect/bind/send/DNS are counted; tmcore.cli turns a counted violation
into E_NETWORK_BLOCKED even when a library swallowed the exception. A refused socket *creation* is not counted (it
reaches nothing; urllib3's import-time IPv6 probe does exactly that). Loopback TCP is refused as well (DESIGN §10.1: only AF_UNIX). Child processes are
not covered by the hook: the app additionally starts tmcore under sandbox-exec with (deny network*) except the
usbmuxd socket (packaging), and media workers install the guard themselves.
"""
from __future__ import annotations

import socket as _socket
import sys
from typing import Any

USBMUXD_PATHS = ("/var/run/usbmuxd", "/private/var/run/usbmuxd")
_AF_UNIX = getattr(_socket, "AF_UNIX", 1)
_DNS_EVENTS = frozenset({"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                         "socket.gethostbyaddr", "socket.getnameinfo"})

_state: dict[str, Any] = {"installed": False, "allow_unix": True, "violations": 0, "last": None}


class NetworkBlocked(OSError):
    """Raised for every attempt to use the network."""


def _deny(what: str, *, count: bool = True) -> None:
    if count:
        _state["violations"] += 1
        _state["last"] = what
    raise NetworkBlocked(f"network access is blocked in tmcore ({what})")


def _family(sock) -> int:
    try:
        return int(sock.family)
    except Exception:  # noqa: BLE001 -- half-initialised socket object
        return -1


def _unix_target_ok(address) -> bool:
    if not _state["allow_unix"]:
        return False
    if isinstance(address, bytes):
        address = address.decode("utf-8", "replace")
    return isinstance(address, str) and address in USBMUXD_PATHS


def _hook(event: str, args) -> None:
    if not event.startswith("socket.") or not _state["installed"]:
        return
    if event == "socket.__new__":
        fam = args[1]
        if fam not in (_AF_UNIX, -1):
            # Refused, but NOT counted: creating a socket reaches nothing. urllib3 (imported by pymobiledevice3 on the
            # real-device path) probes IPv6 support at import time with socket.socket(AF_INET6) and swallows the
            # error; counting that turned every real-device command into E_NETWORK_BLOCKED (first device run,
            # 2026-10-03). Any actual use of such a socket would need connect/bind/send/DNS, which stay counted.
            _deny("socket family", count=False)
    elif event in ("socket.connect", "socket.connect_ex"):
        sock, address = args[0], args[1]
        if _family(sock) != _AF_UNIX or not _unix_target_ok(address):
            _deny("connect")
    elif event == "socket.bind":
        _deny("bind")
    elif event in ("socket.sendto", "socket.sendmsg"):
        sock, address = args[0], args[1] if len(args) > 1 else None
        if address is not None and _family(sock) != _AF_UNIX:
            _deny("send to address")
    elif event in _DNS_EVENTS:
        _deny("name lookup")


def install(allow_unix: bool = True) -> None:
    """Activate the guard (idempotent). The first call wins for allow_unix=False: once a process ran without
    AF_UNIX it never gets it back; allow_unix=True can always be tightened later, never loosened."""
    if _state["installed"]:
        if not allow_unix:
            _state["allow_unix"] = False
        return
    _state["allow_unix"] = bool(allow_unix)
    _state["installed"] = True
    sys.addaudithook(_hook)


def is_active() -> bool:
    return _state["installed"]


def allows_unix() -> bool:
    return _state["installed"] and _state["allow_unix"]


def violations() -> int:
    """Number of refused network operations in this process (also those a library caught and ignored)."""
    return _state["violations"]


def self_test() -> bool:
    """selftest: True when an AF_INET socket and a name lookup are refused. The probe's own refusals are not
    counted as violations."""
    if not _state["installed"]:
        return False
    before = _state["violations"], _state["last"]
    ok = True
    for probe in (lambda: _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM),
                  lambda: _socket.getaddrinfo("localhost", 80)):
        try:
            s = probe()
        except NetworkBlocked:
            continue
        ok = False
        if hasattr(s, "close"):
            s.close()
    _state["violations"], _state["last"] = before
    return ok
