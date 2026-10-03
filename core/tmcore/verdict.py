# SPDX-License-Identifier: AGPL-3.0-or-later
"""
verdict.py -- P.2/P.3/P.4 + purplebuddy + user answer -> verdict (DESIGN §7). Owner: coreB.

Priority (first match wins): setup_full > data_keychain > data > restore_state > threema_only > needs_answer >
ok_with_notes > ok. There is no "yellow" and no waiver in the product. restore_state comes before threema_only
(DESIGN §7 lists them the other way round): R1 would use the same restore mechanism that left Setup Assistant in the
"Restored" state, so that case must never be offered a reset (REVIEW m2, documented deviation).

Benign classes (backup_diff notes) count as harmless ONLY when they are listed in compat/ios.json expected_notes
for the device's build; an unlisted class becomes a data alarm. apple_account_rerun is harmless only with the
proof in the backups (SetupDone true before and after, only rerun keys changed) AND a user answer (S16) that rules
out the full Setup Assistant: "only Apple Account / Apple Pay" or "none". Every USB restore runs Setup Assistant
again and moves SetupLastExit (private runbook E.4), so a user who only saw "Restore completed" truthfully answers
"none"; what the answer must exclude is language/country/"Apps & data" (REVIEW M6, deviation from DESIGN §7, which
names only the first answer). Without the answer the verdict is needs_answer; "Also language, country or 'Apps &
data'" is always setup_full.

threema_answer (S17, REVIEW M1): "problem" = the user saw that Threema did not take over the history. With every
system check green this is threema_only (R1 allowed), exactly as P.2 red would be -- DESIGN §8.5 offers R1 for
"S17 'Problem' with green system checks", and the rollback guard (§6.1) only accepts threema_only, so the engine
decides it here instead of the app.
"""
from __future__ import annotations

import dataclasses

PRIORITY = ("setup_full", "data_keychain", "data", "restore_state", "threema_only", "needs_answer",
            "ok_with_notes", "ok")
RED = frozenset({"setup_full", "data_keychain", "data", "threema_only", "restore_state"})
NEXT_STEP = {"setup_full": "R4", "data_keychain": "R2k", "data": "R2", "threema_only": "R1", "restore_state": "R2",
             "needs_answer": "S16", "ok_with_notes": "S19", "ok": "S19"}

# backup_diff note class -> code
NOTE_CODES = {"poster_cache_regenerated": "N_POSTER_CACHE_REGENERATED",
              "shortcuts_catalogue_regenerated": "N_SHORTCUTS_CATALOGUE_REGENERATED",
              "calendar_sync_tables": "N_CALENDAR_SYNC_TABLES",
              "apple_account_rerun": "N_APPLE_ACCOUNT_RERUN"}
NOTE_AREAS = {"N_POSTER_CACHE_REGENERATED": "wallpapers", "N_SHORTCUTS_CATALOGUE_REGENERATED": "shortcuts",
              "N_CALENDAR_SYNC_TABLES": "calendar", "N_APPLE_ACCOUNT_RERUN": "setup_assistant",
              "N_KEYCHAIN_ITEMS_ADDED": "keychain"}
INFO_NOTES = frozenset({"N_KEYCHAIN_ITEMS_ADDED"})      # informational, not a regeneration class

DB_AREAS = {"tcc": "privacy_permissions", "messages": "messages_db", "accounts": "accounts",
            "call_history": "call_history", "calendar": "calendar", "shortcuts": "shortcuts"}
DOMAIN_AREAS = {"HomeDomain": "home_files", "CameraRollDomain": "photos_files", "KeyboardDomain": "keyboard_files",
                "KeychainDomain": "keychain", "MediaDomain": "sms_attachments"}


@dataclasses.dataclass
class GateView:
    """What one backup_diff run (P.3 payload view or P.4 strict view) found -- ids/classes only."""
    alert_ids: list[str]
    note_classes: list[str]
    buddy_class: str | None = None             # unchanged | apple_account_rerun | restore_state | setup_reset
    buddy_setup_done: bool = False             # SetupDone true before and after
    keychain_added: bool = False


@dataclasses.dataclass
class Verdict:
    verdict: str
    notes: list[str]
    areas: list[dict]
    threema_ok: bool

    @property
    def red(self) -> bool:
        return self.verdict in RED


def area_of(alert_id: str) -> str:
    kind, _, rest = alert_id.partition(":")
    if kind == "sentinel":
        return rest or "other_system"
    if kind == "db":
        return DB_AREAS.get(rest, "other_system")
    if kind in ("domain", "area_wiped", "collapse", "identity"):
        if rest in DOMAIN_AREAS:
            return DOMAIN_AREAS[rest]
        return "other_apps" if rest.startswith("app#") or rest.startswith(("AppDomain", "SysContainer",
                                                                            "SysSharedContainer")) \
            else "other_system"
    if kind == "keychain_items":
        return "keychain"
    if kind == "purplebuddy":
        return "setup_assistant"
    if kind == "threema":
        return "threema"
    return "other_system"


RERUN_ANSWERS = frozenset({"account_only", "none"})     # S16 answers that rule out the full Setup Assistant


def decide(*, p2_ok: bool, views: list[GateView], buddy_answer: str | None, expected_notes: list[str],
           threema_answer: str | None = None) -> Verdict:
    """views: P.3 and P.4 (both must be green). expected_notes: compat/ios.json of the device's build.
    threema_answer: S17 "ok" / "problem" / None (not asked)."""
    alerts: list[str] = []
    notes: list[str] = []
    buddy_class, keychain_added = "unchanged", False
    rerun_proof = True
    for v in views:
        for a in v.alert_ids:
            if a != "purplebuddy" and a not in alerts:      # purplebuddy is decided below, from its class
                alerts.append(a)
        for cls in v.note_classes:
            code = NOTE_CODES.get(cls)
            if code is None:
                alerts.append("note:unknown")
            elif code != "N_APPLE_ACCOUNT_RERUN" and code not in notes:
                notes.append(code)
        if v.buddy_class and v.buddy_class != "unchanged":
            # the most severe class of the two views wins
            order = ("unchanged", "apple_account_rerun", "restore_state", "setup_reset")
            if order.index(v.buddy_class if v.buddy_class in order else "setup_reset") > order.index(buddy_class):
                buddy_class = v.buddy_class if v.buddy_class in order else "setup_reset"
        if v.buddy_class == "apple_account_rerun" and not v.buddy_setup_done:
            rerun_proof = False
        keychain_added = keychain_added or v.keychain_added
    # regeneration classes not expected for this build are alarms (DESIGN §6.2)
    for n in list(notes):
        if n not in expected_notes:
            notes.remove(n)
            alerts.append(f"note:{n}")
    buddy_alarm = False
    if buddy_class == "apple_account_rerun":
        explained = rerun_proof and buddy_answer in RERUN_ANSWERS and "N_APPLE_ACCOUNT_RERUN" in expected_notes
        if explained:
            notes.append("N_APPLE_ACCOUNT_RERUN")
        elif buddy_answer is not None or not rerun_proof or "N_APPLE_ACCOUNT_RERUN" not in expected_notes:
            buddy_alarm = True
    if keychain_added:
        notes.append("N_KEYCHAIN_ITEMS_ADDED")

    areas: list[dict] = []

    def add(area: str, sev: str) -> None:
        if {"area": area, "severity": sev} not in areas:
            areas.append({"area": area, "severity": sev})
    for a in alerts:
        add(area_of(a), "alert")
    if buddy_alarm or buddy_class in ("restore_state", "setup_reset") or buddy_answer == "full_setup":
        add("setup_assistant", "alert")
    threema_problem = not p2_ok or threema_answer == "problem"
    if threema_problem:
        add("threema", "alert")
    for n in notes:
        add(NOTE_AREAS.get(n, "other_system"), "note")

    def out(v: str) -> Verdict:
        return Verdict(v, list(notes), areas, p2_ok)        # threema_ok = the P.2 machine check itself

    if buddy_answer == "full_setup" or buddy_class == "setup_reset":
        return out("setup_full")
    if "keychain_items" in alerts:
        return out("data_keychain")
    if alerts or buddy_alarm:
        return out("data")
    if buddy_class == "restore_state":
        return out("restore_state")
    if threema_problem:
        return out("threema_only")
    if buddy_class == "apple_account_rerun" and buddy_answer is None:
        return out("needs_answer")
    return out("ok_with_notes" if notes else "ok")
