# SPDX-License-Identifier: AGPL-3.0-or-later
"""The virtual iPhone of --fake-device (DESIGN §13.2): scenarios, the proven restore option set, iOS-27 annotation
semantics of a restore, parsing through the same code as a real device. No device. Owner: coreB."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests import support
from tmcore.fake import device as D
from tmcore.fake import scenario as S
from tmcore.protocol import EngineError
from tmcore.steps import iphone as I

DESIGN_SCENARIOS = ("happy happy_with_notes first_backup_dropped find_my_on airplane_off freshness_expired "
                    "dcim_changed ios_unknown threema_missing threema_model_unknown id_mismatch wrong_backup_password "
                    "iphone_space_low photos_limit link_lost_after_send app_crash_after_send threema_only_fail "
                    "data_fail keychain_fail setup_full restore_state rollback_threema_ok android_two_backups "
                    "android_incomplete android_format_new duplicate_chat").split()


def test_the_proven_restore_options_exactly():
    # CLI equivalent: backup2 restore --system --reboot --no-settings --no-copy --no-remove --skip-apps
    assert I.RESTORE_OPTIONS == {"system": True, "reboot": True, "copy": False, "settings": False, "remove": False,
                                 "skip_apps": True}


def test_every_design_scenario_exists_and_loads():
    names = S.names()
    assert set(DESIGN_SCENARIOS) <= set(names)
    for n in names:
        sc = S.load(n)
        assert sc.expect_screen and sc.summary and sc.expect.get("cmd") and sc.expect.get("code")
        assert set(sc.raw) <= {"summary", "expect_screen", "expect", "device", "behaviour", "flow", "compat_ios"}
        assert set(sc.device) == set(S.DEVICE), n
        assert set(sc.behaviour) == set(S.BEHAVIOUR), n
        assert set(sc.flow) == set(S.FLOW), n


def test_scenario_names_are_identical_to_the_mock_files():
    mocks = {p.stem for p in (support.REPO / "app" / "Tests" / "Scenarios").glob("*.jsonl")}
    assert mocks <= set(S.names())


@pytest.mark.parametrize("bad", ["", "../happy", "happy.json", "nope"])
def test_unknown_scenario_is_a_protocol_error(bad):
    with pytest.raises(EngineError) as ei:
        S.load(bad)
    assert ei.value.code == "E_PROTOCOL"


def test_fake_compat_comes_from_the_scenario_not_from_a_switch():
    """The shipped compat/ios.json marks a build verified only with an evidence record (fail-closed); a fake run uses
    the scenario's list. There is no engine option that changes the list."""
    shipped = json.loads((support.REPO / "compat" / "ios.json").read_text())
    assert all(b.get("status") != "verified" or b.get("evidence") for b in shipped.get("builds") or [])
    assert S.load("happy").compat_ios["builds"][0]["status"] == "verified"


def test_parse_facts_is_shared_and_never_leaks_free_text():
    with pytest.raises(I.DeviceError):
        I.parse_facts({"ProductType": "iPhone17,1", "ProductVersion": "27.0", "BuildVersion": "Name's build"},
                      {}, {}, None, None, None, False, {})
    f = I.parse_facts({"UniqueDeviceID": "x", "ProductType": "iPhone17,1", "ProductVersion": "27.0",
                       "BuildVersion": "24A437"}, {"BatteryCurrentCapacity": 40, "BatteryIsCharging": False},
                      {"TotalDataAvailable": 9, "AmountDataAvailable": 7, "PhotoUsage": 5}, {}, {"IsSupervised": True},
                      None, True, {"ch.threema.iapp": {"CFBundleShortVersionString": "7.4 (beta)"}})
    assert (f.free_bytes, f.photos_bytes_estimate, f.find_my, f.managed, f.threema_variant, f.threema_version) == \
        (7, 5, "unknown", True, "regular", None)


@pytest.fixture()
def phone(tmp_path, monkeypatch):
    monkeypatch.setenv("TMCORE_FAKE_HOME", str(tmp_path / "fake"))
    return D.VirtualIPhone(tmp_path, "happy")


def _image(phone):
    g = D.fixtures()
    img = g.build_image(variant="none", extras=True)
    phone.save_image(img)
    return img


def test_restore_refuses_any_other_option_set(phone, tmp_path):
    for k in I.RESTORE_OPTIONS:
        opts = {**I.RESTORE_OPTIONS, k: not I.RESTORE_OPTIONS[k]}
        with pytest.raises(D.FakeDeviceLinkError) as ei:
            phone.restore(tmp_path, phone.udid, "pw", opts, lambda p: None)
        assert ei.value.reason == "unexpected_options"
    assert phone.state["restores"] == 0


def test_annotation_semantics_partial_payload_wipes_home(phone):
    """iOS 27: HomeDomain/CameraRollDomain rows missing from the payload are deleted at the commit; a payload WITHOUT
    HomeDomain wipes it (the incident) -- the fixture E2E uses this to prove the gate turns red."""
    img = _image(phone)
    home_before = {e.rel for e in img.domain("HomeDomain")}
    keep = sorted(home_before)[:3]
    payload = {("HomeDomain", r): img.entries[("HomeDomain", r)] for r in keep}
    payload.update({k: e for k, e in img.entries.items() if k[0] in ("CameraRollDomain", "KeyboardDomain")})
    other = next(d for d in img.domains() if d not in D.FULL_DOMAINS)
    other_rows = {k for k in img.entries if k[0] == other}
    phone._apply(img, payload)
    assert {e.rel for e in img.domain("HomeDomain")} == set(keep)        # missing in the payload = deleted
    assert {k for k in img.entries if k[0] == other} == other_rows        # not in the payload = untouched


def test_annotation_semantics_keyboard_absent_collapses(phone):
    img = _image(phone)
    big = [e.rel for e in img.domain("KeyboardDomain") if e.flags == 1 and e.size > 16 * 1024]
    assert big, "the extras carry a learned keyboard model"
    payload = {k: e for k, e in img.entries.items() if k[0] in ("HomeDomain", "CameraRollDomain")}
    phone._apply(img, payload)
    assert all(img.entries[("KeyboardDomain", r)].size == 4096 for r in big)


def test_annotation_semantics_full_set_keeps_everything(phone):
    img = _image(phone)
    before = {k: (e.flags, e.size) for k, e in img.entries.items()}
    payload = {k: e for k, e in img.entries.items() if k[0] in D.FULL_DOMAINS}
    phone._apply(img, payload)
    assert {k: (e.flags, e.size) for k, e in img.entries.items()} == before


def test_device_password_canary(phone):
    assert D.device_password(S.load("happy_with_notes")) == S.canaries()["password"]
    with pytest.raises(ValueError):
        D.device_password(S.load("happy"))                                # encryption off: no password yet


def test_fake_state_never_stores_the_password(tmp_path, monkeypatch):
    monkeypatch.setenv("TMCORE_FAKE_HOME", str(tmp_path / "fake"))
    ph = D.VirtualIPhone(tmp_path, "happy_with_notes")
    raw = (tmp_path / "fake" / "state.json").read_bytes()
    assert S.canaries()["password"].encode() not in raw and ph.state["secret"]
    assert os.stat(tmp_path / "fake" / "state.json").st_mode & 0o077 == 0


def test_usbmux_watch_sequence(tmp_path, monkeypatch):
    from tmcore.fake import usbmux
    monkeypatch.setenv("TMCORE_FAKE_HOME", str(tmp_path / "fake"))
    ph = D.VirtualIPhone(tmp_path, "happy")
    seen = [usbmux.list_devices(ph, watch=True) for _ in range(5)]
    assert [s[0][1] if s else "none" for s in seen] == ["none", "locked", "untrusted", "ready", "ready"]
    assert usbmux.list_devices(ph) == [(ph.udid, "ready")]


def test_fake_gateway_is_selected_only_with_fake_device(tmp_path):
    from tmcore.session import Session

    class Ctx:
        fake_device = None
        session = Session.create(tmp_path / "s")
    assert isinstance(I.gateway(Ctx()), I.RealGateway)
    Ctx.fake_device = "happy"
    from tmcore.fake.gateway import FakeGateway
    assert isinstance(I.gateway(Ctx()), FakeGateway)


def test_scenario_files_are_synthetic_only():
    for p in Path(S.SCENARIO_DIR).glob("*.json"):
        text = p.read_text(encoding="utf-8")
        assert "/Users/" not in text and "@" not in text.replace("@rg=", "")
