# SPDX-License-Identifier: AGPL-3.0-or-later
"""
netguard: what is refused vs. what is COUNTED (regression of the first real-device run, fixed in 0.3.1-dev).

urllib3 (imported by pymobiledevice3 on the real-device path) probes IPv6 at import time with
socket.socket(AF_INET6) and swallows the refusal. Counting that refused *creation* turned every real-device command
into E_NETWORK_BLOCKED. A created-but-unused socket reaches nothing, so creation stays refused but is not counted;
everything that could reach something (connect, bind, send to an address, DNS) stays counted even when the caller
swallows NetworkBlocked -- tmcore.cli turns a counted violation into E_NETWORK_BLOCKED.

The guard is an audit hook and cannot be removed: every case runs in its own interpreter. Every refused call is
stopped by the hook BEFORE the system call, so nothing here sends a packet, resolves a name or touches a device.
"""
from __future__ import annotations

import textwrap

import pytest

from tests import support

NOT_USBMUXD = "/tmp/tmcore-test-not-usbmuxd.sock"


def _guarded(body: str, *, before: str = "", allow_unix: bool = True, python: str | None = None) -> dict:
    """`before` runs without the guard, `body` with it; `swallow(name, fn)` calls fn and swallows NetworkBlocked
    the way a library would."""
    code = "\n".join([
        "import json, socket, sys, _socket",
        "from tmcore import netguard",
        textwrap.dedent(before),
        f"netguard.install(allow_unix={allow_unix!r})",
        "blocked = []",
        "def swallow(name, fn):",
        "    try:",
        "        r = fn()",
        "        if hasattr(r, 'close'):",
        "            r.close()",
        "    except netguard.NetworkBlocked:",
        "        blocked.append(name)",
        textwrap.dedent(body),
        "print(json.dumps({'violations': netguard.violations(), 'last': netguard._state['last'],",
        "                  'blocked': blocked, 'guard': netguard.__file__}))",
    ])
    r = support.run_guarded(code, python=python)
    assert r["guard"].startswith(str(support.CORE / "tmcore")), r["guard"]   # this checkout, not a bundled copy
    return r


# ------------------------------------------------------------------------------------------- (a) creation: refused,
# ------------------------------------------------------------------------------------------- not counted
def test_refused_socket_creation_is_not_a_violation():
    r = _guarded("""
        swallow("inet6", lambda: socket.socket(socket.AF_INET6))
        swallow("inet6_dgram", lambda: socket.socket(socket.AF_INET6, socket.SOCK_DGRAM))
        swallow("inet", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        swallow("raw_c_module", lambda: _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM))
    """)
    assert r["blocked"] == ["inet6", "inet6_dgram", "inet", "raw_c_module"]   # still refused ...
    assert r["violations"] == 0 and r["last"] is None                          # ... but not a violation


def test_urllib3_style_ipv6_probe_is_not_a_violation():
    """urllib3.util.connection._has_ipv6 verbatim in shape: create AF_INET6, bind ::1, swallow every Exception.
    The bind is never reached (creation is refused first), so nothing is counted."""
    r = _guarded("""
        def _has_ipv6(host):
            sock, ok = None, False
            if socket.has_ipv6:
                try:
                    sock = socket.socket(socket.AF_INET6)
                    sock.bind((host, 0))
                    ok = True
                except Exception:
                    pass
            if sock:
                sock.close()
            return ok
        HAS_IPV6 = _has_ipv6("::1")
        assert HAS_IPV6 is False
    """)
    assert r["violations"] == 0 and r["last"] is None


@pytest.mark.parametrize("allow_unix", [True, False], ids=["real-device", "fake-device"])
def test_import_urllib3_under_the_guard_is_not_a_violation(allow_unix):
    py = support.python_with("urllib3")
    if py is None:
        pytest.skip("no interpreter with urllib3 (neither this one nor build/stage) -- `make core` stages it")
    r = _guarded("""
        import urllib3, urllib3.util.connection
        assert urllib3.util.connection.HAS_IPV6 is False      # the probe ran and was refused
    """, allow_unix=allow_unix, python=py)
    assert r["violations"] == 0 and r["last"] is None


# ------------------------------------------------------------------------------------------- (b) connect / bind / send:
# ------------------------------------------------------------------------------------------- counted even if swallowed
# AF_INET sockets cannot be created under the guard, so they are made BEFORE install (never connected unguarded);
# "adopted" re-wraps such a socket's fd after install (socket.__new__ family -1 is allowed, checked on connect).
CONNECT_CASES = {
    "unix_other_path": ("", f"swallow('c', lambda: socket.socket(socket.AF_UNIX).connect({NOT_USBMUXD!r}))"),
    "unix_other_path_connect_ex": ("", f"swallow('c', lambda: socket.socket(socket.AF_UNIX).connect_ex({NOT_USBMUXD!r}))"),
    "inet_made_before_install": ("pre = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
                                 "swallow('c', lambda: pre.connect(('127.0.0.1', 9)))"),
    "inet_connect_ex": ("pre = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
                        "swallow('c', lambda: pre.connect_ex(('127.0.0.1', 9)))"),
    "inet6_made_before_install": ("pre = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)",
                                  "swallow('c', lambda: pre.connect(('::1', 9)))"),
    "inet_fd_adopted_after_install": ("pre = socket.socket(socket.AF_INET, socket.SOCK_STREAM)",
                                      "a = socket.socket(fileno=pre.detach())\nswallow('c', lambda: a.connect(('127.0.0.1', 9)))"),
}


@pytest.mark.parametrize("case", sorted(CONNECT_CASES))
def test_swallowed_connect_is_still_a_violation(case):
    before, body = CONNECT_CASES[case]
    r = _guarded(body, before=before)
    assert r["blocked"] == ["c"]
    assert r["violations"] == 1 and r["last"] == "connect"


def test_swallowed_bind_and_send_are_still_violations():
    r = _guarded("""
        swallow("bind", lambda: socket.socket(socket.AF_UNIX).bind("/tmp/tmcore-test-bind.sock"))
        swallow("sendto", lambda: pre.sendto(b"x", ("127.0.0.1", 9)))
    """, before="pre = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)")
    assert r["blocked"] == ["bind", "sendto"]
    assert r["violations"] == 2 and r["last"] == "send to address"


# ------------------------------------------------------------------------------------------- (c) DNS: counted
DNS_CASES = {
    "getaddrinfo": "socket.getaddrinfo('localhost', 80)",
    "gethostbyname": "socket.gethostbyname('localhost')",
    "gethostbyname_ex": "socket.gethostbyname_ex('localhost')",
    "gethostbyaddr": "socket.gethostbyaddr('127.0.0.1')",
    "getnameinfo": "socket.getnameinfo(('127.0.0.1', 80), 0)",
}


@pytest.mark.parametrize("case", sorted(DNS_CASES))
@pytest.mark.parametrize("allow_unix", [True, False], ids=["real-device", "fake-device"])
def test_swallowed_name_lookup_is_still_a_violation(case, allow_unix):
    r = _guarded(f"swallow('dns', lambda: {DNS_CASES[case]})", allow_unix=allow_unix)
    assert r["blocked"] == ["dns"]
    assert r["violations"] == 1 and r["last"] == "name lookup"


# ------------------------------------------------------------------------------------------- (d) self_test
@pytest.mark.parametrize("allow_unix", [True, False], ids=["real-device", "fake-device"])
def test_self_test_still_passes_and_leaves_the_count_alone(allow_unix):
    code = textwrap.dedent(f"""
        import json, socket
        from tmcore import netguard
        out = {{"before_install": netguard.self_test()}}
        netguard.install(allow_unix={allow_unix!r})
        out["clean"] = [netguard.self_test(), netguard.violations(), netguard._state["last"]]
        for fn in (lambda: socket.socket(socket.AF_INET6), lambda: socket.getaddrinfo("localhost", 80)):
            try:
                fn()
            except netguard.NetworkBlocked:
                pass
        out["after"] = [netguard.self_test(), netguard.violations(), netguard._state["last"]]
        out["guard"] = netguard.__file__
        print(json.dumps(out))
    """)
    r = support.run_guarded(code)
    assert r["guard"].startswith(str(support.CORE / "tmcore"))
    assert r["before_install"] is False                             # no guard -> selftest fails
    assert r["clean"] == [True, 0, None]                            # the probes' own refusals are not counted
    assert r["after"] == [True, 1, "name lookup"]                   # IPv6 creation not counted, DNS counted, kept
