# SPDX-License-Identifier: AGPL-3.0-or-later
"""tmcore.protocol: envelope, safe values, exactly one result, critical, exit codes (DESIGN §5.2-§5.6)."""
import io
import json

import pytest

from tmcore import protocol as P


def make(cmd="restore"):
    out = io.StringIO()
    return P.Protocol(cmd, out=out), out


def lines(out):
    return [json.loads(x) for x in out.getvalue().splitlines()]


def test_envelope_and_gapless_seq():
    p, out = make()
    p.hello(engine_version="0.3.0-dev", pymobiledevice3="11.19.4", importer_version="0.3.0-dev", models=["V56"],
            compat_digest="h:3f2a91c0")
    p.phase("guards", 1, 3)
    p.check("freshness", "pass", age_min=7, limit_min=60)
    evs = lines(out)
    assert [e["seq"] for e in evs] == [1, 2, 3]
    for e in evs:
        assert e["v"] == 1 and e["cmd"] == "restore" and e["ts"].endswith("Z") and len(e["ts"]) == 24
    assert evs[2]["data"] == {"age_min": 7, "limit_min": 60}


@pytest.mark.parametrize("bad", ["Some Person", "/tmp/x/Manifest.db", "a b", "ZZ@example.org", "Hello!"])
def test_free_text_paths_names_are_refused(bad):
    p, out = make()
    with pytest.raises(P.ProtocolViolation):
        p.check("extract", "pass", value=bad)
    assert out.getvalue() == ""


@pytest.mark.parametrize("good", ["apfs", "E_GUARD_FRESHNESS", "h:0a1b2c3d", "2026-10-01T13:43:40.120Z", "27.0",
                                  "24A437", "iPhone17,1", "V56", "S16", "F-INTERNAL", "android/missing-senders.json"])
def test_safe_values_pass(good):
    p, _ = make()
    p.check("extract", "pass", value=good)


def test_exactly_one_result_and_nothing_after():
    p, out = make("cleanup")
    assert p.result(True, "R_OK", {"freed_bytes": 1}) == 0
    with pytest.raises(P.ProtocolViolation):
        p.result(True, "R_OK", {})
    with pytest.raises(P.ProtocolViolation):
        p.note("W_USB2_SLOW")
    assert len(lines(out)) == 1


def test_result_code_consistency_and_exit_codes():
    p, _ = make()
    with pytest.raises(P.ProtocolViolation):
        p.result(True, "E_INTERNAL", {})
    assert make()[0].result(False, "E_GUARD_FRESHNESS", {"age_min": 71}) == 1
    assert make()[0].result(False, "E_INTERNAL", {}) == 2
    assert make()[0].result(False, "E_RESTORE_INTERRUPTED", {}) == 3
    assert make()[0].result(False, "E_CANCELLED", {}) == 4
    p2, out2 = make()
    p2.result(True, "R_RESTORE_SENT_LINK_LOST", {"last_progress": 100})
    res = lines(out2)[-1]
    assert res["device_modified"] == "yes" and res["retryable"] is False


def test_critical_brackets_and_blocks_result_inside():
    p, out = make()
    with p.critical():
        assert p.in_critical
        with pytest.raises(P.ProtocolViolation):
            p.result(True, "R_OK", {})
    p.result(True, "R_RESTORE_SENT_LINK_LOST", {"last_progress": 100})
    types = [(e["type"], e.get("on")) for e in lines(out)]
    assert types == [("critical", True), ("critical", False), ("result", None)]


def test_critical_closed_on_exception_and_cancel_deferred():
    p, out = make()
    p.request_cancel()
    with pytest.raises(RuntimeError):
        with p.critical():
            p.check_cancel()            # deferred while critical
            raise RuntimeError("boom")
    assert [e["on"] for e in lines(out)] == [True, False]
    with pytest.raises(P.Cancelled):
        p.check_cancel()


def test_line_limit():
    p, _ = make()
    with pytest.raises(P.ProtocolViolation):
        p.check("extract", "pass", values=[12345] * 20000)


def test_every_catalog_code_has_a_valid_exit():
    for code, entry in P.catalog().items():
        assert entry["exit"] in (0, 1, 2, 3, 4), code
        assert P.exit_code_for(code.startswith("R_"), code) == (0 if code.startswith("R_") else entry["exit"])
