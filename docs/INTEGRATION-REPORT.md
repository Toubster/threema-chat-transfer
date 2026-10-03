# Integration report (P5)

Date: 2026-10-01 (updated after the fix round of `docs/REVIEW.md`). Version marker: `0.3.0-dev` (no new tag).
Target of this work: **0.5.0** (DESIGN §16 P5, integration and package). It is public-safe by construction: only
synthetic data, the virtual iPhone, codes, test counts and sizes of the build; no device was connected or contacted,
nothing was pushed or published.

## 0. Fix round after the review (`docs/REVIEW.md`)

Every BLOCKER and MAJOR of the review was confirmed and fixed (resolution table in `docs/REVIEW.md`). **Behaviour
changes** (app and engine), all for 0.5.0; restore set, guards of `restore`/`rollback-threema`, restore flags and the
shipped allow-list (24A437 `unknown`) are unchanged.

| # | Change | Where |
|---|---|---|
| B1 | S09 re-reads `device-status` when encryption was off at S08 (a Finder backup turns it on with the user's password) → S10b with a Finder note; `encryption-enable` result `changed:false` when it was already on → the app keeps and stores no password; every `E_BACKUP_PASSWORD` drops the password and the keychain copy is not used again; F-PW-WRONG always asks (S18: re-checks the POST backup); S09 Finder text mentions the password | `WizardStore+Flows.swift`, `steps/encryption.py`, `events.v1` (optional field), strings |
| M1 | `postcheck --threema-answer ok\|problem`; S17 "Problem" with green system checks = `threema_only` in the engine, so R1 is accepted; `stopVariant` follows the verdict only; S17/S19 rollback texts; refused reset → "So lassen" + Apple guide | `verdict.py`, `steps/postcheck.py`, `cli.py`, app, new scenario `threema_problem_reported` |
| M2 | S16 `full_setup` → `postcheck --buddy-answer full_setup` without POST backup and without password → S21 R4; S22 R4 texts start in Setup Assistant | `steps/postcheck.py`, app, `flow.py` |
| M3 | S22 iCloud: check today's iCloud backup from before the transfer first, else erase nothing; why erasing is right only here; Wi-Fi | strings, guides |
| M4 | "turn back on" list (from the ticked S08/S11 items) on every F-screen without a way on, S23 before the send, the cancel/quit/discard alerts and S22 | app, strings, guides |
| M5 | `E_THREEMA_ID_UNREADABLE` → F-THREEMA-ID with its own text (nothing changed, note group, "Neue Sicherung", `needs_new_backup`) | `codes.v1.json` |
| M6 | S16 "none" rules out the full Setup Assistant like "account_only" (note `N_APPLE_ACCOUNT_RERUN` only with the backups' proof); answer texts reworded | `verdict.py`, strings |
| m1/m2 | `prepare` refuses a PRE backup whose checks failed; `verdict.PRIORITY` and the docs now state the real order (`restore_state` before `threema_only`) | `steps/prepare.py`, `verdict.py`, `RESTORE-MECHANISM.md` |

## 1. What changed in the integration round

| Area | Change | Behaviour? |
|---|---|---|
| App ↔ engine | `LiveEngine` runs the bundled `tmcore` of the signed app (`Contents/Resources/core/python/bin/python3 -I -B -m tmcore`, complete environment, `sandbox-exec` with `engine-sandbox.sb`, secrets only on stdin). For `fake:` runs only it also passes `TMCORE_FIXTURES` (from `TM_DEMO_FIXTURES`): the virtual iPhone builds its backups with `fixtures/gen_ios_backup.py`, which the bundle does not ship. | demo only |
| Demo autopilot | `app/Sources/System/DemoAutopilot.swift`: with `TM_AUTOPILOT=1` the app plays the user directives of a scenario recording against its own store and renders its window in-process (title bar, DEMO watermark). It refuses the live engine. `devtools/demo_screenshots.py` drives the signed app with it (`make demo-screenshots`, `demo-screenshots.yml`); no UI automation permission is needed. | demo only |
| Packaging (privacy) | Every `.pyc` of the bundle recorded the build machine's home folder in `co_filename`, and `threema-import` carried build paths in its debug stabs. `build-core.sh` now compiles with `-f -s <stage> -p /Chat Transfer for Threema.app/Contents/Resources`, `build-importer.sh` maps source paths and strips debug symbols, and both fail on a remaining build path; `verify-bundle.sh` checks every file of the bundle. | no (bytes only) |
| Engine | `host-check --fake-device` reports a fixed virtual Mac (recordings and screenshots never show the build machine). `redact.py` keeps ISO timestamps in `debug.log` (they were redacted as phone numbers); phone numbers stay redacted. `lib/restore_engine.py` and its ported test are deleted (steps + guards replace them). | fake runs / debug log only |
| Contract | `codes.v1.json` gained `x-area-tokens` (27 tokens, DE/EN); `gen_codes.py` manages `area.*`; `validate_schemas.py` validates the block; a core test fails when the verdict can emit a token without words. Compatible addition, protocol stays 1. | no |
| Texts and layout (found on the screenshots) | counts that can be 1 read correctly (S06 result line, S07 item 5, `W_OWN_UNSENT_AS_SENT`, `W_MISSING_KEY_SENDERS`); sizes below 0.1 GB show "< 0,1 GB" instead of "0 GB"; S19 notes as one compact list (all notes fit the smallest window); the sidebar shows S21/S22 under "Kontrolle" (not "Fertig") and S23 at the session's step. | UI only |
| Wizard (bug found by the MockEngine run of the real app) | F-FRESHNESS starts the new backup by itself after a short pause (DESIGN §8.4). That automatic start called `runPreBackup()` from the very task that `runPreBackup()` cancels, so the backup ran in a cancelled task, its event stream ended at once and the run always stopped on F-INTERNAL; only clicking "Neue Sicherung" worked. Fixed, with a unit test that waits for the automatic path; a late click never starts a second backup. | **yes** (app) |
| Accessibility crash (found by the XCUITests) | S10a crashed the app (stack overflow inside SwiftUI's accessibility label resolution) as soon as an accessibility client looked at it -- VoiceOver or a UI test: the spelled-out label sat directly on selectable text. The password is now one accessibility element of its own (static text, spelled label). | **yes** (app, VoiceOver) |
| Accessibility (found by the XCUITests) | every sheet is an accessibility container (its own identifier no longer replaced the identifiers of its controls, e.g. the password field of the restart prompt); a text box that holds buttons keeps them reachable (the cleanup buttons of S20 were merged into one element for VoiceOver). The UI tests themselves: the title is looked up by identifier, not as static text (headings are not static text on macOS 27); AppKit's own title-bar buttons are not checked for wizard labels; one password per chosen Android backup. | UI / accessibility only |
| CI | e2e job sets `TMCORE_IMPORTER` (was `THREEMA_IMPORT_BIN`, which nothing reads); `ffmpeg` and `pillow` for the Android fixture; `model/build-momd.sh` treats a byte difference as a note when the Xcode build differs from the one in `model/V56/SOURCE` (canonical dump and identity stay hard). | no |

Restore set, guards, flags and verdicts were unchanged in the integration round (the fix round in §0 changes verdict inputs only).

## 2. Verification

| Check | Command | Result |
|---|---|---|
| Core, full suite | `cd core && python -m pytest -q` | all passed except 1 skip (the skip needs pymobiledevice3, which only the bundled runtime has); 4 more tests than in the integration round |
| Core with the bundled runtime | `build/stage/Resources/core/python/bin/python3 -B -m pytest tests/unit tests/contract` (test tools on `PYTHONPATH`, never in the bundle) | 344 passed, 0 skipped |
| E2E, all scenarios, network denied | `sandbox-exec -p '(version 1) (allow default) (deny network*)' python -m pytest tests/e2e` (canary scan per scenario) | 45 passed (28 scenarios + 17 guard tests on real artifacts); canary scan 0 hits |
| Scenario recording + drift | `make record-scenarios` (all 28 re-recorded: S09 re-read, `--threema-answer`, `full_setup` path), then `devtools/record_scenarios.py --check` | 28 of 28 structurally equal |
| Importer | `python importer/Tests/run_checks.py` | 182 pass, 0 fail |
| Model | `model/build-momd.sh --verify --source <threema-ios at the pinned commit>` | RESULT OK (file set, bytes, canonical dump, identity) |
| Schemas, codes, strings, docs | `validate_schemas.py`, `gen_codes.py --check`, `check_strings.py`, `docs/tools/check_docs.py --require-images app` | PASS (37 schema checks), up to date, PASS (661 keys), CLEAN (every app screenshot present) |
| Lint | `ruff check --select E9,F`, `mypy tmcore`, `actionlint`, `shellcheck` (no shell script changed) | clean |
| App unit + snapshot (DE/EN, light/dark, smallest window) | `app/scripts/test.sh unit` | 83 tests, 0 failures (every scenario replayed through the store in DE and EN, size checks of every screen, the automatic F-FRESHNESS backup, 7 tests of the review fixes in `ReviewFixTests`) |
| App XCUITests (MockEngine, every scenario DE + EN, VoiceOver labels) | `app/scripts/test.sh ui` | integration round: 54 of 54 pass. **Fix round: not run** -- the runner stops at "Timed out while enabling automation mode" (twice): macOS asks for the user's authentication to enable Automation Mode and nobody was at the Mac; changing that setting is the maintainer's decision. The same UI flows of all 28 recordings ran through the real signed app (two rows below) |
| Bundle | `make dmg OFFLINE=1` → `verify-bundle.sh --dmg` (final build of the fix round) | RESULT OK twice (app, then DMG incl. the app inside it); app 140 MB, DMG 44 MiB |
| Real engine, synthetic data, no fake device | `devtools/synthetic_prepare_run.py` | every check OK (see below) |
| Real app, real engine, virtual iPhone | `devtools/demo_screenshots.py --all-scenarios` (final DMG build) | 56 of 56 runs on the expected screen (table below), bundle unchanged, no network attempt, canary scan clean |
| Real app, MockEngine (UI flows of every recording) | `devtools/demo_screenshots.py --all-scenarios --engine mock` | 56 of 56 runs on the expected screen, bundle unchanged |
| Fresh copy and quarantine | `devtools/fresh_copy_check.sh` (final DMG) | all OK (see §3) |
| Privacy scan | `scripts/scrub_check.py --require-layer2` (tree, screenshots, commit messages, unpacked DMG) | CLEAN, layers 1 + 2 (tracked tree incl. the 68 regenerated screenshots; every commit message of the repository); final DMG mounted read-only: no home or build path in any file; first-party parts: 18 hits only in `legal/THIRD_PARTY_LICENSES` (third-party author lists, m8) and the two compiled `Localizable.strings` flagged only as UTF-16 "binary" (their UTF-8 copy is CLEAN, layer 2) |

### Real engine on synthetic data (no `--fake-device`)

The engine of the signed app ran exactly as `LiveEngine` starts it (`sandbox-exec -f engine-sandbox.sb`, `-I -B`,
the complete app environment): `version` → `selftest` → `android-inspect` (two synthetic Android backups, plan
`text_plus_media`) → `android-normalize` → a synthetic encrypted PRE backup of `fixtures/gen_ios_backup.py` announced
as `backup --role pre` leaves it → `prepare` (checks extract, threema_model, import, coredata_open, verify_import,
restoreset, verify_restoreset, freeze: all pass) → `session-status` (resume at S14) → `diag-report`. An independent
`backup_pipeline verify --restoreset --source --against` of the frozen set with the bundled interpreter: PASS; the set
holds exactly the Threema domains plus HomeDomain, CameraRollDomain and KeyboardDomain. Every stdout line validated
against `events.v1`; no `E_NETWORK_BLOCKED`, no netguard refusal; canary scan clean; the password is in no file of the
session; the bundle is byte-identical afterwards.

### Real app in demo mode (virtual iPhone)

`devtools/demo_screenshots.py --all-scenarios` started the signed app (final DMG build of the fix round) once per scenario and language (28 × 2, `app_crash_after_send` twice per language: crash during the transfer, then the relaunch), with `TM_ENGINE=fake:<scenario>`: every command ran in the bundled `tmcore` on the virtual iPhone (no socket at all) under the app's `sandbox-exec` profile, and the demo autopilot clicked through the scenario's user directives. Result: 56 of 56 ended on the expected screen, the bundle was byte-identical afterwards, the engine logs show no network attempt, and the canary scan of every session found nothing. The happy path and every failure scenario of DESIGN §13.3 are covered:

| Scenario | Expected end | Reached (DE / EN) | Screenshots taken here |
|---|---|---|---|
| `airplane_off` | F-AIRPLANE | F-AIRPLANE / F-AIRPLANE | F-AIRPLANE |
| `android_format_new` | F-ANDROID-FORMAT | F-ANDROID-FORMAT / F-ANDROID-FORMAT | – |
| `android_incomplete` | S05 | S05 / S05 | – |
| `android_two_backups` | S20 | S20 / S20 | – |
| `app_crash_after_send` | S20 | S20 / S20 | S23 |
| `data_fail` | S21 | S22 / S22 | S21-data, S22 |
| `dcim_changed` | S20 | S20 / S20 | F-DCIM |
| `duplicate_chat` | F-IMPORT-DUP | F-IMPORT-DUP / F-IMPORT-DUP | – |
| `find_my_on` | F-FINDMY | F-FINDMY / F-FINDMY | F-FINDMY |
| `first_backup_dropped` | S20 | S20 / S20 | – |
| `freshness_expired` | S20 | S20 / S20 | F-FRESHNESS |
| `happy` | S20 | S20 / S20 | S00–S18 (without S10b) and S20 |
| `happy_with_notes` | S20 | S20 / S20 | S19 |
| `id_mismatch` | F-THREEMA-ID | F-THREEMA-ID / F-THREEMA-ID | F-THREEMA-ID |
| `ios_unknown` | F-IOS-UNKNOWN | F-IOS-UNKNOWN / F-IOS-UNKNOWN | F-IOS-UNKNOWN |
| `iphone_space_low` | F-IPHONE-SPACE | F-IPHONE-SPACE / F-IPHONE-SPACE | – |
| `keychain_fail` | S21 | S21-data_keychain / S21-data_keychain | S21-data_keychain |
| `link_lost_after_send` | S20 | S20 / S20 | – |
| `photos_limit` | F-PHOTOS-LIMIT | F-PHOTOS-LIMIT / F-PHOTOS-LIMIT | – |
| `restore_state` | S21 | S21-data / S21-data | – |
| `rollback_threema_ok` | S20 | S20 / S20 | – |
| `setup_full` | S21 (S16 → `postcheck --buddy-answer full_setup`, no S17/S18) | S21-setup_full / S21-setup_full | S21-setup_full |
| `threema_missing` | F-THREEMA-MISSING | F-THREEMA-MISSING / F-THREEMA-MISSING | – |
| `threema_model_unknown` | F-THREEMA-VERSION | F-THREEMA-VERSION / F-THREEMA-VERSION | – |
| `threema_only_fail` | S21 | S21-threema_only / S21-threema_only | S21-threema_only |
| `threema_problem_reported` (new) | S20 (S17 "Problem" → threema_only → reset → ok) | S20 / S20 | – |
| `wrong_android_password` | S07 | S07 / S07 | – |
| `wrong_backup_password` | S20 | S20 / S20 | S10b |

("S21-…" names the S21 variant; data_fail goes on to S22 for its screenshot.)

## 3. Package

| Item | Result |
|---|---|
| App bundle | 140 MB (budget 200 MB); 91 Mach-O files, every one ad-hoc signed inside out, arm64 only, minimum macOS 14.0; no Hardened Runtime, no App Sandbox (DESIGN §14.1) |
| Versions | `CFBundleShortVersionString` 0.3.0, `TMEngineVersion` 0.3.0-dev = `engine_version` = `importer_version`; `CFBundleVersion` 1 |
| Engine from the bundle | `tmcore version` and `selftest` (61 modules, manifest digests, model, importer, network guard) pass under an empty environment; the bundle is byte-identical before and after |
| Build paths | no file of the bundle contains the build checkout or a home folder (new hard check) |
| DMG | 44 MiB = 47 MB (budget 90 MB), UDZO, ad-hoc signed, `hdiutil verify` OK; content: the app, "Programme"/Applications link, "Zuerst lesen.pdf", "Read me first.pdf", background with the four Gatekeeper steps |
| Fresh copy | the app copied out of the mounted DMG into a folder whose path has spaces and umlauts: strict signature OK, and the copy starts and runs the happy path end to end on the virtual iPhone with its bundled engine; bundle unchanged |
| Quarantine | a second copy with `com.apple.quarantine` (as from a browser download) is only assessed, never opened (no Gatekeeper dialog on the build Mac): `spctl --assess` rejects it, as expected for an ad-hoc signed, not notarized app. The user path "Trotzdem öffnen" / "Open Anyway" is documented in both READMEs, both user guides, both read-me-first PDFs and the DMG background |
| Privacy of the DMG content | layer 2 of the scrub check over the unpacked DMG: no hit in any file of Chat Transfer for Threema itself; the only hits are common first names in third-party author/licence lists and numeric constants inside bundled third-party libraries (false positives of the deny list, no data of the maintainer) |


## 4. Screenshots

Regenerated in the fix round (`devtools/demo_screenshots.py --install`: 28 runs, 0 problems, 68 files marked
`tm-demo=1`, allowlist block rewritten); S09, S16 and S22 show the new texts.

All 34 app screenshots of `docs/images/screenshots.json` exist in German and English under
`docs/images/app/<lang>/` (`docs/images/{group}/{lang}/{id}.png`, the layout the guides and `check_docs.py` use). They
come from the signed app with the real engine on the virtual iPhone, carry the DEMO watermark and the PNG marker
`tm-demo=1`, and are listed in the allowlist block "demo screenshots". Every picture was looked at; the problems found
are fixed in §1 ("Texts and layout"). The counts on them are those of the synthetic fixture (for example a two-digit
number of messages), not the canonical example values of DESIGN §10.4; `--engine mock` produces the same pictures
from the recordings, which carry the canonical values.

## 5. Open

| Item | Who |
|---|---|
| **Device acceptance run** (DESIGN §20 D6): a full wizard run with exactly this DMG on the maintainer's own iPhone, only after the maintainer's explicit go. Until then `compat/ios.json` keeps 24A437 `unknown` and every real restore is refused. It must also confirm the open device facts of the review: the Threema ID source (group conversations) and the note-group remedy (M5), the Setup Assistant behaviour after the restore and what users answer on S16 (M2, M6), the R4/iCloud texts of S22 and every other † text (m13). | maintainer, explicit go only |
| **Publishing go** (push, GitHub release): nothing is pushed or published; no remote exists. | maintainer, explicit go only |
| XCUITests of the fix round (`app/scripts/test.sh ui`): run once with someone at the Mac to authenticate "Automation Mode" (not run in the fix round, see §2). | maintainer (or the integrator while the maintainer authenticates) |
| Gatekeeper click-through ("Trotzdem öffnen" / "Open Anyway") on clean macOS 14/15/26/27 VMs (DESIGN §13.1 level 10). Locally only assessed: the quarantined copy is rejected as expected and the path is documented. The macOS 14 dialog wording (m12) is part of this. | maintainer |
| No workflow has run on GitHub yet (no remote). | maintainer (after the publishing go) |
| Review MINOR findings left open on purpose (no device or a design decision needed; none blocks the acceptance run): m3 unknown battery/Mac power/Find My only warn; m4 `TMCORE_RESOURCES`/`TMCORE_IMPORTER` and `TM_ENGINE` from UserDefaults in the shipped build; m5 rollback error paths reuse PRE actions, an interrupted rollback cannot be retried; m6 retry after `E_RESTORE_INTERRUPTED` relies on the user; m7 `debug.log` kept after cleanup, worker stderr not redacted, letter-only IDs; m8 release scrub of the DMG needs an allowlist for vendored third-party author lists; m9 generated password: hyphens/capitals not explained; m10 S11 item 2 wording; m11 S03 model identifier; m12 Gatekeeper text for macOS 14, 29 missing device/Gatekeeper images per guide; m13 † texts unverified; m14 keychain item silently overwritten on a second run. | integrator, next round (0.5.x) |

## 6. Deviations from DESIGN

| DESIGN | Here | Why |
|---|---|---|
| §13.1 level 7 / §12 `demo-screenshots.yml`: XCUITest produces the screenshots | the app's demo autopilot + in-process rendering (`devtools/demo_screenshots.py`); `DemoScreenshotTests` stays as a MockEngine aid | needs no UI automation permission, runs the real signed app with the real engine, deterministic pictures |
| §15: screenshots in `docs/user/*/images` | `docs/images/app/<lang>/` | the guides, `screenshots.json` and `check_docs.py` already use that layout |
| §10.4: only canonical counts in docs | screenshots show the synthetic fixture's counts | they come from the real engine on the virtual iPhone, as §13.1 asks; mock-mode pictures with canonical values are one flag away |
| §8.3 S06/S07 texts with "{n} …" | "Chats: {chats} · Gruppen: {groups} …", "(Personen: {k}, Nachrichten: {m})" and the two W_ notes number-neutral | grammatically correct for 1 in both languages |
| §8.3 sidebar phases | S21/S22 under "Kontrolle", S23 at the session's step | the move is not "done" on a red stop; S23 showed "Start" after a sent restore |
| §6.1 vs §8.5 (R1 for S17 "Problem" with green checks, but the rollback guard only for `threema_only`) | `postcheck --threema-answer`: S17 "Problem" with green system checks is the verdict `threema_only`; the guard is unchanged | DESIGN contradicted itself; the engine stays the only place that decides (REVIEW M1) |
| §7 order `threema_only` before `restore_state` | `restore_state` first (code, `verdict.PRIORITY`, docs) | R1 must never be offered for a restore that left Setup Assistant "Restored" (REVIEW m2) |
| §7 `N_APPLE_ACCOUNT_RERUN` only with the answer "nur Apple-Account/Apple Pay" | also with "Gar keine Fragen" (still only with the backups' proof) | every USB restore re-runs Setup Assistant; the answer must rule out the full Setup Assistant, which both answers do (REVIEW M6) |
| §5.4/§8.3 S16 → S17 → S18 for every answer | `full_setup` → `postcheck` without POST backup and password → S21 R4 | an iPhone in Setup Assistant can neither open Threema nor be expected to make a backup (REVIEW M2) |
| §5.4 `encryption-enable` → `encryption:"on"` | plus optional `changed` | the app must know whether the backups are protected by its password (REVIEW B1); compatible addition, protocol stays 1 |
| §8.3 S09 → S10a/S10b from the S08 status | S09 re-reads `device-status` when encryption was off | the Finder option of S09 turns encryption on (REVIEW B1) |
| §5.5 `E_THREEMA_ID_UNREADABLE` → F-INTERNAL | → F-THREEMA-ID with its own text and "Neue Sicherung" | an internal-error text in the offline window gave no way on (REVIEW M5); still fail-closed |
| §8.3 texts S09 (Finder), S16 answers, S17/S19 after a reset, S22 (iCloud check first, R4 variant, turn-back-on list), exits after S08 | extended as listed in §0 | REVIEW B1, M1–M4, M6 |
| §13.3 26 scenarios | 28 recordings (+ `wrong_android_password`, + `threema_problem_reported`) | the S17 "Problem" path needs its own end-to-end proof |
