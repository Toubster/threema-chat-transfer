# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Contract of every command (DESIGN §5, §13.1 level 3; owner: coreA): stdout carries only events.v1 lines, `hello`
first, `seq` gapless, exactly one `result` as the last event, phases from `x-phases`, every code in the catalog,
error data only with the catalog's keys, `critical` balanced, exit code = catalog exit of the result code, secrets
only on stdin and never echoed. Plus the process frame: SIGTERM (cancel outside `critical`, deferred inside),
exceptions without messages, network attempts, stray prints.

Commands of coreB (device, backup, restore, postcheck, rollback) are held to the contract only; their outcomes are
tested by coreB. Every command also runs with an empty session and the fake device, which must never crash.
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import time

import jsonschema
import pytest
from referencing import Registry, Resource

from tests import support
from tests.support import canary
from tmcore import __version__
from tmcore.cli import COMMANDS, build_parser
from tmcore.session import Session

SCHEMA = support.CORE / "schema"
CATALOG = json.loads((SCHEMA / "codes.v1.json").read_text())["codes"]
X_PHASES = json.loads((SCHEMA / "events.v1.json").read_text())["x-phases"]
CANARY_PW = support.canaries()["password"]
COREA = {"version", "selftest", "host-check", "android-inspect", "android-normalize", "prepare", "session-status",
         "diag-report", "cleanup"}

ARGS = {
    "host-check": ["--workdir", "{tmp}"],
    "android-inspect": ["{tmp}/not-a-backup.bin"],
    "android-normalize": ["--plan", '{"text_ref":0,"media_refs":[]}', "{tmp}/not-a-backup.bin"],
    "device-status": [],
    "backup": ["--role", "pre"],
    "postcheck": ["--buddy-answer", "account_only"],
    "cleanup": ["--what", "work"],
}
# outcome of the coreA commands on an EMPTY session with only a backup password on stdin
EXPECT = {
    "version": {"R_OK"},
    "selftest": {"R_OK"},
    "host-check": {"R_OK", "E_HOST_POWER", "E_HOST_SPACE", "E_HOST_FS_NOT_APFS"},   # depends on the machine
    "android-inspect": {"R_OK"},                    # an unknown file: plan "none", the app shows the kinds
    "android-normalize": {"E_SECRETS_MISSING"},     # no android_passwords on stdin
    "prepare": {"E_PROTOCOL"},                      # no Android part, no PRE backup
    "session-status": {"R_OK"},
    "diag-report": {"R_OK"},
    "cleanup": {"R_OK"},
}


@pytest.fixture(scope="module")
def validator():
    reg = Registry()
    schemas = {}
    for name in ("events.v1.json", "session.v1.json", "compat.v1.json", "report.v1.json"):
        s = json.loads((SCHEMA / name).read_text())
        schemas[name] = s
        reg = reg.with_resource(s["$id"], Resource.from_contents(s))
    return jsonschema.Draft202012Validator(schemas["events.v1.json"], registry=reg)


def check_stream(validator, cmd, out, rc):
    lines = out.splitlines()
    assert lines, "no events"
    assert all(len(line.encode()) <= 64 * 1024 for line in lines)
    evs = [json.loads(line) for line in lines]
    depth = 0
    for e in evs:
        errs = list(validator.iter_errors(e))
        assert not errs, (e.get("type"), jsonschema.exceptions.best_match(errs).message)
        assert e["cmd"] == cmd
        if e["type"] in ("phase", "progress"):
            assert e["phase"] in X_PHASES[cmd], (cmd, e["phase"])
        for key in ("code", "reason"):
            if isinstance(e.get(key), str):
                assert e[key] in CATALOG, e[key]
        if e["type"] == "critical":
            depth += 1 if e["on"] else -1
            assert depth in (0, 1), "critical not balanced"
    assert depth == 0, "critical still on at the end"
    assert evs[0]["type"] == "hello" and evs[0]["protocol"] == 1 and evs[0]["engine_version"] == __version__
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    results = [e for e in evs if e["type"] == "result"]
    assert len(results) == 1 and evs[-1]["type"] == "result"
    res = results[0]
    assert res["code"] in CATALOG
    if not res["ok"]:
        allowed = set(CATALOG[res["code"]].get("data") or [])
        assert set(res["data"]) <= allowed | {"sub"}, (res["code"], sorted(res["data"]))
    assert rc == (0 if res["ok"] else CATALOG[res["code"]]["exit"])
    return res


def _run_stream(argv, stdin, after_s=2.0):
    p = subprocess.Popen([sys.executable, "-E", "-s", "-B", "-m", "tmcore", *argv], cwd=support.CORE,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=support.tmcore_env())
    p.stdin.write(stdin)
    p.stdin.close()
    time.sleep(after_s)
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=60)
    return p.returncode, out.decode(), err.decode()


def _argv(cmd, sess, tmp_path):
    return [cmd, "--session", str(sess.root), *[a.replace("{tmp}", str(tmp_path)) for a in ARGS.get(cmd, [])]]


@pytest.mark.parametrize("cmd", sorted(COMMANDS))
def test_every_command_obeys_the_contract(validator, tmp_path, cmd):
    sess = Session.create(tmp_path / "sessions")
    (tmp_path / "not-a-backup.bin").write_bytes(b"\0" * 64)
    argv = _argv(cmd, sess, tmp_path) + ["--secrets-stdin", "--fake-device", "happy"]
    stdin = json.dumps({"backup_password": CANARY_PW}).encode() + b"\n"
    if cmd == "device-watch":                   # a stream: ends on SIGTERM by design (DESIGN §5.4)
        rc, out, err = _run_stream(argv, stdin)
    else:
        rc, out, err = support.run_tmcore(argv, stdin)
    res = check_stream(validator, cmd, out, rc)
    assert CANARY_PW not in out and CANARY_PW not in err
    assert json.loads(out.splitlines()[0]).get("fake_device") is True
    if cmd in EXPECT:
        assert res["code"] in EXPECT[cmd], (res["code"], res["data"])
    if cmd == "version":
        assert res["data"]["protocol"] == 1 and res["data"]["models"] == ["V56"]
    if cmd == "session-status":
        assert res["data"]["resume_at"] == "S00" and res["data"]["phase"] == "new"
    if cmd == "prepare":
        assert res["data"]["sub"] in ("no_android", "no_pre_backup")
    # the session holds no secret in any file afterwards (no password files, DESIGN §9)
    assert not canary.scan_tree(sess.root, {"password": CANARY_PW})


@pytest.mark.parametrize("cmd", sorted(c for c in COMMANDS if c not in ("version", "selftest", "host-check")))
def test_session_is_required_and_checked(validator, tmp_path, cmd):
    rc, out, _ = support.run_tmcore([cmd, *[a.replace("{tmp}", str(tmp_path)) for a in ARGS.get(cmd, [])]])
    res = check_stream(validator, cmd, out, rc)
    assert res["code"] == "E_PROTOCOL" and res["data"]["sub"] == "session_missing"
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    loose.chmod(0o755)
    rc, out, _ = support.run_tmcore([cmd, "--session", str(loose),
                                     *[a.replace("{tmp}", str(tmp_path)) for a in ARGS.get(cmd, [])]])
    res = check_stream(validator, cmd, out, rc)
    assert res["code"] == "E_PROTOCOL" and res["data"]["sub"] == "session_mode"


def test_usage_errors(validator):
    rc, out, _ = support.run_tmcore(["backup"])                     # --role missing
    res = check_stream(validator, "backup", out, rc)
    assert res["code"] == "E_PROTOCOL" and res["data"]["sub"] == "usage" and rc == 2
    rc, out, err = support.run_tmcore(["bogus-command"])
    assert rc == 2 and out == ""
    rc, out, _ = support.run_tmcore(["cleanup", "--what", "everything", "--session", "/nonexistent"])
    assert check_stream(validator, "cleanup", out, rc)["code"] == "E_PROTOCOL"


def test_no_override_options_and_no_secret_options():
    sub = build_parser()._subparsers._group_actions[0].choices          # noqa: SLF001
    opts = {o for p in [build_parser(), *sub.values()] for a in p._actions for o in a.option_strings}  # noqa: SLF001
    for forbidden in ("--allow", "--force", "--waive", "--skip", "--no-guard", "--override", "--deep-paths",
                      "--show-app-names", "--trim", "--password", "--passphrase", "--key", "--expect-real"):
        assert not [o for o in opts if o.startswith(forbidden)], forbidden
    assert "--secrets-stdin" in opts


@pytest.mark.parametrize("stdin,sub", [
    (b"", "secrets_not_one_line"),
    (b'{"backup_password":"a"}\n{"backup_password":"b"}\n', "secrets_not_one_line"),
    (b"backup_password=x\n", "secrets_not_json"),
    (b'{"backup_password":"x","password_file":"/tmp/x"}\n', "secrets_unknown_field"),
])
def test_malformed_secrets_line(validator, tmp_path, stdin, sub):
    sess = Session.create(tmp_path / "sessions")
    rc, out, err = support.run_tmcore(["prepare", "--session", str(sess.root), "--secrets-stdin"], stdin)
    res = check_stream(validator, "prepare", out, rc)
    assert res["code"] == "E_PROTOCOL" and res["data"]["sub"] == sub
    assert b"/tmp/x" not in out.encode() and "/tmp/x" not in err


# ------------------------------------------------------------------------------------------------ process frame
# A step of the real command table is replaced inside a fresh interpreter (`resolve()` looks the function up at call
# time); the frame around it -- hello, session, secrets, guard, result, exit code -- is the shipped one.
HARNESS = r"""
import sys, time, socket
sys.path.insert(0, ".")
import importlib
mod_name, fn_name, behaviour = sys.argv[1], sys.argv[2], sys.argv[3]
from tmcore.cli import StepResult, isolate_stdout, main
from tmcore import protocol as P
mod = importlib.import_module("tmcore.steps." + mod_name)

def step(ctx):
    if behaviour == "loop":                     # safe points only
        print("READY", file=sys.stderr, flush=True)
        for _ in range(600):
            time.sleep(0.05)
            ctx.proto.check_cancel()
        return StepResult({})
    if behaviour == "critical":                 # SIGTERM arrives while 'sending'
        with ctx.proto.critical():
            print("READY", file=sys.stderr, flush=True)
            time.sleep(1.5)
            ctx.proto.check_cancel()            # deferred: must not raise inside critical
        return StepResult({"last_progress": 100, "finished_at": P.utc_now()}, code="R_RESTORE_SENT_LINK_LOST",
                          device_modified="yes")
    if behaviour == "crash":
        raise ValueError("secret " + sys.argv[4])
    if behaviour == "inet":
        socket.create_connection(("127.0.0.1", 9), timeout=1)
    if behaviour in ("inet_swallowed", "unix_connect_swallowed", "ipv6_probe_swallowed"):
        try:
            if behaviour == "inet_swallowed":           # name lookup first: refused and counted
                socket.create_connection(("127.0.0.1", 9), timeout=1)
            elif behaviour == "unix_connect_swallowed": # not the usbmuxd socket: refused and counted
                socket.socket(socket.AF_UNIX).connect("/tmp/tmcore-test-not-usbmuxd.sock")
            else:                                       # urllib3's import-time probe: refused, NOT counted (0.3.1-dev)
                socket.socket(socket.AF_INET6).bind(("::1", 0))
        except OSError:
            pass
        return StepResult({"phase": "new", "resume_at": "S00", "restore_sent_at": None, "fresh_until": None,
                           "verdict": None})
    if behaviour == "print":
        print("free text on stdout " + sys.argv[4])
        sys.stdout.write("more free text\n")
        return StepResult({"phase": "new", "resume_at": "S00", "restore_sent_at": None, "fresh_until": None,
                           "verdict": None})
    if behaviour == "exit":
        sys.exit(3)
    raise AssertionError(behaviour)

setattr(mod, fn_name, step)
sys.exit(main(sys.argv[5:], out=isolate_stdout()))
"""


def _harness(mod, fn, behaviour, cli_argv, *, extra="x", stdin=b"", signal_after_ready=False):
    p = subprocess.Popen([sys.executable, "-E", "-s", "-B", "-c", HARNESS, mod, fn, behaviour, extra, *cli_argv],
                         cwd=support.CORE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=support.tmcore_env())
    p.stdin.write(stdin)
    p.stdin.close()
    if signal_after_ready:
        line = p.stderr.readline()
        assert b"READY" in line, line
        time.sleep(0.1)
        p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=60)
    return p.returncode, out.decode(), err.decode()


def test_sigterm_outside_critical_cancels_at_a_safe_point(validator, tmp_path):
    sess = Session.create(tmp_path / "s")
    rc, out, _ = _harness("status", "session_status", "loop", ["session-status", "--session", str(sess.root)],
                          signal_after_ready=True)
    res = check_stream(validator, "session-status", out, rc)
    assert res["code"] == "E_CANCELLED" and rc == 4 and res["device_modified"] == "no"


def test_sigterm_inside_critical_is_deferred_until_the_send_is_over(validator, tmp_path):
    sess = Session.create(tmp_path / "s")
    rc, out, _ = _harness("restore", "restore", "critical", ["restore", "--session", str(sess.root)],
                          signal_after_ready=True)
    res = check_stream(validator, "restore", out, rc)
    assert res["ok"] and res["code"] == "R_RESTORE_SENT_LINK_LOST" and res["device_modified"] == "yes" and rc == 0
    types = [json.loads(line)["type"] for line in out.splitlines()]
    assert types.index("critical") < types.index("result")


def test_exception_in_a_step_is_internal_without_its_message(validator, tmp_path):
    sess = Session.create(tmp_path / "s")
    rc, out, err = _harness("status", "session_status", "crash", ["session-status", "--session", str(sess.root)],
                            extra=CANARY_PW)
    res = check_stream(validator, "session-status", out, rc)
    assert res["code"] == "E_INTERNAL" and res["data"]["exc"] == "valueerror" and rc == 2
    assert CANARY_PW not in out and CANARY_PW not in err


def test_exception_after_critical_reports_device_modified_unknown(validator, tmp_path):
    code = HARNESS.replace('if behaviour == "crash":', 'if behaviour == "crash_critical":\n'
                           '        with ctx.proto.critical():\n            raise RuntimeError("x")\n'
                           '    if behaviour == "crash":')
    sess = Session.create(tmp_path / "s")
    p = subprocess.run([sys.executable, "-E", "-s", "-B", "-c", code, "restore", "restore", "crash_critical", "x",
                        "restore", "--session", str(sess.root)], cwd=support.CORE, capture_output=True,
                       env=support.tmcore_env(), timeout=60)
    res = check_stream(validator, "restore", p.stdout.decode(), p.returncode)
    assert res["code"] == "E_INTERNAL" and res["device_modified"] == "unknown"


@pytest.mark.parametrize("behaviour", ["inet", "inet_swallowed", "unix_connect_swallowed"])
def test_network_attempt_ends_as_network_blocked(validator, tmp_path, behaviour):
    sess = Session.create(tmp_path / "s")
    rc, out, _ = _harness("status", "session_status", behaviour, ["session-status", "--session", str(sess.root)])
    res = check_stream(validator, "session-status", out, rc)
    assert res["code"] == "E_NETWORK_BLOCKED" and rc == 2


def test_swallowed_socket_creation_is_not_network_blocked(validator, tmp_path):
    """Regression (first real-device run, 0.3.1-dev): urllib3 probes IPv6 at import time with
    socket.socket(AF_INET6) and swallows the refusal; that alone must not turn a command into E_NETWORK_BLOCKED."""
    sess = Session.create(tmp_path / "s")
    rc, out, _ = _harness("status", "session_status", "ipv6_probe_swallowed",
                          ["session-status", "--session", str(sess.root)])
    res = check_stream(validator, "session-status", out, rc)
    assert res["ok"] and res["code"] != "E_NETWORK_BLOCKED" and rc == 0


def test_stray_prints_never_reach_the_event_stream(validator, tmp_path):
    sess = Session.create(tmp_path / "s")
    rc, out, err = _harness("status", "session_status", "print", ["session-status", "--session", str(sess.root)],
                            extra=CANARY_PW)
    res = check_stream(validator, "session-status", out, rc)
    assert res["ok"] and "free text" not in out and "free text on stdout" in err
    assert out.count("\n") == len(out.splitlines())           # nothing but complete event lines


def test_sys_exit_in_a_step_still_gives_one_result(validator, tmp_path):
    sess = Session.create(tmp_path / "s")
    rc, out, _ = _harness("status", "session_status", "exit", ["session-status", "--session", str(sess.root)])
    res = check_stream(validator, "session-status", out, rc)
    assert res["code"] == "E_INTERNAL" and res["data"]["exc"] == "systemexit"


def test_stdin_secrets_are_redacted_from_stderr(validator, tmp_path):
    sess = Session.create(tmp_path / "s")
    secret = json.dumps({"backup_password": "Zz-very-secret-77"}).encode() + b"\n"
    rc, out, err = _harness("status", "session_status", "print",
                            ["session-status", "--session", str(sess.root), "--secrets-stdin"],
                            extra="Zz-very-secret-77", stdin=secret)
    check_stream(validator, "session-status", out, rc)
    assert "Zz-very-secret-77" not in err and "<redacted>" in err


def test_bundle_paths_are_never_written(tmp_path):
    """No .pyc next to the sources (python -B / PYTHONDONTWRITEBYTECODE) after a run: writing into the bundle would
    break the ad-hoc signature (DESIGN §3.3)."""
    before = {p for p in (support.CORE / "tmcore").rglob("*.pyc")}
    sess = Session.create(tmp_path / "s")
    support.run_tmcore(["session-status", "--session", str(sess.root)])
    after = {p for p in (support.CORE / "tmcore").rglob("*.pyc")}
    assert after <= before
