# SPDX-License-Identifier: AGPL-3.0-or-later
"""
scenario.py -- the switches of the virtual iPhone (DESIGN §13.2), one JSON file per scenario of DESIGN §13.3 in
fake/scenarios/<name>.json. A file lists only what differs from the defaults below.

    sc = load("find_my_on")
    sc.device["build"], sc.behaviour["find_my_on"], sc.compat_ios, sc.flow, sc.expect

Keys:
  device     what the phone is: product type, iOS build, encryption (and the synthetic password when it is already
             on), battery, free space, Find My (live lockdown answer), managed, Threema variant/version, airplane
             mode in the radios plist, the Threema store (default spec, or a spec), model ("V56" | "tampered"),
             Threema group prefs, claimed photo sizes, the device-watch state sequence, number of devices.
  behaviour  what the phone does: drop_first_backup, find_my_on (MBError 211 on restore), dcim_change_before_send,
             restore_end (link_lost | clean | link_lost_early | crash), clock_skip_min {cmd: minutes} (engine clock
             of that command moves forward; fake runs only), effects [poster, shortcuts, calendar, buddy_rerun]
             and damage [home_wipe, keyboard_collapse, keychain_items_lost, setup_reset, restore_state,
             threema_store_corrupt] applied by the first (final) restore.
  compat_ios the iOS allow-list a fake run uses instead of compat/ios.json (DESIGN: the shipped engine has no switch).
  flow       parameters of the wizard run that records the scenario (core/tests/e2e/flow.py).
  expect     the command and code (and verdict) the scenario ends with; checked by the e2e tests.
"""
from __future__ import annotations

import copy
import functools
import json
from pathlib import Path

from tmcore.protocol import EngineError

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
REPO = Path(__file__).resolve().parents[3]

EXPECTED_NOTES_24A437 = ["N_POSTER_CACHE_REGENERATED", "N_SHORTCUTS_CATALOGUE_REGENERATED", "N_CALENDAR_SYNC_TABLES",
                         "N_APPLE_ACCOUNT_RERUN"]
COMPAT_IOS = {"schema": 1, "kind": "ios", "builds": [
    {"build": "24A437", "ios": "27.0", "status": "verified", "verified_on": "2026-10-01", "evidence": None,
     "expected_notes": EXPECTED_NOTES_24A437, "min_app": "0.0.0", "comment_code": "fake_device"}]}

DEVICE = {
    "product_type": "iPhone17,1", "ios_version": "27.0", "build": "24A437",
    "encryption": False, "backup_password": None,
    "battery_pct": 81, "charging": True,
    "free_bytes": 48_000_000_000, "photos_bytes_estimate": 6_000_000_000,
    "find_my": "off", "managed": False,
    "threema": "regular", "threema_version": "7.4", "threema_build": "74051",
    "airplane": True,
    "store": "default", "model": "V56", "threema_prefs": {},
    "photos": 2, "photos_claimed_bytes": None,
    "watch": ["none", "locked", "untrusted", "ready"],
    "devices": 1,
}
BEHAVIOUR = {
    "drop_first_backup": False,
    "find_my_on": False,
    "dcim_change_before_send": False,
    "restore_end": "link_lost",
    "clock_skip_min": {},
    "effects": [],
    "damage": [],
}
FLOW = {
    "android": "full",                 # full | two_backups | incomplete | format_new | wrong_password
    "backup_password": "right",        # right | wrong_then_right
    "buddy_answer": "account_only",
    "threema_answer": "ok",            # S17 of the first check: ok | problem (after a rollback the answer is ok)
    "after_postcheck": [],             # ["rollback"]
}


@functools.lru_cache(maxsize=1)
def canaries() -> dict:
    """fixtures/canaries.json of the checkout (CI, recordings) or of $TMCORE_FIXTURES (demo run of the app bundle, the
    same folder fake/device.py takes gen_ios_backup.py from); neutral synthetic values when neither exists."""
    import os
    env = os.environ.get("TMCORE_FIXTURES")
    p = (Path(env) if env else REPO / "fixtures") / "canaries.json"
    try:
        c = json.loads(p.read_text(encoding="utf-8"))
        c["udid"] = "-".join(c["udid_parts"])
        return c
    except (OSError, ValueError, KeyError):
        return {"threema_id": "ZZDEMO01", "contact_first_name": "Demo", "contact_last_name": "Kontakt",
                "group_name": "Demo-Gruppe", "message_text": "Demo", "device_name": "ZZ Demo",
                "udid": "00008110-00ZZDEMODEV0001", "serial": "ZZDEMOSN0001", "phone": "+41 00 000 00 00",
                "password": "ZZ-Demo-Sicherung-01"}


def default_store_spec(own: str = "ZZFIXN01") -> dict:
    """Threema on the virtual iPhone right after a Threema Safe restore (+ one later message): own identity (the
    Android fixture's), one contact, one own group. Canary values feed the privacy scan (DESIGN §10.2)."""
    c = canaries()
    cid = c["threema_id"]
    return {"own": own,
            "contacts": [{"identity": cid, "publicKey": "c4" * 32, "firstName": c["contact_first_name"],
                          "lastName": c["contact_last_name"]}],
            "groups": [{"groupId": "c4c4000000000001", "creator": own, "name": c["group_name"],
                        "members": [own, cid]}],
            "oneToOne": [cid],
            "messages": [{"chat": f"contact:{cid}", "id": "c4c4c4c400000001", "text": c["message_text"],
                          "dateMs": 1790000000000, "isOwn": False}]}


class Scenario:
    def __init__(self, name: str, raw: dict):
        self.name = name
        self.raw = raw
        self.summary = raw.get("summary", "")
        self.expect_screen = raw.get("expect_screen", "")
        self.device = {**copy.deepcopy(DEVICE), **raw.get("device", {})}
        self.behaviour = {**copy.deepcopy(BEHAVIOUR), **raw.get("behaviour", {})}
        self.flow = {**copy.deepcopy(FLOW), **raw.get("flow", {})}
        self.expect = raw.get("expect", {})
        self.compat_ios = raw.get("compat_ios") or copy.deepcopy(COMPAT_IOS)

    def store_spec(self) -> dict | None:
        st = self.device["store"]
        if self.device["threema"] == "none":
            return None
        if st == "default":
            return default_store_spec()
        if isinstance(st, dict) and st.get("base") == "default":
            spec = default_store_spec(st.get("own", "ZZFIXN01"))
            for k, v in st.items():
                if k not in ("base", "own"):
                    spec[k] = spec.get(k, []) + v if isinstance(v, list) else v
            return spec
        return st


def names() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.json") if not p.name.startswith("_"))


@functools.lru_cache(maxsize=None)
def load(name: str) -> Scenario:
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise EngineError("E_PROTOCOL", sub="fake_scenario_unknown")
    p = SCENARIO_DIR / f"{name}.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EngineError("E_PROTOCOL", sub="fake_scenario_unknown") from None
    return Scenario(name, raw)


def clock_skip_min(name: str, cmd: str) -> int:
    return int(load(name).behaviour.get("clock_skip_min", {}).get(cmd, 0))
