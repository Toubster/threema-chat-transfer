# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Contract: loading the real-device path under the netguard counts no violation (regression of the first real-device
run, fixed in 0.3.1-dev). tmcore.steps.iphone imports pymobiledevice3 lazily; pymobiledevice3 pulls in urllib3,
whose import-time IPv6 probe (socket.socket(AF_INET6), swallowed) used to be counted, so every real-device command
ended in E_NETWORK_BLOCKED before it talked to the iPhone.

Imports only: no usbmuxd connection, no device, no network (the guard is installed first, as tmcore.cli does).
Runs on the interpreter that has pymobiledevice3 -- the test interpreter (CI: bundled runtime), else the staged
bundle runtime of `make core`; skipped when neither exists.
"""
from __future__ import annotations

import pytest

from tests import support

REAL_DEVICE_MODULES = (
    "pymobiledevice3.usbmux",
    "pymobiledevice3.lockdown",
    "pymobiledevice3.exceptions",
    "pymobiledevice3.services.mobilebackup2",
    "pymobiledevice3.services.afc",
    "pymobiledevice3.services.installation_proxy",
    "pymobiledevice3.services.mobile_config",
)

CODE = """
import importlib, json, sys
from tmcore import netguard
netguard.install(allow_unix=True)                      # real device: exactly what tmcore.cli does without --fake-device
out = {"guard": netguard.__file__, "steps": []}
import tmcore.steps.iphone as iphone                   # the gateway module (pymobiledevice3 imported lazily)
out["steps"].append(["tmcore.steps.iphone", netguard.violations()])
for name in %(modules)r:
    importlib.import_module(name)
    out["steps"].append([name, netguard.violations()])
out["urllib3_via_pmd3"] = "urllib3" in sys.modules     # proves pymobiledevice3 really pulls urllib3 in
import urllib3                                         # the original trigger, explicitly
out["steps"].append(["urllib3", netguard.violations()])
iphone.RealGateway()                                   # constructor only sets log levels -- no device I/O
out["steps"].append(["RealGateway()", netguard.violations()])
out["violations"] = netguard.violations()
out["last"] = netguard._state["last"]
out["self_test"] = netguard.self_test()
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def pmd3_python() -> str:
    py = support.python_with("pymobiledevice3", "urllib3")
    if py is None:
        pytest.skip("no interpreter with pymobiledevice3 (neither this one nor build/stage) -- `make core` stages it")
    return py


def test_real_device_imports_count_no_violation(pmd3_python):
    r = support.run_guarded(CODE % {"modules": REAL_DEVICE_MODULES}, python=pmd3_python, timeout=110)
    assert r["guard"].startswith(str(support.CORE / "tmcore")), r["guard"]   # this checkout's guard, not a bundled one
    assert r["urllib3_via_pmd3"] is True, "pymobiledevice3 no longer imports urllib3 -- the regression path changed"
    assert [s for s in r["steps"] if s[1] != 0] == [], r["steps"]
    assert r["violations"] == 0 and r["last"] is None
    assert r["self_test"] is True


def test_real_device_cli_path_would_not_report_network_blocked(pmd3_python):
    """The decision tmcore.cli makes after a step: `if netguard.violations(): E_NETWORK_BLOCKED`. After loading the
    whole real-device stack (and a swallowed urllib3-style probe) it must still be 0; a swallowed connect still
    flips it (the guard did not go soft)."""
    code = """
import json, socket
from tmcore import netguard
netguard.install(allow_unix=True)
import tmcore.steps.iphone, pymobiledevice3.lockdown, pymobiledevice3.usbmux, urllib3
loaded = netguard.violations()
try:
    socket.socket(socket.AF_UNIX).connect("/tmp/tmcore-test-not-usbmuxd.sock")
except netguard.NetworkBlocked:
    pass
print(json.dumps({"loaded": loaded, "after_connect": netguard.violations(), "last": netguard._state["last"]}))
"""
    r = support.run_guarded(code, python=pmd3_python, timeout=110)
    assert r["loaded"] == 0
    assert r["after_connect"] == 1 and r["last"] == "connect"
