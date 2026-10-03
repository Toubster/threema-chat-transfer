# Compatibility policy

Chat Transfer for Threema refuses everything it does not know. This document says what "known" means for iOS builds, Threema data
models and Android backup formats, where the lists live and how an entry gets in. The lists ship **inside the app**:
there is no online index, and a new entry only reaches users with a new release.

Machine-readable sources: `compat/ios.json`, `compat/threema-ios.json`, `compat/android.json`, evidence in
`compat/records/ios/<build>.json`; schema `core/schema/compat.v1.json`. How to add an entry: [MAINTAINER.md](MAINTAINER.md).

## 1. iOS builds (`compat/ios.json`)

The restore behaviour of iOS can change between builds (it did in iOS 27, see
[RESTORE-MECHANISM.md](RESTORE-MECHANISM.md)). The decision is therefore per **build** (for example `24A437`), not per
version number.

| Status | Condition | App behaviour |
|---|---|---|
| `verified` | device evidence for this exact build (§1.1) | restore allowed |
| `static-checked` | a patch build of the same line whose ipsw-diff shows no change to `BackupAgent2`, `MobileBackup.framework` or `Domains.plist` | **version 1: treated like `unknown`.** The app says "checked in the program code, not yet on a device". Allowing this level is an open decision (re-evaluate after three patch builds in which the static check predicted the device result correctly). |
| `unknown` | everything else, including every beta and every build with a letter suffix | no restore. The Android part may run; the session waits for an app update. |
| `blocked` | withdrawn after a problem | like `unknown`, with its own text |

Each entry also lists `expected_notes`: the harmless post-restore note classes allowed **for this build**
(RESTORE-MECHANISM.md §6). A note class that is not listed for the user's build becomes an alarm (red verdict).

### 1.1 Evidence for `verified`

Target rule (DESIGN §6.2), on a dedicated test iPhone with seeded test data:

- C0: backup → reboot → backup; the gate is green (baseline churn of the build);
- **two** C3 runs: a full wizard run with a fixture import from the release candidate; verdict `ok` or
  `ok_with_notes`, no waiver, no manual analysis; Threema shows the history; all seeds intact;
- for a new major iOS version additionally: C1a (a trimmed payload through the maintainer's private tool chain must be
  red), R1 (Threema rollback) and R3 (Apple-native restore from iCloud and from an archived Finder backup).

**Current interim rule (lead decision D6).** There is no dedicated spare iPhone at the moment. Evidence for 24A437
comes from a no-op canary on the maintainer's own device, recorded as counts only. The acceptance run for P1 and P5 is
one full wizard run with the finished DMG on the maintainer's device, only after the maintainer's explicit go; the
import is idempotent, so it adds no new messages.

State of 24A437 (`verified`, `comment_code: acceptance_partial`), from `records/ios/24A437.json`:

| Run | Date | What it shows |
|---|---|---|
| `C0` no-op canary | 2026-10-01 | the restore-set mechanism (fixed options, complete `HomeDomain` + `CameraRollDomain`) is accepted and committed; gate green after classification |
| `app_run` (app 0.3.1-dev) | 2026-10-03 | the app's full flow on real hardware: Android import, iPhone backup, `prepare` and all 15 restore guards passed; the send reached the device, which refused it itself (`MBError 211`, Find My on) → `E_GUARD_FINDMY`; nothing on the device changed |
| acceptance run (restore executed by the app) | open | needs Find My off; only after the maintainer's explicit go |

`acceptance_partial` means: the mechanism and every step up to the device are proven on this build, a restore applied
by the app itself is not. Releases on this basis are marked **beta** (0.9.x), and the README says who should wait.

### 1.2 Records

Every device run writes `compat/records/ios/<build>.json` (schema `compat.v1`, record part) through
`devtools/make_compat_record.py`: build, iOS version, run types, verdicts, gate classes, counts of the gate (rows,
files, domains) and the observed note classes. **No identifiers**: no UDID, ECID, serial number, device name, Apple
Account, Threema ID, file names or message content. A record is reviewed in the pull request and must pass the scrub
check (both layers).

### 1.3 Release timing

- iOS betas get canary runs from June on, so that the release build can be `verified` on day one. A beta entry itself
  stays `unknown`.
- `upstream-watch` opens an issue for every new iOS build with a link to its ipsw-diff
  (`.github/workflows/upstream-watch.yml`).
- When the app is older than 90 days, the iPhone check screen adds "There may be a newer version." The link opens the
  browser; the app itself never asks the network.

## 2. Threema iOS data model (`compat/threema-ios.json`)

- **Hard check = model hash.** The engine reads `NSStoreModelVersionHashes` and `NSStoreModelVersionIdentifiers` from
  the store metadata in the iPhone backup. Only an exactly known model is written, with exactly the matching compiled
  model (`.momd`). The engine never migrates, neither up nor down. Unknown → `E_THREEMA_MODEL_UNKNOWN` before anything
  happens on the iPhone. This also covers variants such as an encrypted store: different hash, refused.
- **App version = soft check.** Newer Threema versions with an **identical** model hash are allowed (Threema publishes
  every few weeks). Versions listed in `blocked_app_versions` are refused. Runtime problems are caught by the
  post-launch check, and R1 resets Threema.
- **Variant.** Only `ch.threema.iapp` (`variants_allowed`). Threema Work and OnPrem are refused.
- **New model.** `upstream-watch` reports a new Threema iOS tag, compiles its model and compares the hash. The
  maintainer then checks the importer mapping, adds tests, runs the fixture end-to-end tests, the simulator gallery and
  a C3 run, and releases a new version with both models in the bundle. Between an App Store release and its source tag
  there can be days; during that time the app refuses. Airplane mode prevents app updates during the transfer window.

Current list: model `V56` (Threema iOS 7.4), status `verified` for the importer; the model files and their hashes are
described in `model/README.md`.

## 3. Android backup formats (`compat/android.json`)

- `verified_formats: [27]`. A newer format → `E_ANDROID_FORMAT_NEW` ("comes from a newer Threema version").
- Older formats that were not tested with real data → `E_ANDROID_FORMAT_UNVERIFIED` (refused in version 1). An older
  format is only added with a synthetic fixture **and** a real test backup.
- `upstream-watch` compares the backup version constant of each new Threema for Android release with
  `max_known_format`.

## 4. What this means for users

- "Your iOS version is not tested yet" is the expected answer for a few days after every iOS update. Do not install
  further updates; prepare the Android part; update Chat Transfer for Threema when a new version appears.
- Chat Transfer for Threema never transfers on a build it does not list as `verified`, and there is no switch to force it.
- When Threema officially supports moving chats from Android to iPhone, Chat Transfer for Threema will point to the official way
  instead. The built-in allow-list already prevents an abandoned version from touching new iOS builds.

## 5. Current state

| List | Entries |
|---|---|
| iOS | `24A437` (iOS 27.0): `verified` 2026-10-01 under interim rule D6 (evidence `records/ios/24A437.json`: no-op canary on the maintainer's device, without KeyboardDomain and without an imported store; app run 2026-10-03 up to the device's refusal, nothing changed), `acceptance_partial`, `min_app` 0.9.0-beta.1; expected notes: poster cache, Shortcuts catalogue, calendar sync tables, Apple Account rerun |
| Threema iOS models | `V56`, from Threema iOS 7.4 |
| Android formats | `27` |
