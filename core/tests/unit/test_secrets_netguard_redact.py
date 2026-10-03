# SPDX-License-Identifier: AGPL-3.0-or-later
"""secrets (stdin only), netguard (offline), redact (debug log), hashing (h: ids). DESIGN §5.1, §9, §10."""
import io
import json

import pytest

from tmcore import hashing, netguard, redact
from tmcore.protocol import EngineError
from tmcore.secrets import Secrets, parse_line, read_stdin

CANARY = json.loads((__import__("pathlib").Path(__file__).resolve().parents[3] / "fixtures" / "canaries.json")
                    .read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------ secrets
def test_one_json_line_then_eof():
    s = read_stdin(io.BytesIO(b'{"backup_password":"pw-1","android_passwords":{"0":"a","1":"b"}}\n'))
    assert s.require("backup_password") == "pw-1" and s.android(1) == "b"
    assert "pw-1" not in repr(s) and "pw-1" not in str(s)


@pytest.mark.parametrize("raw,sub", [
    (b'{"backup_password":"a"}\n{"backup_password":"b"}\n', "secrets_not_one_line"),
    (b"not json\n", "secrets_not_json"),
    (b'{"password":"x"}\n', "secrets_unknown_field"),
    (b'{"android_passwords":{"x":"1"}}\n', "secrets_type"),
])
def test_malformed_stdin_is_a_protocol_error(raw, sub):
    with pytest.raises(EngineError) as e:
        parse_line(raw)
    assert e.value.code == "E_PROTOCOL" and e.value.data["sub"] == sub


def test_missing_secret_code():
    with pytest.raises(EngineError) as e:
        Secrets.empty().require("backup_password")
    assert e.value.code == "E_SECRETS_MISSING"
    s = parse_line(b'{"backup_password":"x"}')
    s.wipe()
    assert not s.has("backup_password")


# ------------------------------------------------------------------------------------------------ netguard
# The guard is a CPython audit hook and cannot be removed once installed: every case runs in its own interpreter.
GUARD_PROBE = r"""
import asyncio, json, socket, sys, _socket
from tmcore import netguard
netguard.install(allow_unix=sys.argv[1] == "unix")
res = {}
def probe(name, fn):
    try:
        r = fn()
        if hasattr(r, "close"):
            r.close()
        res[name] = "allowed"
    except netguard.NetworkBlocked:
        res[name] = "blocked"
    except OSError as e:
        res[name] = "blocked" if "blocked in tmcore" in str(e) else "oserror"
def unix_connect(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
    finally:
        s.close()
def udp_sendto():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.sendto(b"x", ("127.0.0.1", 9))
    finally:
        s.close()
probe("inet", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
probe("inet6", lambda: socket.socket(socket.AF_INET6, socket.SOCK_DGRAM))
probe("raw_c_module", lambda: _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM))
probe("create_connection", lambda: socket.create_connection(("127.0.0.1", 9), timeout=1))
probe("dns", lambda: socket.getaddrinfo("localhost", 80))
probe("gethostbyname", lambda: socket.gethostbyname("localhost"))
probe("unix_socket_object", lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
probe("unix_connect_other", lambda: unix_connect("/tmp/tmcore-test-not-usbmuxd.sock"))
probe("bind", lambda: socket.socket(socket.AF_UNIX).bind("/tmp/tmcore-test-bind.sock"))
probe("socketpair", lambda: socket.socketpair()[0])
probe("asyncio_loop", lambda: asyncio.new_event_loop())
probe("urllib", lambda: __import__("urllib.request").request.urlopen("http://127.0.0.1:9/", timeout=1))
res["usbmuxd_target_ok"] = netguard._unix_target_ok("/var/run/usbmuxd")
res["self_test"] = netguard.self_test()
res["violations"] = netguard.violations()
print(json.dumps(res))
"""


def _probe(mode):
    import subprocess
    import sys
    from tests import support
    p = subprocess.run([sys.executable, "-c", GUARD_PROBE, mode], cwd=support.CORE, capture_output=True, text=True,
                       timeout=60, env=support.tmcore_env())
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout)


def test_netguard_real_device_mode_only_usbmuxd():
    r = _probe("unix")
    for name in ("inet", "inet6", "raw_c_module", "create_connection", "dns", "gethostbyname",
                 "unix_connect_other", "bind", "urllib"):
        assert r[name] == "blocked", name
    assert r["unix_socket_object"] == "allowed"                # AF_UNIX objects (usbmuxd client)
    assert r["socketpair"] == "allowed" and r["asyncio_loop"] == "allowed"   # local IPC only
    assert r["usbmuxd_target_ok"] is True                      # the one allowed connect target (not dialled here)
    assert r["self_test"] is True
    # counted, also when a library swallows the error: create_connection + urllib (both DNS first), dns,
    # gethostbyname, unix_connect_other, bind. Refused socket CREATION (inet, inet6, raw_c_module) is not counted
    # (0.3.1-dev: urllib3's import-time IPv6 probe made every real-device command E_NETWORK_BLOCKED).
    assert r["violations"] == 6


def test_netguard_fake_device_mode_blocks_every_connect():
    r = _probe("fake")
    assert r["usbmuxd_target_ok"] is False                     # --fake-device: not even usbmuxd
    assert r["unix_connect_other"] == "blocked" and r["inet"] == "blocked" and r["dns"] == "blocked"
    assert r["socketpair"] == "allowed"


def test_netguard_cannot_be_loosened():
    code = ("from tmcore import netguard\nnetguard.install(allow_unix=False)\nnetguard.install(allow_unix=True)\n"
            "print(netguard.allows_unix())")
    import subprocess
    import sys
    from tests import support
    p = subprocess.run([sys.executable, "-c", code], cwd=support.CORE, capture_output=True, text=True, timeout=60,
                       env=support.tmcore_env())
    assert p.stdout.strip() == "False"
    assert not hasattr(netguard, "uninstall")


# ------------------------------------------------------------------------------------------------ redact
def test_redactor_removes_personal_values():
    udid = "-".join(CANARY["udid_parts"])
    r = redact.Redactor(extra=[CANARY["device_name"], CANARY["password"]], home="/" + "Users/someone")
    text = (f"open /{'Users'}/someone/x/threema-backup_1700000000000_1 dev={udid} name={CANARY['device_name']} "
            f"mail {CANARY['email']} phone {CANARY['phone']} id ZZCAN4RY pw={CANARY['password']} ECID: 0x1a2b3c4d5e")
    out = r(text)
    for leaked in (udid, CANARY["device_name"], CANARY["email"], CANARY["password"], "someone", "ZZCAN4RY",
                   "threema-backup_1700000000000_1", "0x1a2b3c4d5e", "+41"):
        assert leaked not in out, leaked
    assert out.startswith("open ~/x/")  # scrub:ok workspace (the redacted placeholder path)


def test_exception_summary_has_no_message():
    try:
        raise ValueError(f"secret {CANARY['password']}")
    except ValueError as e:
        s = redact.exception_summary(e)
    assert s["exc"] == "valueerror" and CANARY["password"] not in json.dumps(s)
    assert s["where"].startswith("test_secrets_netguard_redact_py_")


# ------------------------------------------------------------------------------------------------ hashing
def test_hashes_are_salted_short_and_case_insensitive():
    h1, h2 = hashing.SessionHasher(b"a" * 32), hashing.SessionHasher(b"b" * 32)
    assert h1.h("ZZCANARY") == h1.h("zzcanary ") and h1.h("ZZCANARY") != h2.h("ZZCANARY")
    assert h1.h("ZZCANARY").startswith("h:") and len(h1.h("ZZCANARY")) == 10
    assert h1.same("ZZCANARY", "zzcanary") and not h1.same("ZZCANARY", "ZZOTHER1")
    assert h2.rehash(h1.h("ZZCANARY")) != h1.h("ZZCANARY")


def test_redactor_keeps_iso_timestamps_but_not_phone_numbers():
    """ISO dates in debug.log stay readable (coreB request); phone numbers in every common format are still removed."""
    r = redact.Redactor(home="/nonexistent")
    for ts in ("2026-10-01T16:23:24Z", "2026-10-01T16:23:24.120Z", "2026-10-01 16:23:24", "at 2026-10-01: ok"):
        assert r(ts) == ts, ts
    for phone in (CANARY["phone"], "+41 00 000 00 00", "0041 00 000 00 00", "000 000 00 00", "(000) 000-0000"):
        out = r(f"call {phone} now")
        assert "<phone>" in out and phone not in out, phone
