# Restore mechanism: what iOS 27 changed and how Chat Transfer for Threema restores

This document explains why Chat Transfer for Threema restores the way it does. It contains **structure only**: domain names, restore
options, public strings from Apple binaries and the shape of the evidence. It contains no personal data, no device
identifiers and no counts from anyone's device.

Confidence words used below: **proven** (observed on a device and reproducible from the data), **strong** (several
independent public sources agree), **plausible** (consistent with the evidence, not proven), **open**.

## 1. Background: iOS backups and restores in five terms

| Term | Meaning |
|---|---|
| Domain | An iOS backup is split into domains: one per app (`AppDomain-<bundle id>`), per app group (`AppDomainGroup-…`, where Threema keeps its database) and per system area (`HomeDomain`, `CameraRollDomain`, `KeyboardDomain`, `KeychainDomain`, `MediaDomain`, `SystemPreferencesDomain`, …). `Manifest.db` lists every file with its domain. |
| MobileBackup2 restore | The USB protocol a Mac uses to restore a backup. The host sends the files and a few options; the device (`BackupAgent2`) stages them and commits them **at the next boot** (`FinishRestoreFromBackup`). |
| Restore options | `RestoreSystemFiles`, `RemoveItemsNotRestored`, `RestoreShouldReboot`, `RestoreDontCopyBackup`, `RestorePreserveSettings`. The device uses its own defaults for options the host does not send. |
| Annotation | Before commit, `BackupAgent2` can *annotate* a domain: every path of the domain's restore set that is missing in the staged data gets the marker `NotRestored`. At the next boot the live file behind such a marker is **deleted**. A staged directory marked `RestoreRoot` is cleaned: live children absent from the staging are removed. |
| `Domains.plist` | The system's list of domains and their path rules, e.g. `RelativePathsToBackupAndRestore`, `RelativePathsNotToRemoveIfNotRestored` and `RelativePathsOfSystemFilesToAlwaysRemoveOnRestore`. |

pymobiledevice3 maps its command-line flags to these options: `--system` → `RestoreSystemFiles`, `--remove` →
`RemoveItemsNotRestored`, `--reboot` → `RestoreShouldReboot`, `--copy` → the inverse of `RestoreDontCopyBackup`, and
`--settings` → `RestorePreserveSettings` (note: `--settings` means *preserve*). It always sends all five keys.

## 2. What iOS 27 changed

Up to iOS 26.5, a restore with `RemoveItemsNotRestored = false` logged *"Not annotating"* and behaved as a pure
**overlay**: every staged file replaced its live counterpart and nothing else was touched. Community tools used this
for years to restore single apps.

The string diff of `BackupAgent2` between iOS 26.5 (23F77) and the first iOS 27.0 beta (24A5355q), published by
ipsw-diffs, shows the change ([blacktop/ipsw-diffs](https://github.com/blacktop/ipsw-diffs),
`iOS/26_5_23F77_vs_27_0_24A5355q/MACHOS/filesystem/usr/libexec/BackupAgent2.md`):

| Change | String |
|---|---|
| added | `"Only annotating domains with system files to always remove on restore"` |
| added | `domainsWithSystemFilesToAlwaysRemoveOnRestore` (new selector) |
| removed | `"Not annotating"` |
| added | `"Failed to validate backup"` |

(Search the diff file for these strings; line numbers are left out on purpose.)

The later iOS 27 diffs up to 24A437 (27.0) and the 27.0.1 / 27.2 betas that were checked do not revert this. The
binary `FinishRestoreFromBackup`, which performs the deletions at boot, is unchanged apart from a rebuild.

**Reading (strong):** on iOS 27, `RemoveItemsNotRestored = false` no longer means "overlay". Every domain that has
*system files to always remove on restore* is annotated anyway. In the iOS 16.4 `Domains.plist` (as published in the
community dump `Domains.md` by leminlimez), the key `RelativePathsOfSystemFilesToAlwaysRemoveOnRestore` exists for
exactly two domains:

- `CameraRollDomain` (`Media/Photos`, `PhotoData/…` caches and sync state),
- `HomeDomain` (`Library/Preferences/com.apple.migration.plist`).

One such path is enough to pull the whole domain into annotation. A domain that is annotated but **absent** from the
payload is then deleted path by path at the next boot.

Independent confirmation that this is an iOS 27 change: the README of the restore tool *Nugget* warns
"DO NOT USE THIS ON iOS 27!" because Apple "patched the partial restore method", and users of partial-restore tools
reported reset settings and photos on iOS 27 while apps and app data stayed.

## 3. What this looks like on a device

During development, a payload that contained **only the four Threema domains** was restored on iOS 27.0 (24A437) with
`RestoreSystemFiles = true` and `RemoveItemsNotRestored = false`. After the next boot:

| Area | Effect |
|---|---|
| `HomeDomain` | most files removed; system daemons recreated many of them with default content |
| Settings, language/region, notifications, app permissions (TCC), wallpapers, Shortcuts | back to defaults |
| SMS/iMessage database, call history, Safari bookmarks, calendar cache | recreated empty |
| Apple Watch pairing registry | removed |
| `CameraRollDomain` (photos stored on the iPhone) | removed (iCloud Photos downloads them again if enabled) |
| Setup Assistant | ran the full setup, because its own state file in `HomeDomain` was gone |
| All other app, app-group and system domains, Keychain | untouched |
| Threema domains | arrived as intended |

The deletions happened at the first boot after the restore, in the same second as the Threema data was committed.
The Setup Assistant was a consequence, not the cause.

**Confidence.** That *partial payload + `RestoreSystemFiles` on iOS 27 empties `HomeDomain` and `CameraRollDomain`*
is strong: the domain boundary of the damage is exact and matches the two domains named above. The detailed mechanism
is **plausible**: the annotation logic is known from an older iOS release (26.1), the domain rules from iOS 16.4, and
a few `HomeDomain` paths survived that the model predicts would be removed. The exact iOS 27 rule set is **open**; it
would need the iOS 27 `BackupAgent2` binary to be analysed.

## 4. Consequence: Chat Transfer for Threema never sends a partial payload

### 4.1 The restore set

Chat Transfer for Threema restores a **restore set** built from **one** fresh, encrypted full backup of the **same** iPhone, made
minutes earlier with the iPhone in airplane mode:

| Part of the set | Content |
|---|---|
| The four Threema domains | `AppDomain-ch.threema.iapp`, `AppDomainGroup-group.ch.threema` and the `AppDomainPlugin-ch.threema.iapp.*` extension domains, with the **imported** Threema database (`ThreemaData.sqlite`) and empty `-wal`/`-shm` files |
| `HomeDomain` | **complete**, bit-identical to the backup |
| `CameraRollDomain` | **complete**, bit-identical to the backup |
| `KeyboardDomain` | **complete**, bit-identical to the backup |
| Everything else (`KeychainDomain`, `MediaDomain`, all other app and system domains) | **not** in the set; stays live and untouched |

Why this works: the annotated domains are present and complete, so annotation can only remove what was created
**after** the backup. That is the reason for the user-visible rule "settings, notifications, photos on the iPhone,
SMS/iMessage, call history, Wallet passes and keyboard go back to the state of the backup", and the reason for airplane
mode and the 60-minute limit between backup and restore.

Why `KeyboardDomain`: a no-op restore set without it (see §7) reset the keyboard learning data, although the domain was
not part of the payload. Including it complete removes that loss. This addition is implemented and tested on fixtures
but **not yet proven on a device**.

Why the keychain stays out: the live keychain keeps all items, including items that are never in any backup
(`…ThisDeviceOnly` items bound to the Secure Enclave). Saved passwords and app sign-ins therefore stay as they are.
`MediaDomain` stays out and serves as a sentinel in the post-check.

The engine has no way to build anything else: there is no "trim" function, and the set is frozen read-only after it
has been verified.

### 4.2 Fixed restore options

Every restore is exactly one `Mobilebackup2Service.restore` call with these options (pymobiledevice3 notation
`--system --reboot --no-settings --no-copy --no-remove --skip-apps`):

| Option | Value | Why |
|---|---|---|
| `RestoreSystemFiles` | true | Without it, field reports for iOS 16–17 show that nothing is restored; the device most likely forces it from the device class anyway. |
| `RestoreShouldReboot` | true | Immediate commit. There is no window in which the device keeps running with a pending, uncommitted restore. |
| `RestorePreserveSettings` | false | Same as Finder; it is passed to the data migrator only and has no effect on files. |
| `RestoreDontCopyBackup` | true (`--no-copy`) | Proven path; the iPhone needs no second copy of the payload. |
| `RemoveItemsNotRestored` | false | App domains outside the set stay an overlay (untouched). The annotated system domains are complete in the set. |
| skip apps | yes | All apps are installed; no reinstall through `RestoreApplications.plist`. |

### 4.3 Why not a Finder restore of a modified full backup?

A Finder restore sends `RestoreSystemFiles = true` and relies on the device defaults for the rest
(`RemoveItemsNotRestored = true`, reboot true), so **every** domain is annotated and cleaned. That replaces the whole
keychain with the backup's keychain (items that are never backed up are lost, which affects banking and authenticator
apps), removes data that apps exclude from backups, needs Full Disk Access for the tool, and the Finder backup picker
makes it easy to choose the wrong backup. Chat Transfer for Threema therefore uses Finder only in the emergency path, with **Apple's own,
unmodified** backup (user guide, chapter 6).

## 5. Checks right before sending

The restore step refuses to send unless all of these hold (details: [SECURITY-MODEL.md](SECURITY-MODEL.md)):

- the iOS build is `verified` in the bundled allow-list ([COMPAT-POLICY.md](COMPAT-POLICY.md)),
- same device and same iOS build as the backup, in the same connection,
- the backup was made with airplane mode on and is at most 60 minutes old,
- the photo library (`/DCIM`) has not changed since the backup,
- the set is intact: layout, marker, source, plists, structure (only the domains above, identical to the source, empty
  `-wal`/`-shm`), `verify --restoreset`, and an unchanged sha256 directly before sending,
- Find My is off (the device itself refuses with `MBError 211` and changes nothing if it is on),
- free space on the iPhone ≥ 1.5 × payload, battery ≥ 50 % or charging, Mac on power.

## 6. After the restore

**What the user always sees.** For every USB restore the device marks itself as *restored from a computer backup*
(`RestoredFromiTunesBackup` in the Setup Assistant state), independent of the options. After the reboot the iPhone
shows "Swipe up to upgrade", "Restore completed", the Apple Account sign-in and the Apple Pay prompt. Threema's
`threema-fs.db` is excluded from backups by Threema, so some chats show "session reset" notes; no history is lost.

**The gate (post-check, version 2).** A second backup is taken after the reboot and compared with the first:

| View | Checks |
|---|---|
| P.2 Threema | the imported data is present and the database opens after Threema's first launch (`verify_import --post-launch`) |
| P.3 payload view | the identity domains `HomeDomain`, `CameraRollDomain`, `KeyboardDomain` equal the backup within normal churn; every other domain is judged against thresholds and sentinels |
| P.4 strict view | the same comparison without any payload knowledge, as an independent second opinion |

Known harmless effects on iOS 27.0 are reported as notes, never as warnings, and only **with counter-evidence**:

| Note | Harmless only if |
|---|---|
| `N_POSTER_CACHE_REGENERATED` | PosterBoard lost files, but for every file extension the count after ≥ before and the total did not shrink; never with a wiped or collapsed area |
| `N_SHORTCUTS_CATALOGUE_REGENERATED` | only the ToolKit catalogue `Tools-*.sqlite` was replaced; `Shortcuts.sqlite` is present, not collapsed, its rows did not decrease |
| `N_CALENDAR_SYNC_TABLES` | total rows of `Calendar.sqlitedb` dropped by 25 % or more, but the tables `Store`, `Calendar` and `CalendarItem` each did not shrink |
| `N_APPLE_ACCOUNT_RERUN` | Setup Assistant state: setup done before and after; only the last-exit, "presented" bookkeeping keys and the region suffix of the locale changed; **and** the user's answer rules out the full Setup Assistant ("only Restore Completed, Apple Account / Apple Pay" or "no questions at all"; every USB restore re-runs Setup Assistant for the Apple account) |

Each iOS build lists the notes it may produce (`expected_notes` in `compat/ios.json`). A note class that is not listed
for the user's build turns into an alarm. There are no waivers in the product.

Verdicts, first match wins: `setup_full` (red) → `data_keychain` (red) → `data` (red) → `restore_state` (red) →
`threema_only` (red, Threema can be reset: the Threema check failed or the user reported a problem in Threema while
every system check is green) → `needs_answer` → `ok_with_notes` (green) → `ok` (green). There is no yellow.
`restore_state` is checked before `threema_only` because a reset uses the same mechanism as the restore that left the
Setup Assistant in that state.

## 7. Evidence status

| Claim | Status |
|---|---|
| A partial payload with `RestoreSystemFiles` on iOS 27 empties `HomeDomain` and `CameraRollDomain` | strong (one device event, exact domain boundary, public string diff, community reports) |
| The annotation mechanism as described in §2 | plausible |
| A no-op restore set (Threema unchanged, complete `HomeDomain` + `CameraRollDomain`) with the fixed options is accepted and committed on iOS 27.0 (24A437); `HomeDomain` and `CameraRollDomain` afterwards equal the payload | **proven once** (maintainer device; counts only were recorded) |
| The gate flags that run with five alarms: four are the harmless classes of §6, one is a real loss (`KeyboardDomain` collapsed) | proven once; the four classes now need counter-evidence, `KeyboardDomain` is now in the set |
| A restore set **with** `KeyboardDomain` | not yet proven on a device |
| A restore set **with an imported** Threema database (the real transfer) | not yet proven on a device |
| The app's full flow on real hardware up to the send (Android import, iPhone backup, `prepare`, all 15 restore guards); with Find My on, the device refuses before staging (`MBError 211`), the app reports `E_GUARD_FINDMY` and nothing changes | proven once (app run 2026-10-03, app 0.3.1-dev; structural counts only) |
| Large local photo libraries (tens of GB) | not tested; version 1 is limited to 20 GB |

Build 24A437 is `verified` under the interim rule D6 with `comment_code: acceptance_partial`
([COMPAT-POLICY.md](COMPAT-POLICY.md) §1.1): the no-op canary and the app run above are recorded, a restore with
`KeyboardDomain` and an imported Threema database applied by the app itself is still open. Until it passes, releases
are betas.

## 8. Open questions

- The exact iOS 27 set of "domains with system files to always remove on restore".
- Whether the live Find My state can be read reliably on iOS 27 before sending. In the app run of 2026-10-03 the
  pre-send guards did not stop although Find My was on; the device refusal `MBError 211` stopped it and changed
  nothing. The refusal stays the hard fallback.
- Whether the Setup Assistant can always finish without internet ("Later" path), so that airplane mode can stay on until
  the gate is green.

## 9. Sources

- ipsw-diffs, `BackupAgent2.md` string diff iOS 26.5 (23F77) → 27.0 beta (24A5355q) with the four strings of §2, and the
  later iOS 27 diffs: <https://github.com/blacktop/ipsw-diffs>
- `Domains.plist` of iOS 16.4 as published by leminlimez (`Domains.md`), keys
  `RelativePathsOfSystemFilesToAlwaysRemoveOnRestore` and `RelativePathsNotToRemoveIfNotRestored`.
- Nugget README (leminlimez) warning about iOS 27, and Nugget issue reports from June 2026.
- pymobiledevice3 `services/mobilebackup2.py` (option mapping) and its restore CLI defaults.
- libimobiledevice issues #1053, #1548 (nothing restored without system files) and #1664 (annotation of every domain
  with `RemoveItemsNotRestored = true`).
- Apple Platform Security guide, sections on backup keybags and `ThisDeviceOnly` keychain items.
- Publicly known class and method names of `BackupAgent2` / `FinishRestoreFromBackup` in iOS 26.1
  (`MBRestoreDirectoryAnnotator`, `MBDriveRestoreEngine`, `merge_restore_path_to_root`).
