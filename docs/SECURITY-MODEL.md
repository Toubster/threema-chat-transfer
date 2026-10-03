# Security model

Short version for users: [../SECURITY.md](../SECURITY.md). Privacy details: [PRIVACY.md](PRIVACY.md). Why the
restore is built as it is: [RESTORE-MECHANISM.md](RESTORE-MECHANISM.md).

## 1. What we protect

| Asset | Why it matters |
|---|---|
| Data on the iPhone **outside** Threema | the tool restores to a phone people depend on; any loss is the worst outcome |
| The Threema history | private conversations, readable on the Mac while the move runs |
| The iPhone backup password and the decrypted backup content | an encrypted iPhone backup plus its password opens the backup keychain, Wi-Fi passwords, health data |
| The Android backup password | opens the complete chat history |
| The user's trust in the release | an unsigned download from GitHub asks for powerful access (USB device restore, backup password) |

## 2. What can go wrong (threats and failure modes)

| # | Threat or failure | Examples |
|---|---|---|
| T1 | Platform change | iOS changes restore semantics (it did in iOS 27, see RESTORE-MECHANISM.md); Threema changes its data model or backup format |
| T2 | User mistakes | wrong Android file, iPhone used during the window, Find My left on, wrong iPhone connected, "Set up as new" in Setup Assistant |
| T3 | Bugs in the tool | importer writes wrong data, restore set incomplete, gate misses damage |
| T4 | Interrupted operation | cable pulled, Mac sleeps, app crash, power loss |
| T5 | Leaks from the Mac | readable chat copies, logs, diagnostic reports, screenshots attached to issues |
| T6 | Supply chain | a tampered download, a compromised maintainer account or CI, a malicious dependency |
| T7 | Weakening by others | forks or forum guides that add "skip this check" switches |
| T8 | Network exposure | any socket opened by the app or a dependency |

Out of scope: an attacker who already runs code as the user on the Mac (they can read the session folder and the
user's other files), a compromised iPhone, and attacks on Threema's or Apple's services.

## 3. Principles

1. **One exposure.** Each user gets exactly one restore. There is no "test restore" on the iPhone of a user.
2. **Guards in the engine, no overrides.** Every check runs in `tmcore`. The app only displays results. There is no
   option to skip a guard: not in the app, not in the shipped command line, not through an environment variable.
   Experts have the source code. (T2, T7)
3. **Fail closed on anything unknown:** iOS build, Threema model, Android format, Threema variant, managed device,
   unreadable Threema ID. (T1)
4. **Offline**, enforced twice. (T8)
5. **No personal data in outputs.** (T5)
6. **Everything without the device first.** Mac check, compatibility check (read-only) and the Android part run before
   the iPhone goes offline, so the critical window stays short. (T2, T4)
7. **Minimal intervention.** On the iPhone the tool changes only two things: the backup encryption (if it was off, with
   consent) and the restore. Airplane mode, Find My and Bluetooth are switched by the user, following the guide.

## 4. Trust boundaries

```
 user ──> SwiftUI app ──(argv: command; stdin: 1 JSON line of secrets; stdout: JSON events)──> tmcore (Python)
                                                                                                │
                         threema-import (Swift, subprocess, no network) <──────────────────────┤
                                                                                                │
                                              usbmuxd (/var/run/usbmuxd, Unix socket, USB only) ──> iPhone
```

| Boundary | Rule |
|---|---|
| App → engine | argv carries the command and options only (never secrets; the only user-chosen paths are the Android backup files). Secrets travel as **one** JSON line on stdin, then EOF. The environment is set completely by the app; nothing is inherited. |
| Engine → app | stdout is JSON lines validated against `core/schema/events.v1.json`: numbers, enums, codes, salted hashes, public version numbers. No free text, no paths, no names. Exactly one `result` event, always last. |
| Engine → importer | subprocess with fixed arguments inside the session folder; the importer refuses any store whose model hash it does not know. |
| Engine → device | only through Apple's `usbmuxd` over USB; no Wi-Fi sync, no "local network" permission. |
| Engine → bundle | the bundle is read-only at runtime (`-B`, precompiled bytecode); writing into it would break the ad-hoc signature, and the package test checks that the bundle is unchanged after a demo run. |

## 5. Controls

### 5.1 Before the restore (T1, T2, T3)

| Guard | Checks | Code |
|---|---|---|
| `host` | macOS ≥ 14, arm64, APFS, enough space, Mac on power | `E_HOST_*` |
| `compat_ios` | device build is `verified` in `compat/ios.json` | `E_IOS_UNKNOWN`, `E_IOS_BLOCKED` |
| `managed` | not supervised, no MDM profile | `E_DEV_MANAGED` |
| `android_format` | format version verified, file complete, password correct | `E_ANDROID_*` |
| `threema_variant` | bundle `ch.threema.iapp` installed | `E_THREEMA_MISSING`, `E_THREEMA_VARIANT` |
| `threema_model` | store model hash is in `compat/threema-ios.json`; the engine never migrates | `E_THREEMA_MODEL_UNKNOWN` |
| `threema_setup` | setup complete, no setup marker, "keep messages" unlimited, integrity check ok | `E_THREEMA_*` |
| `identity` | Threema ID of the Android backup = Threema ID in the iPhone backup (HMAC comparison in memory) | `E_THREEMA_ID_MISMATCH` |
| `password` | the backup keybag opens with the password | `E_BACKUP_PASSWORD` |
| `airplane` | airplane mode was on in the backup | `E_GUARD_AIRPLANE` |
| `photos_limit` | photos stored on the iPhone ≤ 20 GB (v1) | `E_GUARD_PHOTOS_LIMIT` |
| `freshness` | backup ≤ 60 minutes old at start and directly before sending | `E_GUARD_FRESHNESS` |
| `device` | same UDID and build as in the backup, in the same connection | `E_DEV_OTHER`, `E_IOS_CHANGED` |
| `iphone_space` | free space ≥ 1.5 × payload | `E_GUARD_IPHONE_SPACE` |
| `battery` | iPhone ≥ 50 % or charging; Mac on power | `E_DEV_BATTERY`, `E_HOST_POWER` |
| `findmy` | Find My off (device refusal `MBError 211` = nothing changed) | `E_GUARD_FINDMY` |
| `dcim_unchanged` | the photo library on the device equals the backup (count and sizes, in memory) | `E_GUARD_DCIM_CHANGED` |
| `set_integrity` | layout, marker, source, plists, structure, `verify --restoreset`, unchanged sha256 directly before sending | `E_GUARD_SET_INTEGRITY` |
| `never_partial` | the engine has no "trim" and cannot build a partial payload | – |
| `confirmation` | the user ticked the boxes and clicked "Transfer now" | – |

Things that cannot be checked technically are mandatory tick boxes in the app: Apple Watch and Bluetooth off, Threema
closed, an Apple backup exists, Wi-Fi off in airplane mode.

### 5.2 During the restore (T4)

- The engine announces `critical: on` from the first byte sent to the device until its `result`. The app then locks
  "Cancel" and `⌘Q` and holds a power assertion; the engine ignores SIGTERM until sending ends.
- If the engine dies while `critical` was on, the app treats it as "restore possibly sent" and continues with the check
  (S16), never with "start over". Resume after a crash follows the stored session state.
- A connection loss before the device restarts means nothing was applied (the commit happens at boot).

### 5.3 After the restore (T3)

The post-check compares a second backup with the first one (gate v2, see RESTORE-MECHANISM.md §6). Known harmless
effects need counter-evidence and must be listed for the device's iOS build; anything else is red. Red always means
"stop and keep the iPhone offline", never "delete". The rollback ladder:

| Level | Trigger | Path |
|---|---|---|
| R0 | stop before sending | nothing to do |
| R1 | only Threema failed | `rollback-threema`: a no-op set from the same backup, ≤ 6 h, only after a final restore |
| R2 / R2k | other data or saved sign-ins changed | stay offline; restore the user's Apple backup (R3) or keep as is |
| R3 | from R2, R2k, R4 | Apple-native restore of the iCloud or archived Finder backup made in step S09 |
| R4 | Setup Assistant wants a full setup | do not erase, do not set up as new; continue with R3 |

### 5.4 Secrets (T5)

| Secret | Source | Kept |
|---|---|---|
| Android backup password | typed | memory only; discarded after the Android step |
| iPhone backup password (encryption was off) | generated (six groups of four characters, no look-alike characters) or chosen (≥ 10 characters) | login keychain item "{App} – backup password" by default, until the user deletes it |
| iPhone backup password (encryption was on) | typed | memory only; checked against the keybag right after the first backup (up to five attempts, no new backup needed) |

Never in argv, environment, files, logs, reports or diagnostic reports. The engine passes passwords to its libraries
in-process; there is no password file, not even a temporary one. A canary password in `fixtures/canaries.json` is
searched for in every output of the end-to-end tests.

### 5.5 Offline enforcement (T8)

- In-process socket guard in `tmcore`: only `AF_UNIX` is allowed; any other socket aborts with `E_NETWORK_BLOCKED`.
  With `--fake-device`, even `AF_UNIX` is refused, so a test run can never reach a real device.
- The engine additionally runs under `sandbox-exec` with `(deny network*)` except `/var/run/usbmuxd`, as long as macOS
  ships `sandbox-exec`.
- No telemetry, no crash-reporter SDK, no update check. "Check for updates" opens the release page in the browser.
- CI runs the fixture end-to-end tests under a profile without network.

### 5.6 Supply chain (T6)

- Dependencies: a curated list installed with `pip install --require-hashes --only-binary :all: --no-deps`; the Python
  runtime (python-build-standalone) is pinned by URL and sha256. pymobiledevice3 is only updated together with a device
  run.
- CI: GitHub Actions pinned by commit SHA, `permissions: contents: read` by default, no self-hosted runner, no device
  ever reachable from CI.
- Releases: built by CI as **drafts only**, with `SHA256SUMS`, an SBOM (CycloneDX), a source bundle and a build
  provenance attestation. The maintainer checks the draft on clean macOS VMs and a device, signs `SHA256SUMS` locally
  with minisign (key offline, never in GitHub) and publishes by hand.
- The maintainer account uses a passkey or hardware key; `main` is branch-protected.
- Ad-hoc signing (no paid Apple developer account): macOS cannot confirm the developer, so users see the "Open Anyway"
  flow once, and macOS asks once per app update before the app may read its keychain item. The checksum, the minisign
  signature and the attestation are the integrity anchors. The user guide never tells users to run `xattr` or
  `curl | bash`; a "damaged" message in our own tests is a release blocker.

### 5.7 Repository hygiene (T5, T7)

- `scripts/scrub_check.py` blocks real identifiers in the repository: UDIDs, ECIDs, Threema-ID-shaped tokens (only
  `ZZ…` fixtures allowed), e-mail addresses, home paths, device names, phone numbers, private IP addresses, secrets,
  unreviewed binaries and images without a demo or review marker. Layer 2 compares every token against a private,
  HMAC-hashed deny list of real values; it runs in the maintainer's pre-push hook and, when the repository secrets
  exist, in CI.
- Pull requests that add a way to skip or weaken a guard are not merged (CONTRIBUTING.md).

## 6. Residual risks

| Risk | Mitigation |
|---|---|
| iOS changes restore behaviour again | build allow-list, `upstream-watch`, canary runs per build, gate as last line |
| The real transfer (set with import) is not yet proven on a device | no build is `verified` until it is; the app refuses restores until then |
| Harmless note classes hide real damage | counter-evidence required, `expected_notes` per build, negative tests |
| False alarms confuse users | red means "stop, stay offline", never "delete"; clear guides; beta calibration |
| The user uses the iPhone during the window | airplane-mode guard, photo-library guard, countdown, texts |
| Large photo libraries | 20 GB limit in v1 |
| Gatekeeper friction | illustrated guide; measured in the beta |
| Readable chat copies on a Mac without FileVault | FileVault warning, cleanup offer at the end, session folder `0700` |
