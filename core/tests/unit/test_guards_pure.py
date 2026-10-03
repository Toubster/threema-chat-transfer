# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guards (DESIGN §6.1) as pure functions: facts in, GuardResult out. No device, no files. Owner: coreB."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tmcore.guards import GuardResult, emit, report
from tmcore.guards import airplane, battery, compat, dcim, device, findmy, freshness, identity, managed
from tmcore.guards import photos_limit, rollback, space, threema
from tmcore.protocol import EngineError

GUARDS_DIR = Path(__file__).resolve().parents[2] / "tmcore" / "guards"


class _Proto:
    def __init__(self):
        self.events = []

    def check(self, check_id, status, code=None, **data):
        self.events.append((check_id, status, code, data))


class _Ctx:
    def __init__(self):
        self.proto = _Proto()


# ------------------------------------------------------------------------------------------------ report
def test_report_raises_with_catalog_data_and_emits_once():
    ctx = _Ctx()
    res = GuardResult("freshness", "fail", "E_GUARD_FRESHNESS", data={"age_min": 61, "limit_min": 60, "sub": "x"},
                      error_data={"age_min": 61, "limit_min": 60})
    with pytest.raises(EngineError) as ei:
        report(ctx, res)
    assert ei.value.code == "E_GUARD_FRESHNESS" and ei.value.data == {"age_min": 61, "limit_min": 60}
    assert ctx.proto.events == [("freshness", "fail", "E_GUARD_FRESHNESS", {"age_min": 61, "limit_min": 60,
                                                                            "sub": "x"})]


def test_emit_never_raises():
    ctx = _Ctx()
    assert emit(ctx, managed.check(managed=True)).failed
    assert ctx.proto.events[0][:3] == ("managed", "fail", "E_DEV_MANAGED")


def test_no_override_anywhere_in_the_guards():
    """No environment variable, option or flag can loosen a guard (DESIGN §6.1, §19)."""
    for p in GUARDS_DIR.glob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
                pytest.fail(f"{p.name} reads the environment")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                    node.value.startswith(("--allow", "--force", "--waive", "ALLOW_")):
                pytest.fail(f"{p.name} mentions an override")


# ------------------------------------------------------------------------------------------------ compat / ios
@pytest.mark.parametrize("stage,code", [("unknown", "E_IOS_UNKNOWN"), ("static_checked", "E_IOS_UNKNOWN"),
                                        ("blocked", "E_IOS_BLOCKED")])
def test_compat_only_verified(stage, code):
    assert compat.check(stage="verified", ios_version="27.0", ios_build="24A437").status == "pass"
    r = compat.check(stage=stage, ios_version="27.1", ios_build="24B5070a")
    assert (r.status, r.code) == ("fail", code)
    assert r.error_data == {"ios_version": "27.1", "ios_build": "24B5070a"}


def test_ios_unchanged():
    assert compat.ios_unchanged(device_build="24A437", pre_build="24A437").status == "pass"
    assert compat.ios_unchanged(device_build="24A440", pre_build="24A437").code == "E_IOS_CHANGED"
    assert compat.ios_unchanged(device_build="24A437", pre_build=None).code == "E_IOS_CHANGED"


# ------------------------------------------------------------------------------------------------ freshness
@pytest.mark.parametrize("age,ok", [(0, True), (60, True), (61, False), (-5, True), (-6, False), (400, False)])
def test_freshness_60_min(age, ok):
    r = freshness.check(age_min=age)
    assert (r.status == "pass") is ok
    if not ok:
        assert r.code == "E_GUARD_FRESHNESS" and r.error_data["limit_min"] == 60 and r.error_data["age_min"] >= 0


def test_rollback_window_is_its_own_check():
    assert rollback.window(age_min=359).status == "pass"
    r = rollback.window(age_min=361)
    assert (r.id, r.code) == ("rollback_window", "E_GUARD_ROLLBACK_WINDOW")
    assert rollback.window(age_min=-1).failed


# ------------------------------------------------------------------------------------------------ dcim
def test_dcim_unchanged_compares_paths_and_sizes():
    pre = [("DCIM/100APPLE/IMG_0001.HEIC", 1000), ("DCIM/100APPLE/IMG_0002.MOV", 5000)]
    same = [("/DCIM/100APPLE/IMG_0002.MOV", 5000), ("DCIM/100APPLE/IMG_0001.HEIC", 1000),
            ("DCIM/.MISC/info.plist", 3)]
    assert dcim.check(pre_rows=pre, device_rows=same).status == "pass"
    r = dcim.check(pre_rows=pre, device_rows=same + [("DCIM/100APPLE/IMG_0003.HEIC", 7)])
    assert (r.code, r.data["added"], r.data["removed"]) == ("E_GUARD_DCIM_CHANGED", 1, 0)
    r = dcim.check(pre_rows=pre, device_rows=[("DCIM/100APPLE/IMG_0001.HEIC", 1001), pre[1]])
    assert r.failed and r.data["added"] == 1 and r.data["removed"] == 1
    assert dcim.check(pre_rows=pre, device_rows=[]).failed


# ------------------------------------------------------------------------------------------------ identity
def test_identity_fail_closed():
    assert identity.check(same=True, iphone_readable=True, android_known=True).status == "pass"
    assert identity.check(same=False, iphone_readable=True, android_known=True).code == "E_THREEMA_ID_MISMATCH"
    r = identity.check(same=None, iphone_readable=False, android_known=True)
    assert (r.code, r.data) == ("E_THREEMA_ID_UNREADABLE", {"sub": "iphone"})
    assert identity.check(same=None, iphone_readable=True, android_known=False).code == "E_THREEMA_ID_UNREADABLE"


# ------------------------------------------------------------------------------------------------ space / photos
def test_iphone_space_1_5_x_payload():
    assert space.need_for(1000) == 1500 and space.need_for(1) == 2
    assert space.check(free_bytes=1500, need_bytes=1500).status == "pass"
    r = space.check(free_bytes=1499, need_bytes=1500)
    assert r.code == "E_GUARD_IPHONE_SPACE" and r.error_data == {"need_bytes": 1500, "free_bytes": 1499}
    assert space.check(free_bytes=None, need_bytes=10).failed                    # unknown free space = refused
    assert space.check(free_bytes=1, need_bytes=10, estimate=True).status == "warn"


def test_photos_limit_20_gb():
    assert photos_limit.LIMIT_BYTES == 20 * 10**9
    assert photos_limit.check(photos_bytes=20 * 10**9).status == "pass"
    assert photos_limit.check(photos_bytes=20 * 10**9 + 1).code == "E_GUARD_PHOTOS_LIMIT"
    assert photos_limit.check(photos_bytes=21 * 10**9, estimate=True).status == "warn"
    assert photos_limit.check(photos_bytes=None).failed                          # unknown after PRE = refused
    assert photos_limit.check(photos_bytes=None, estimate=True).status == "skip"


# ------------------------------------------------------------------------------------------------ device facts
def test_battery_and_mac_power():
    assert battery.check(battery_pct=50, charging=False).status == "pass"
    assert battery.check(battery_pct=10, charging=True).status == "pass"
    assert battery.check(battery_pct=49, charging=False).code == "E_DEV_BATTERY"
    assert battery.check(battery_pct=None, charging=False).status == "warn"
    assert battery.mac_check(power="battery").code == "E_HOST_POWER"
    assert battery.mac_check(power="ac").status == "pass"
    assert battery.mac_check(power="unknown").status == "warn"


def test_findmy_live_and_211():
    assert findmy.check(find_my="off").status == "pass"
    assert findmy.check(find_my="on").code == "E_GUARD_FINDMY"
    assert findmy.check(find_my="unknown").status == "warn"                     # MBError 211 is the hard stop
    r = findmy.refused_211()
    assert (r.code, r.data) == ("E_GUARD_FINDMY", {"source": "mberror_211"})


def test_managed_airplane_device():
    assert managed.check(managed=True).code == "E_DEV_MANAGED"
    assert managed.check(managed=False).status == "pass"
    assert airplane.check(airplane=True).status == "pass"
    assert airplane.check(airplane=False).code == "E_GUARD_AIRPLANE"
    assert airplane.check(airplane=None).data == {"state": "unreadable"}       # not proven = refused
    assert device.check(session_device="h:00000001", connected="h:00000001").status == "pass"
    assert device.check(session_device="h:00000001", connected="h:00000002").code == "E_DEV_OTHER"
    assert device.check(session_device=None, connected="h:00000002").failed


# ------------------------------------------------------------------------------------------------ threema
def test_threema_variant_setup_retention():
    assert threema.variant(installed=True, variant="regular", hard=True).status == "pass"
    assert threema.variant(installed=False, variant="work", hard=False).code == "E_THREEMA_VARIANT"
    assert threema.variant(installed=False, variant="none", hard=False).status == "skip"
    assert threema.variant(installed=False, variant="none", hard=True).code == "E_THREEMA_MISSING"
    assert threema.setup(app_setup_state=40, setup_marker=False, integrity_ok=True).status == "pass"
    assert threema.setup(app_setup_state=30, setup_marker=False, integrity_ok=True).data == {"sub": "state"}
    assert threema.setup(app_setup_state=40, setup_marker=True, integrity_ok=True).data == {"sub": "marker"}
    assert threema.setup(app_setup_state=40, setup_marker=False, integrity_ok=False).data == {"sub": "integrity"}
    assert threema.retention(keep_messages_days=None).status == "pass"
    assert threema.retention(keep_messages_days=0).status == "pass"
    assert threema.retention(keep_messages_days=30).code == "E_THREEMA_RETENTION"


def test_threema_model_hash_exact():
    hashes = threema.model_hashes_b64({"Message": b"\x01" * 32, "Contact": b"\x02" * 32})
    digest = threema.model_digest(hashes)
    comp = {"models": [{"id": "V56", "version_hashes_sha256": digest, "status": "verified",
                        "blocked_app_versions": ["7.4.1"]}]}
    res, mid = threema.model(digest=digest, app_version="7.4", compat=comp)
    assert (res.status, mid) == ("pass", "V56")
    other = threema.model_digest({**hashes, "Message": "AA=="})
    res, mid = threema.model(digest=other, app_version="7.4", compat=comp)
    assert (res.code, mid) == ("E_THREEMA_MODEL_UNKNOWN", None)
    res, _ = threema.model(digest=digest, app_version="7.4.1", compat=comp)  # explicitly blocked app version
    assert res.code == "E_THREEMA_MODEL_UNKNOWN"
    res, _ = threema.model(digest=None, app_version=None, compat=comp)
    assert res.failed
    comp["models"][0]["status"] = "unknown"
    assert threema.model(digest=digest, app_version="7.4", compat=comp)[0].failed


def test_rollback_allowed_only_after_final_threema_only():
    ok = dict(last_kind="final", verdict="threema_only", pre_matches=True, used=False)
    assert rollback.allowed(**ok).status == "pass"
    for k, v, sub in (("last_kind", "rollback", "not_final"), ("verdict", "data", "verdict"),
                      ("pre_matches", False, "pre_changed"), ("used", True, "used")):
        r = rollback.allowed(**{**ok, k: v})
        assert (r.code, r.data["sub"]) == ("E_GUARD_ROLLBACK_NOT_ALLOWED", sub)
    assert rollback.allowed(**{**ok, "verdict": None}).error_data == {"verdict": "none"}


def test_mac_space_before_a_backup():
    need = space.mac_need_for_backup(photos_bytes=6 * 10**9, home_estimate=2 * 10**9)
    assert need == 9 * 10**9
    assert space.mac_check(free_bytes=need, need_bytes=need).status == "pass"
    r = space.mac_check(free_bytes=need - 1, need_bytes=need)
    assert (r.id, r.code, r.data) == ("free_space", "E_HOST_SPACE", {"free_bytes": need - 1, "need_bytes": need})
