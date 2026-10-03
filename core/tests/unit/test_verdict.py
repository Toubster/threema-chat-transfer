# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verdict classes of gate v2 (DESIGN §7): priority, benign classes only per build and only with evidence, the
purplebuddy re-run only with the user's answer, no yellow, no waiver. Owner: coreB."""
from __future__ import annotations

import pytest

from tmcore.verdict import PRIORITY, GateView, area_of, decide

EXPECTED = ["N_POSTER_CACHE_REGENERATED", "N_SHORTCUTS_CATALOGUE_REGENERATED", "N_CALENDAR_SYNC_TABLES",
            "N_APPLE_ACCOUNT_RERUN"]
BENIGN = ["poster_cache_regenerated", "shortcuts_catalogue_regenerated", "calendar_sync_tables"]


def green(**kw) -> GateView:
    return GateView(alert_ids=[], note_classes=[], buddy_class="unchanged", buddy_setup_done=True, **kw)


def v(*, p2=True, views=None, answer="account_only", expected=EXPECTED, threema=None):
    return decide(p2_ok=p2, views=views or [green(), green()], buddy_answer=answer, expected_notes=expected,
                  threema_answer=threema)


def test_priority_order_is_fixed():
    assert PRIORITY == ("setup_full", "data_keychain", "data", "restore_state", "threema_only", "needs_answer",
                        "ok_with_notes", "ok")


def test_all_green_is_ok():
    r = v()
    assert (r.verdict, r.notes, r.areas, r.threema_ok) == ("ok", [], [], True)


def test_benign_classes_of_the_build_are_notes():
    view = GateView(alert_ids=[], note_classes=BENIGN + ["apple_account_rerun"], buddy_class="apple_account_rerun",
                    buddy_setup_done=True)
    r = v(views=[view, view])
    assert r.verdict == "ok_with_notes"
    assert sorted(r.notes) == sorted(EXPECTED)
    assert all(a["severity"] == "note" for a in r.areas)


def test_benign_class_not_listed_for_the_build_is_an_alarm():
    view = GateView(alert_ids=[], note_classes=["poster_cache_regenerated"], buddy_class="unchanged",
                    buddy_setup_done=True)
    r = v(views=[view, green()], expected=["N_CALENDAR_SYNC_TABLES"])
    assert r.verdict == "data"
    assert {"area": "other_system", "severity": "alert"} in r.areas or any(a["severity"] == "alert" for a in r.areas)
    assert "N_POSTER_CACHE_REGENERATED" not in r.notes


def test_unknown_note_class_is_an_alarm():
    r = v(views=[GateView(alert_ids=[], note_classes=["something_new"]), green()])
    assert r.verdict == "data"


def test_apple_account_rerun_needs_the_answer():
    view = GateView(alert_ids=[], note_classes=["apple_account_rerun"], buddy_class="apple_account_rerun",
                    buddy_setup_done=True)
    assert v(views=[view, view], answer=None).verdict == "needs_answer"
    assert v(views=[view, view], answer="account_only").verdict == "ok_with_notes"
    # every USB restore re-runs Setup Assistant (SetupLastExit moves); a user who only saw "Restore completed"
    # answers "none" -- that rules out the full Setup Assistant as well (REVIEW M6)
    r = v(views=[view, view], answer="none")
    assert (r.verdict, r.notes) == ("ok_with_notes", ["N_APPLE_ACCOUNT_RERUN"])
    assert v(views=[view, view], answer="full_setup").verdict == "setup_full"


def test_answer_none_never_explains_a_rerun_without_proof():
    view = GateView(alert_ids=["purplebuddy"], note_classes=[], buddy_class="apple_account_rerun",
                    buddy_setup_done=False)
    assert v(views=[view, view], answer="none").verdict == "data"
    reset = GateView(alert_ids=[], note_classes=[], buddy_class="setup_reset", buddy_setup_done=False)
    assert v(views=[reset, reset], answer="none").verdict == "setup_full"


def test_s17_problem_with_green_system_is_threema_only():
    """DESIGN §8.5: R1 for "S17 'Problem' with green system checks"; the rollback guard only accepts threema_only,
    so the engine turns the answer into that verdict (REVIEW M1)."""
    r = v(threema="problem")
    assert (r.verdict, r.threema_ok) == ("threema_only", True)
    assert r.areas == [{"area": "threema", "severity": "alert"}]
    assert v(threema="ok").verdict == "ok"
    # never for a data alarm, a Setup Assistant reset or restore_state
    assert v(threema="problem", views=[GateView(["db:tcc"], []), green()]).verdict == "data"
    assert v(threema="problem", answer="full_setup").verdict == "setup_full"
    assert v(threema="problem", views=[GateView([], [], "restore_state", True), green()]).verdict == "restore_state"


def test_rerun_without_setup_done_proof_stays_an_alarm():
    view = GateView(alert_ids=["purplebuddy"], note_classes=[], buddy_class="apple_account_rerun",
                    buddy_setup_done=False)
    assert v(views=[view, view]).verdict == "data"


def test_rerun_not_expected_for_the_build_stays_an_alarm():
    view = GateView(alert_ids=[], note_classes=["apple_account_rerun"], buddy_class="apple_account_rerun",
                    buddy_setup_done=True)
    assert v(views=[view, view], expected=[]).verdict == "data"


@pytest.mark.parametrize("views,p2,answer,want", [
    ([GateView(["keychain_items", "domain:HomeDomain"], [], "setup_reset", False), green()], False, "account_only",
     "setup_full"),
    ([GateView(["keychain_items", "sentinel:keyboard"], []), green()], False, "account_only", "data_keychain"),
    ([GateView(["sentinel:preferences"], []), green()], False, "account_only", "data"),
    ([green(), green()], False, "account_only", "threema_only"),
    ([GateView([], [], "restore_state", True), green()], True, "account_only", "restore_state"),
    ([GateView([], [], "restore_state", True), green()], False, "account_only", "restore_state"),   # never R1
    ([green(), green()], True, "full_setup", "setup_full"),
])
def test_first_matching_class_wins(views, p2, answer, want):
    assert v(views=views, p2=p2, answer=answer).verdict == want


def test_threema_only_only_when_system_is_green():
    r = v(p2=False)
    assert r.verdict == "threema_only" and r.areas == [{"area": "threema", "severity": "alert"}]
    r = v(p2=False, views=[GateView(["db:tcc"], []), green()])
    assert r.verdict == "data"                     # R1 would use the same mechanism: never offered for data


def test_keychain_items_added_is_a_note():
    r = v(views=[green(keychain_added=True), green()])
    assert r.verdict == "ok_with_notes" and r.notes == ["N_KEYCHAIN_ITEMS_ADDED"]


@pytest.mark.parametrize("alert,area", [("sentinel:keyboard", "keyboard"), ("db:tcc", "privacy_permissions"),
                                        ("domain:HomeDomain", "home_files"), ("area_wiped:CameraRollDomain",
                                                                              "photos_files"),
                                        ("identity:KeyboardDomain", "keyboard_files"),
                                        ("domain:app#1a2b3c4d", "other_apps"), ("keychain_items", "keychain"),
                                        ("purplebuddy", "setup_assistant"), ("weird", "other_system")])
def test_area_tokens(alert, area):
    assert area_of(alert) == area


def test_no_yellow_and_no_waiver_in_the_api():
    import inspect
    from tmcore import verdict
    assert "waive" not in inspect.signature(decide).parameters
    assert "yellow" not in inspect.getsource(verdict).lower().replace('no "yellow"', "")


def test_every_area_token_has_de_en_words():
    """postcheck areas[].area is a closed token set; the app shows only the words of codes.v1.json x-area-tokens
    (S21 R2 {Bereiche}), never a raw token. Every token the verdict can emit must be listed."""
    import json
    from pathlib import Path
    from tmcore import verdict as V
    from tmcore.lib import backup_diff
    cat = json.loads((Path(__file__).resolve().parents[2] / "schema" / "codes.v1.json").read_text(encoding="utf-8"))
    listed = set(cat["x-area-tokens"])
    emitted = ({sid for sid, *_ in backup_diff.SENTINELS} | set(V.NOTE_AREAS.values()) | set(V.DB_AREAS.values())
               | set(V.DOMAIN_AREAS.values()) | {"other_system", "other_apps", "setup_assistant", "threema"})
    assert emitted <= listed, sorted(emitted - listed)
    for probe in ("sentinel:keyboard", "db:tcc", "domain:HomeDomain", "domain:AppDomain-x", "collapse:zz",
                  "keychain_items:genp", "purplebuddy:x", "threema:p2", "unknown:x"):
        assert V.area_of(probe) in listed, probe
