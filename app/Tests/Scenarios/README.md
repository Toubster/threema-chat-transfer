# Mock scenarios (recordings)

Replayed by the MockEngine (`TM_ENGINE=mock:<name>`) in unit and UI tests. Format and rules:
`docs/ENGINE-PROTOCOL.md` § Mock scenarios. Validate with `python3 scripts/validate_schemas.py`.

Every file here is a **recording of the real engine on the virtual iPhone** (header `"draft": false`):
`make record-scenarios` (`devtools/record_scenarios.py`) runs each scenario of `core/tmcore/fake/scenarios/` as a
whole wizard flow (`core/tests/e2e/flow.py`) and writes `<name>.jsonl`; `make check-scenarios` re-records into a
temporary folder and compares the structure (directives, event types, check ids, codes, verdicts). Timestamps start at
2026-01-01T09:00:00Z, session hashes are fixed (device `h:5c0ffee1`, own ID `h:0a1b2c3d`), host-check shows the
canonical example Mac, chat counts are the canonical example values; sizes are those of the synthetic fixtures.
`devtools/draft_scenarios.py` (the hand-written drafts of P2.5) never overwrites a recording.

| File | Ends on | What happens |
|---|---|---|
| happy | S20 | encryption switched on by the tool (S10a), full run, verdict ok, cleanup |
| happy_with_notes | S20 | the four harmless iOS 27 effects → ok_with_notes |
| first_backup_dropped | S20 | first backup session dropped, `retry` W_BACKUP_RETRY |
| find_my_on | F-FINDMY | MBError 211 before staging, nothing changed |
| airplane_off | F-AIRPLANE | PRE backup taken online |
| freshness_expired | S20 | restore refused (> 60 min), new backup + prepare, transfer |
| dcim_changed | S20 | photo taken after the PRE backup, new backup + prepare, transfer |
| ios_unknown | F-IOS-UNKNOWN | beta build; Android part runs, nothing transferred |
| threema_missing | F-THREEMA-MISSING | Threema not installed |
| threema_model_unknown | F-THREEMA-VERSION | unknown Core Data model hash |
| id_mismatch | F-THREEMA-ID | Android ID ≠ iPhone ID |
| wrong_backup_password | S20 | wrong password, same backup re-checked |
| iphone_space_low | F-IPHONE-SPACE | not enough space on the iPhone |
| photos_limit | F-PHOTOS-LIMIT | > 20 GB local photos |
| link_lost_after_send | S20 | link lost at 97 % (E_RESTORE_INTERRUPTED), iPhone restarted, check |
| app_crash_after_send | S20 | engine killed while sending, relaunch → S16, check |
| threema_only_fail | S21 | threema_only (R1 offered) |
| rollback_threema_ok | S20 | threema_only → rollback-threema → ok |
| threema_problem_reported | S20 | S17 "Problem" with every system check green → threema_only (R1) → rollback-threema → ok |
| data_fail | S21 | data (R2) |
| keychain_fail | S21 | data_keychain (R2k) |
| setup_full | S21 | S16 "also language, country or Apps & data" → `postcheck --buddy-answer full_setup` without S17 and without a POST backup → setup_full (R4) |
| restore_state | S21 | restore_state |
| android_two_backups | S20 | chats from the newer, media from the older backup |
| android_incomplete | S05 | interrupted Android backup |
| android_format_new | F-ANDROID-FORMAT | newer Android format |
| duplicate_chat | F-IMPORT-DUP | two 1:1 chats with the same person |
| wrong_android_password | S07 | first Android password wrong (inline S05) |
