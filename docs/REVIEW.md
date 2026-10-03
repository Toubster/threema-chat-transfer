<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Adversarial review after integration (P5, 0.3.0-dev)

Scope: the 0.3.0-dev integration tree (pre-public history, not in this repository) plus the built `build/dist/threema-chat-transfer-0.3.0-dev.dmg`. Two lenses: (A) safety, security
and privacy of the engine and the app; (B) a non-technical German user walking the wizard. Nothing in this review
touched an iPhone. Version impact: none of the findings changes the marker; fixes would go into 0.5.0 (P5) unless
noted.

## Verdict

The restore path itself is sound. Every guard of the proven private `restore.py` is present, none can be switched
off from the app, and the shipped allow-list (`compat/ios.json`: 24A437 = `unknown`) makes a live restore
impossible today. Privacy: tree, commit range and DMG are clean of personal data.

The weak spots are in the wizard around the guards. Three paths lead a user into a dead end or to wrong advice, and
two of them push towards "erase the iPhone" when that is not needed:

| # | Severity | Finding |
|---|---|---|
| B1 | **BLOCKER** | S09 "Finder" + encryption off → the app keeps a password that does not protect the backups; F-PW-WRONG loops without ever asking |
| M1 | MAJOR | S17 "Problem" with a green gate offers R1, the engine refuses it, S21 then claims "something other than Threema changed" |
| M2 | MAJOR | S16 answer "language/country/Apps & Data" still sends the user to S17 (open Threema) and S18 (backup) |
| M3 | MAJOR | S22 (iCloud) says "Erase All Content and Settings" with no check that today's iCloud backup exists |
| M4 | MAJOR | Every stop after S08 (cancel, F-INTERNAL, "wait for an update") leaves Find My, Stolen Device Protection and updates off without a word |
| M5 | MAJOR | `identity` reads the iPhone's Threema ID only from group conversations: no groups → F-INTERNAL in the offline window |
| M6 | MAJOR (plausible) | S16 truthful answer "None" + the routine `SetupLastExit` change → red `data` verdict → erase advice |
| m1–m14 | MINOR | see below |

## Lens A: safety, security, privacy

### Guard parity with the proven tools (verified by reading both and by probes)

| Private `tools/restore.py` | Public `tmcore` | Result |
|---|---|---|
| layout, do_not_restore/trim markers | `guards/setintegrity.py` `_layout` | same (root-level `<udid>.DO_NOT_RESTORE` dropped; there is no trim in the product) |
| marker (report sha256), source (Manifest.db sha256), plists (Lockdown/keybag/ManifestKey = source, Status date) | `_marker`, `_source` (source must be THIS session's PRE), `_plists` | same, stricter on source |
| structure (only Threema + complete Home/CameraRoll/Keyboard, rows and blob sizes identical, all blobs present, empty `-wal`/`-shm` rows, counts = report) | `_structure` | same |
| verify `--restoreset` subprocess | in-process `bp_run("verify", restoreset=True)` | same |
| freshness ≤ 60 min, `--allow-stale` ≤ 6 h | `freshness` (counts from the engine's backup start, earlier than Status.plist) + 6 h only inside `rollback-threema` | stricter; no option |
| airplane, `--allow-airplane-off` | `airplane` (missing/unreadable = refused) | no option |
| device UDID/build, free space 1.5× | `device`, `compat_ios`, `ios_unchanged`, `iphone_space` (unknown space = refused), plus UDID/build re-check inside the same lockdown session right before `mb.restore` | same |
| unchanged (sha256 right before send) | `set_integrity` `unchanged` | same |
| restore flags | `RESTORE_OPTIONS` = `system reboot no-copy no-settings no-remove skip-apps` | identical |
| — | new: `dcim_unchanged`, `identity`, `photos_limit`, `threema_model` (digest), `managed`, `battery`, Mac power, `findmy` | added |

`grep` over `core/tmcore`, `app/Sources`, `packaging/*.sh`: no `--allow-*`, `--waive`, `ALLOW_*`, `ROLLBACK_BEYOND_6H`.
`backup_pipeline.run` has no `trim` command. One exposure per session: `restore` refuses when `restore_sent_at` is set
(except after `E_RESTORE_INTERRUPTED`, see m6).

### Probes run for this review (fake device, synthetic fixtures, probe test deleted afterwards)

| Probe | Result |
|---|---|
| `happy` to verdict `ok`, then `rollback-threema` | `E_GUARD_ROLLBACK_NOT_ALLOWED {verdict: ok}` (evidence for M1) |
| `airplane_off`: PRE fails `E_GUARD_AIRPLANE`, then `prepare`, then `restore` | `prepare` → `R_OK` (builds a set from the failed PRE), `restore` → `E_PROTOCOL sub=pre_backup_missing`, no `critical` event (m1) |
| `scripts/scrub_check.py --require-layer2` (tree) | `CLEAN files=506 findings=0 layer2=on` |
| same with `--commits <root>..HEAD` | `CLEAN files=519`; only the author/committer GitHub no-reply address |
| DMG mounted read-only, `grep -a` for home paths, names, project paths | only the CI `runner` home path of the python-build-standalone build host; name hits only in third-party author lists (xonsh, tqdm, pygments); `Chat Transfer for Threema` and `threema-import` binaries and all `.pyc`: 0 build paths |
| scrub layer 1+2 on first-party bundle parts (`tmcore`, compat, schema, Scenarios, lproj, legal) | 20 findings, all in `legal/THIRD_PARTY_LICENSES` (third-party e-mails, common first names of upstream authors, one number) |
| `codesign -dv` / `spctl -a` | ad-hoc, `TeamIdentifier=not set`; `rejected` (expected without notarisation) |

### Secrets, network, files

- Passwords: only on stdin (one JSON line, strict schema, `secrets.py`); never in argv (`LiveEngine.arguments`); the
  app sets the complete environment (`LiveEngine.environment`). Keychain item `kSecAttrAccessibleWhenUnlocked`, only
  the generated password, only after `encryption-enable` succeeded (but see B1).
- Network: audit-hook socket guard (`netguard.py`, only AF_UNIX to usbmuxd; none with `--fake-device`) plus
  `engine-sandbox.sb` (`deny network*` except usbmuxd). The Swift app has no URLSession/Network use; "See new
  versions" opens the browser on click.
- Diagnostic report: safe-value filter, fresh salt, never reads `debug.log`. OK.

### MINOR findings (lens A)

- **m1 `prepare` accepts a PRE backup whose checks failed.** `steps/prepare.py:300` falls back to
  `finished + 60 min` when `pre.fresh_until` is null, so the "only a fully checked PRE" rule (docstring of
  `steps/backup.py`) holds only because `restore` re-checks `fresh_until` (`steps/restore.py` `_plan_final`). Make `prepare`
  refuse `fresh_until == null` / `password_ok != true` with the guard code instead of relying on the last line.
- **m2 Verdict priority differs from DESIGN §7.** `verdict.decide` returns `restore_state` before `threema_only`;
  §7 lists `threema_only` first. The code is the safer choice (R1 is the same mechanism); document it as a deviation.
- **m3 Unreadable facts pass.** Battery unreadable = `warn` (`guards/battery.py:18`), Mac power unknown = `warn`,
  Find My unknown = `warn` (per design until the `com.apple.fmip` check is confirmed on iOS 27; MBError 211 stays the
  hard stop). Fail-closed would refuse unknown battery and power.
- **m4 Soft override paths outside the app.** The shipped CLI reads `TMCORE_RESOURCES` and `TMCORE_IMPORTER`
  (`cli.py:114,129`): a copied Resources folder with an edited `compat/ios.json` turns an `unknown` build into
  `verified` for anyone who runs the CLI by hand. The app reads `TM_ENGINE` also from `UserDefaults`
  (`AppEnvironment.swift:38`) and the Release bundle ships `Resources/Scenarios/*.jsonl`, so
  `defaults write … TM_ENGINE mock:happy` turns the shipped app into a convincing (watermarked) fake run. Neither
  reaches a device by itself; restrict the env/defaults keys to Debug builds or to `fake:`/`mock:` only.
- **m5 Rollback error paths reuse PRE-flow actions.** During `rollback-threema`, F-DCIM offers "Neue Sicherung" →
  `backup --role pre` → `E_PROTOCOL restore_already_sent` → F-INTERNAL. An interrupted rollback cannot be retried:
  `rollback_used` is set before the first byte (`steps/restore.py:175`) and only reset on a refusal before staging.
- **m6 Retry after `E_RESTORE_INTERRUPTED`.** If the link drops below 99.5 % but the iPhone did commit and reboot, the
  user decides between "retry" and "it restarted". A wrong "retry" is a second exposure (same set, so low risk).
  Reading lockdown uptime/boot session before allowing the retry would remove the guesswork.
- **m7 Logs.** `debug.log` stays after "Chat-Kopien löschen" (`cleanup` keeps `logs/`); stderr of the media worker
  processes (`android_normalize` process pool) is not routed through the redactor; letter-only Threema IDs are not
  redacted. Local, 0600, never in the report; still worth a cleanup option.
- **m8 Release scrub of the DMG will always flag vendored third-party author lists** (layer-2 first names, e-mails,
  stdlib numbers). The §14.2 gate needs an allowlist for vendored third-party trees, otherwise it trains people to
  ignore findings.

## Lens B: layperson walk (demo screenshots DE + strings + flow code)

### iPhone-side instructions: order check

| Requirement | Where | Status |
|---|---|---|
| Threema Safe on Android checked first, Safe restore on iPhone later | S04.2 → S07.2 | OK |
| Find My off at home (Stolen Device Protection first) before airplane mode | S08.3 → S11 | OK |
| Bluetooth off | S11.2 | weak (m10) |
| Airplane mode before the backup | S11.3 → S12, enforced by guard `airplane` | OK |
| Passcode prompts | S03, S10a overlay, S12, S15, S18 | OK |
| Automatic reboot announced | S14, S15 | OK |
| "Swipe up to upgrade" | S16.1 | OK (†, unverified on a device) |
| Apple Account and Apple Pay afterwards | S16.3/4, S19 note, S20.6 | OK |
| Never erase | S14, S16 | OK, but contradicted by S22 (M3) |

### BLOCKER

**B1 Finder safety net with encryption off.** S09's Finder option tells the user to tick "Lokales Backup
verschlüsseln" — Finder then asks for a password and turns encryption ON. S09 → S10a/S10b is decided on the
`device-status` from S08 (`WizardStore+Flows.swift` `confirmSafetyNet`), so the app still shows S10a and generates a password.
`encryption-enable` sees encryption already on and returns `R_OK` "already_on" (`steps/encryption.py:31`); the app
treats that as success, stores the generated password as the backup password and in the keychain. The PRE backup
then fails `E_BACKUP_PASSWORD`; "Passwort erneut eingeben" clears memory and reloads the same wrong password from the
keychain (`WizardStore+Flows.swift:473`, `WizardStore+Actions.swift:57`) without ever showing the prompt — a loop
until the 5 attempts are used up. Worse, S10a told the user to write this password down "für jede spätere
Wiederherstellung", but the backups (including the archived Finder backup S22 relies on) are protected by the Finder
password. Fix: re-read encryption state when S09 is confirmed (and after any Finder backup), route "already_on" to
S10b, never store a password the device did not accept, and let F-PW-WRONG always ask.

### MAJOR

- **M1 S17 "Problem" with a green gate.** The app sends this to S21 R1 (`WizardStore+Flows.swift:639`, as DESIGN §8.5
  says), but the engine allows R1 only for verdict `threema_only` (`guards/rollback.py`, DESIGN §6.1) — probe above.
  `resetThreema` clears `threemaCheck` first (`:664`), so the refusal lands inline on S21 in the **R2** variant: "Außer
  Threema hat sich etwas verändert: nicht näher bestimmte Bereiche … Apple-Sicherung" plus "Folgen Sie bitte der
  Anleitung zu Ihrer Apple-Sicherung". A user with a Threema-only problem is pushed to erase the iPhone. Resolve the
  §6.1/§8.5 contradiction (let `postcheck` take the S17 answer so the verdict becomes `threema_only`, or do not offer
  R1 there) and add a scenario for it.
- **M2 S16 answer "Auch Sprache, Land oder Apps & Daten".** The red box says "nichts antippen" (stay in Setup
  Assistant), yet `confirmAfterRestart` always goes to S17 "Öffnen Sie Threema" and S18 (a POST backup of an iPhone
  sitting in Setup Assistant). The verdict is `setup_full` from the answer alone (`verdict.py`), so this path should go
  straight to S21 R4. S22 is also not written for a device in Setup Assistant (the iCloud text starts in Settings).
- **M3 S22 iCloud path.** "Einstellungen → Allgemein → … → Alle Inhalte & Einstellungen löschen" with no step "first
  check Settings → [name] → iCloud → iCloud Backup shows today's backup", while S14/S16 say "never erase". S09 is only
  a checkbox, so a failed iCloud backup is not caught anywhere before the erase. Add the check, say why erasing is
  right only here, and that Wi-Fi is needed in Setup Assistant for the iCloud restore.
- **M4 Leaving the wizard early.** From S08 on, the iPhone has Find My, Stolen Device Protection and automatic
  updates off (later also airplane mode/Bluetooth). Cancel (`cancel.text`), S23 discard, F-INTERNAL, "Beenden"
  screens and "wait for an update" (F-THREEMA-VERSION, F-ANDROID-FORMAT, F-IOS-UNKNOWN after S07) never say to turn
  them back on — only S20 does. A user waiting days for an update keeps an iPhone without Find My. Show the S20
  "wieder einschalten" list on every exit after S08.
- **M5 Identity guard and users without groups.** `facts.iphone_identity` reads only `ZCONVERSATION.ZGROUPMYIDENTITY`
  of group conversations (`guards/facts.py:150`). No group (or an old ID in one group) → `E_THREEMA_ID_UNREADABLE` →
  "Interner Prüffehler" with only "Diagnosebericht speichern", in the offline window. Fail-closed is right (DESIGN §18
  open fact), but the user needs a real text ("Ihr iPhone ist unverändert …, was jetzt") and the source of the ID
  should be confirmed on a device before release.
- **M6 S16 "Gar keine" (plausible, depends on the device).** After every restore Setup Assistant bookkeeping changes
  (`SetupLastExit`; private runbook E.4), so the class is `apple_account_rerun`. Only the answer `account_only` makes
  it a note; "none" makes it a `data` alarm (`verdict.py`, per DESIGN §7). A user who only saw "Wiederherstellung
  abgeschlossen → Fortfahren" answers "Gar keine" truthfully and gets red R2 with the erase guide. Reword the answers
  or treat `none` like `account_only` when the SetupDone proof holds.

### MINOR (lens B)

- **m9 Generated password.** Shown as `XXXX-XXXX-…`; the hyphens are part of it and it is upper case, but nothing says
  so, and the "last 4 characters" check ignores case and hyphens (`PasswordGenerator.matchesLastFour`). Say "mit
  Bindestrichen, Großbuchstaben" (it is needed again for Finder restores).
- **m10 S11 item 2** reads as "only if you have an Apple Watch"; Bluetooth off is required for everyone. Split it.
- **m11 S03** shows "iPhone (iPhone17,1)" — a model identifier, not a name a layperson recognises.
- **m12 Gatekeeper texts** (README, guides, "Zuerst lesen"): correct for macOS 15/26/27 (dialog "wurde nicht
  geöffnet" → Fertig → Datenschutz & Sicherheit → Trotzdem öffnen → password/Touch ID). On macOS 14 the first dialog
  reads "kann nicht geöffnet werden, da Apple darin nicht nach Schadsoftware suchen kann" without a "Fertig" button —
  step 2 does not match there. Missing: the "Trotzdem öffnen" button appears only for about an hour after the blocked
  attempt. The "ist beschädigt → Prüfsumme vergleichen" advice needs Terminal (chapter 10). All 29 device/Gatekeeper
  image links per guide (`docs/user/*/README.md`) point to files that do not exist yet.
- **m13 Unverified device texts.** All † menu paths (Threema "Backup wiederherstellen → Threema Safe", "Nachrichten
  behalten", iOS 27 "Zum Aktualisieren nach oben wischen", German Setup Assistant texts) are unconfirmed; with D6 (no
  spare iPhone) only the acceptance run can confirm them. F-IOS-UNKNOWN promises "prüfen wir meist innerhalb von zwei
  Wochen", which D6 cannot support.
- **m14 Second run.** `KeychainStore.save` silently overwrites an existing item; on a later session with encryption on,
  S10b asks for "das Passwort, das Sie damals gewählt haben" although the app generated it and holds it in the
  keychain.

### What reads well

Clear "Sie" German throughout, no jargon on the main path apart from APFS/FileVault on S02. Countdown, "Am iPhone
wurde nichts verändert" on every pre-send error, automatic new backup on F-FRESHNESS, Quit/Cancel locked during
`critical`, resume to S16 after a crash in `critical`, S21 "Flugmodus an lassen, nicht am WLAN laden" are all right.

## Not covered

No iPhone, no VM Gatekeeper run, no GitHub CI run. Statements about Setup Assistant behaviour (M2, M6) and the
Threema ID source (M5) need the device acceptance run.

## Resolution (fix round after this review)

Every BLOCKER and MAJOR was first reproduced from the code (and, where it needs the engine, on the virtual iPhone),
then fixed; none was refuted. Version impact: the fixes go into **0.5.0** (P5); the marker stays `0.3.0-dev`. They
change behaviour of the app (B1, M1, M2, M4, M5, M6) and of the engine (`postcheck`, `encryption-enable`, verdict,
`prepare`); the restore set, the guards of `restore`/`rollback-threema`, the restore flags and the shipped
allow-list (24A437 `unknown`) are unchanged.

| # | Verified | Fix | Evidence |
|---|---|---|---|
| B1 | yes: `confirmSafetyNet` routed by the S08 `device-status`; `encryption-enable` returned `R_OK` for "already on"; F-PW-WRONG reloaded the keychain copy | S09 "Weiter" reads `device-status` again when encryption was off at S08 and routes to S10b (with a note "the password you set in Finder"); `encryption-enable` returns `changed:false` when it was already on, and the app then keeps no password and stores none in the keychain; every `E_BACKUP_PASSWORD` drops the password, stops using the keychain copy (`password_in_keychain=false`) and F-PW-WRONG always shows the prompt (with a Finder hint); after a POST backup it re-checks the POST backup, not a new PRE backup; S09 Finder text says Finder asks for a password | `ReviewFixTests.testFinderSafetyNetReadsEncryptionAgain…`, `…EncryptionAlreadyOnNeverKeepsTheOfferedPassword`, `…RejectedKeychainPasswordIsNeverLoadedAgain`; every recording with encryption off now has the S09 `device-status` |
| M1 | yes (probe above): app sent S17 "Problem" + green to R1, engine refused | `postcheck --threema-answer ok\|problem`; `verdict.decide`: "problem" with every system check green = `threema_only` (so §8.5 R1 and the §6.1 rollback guard agree); the app no longer invents R1, `stopVariant` follows the verdict only; S17/S19 after a reset ask/say "Threema as before" (`s17.rollback.*`, `s19.rollback.text`, also after a restart: `rollback-threema` result or phase `rollback_sent`); a refused reset shows "So lassen" + Apple guide on S21 instead of a dead end | new scenario `threema_problem_reported` (E2E: threema_only → rollback → ok, recorded), `test_s17_problem_with_green_system_is_threema_only`, `ReviewFixTests.testS17ProblemGoesToTheEngineAndR1IsAccepted` |
| M2 | yes: `confirmAfterRestart` always went to S17/S18 | S16 `full_setup` → `postcheck --buddy-answer full_setup` without POST backup and without password (engine records `setup_full`, resume goes to S21) → S21 R4; S22 has R4 texts that start in Setup Assistant (no erase, Wi-Fi, "Aus iCloud-Backup" / Finder "Aus diesem Backup wiederherstellen" †) | scenario `setup_full` re-recorded on the new path, `ReviewFixTests.testSetupFullSkipsThreemaCheckAndControlBackup` |
| M3 | yes | S22 iCloud: step 1 check Settings → [Name] → iCloud → iCloud-Backup for today's backup made before the transfer time (shown), otherwise erase nothing ("So lassen"); step 2 why erasing is right only here; step 3 Wi-Fi in Setup Assistant | `s22.icloud` DE/EN, user guides ch. 6 |
| M4 | yes | `turnBackOnKeys` from the ticked S08/S11 items: shown on every F-screen without a way on, S23 (before the send), in the cancel, quit and discard alerts, and on S22 (after the Apple restore); "keep iOS updates off if you want to continue later" | `ReviewFixTests.testStopReminderListsWhatTheUserSwitchedOff`, user guides ch. 6 |
| M5 | yes (source = group conversations only; the FS session store is excluded from backups, the own ID lives only in the keychain) | stays fail-closed; `E_THREEMA_ID_UNREADABLE` now lands on F-THREEMA-ID with its own text ("Am iPhone wurde nichts verändert", create a note group with only yourself, then "Neue Sicherung", `needs_new_backup`) instead of "Interner Prüffehler" | `ErrorCatalogTests`, `ReviewFixTests.testUnreadableThreemaIdHasItsOwnTextAndANewBackup`; the ID source and the note-group remedy remain † for the device run |
| M6 | yes in code (`verdict.py`: answer `none` + `apple_account_rerun` = `data`); the routine re-run is documented in the private runbook (E.4) | `none` rules out the full Setup Assistant just like `account_only`: with the backups' proof it is the note `N_APPLE_ACCOUNT_RERUN`; without proof it stays `data`; `setup_reset` stays `setup_full`. S16 answers reworded ("Nur „Wiederherstellung abgeschlossen“, Apple Account …", "Gar keine Fragen") | `test_apple_account_rerun_needs_the_answer`, `test_answer_none_never_explains_a_rerun_without_proof` |
| m1 | yes | `prepare` refuses a PRE backup without `fresh_until`/`password_ok` (`E_PROTOCOL sub=pre_checks_failed`) | core suite |
| m2 | – | not changed (the code is the safer order); recorded as a deviation in `docs/INTEGRATION-REPORT.md` §6 | – |
| m3–m14 | – | open, see the table "Open after the fix round" in `docs/INTEGRATION-REPORT.md` | – |

Two further dead ends found while fixing M1 (same area, fixed with it): after "Threema zurücksetzen" S17 asked "are
the old chats there?" although a reset removes the Android history (a truthful "Problem" would have led to R1 again,
refused, and the erase advice), and S19 said "Ihr Threema-Verlauf ist auf dem iPhone". Both now have rollback texts.
