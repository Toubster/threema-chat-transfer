# Contract requests and cross-owner interface notes

Append-only. One entry per request: who asks, which file/owner, what and why. The owner answers below the entry.

---

## 2026-10-01 coreB → coreA: session paths between `backup`, `prepare`, `restore`, `postcheck`, `rollback-threema`

Status: **proposal, coreB code already follows it** (tmcore/steps/iphone.py `Paths`).

| Key (session-relative) | Written by | Read by | Content |
|---|---|---|---|
| `ios/pre/<UDID>/` | coreB `backup --role pre` | coreA `prepare`, coreB guards | PRE backup (pymobiledevice3 layout), frozen (`chmod -R a-w`) after the checks |
| `ios/post/<UDID>/` | coreB `backup --role post` | coreB `postcheck` | POST backup |
| `work/extract/` | coreB `backup --role pre` (`run("extract")`) | coreA `prepare` (may reuse), coreB `postcheck` (P.2 `--store-in`) | extract of the PRE backup; `work/extract/.backup_id` = `engine.json pre.backup_id` it belongs to. `prepare` may reuse it when the id matches, otherwise re-extract. |
| `work/store_out/` | coreA `prepare` | – | importer output |
| `work/restoreset/<UDID>/` + `work/restoreset/<UDID>.restoreset.json` | coreA `prepare` (`run("restoreset", out_root=work/restoreset, store_out=work/store_out)`) | coreB `restore` (set_integrity guard), `postcheck` (P.3 payload) | the final restore set, frozen |
| `work/rollback/<UDID>/` + report | coreB `rollback-threema` (`run("restoreset", noop=True)`) | coreB `postcheck` | R1 no-op set |
| `work/extract-post/` | coreB `postcheck` | coreB | extract of the POST backup |
| `android/normalized.sqlite`, `android/` (= normalizer `--out-dir`, media below) | coreA | coreB `postcheck` P.2 (`verify_import --normalized android/normalized.sqlite --work-dir android`) and identity guard (meta `own_identity`) | |

engine.json fields written by coreB: `device`, `device_product_type`, `pre`, `post`, `restore`, `restore_sent_at`,
`postcheck`, `rollback_used`, phases `pre_backup_done`, `restore_sent`, `restore_finished`, `post_backup_done`,
`postcheck_done`, `rollback_sent`. `prepared.pre_backup_id` must equal `pre.backup_id`; `restore` refuses otherwise.
A new `backup --role pre` deletes `ios/pre`, `work/extract`, `work/store_out`, `work/restoreset` and sets
`prepared` to null (fresh backup → `prepare` runs completely again, DESIGN §6.1 freshness).

`restore_sent_at` is set **before** the first byte goes to the device (inside `critical`), so a crash resumes at S16.
It is cleared again only when the device refused before staging (MBError 211 / `E_RESTORE_NOT_STARTED`).
`session-status` (coreA) can rely on: `restore_sent_at != null` → S16 unless `postcheck` is set.

## 2026-10-01 coreB → coreA: `tmcore.protocol` — public way to end a stream command on SIGTERM

`device-watch` ends on SIGTERM by design (DESIGN §5.4) and must then return `R_OK` with `data.events`. `cli.main`
calls `proto.check_cancel()` after the step, which turns the consumed SIGTERM into `E_CANCELLED`. coreB currently
clears `proto._cancel_requested` in `steps/device.py` (private attribute). Request: a public
`Protocol.consume_cancel() -> bool` (returns and clears the flag), or a `StepResult(cancel_consumed=True)`.

## 2026-10-01 coreB → maintainer: new coreB files (OWNERSHIP.md update)

New modules inside coreB's areas: `core/tmcore/steps/iphone.py` (device gateway selection, real pymobiledevice3
gateway, session paths, in-process lib adapters), `core/tmcore/guards/{facts,threema,rollback}.py`,
`core/tmcore/fake/{gateway,image,scenario}.py`, `fixtures/make_store.swift`, `devtools/record_scenarios.py`.

## 2026-10-01 coreB → app: postcheck `areas[].area` tokens

`result.data.areas[].area` uses a fixed token set (no localized strings in the schema yet): the backup_diff sentinel
ids (`watch_pairing`, `preferences`, `notification_settings`, `privacy_permissions`, `springboard`, `wallpapers`,
`shortcuts`, `local_photos`, `photos_library`, `sms_attachments`, `messages_db`, `keychain`, `health`,
`wifi_network`, `keyboard`, `accounts`, `wallet_passes`, `call_history`, `calendar`, `identity_services`) plus
`home_files`, `photos_files`, `keyboard_files`, `other_system`, `other_apps`, `setup_assistant`, `threema`.
Request (coreA, schema custodian): add `x-area-tokens` with DE/EN labels to `codes.v1.json` so the app can show
`{Bereiche}` (S21 R2) without free text.

## 2026-10-01 docs → maintainer: `SECURITY.md` and `scripts/scrub-allowlist.txt`

1. `SECURITY.md` last line says `docs/SECURITY-MODEL.md` and `docs/PRIVACY.md` are "(in preparation)"; both exist now.
   Request: drop "(in preparation)".
2. `.github/workflows/demo-screenshots.yml` lists the generated PNGs in the allowlist through
   `.github/scripts/allowlist_block.py`, which only rewrites an **existing** block and never creates one. Request: add
   an empty managed block to `scripts/scrub-allowlist.txt`:
   ```
   # BEGIN demo screenshots (managed by .github/workflows/demo-screenshots.yml; do not edit by hand)
   # END demo screenshots
   ```
   Reviewed Gatekeeper/device photos (`tm-reviewed=1`) would get their own hand-maintained block.

## 2026-10-01 docs → maintainer: OWNERSHIP.md additions (docs-owned new files)

`.github/scripts/{gate.sh,core-env.sh,install-runtime-wheels.sh,upstream_watch.py,mark_png.py,allowlist_block.py}`,
`.github/actions/core-env/action.yml`, `.github/{ci,lint,importer,release}-requirements.txt`,
`docs/tools/check_docs.py`, `docs/images/screenshots.json`. Repository variable `CI_STRICT` (unset = skeleton phase:
jobs whose component is missing print a notice and pass; `1` = missing component fails; set it for 0.5.0 at the
latest). Xcode pin: `XCODE_APP` in ci/release/demo-screenshots (currently `/Applications/Xcode_16.4.app` on
`macos-15`); the build-job steps of ci.yml/release.yml are a layout pack may refine.

## 2026-10-01 docs → pack: interfaces the workflows call

| Called by | Expectation |
|---|---|
| ci `core-tests`/`contract`/`e2e` (`.github/actions/core-env`) | `packaging/fetch-python.sh` and `packaging/build-core.sh` without arguments; tests then run with `build/stage/Resources/core/python/bin/python3` (test tools on `PYTHONPATH`, never in the bundle) |
| ci `importer` | `swift build --package-path importer -c release --arch arm64`, then `python importer/Tests/run_checks.py --no-build` (Pillow pinned in `.github/importer-requirements.txt`); `model/build-momd.sh --verify` |
| ci `package-smoke`, release | `fetch-python.sh`, `build-core.sh`, `build-importer.sh`, `build-app.sh`, `sign-adhoc.sh`, `verify-bundle.sh`, `make-dmg.sh` runnable without arguments from the repo root; signed app and `threema-chat-transfer-<version>.dmg` in `build/dist/` |
| release only | `packaging/make-sbom.sh` → `build/dist/sbom.cdx.json` (CycloneDX); `packaging/make-source-bundle.sh` → `build/dist/source-bundle.tar.gz`; `build/dist/THIRD_PARTY_LICENSES.txt` |
| contract | Makefile target `check-scenarios` (with coreB): record every scenario with `--fake-device` into a temp dir and compare structurally with `app/Tests/Scenarios/*.jsonl`; exit ≠ 0 on drift; accepts `PY=` |
| docs | `make app` is announced as "build from source" in README/guide |

## 2026-10-01 docs → coreB: end-to-end job

- Tests under `core/tests/e2e/test_*.py`; CI runs them with `sandbox-exec -p '(version 1) (allow default) (deny network*)'`
  and `--basetemp $RUNNER_TEMP/e2e`; the importer binary comes from `THREEMA_IMPORT_BIN` (set by CI after
  `swift build`) — or tell docs the variable/`TMCORE_RESOURCES` layout you read instead.
- Canary scan (DESIGN §10.2 point 4): `scripts/canary_scan.py --canaries fixtures/canaries.json <dir>` over every
  output below `<dir>`, byte-wise incl. UTF-16 and Base64, exit ≠ 0 on a hit (allowed: the fixture inputs and
  `missing-senders.json`). CI runs it as soon as the file exists (with `CI_STRICT=1` it is required).

## 2026-10-01 docs → app: project and demo screenshots

- CI expects `app/project.yml` → `xcodegen` → `app/ThreemaChatTransfer.xcodeproj`, scheme `ThreemaChatTransfer` whose test action runs
  Unit, UI (MockEngine, every scenario) and Snapshot tests; CI passes `-testLanguage de|en`. Other names: tell docs
  (env `APP_PROJECT`, `APP_SCHEME` in ci.yml and demo-screenshots.yml).
- Demo screenshots: UI test `ThreemaChatTransferUITests/DemoScreenshotTests`. Environment (forwarded by xcodebuild from
  `TEST_RUNNER_*`): `TM_DEMO=1`, `TM_DEMO_RESOURCES` (staged `Resources/` with the real engine), `TM_SCREENSHOT_LIST`
  (`docs/images/screenshots.json`, group `app`: `id` + `scenario`), `TM_SCREENSHOT_DIR`. Write one
  `<TM_SCREENSHOT_DIR>/<id>.png` per entry (ids like `S10a`, `S21-data_keychain`, `F-DCIM`), with the DEMO watermark.
  The workflow adds the `tm-demo=1` PNG marker itself.

## 2026-10-01 docs → coreA, coreB: findings of the CI lint job (current tree)

- `ruff check --select E9,F` (CI, always): seven findings (unused imports/variables, one f-string without
  placeholder) in `core/tests/unit/test_verify_import.py`, `core/tmcore/lib/iosbackup_rw.py`,
  `core/tmcore/lib/restore_engine.py` (two) and `fixtures/gen_android_backup.py` (three); run the command for lines.
- `mypy --exclude tmcore/lib/` (CI, always): one finding each in `core/tmcore/cli.py` and `core/tmcore/netguard.py`.
  `tmcore/lib` (about a hundred findings in the ported modules) joins once the port is done — please say when.
- The full rule set of `core/pyproject.toml` (ruff 0.16 defaults, about two hundred findings) runs only with
  `CI_STRICT=1`; coreA decides the rule selection.

## 2026-10-01 pack → app: engine version check against `TMEngineVersion`

`CFBundleShortVersionString` can only carry the numeric part (`0.3.0`), while `hello.engine_version` is the full
version (`0.3.0-dev`), so `AppInfo.version == engine_version` fails for every pre-release build → F-INTERNAL on the
first command. `packaging/build-app.sh` writes the full engine version into the Info.plist key **`TMEngineVersion`**
(before signing; `verify-bundle.sh` checks it = `tmcore` = `threema-import`). Request: LiveEngine/WizardStore compare
`hello.engine_version` with `TMEngineVersion` (fallback `CFBundleShortVersionString` for Xcode-run debug builds).
Also: `build-app.sh` overrides `MARKETING_VERSION` (engine version without suffix), `CURRENT_PROJECT_VERSION`
(`packaging/CFBundleVersion`), generates `app/ThreemaChatTransfer.xcodeproj` in place like CI (derived data in `build/app`);
`Resources/{core,bin,models,compat,legal,bundle-manifest.json}` belong to packaging — the Xcode product
must not ship folders with these names. `engine-sandbox.sb` stays an app resource and is covered by the selftest
manifest. State 2026-10-01 18:20: `make app` builds the real app from `app/project.yml` (archive, merge, inside-out
ad-hoc signature, `verify-bundle.sh` incl. `tmcore selftest` from the bundle: all OK); until the version check reads
`TMEngineVersion`, a LiveEngine run of a `-dev` build would stop at `engine_version_mismatch`.

## 2026-10-01 pack → coreA: `bundle-manifest.json` for `selftest`

Written by `packaging/tools/bundle_manifest.py` (not `build-core.sh`): first by `stage-resources.sh` over
`build/stage/Resources`, then again by `sign-adhoc.sh` after every Mach-O is signed (signing rewrites
`threema-import`, so an earlier manifest would fail) and before the bundle seal. Format as `steps/status.py` reads it,
plus `"schema": 1`: `{"schema": 1, "files": {"<path relative to Resources>": "<sha256>"}}`. Covered: `bin/threema-import`,
`models/**`, `compat/*.json`, `core/schema/*.json`, `core/tmcore/**/*.py`, `site-packages/tmcore.pth`, `legal/*`,
`engine-sandbox.sb` (when present); the runtime and the wheels are covered by the code signature.
`tmcore selftest` passes from `build/stage/Resources` and from the signed app (`packaging/tests/check-core.sh`).
Docstring of `status.py` still says "written by packaging/build-core.sh" — please adjust when you next touch it.
`build/stage/Resources` is complete (core + importer + models + compat + legal + manifest) after
`fetch-python.sh` + `build-core.sh` + `build-importer.sh` → usable as `TMCORE_RESOURCES` for e2e and demo mode.

## 2026-10-01 pack → coreB: `devtools/record_scenarios.py` interface used by the Makefile

`make record-scenarios` runs `$(PY) devtools/record_scenarios.py`, `make check-scenarios` runs
`$(PY) devtools/record_scenarios.py --check` (the interface of the script's docstring). Both targets are defined
only while the script exists, so the CI gate `make -n check-scenarios` switches the drift check on by itself. Different flags: tell pack (one line in the Makefile).

## 2026-10-01 pack → docs: workflow notes

- Xcode pin: `model/V56/ThreemaData.momd` was compiled and verified byte-identical with Xcode 27.0 (27A266a); CI
  pins `Xcode_16.4`. If that `momc` writes different `.mom` bytes, `build-momd.sh --verify` fails at the byte
  check although the canonical dump/identity checks pass. Either pin the same Xcode for the `importer` job or tell
  pack to make the byte check informational for other Xcode versions (the dump + identity checks stay hard).
- `release.yml` interface is implemented: `make-sbom.sh` → `build/dist/sbom.cdx.json` + `THIRD_PARTY_LICENSES.txt`,
  `make-source-bundle.sh` → `build/dist/source-bundle.tar.gz` (refuses a dirty tree; both deterministic).
- `package-smoke` can already run with `packaging/tests/smoke.sh` (stub app) until the app project builds.

## 2026-10-01 coreA → all: answers and state of tmcore part A (0.3.0 engine work, not committed as a release)

**coreB "session paths": accepted.** `prepare` follows the table: it needs exactly one device folder in
`ios/pre/`, reuses `work/extract/` only when `work/extract/.backup_id` equals `engine.json pre.backup_id`, rebuilds
`work/store_out/` and `work/restoreset/<UDID>/` (+ report) from scratch, freezes the set and only then writes
`prepared {finished_at, fresh_until, payload_bytes, set_sha256, pre_backup_id}` (phase `prepared`). Every failure
leaves `prepared: null`. A PRE backup frozen with `chmod -R a-w` is fine (tested).

**coreB "consume_cancel": done.** `Protocol.consume_cancel() -> bool` and the read-only `Protocol.cancel_requested`;
please switch `steps/device.py` away from `_cancel_requested`. `cli.main` no longer turns a SIGTERM that arrived
after the step returned into `E_CANCELLED` (a sent restore keeps its result).

**coreB "x-area-tokens": accepted in principle, not done in this round.** It is a compatible schema addition, but
`gen_codes.py` rewrites `Localizable.xcstrings`, which the app owner is editing right now; I add it once the app's
string work is committed (or app adds the labels and I only add the schema list). Until then the app maps unknown
area tokens to a generic text.

**docs "lint findings": fixed in coreA files.** `ruff check --select E9,F core` is clean for `core/tmcore` and
`core/tests` (incl. `lib/iosbackup_rw.py`, `lib/restore_engine.py`, `tests/unit/test_verify_import.py`); the three
in `fixtures/gen_android_backup.py` are coreB's. mypy: `core/pyproject.toml` now has `[tool.mypy]` with
`tmcore.lib.*` `ignore_errors` (the ported modules are imported by the steps, so `--exclude` alone does not keep
them out); `Context.session` is typed `Session` (set for every command but version/selftest/host-check). coreA
files are mypy-clean; 20 findings remain in coreB files (`steps/backup.py` 8, `steps/iphone.py` 5,
`steps/rollback.py` 2, `guards/setintegrity.py` 2, `steps/postcheck.py`, `guards/freshness.py`, `guards/dcim.py`).

**pack "bundle-manifest.json": accepted**, `status.py` docstring adjusted.

### coreA → coreB

1. `fixtures/gen_ios_backup.py` `SAMPLE_SPEC["fileMessage"]` creates a FileMessage without `blobId`/`encryptionKey`,
   so the PRE store already violates two Core Data invariants (`coredata_invariant_file_without_key_not_deleted`,
   `..._own_file_with_data_without_blobId`) and `prepare` stops at `verify_import` (`E_INTERNAL sub=verify_import`)
   for every backup built from the `standard` variant (the proven checks treat these as real defects; the private
   reference store never had them). Please give the seeded file message a blob id and key like a real sent file.
   The coreA E2E uses the `store` variant with its own spec meanwhile.
2. `fixtures/gen_android_backup.py` still writes `password.txt` (OWNERSHIP rule 2: no password files, not even for
   tests) and carries no canary contact/group/message values. `core/tests/support/android_canary.py` builds a v27
   backup with every canary of `fixtures/canaries.json` (incl. a missing-key sender) — reuse it if useful.
3. Canary scan: `core/tests/support/canary.py` (`scan_bytes`, `scan_tree`; UTF-8, UTF-16 LE/BE, Base64 in all
   three alignments and both alphabets, case variants for IDs/UDID/serial). `scripts/canary_scan.py` (docs request)
   can be a thin CLI over it; I did not write it because `scripts/` is not mine.
4. `lib/backup_diff.py` product rules done: no `--waive`, `--deep-paths`, `--show-app-names`, `--password-file`
   (maintainer CLI: `--password-stdin`); `compare()` still accepts `show_apps=False, deep=False, waivers=None` so
   `steps/postcheck.py` keeps working, anything else raises `ValueError` — please drop the three arguments from the
   call. The report no longer has `waived`/`waivers`. It now imports `tmcore.lib.backup_pipeline` relatively (one
   module instance), so the "own backup_pipeline instance" workaround in `postcheck._diff` is no longer needed.
   `expected_notes` per build stay in `tmcore/verdict.py` (as you built it). Ported suite `test_backup_diff.py`:
   30 tests, enabled.
5. `lib/restore_engine.py`: every `--allow-*` removed (stale and airplane-off now always refuse). The module is not
   used by any step (your `steps/restore.py` + `guards/` replace it), still has `--password-file` and calls
   `backup_pipeline.py verify --password-file` as a subprocess, which no longer exists. Proposal (needs coreB +
   maintainer): delete `lib/restore_engine.py` and `tests/unit/test_restore_wrapper.py` (the last `collect_ignore`
   entry) and drop it from `packaging/tests/import_set.py`.

### coreA → docs / pack

- CI test environment: the coreA E2E `core/tests/contract/test_e2e_android_prepare.py` needs `swiftc` (it builds
  `threema-import`, or uses `TMCORE_IMPORTER`) and runs every engine process under `/usr/bin/sandbox-exec`
  `(deny network*)` when present. Its media case uses `fixtures/gen_android_backup.py`, which needs **Pillow** and
  **ffmpeg**: please add `pillow` to `.github/ci-requirements.txt` and `brew install ffmpeg` to the core jobs —
  without them that one test is skipped (the canary E2E does not need them).

---

## 2026-10-01 coreB: answers and new requests (tmcore part B)

**coreA "consume_cancel": thanks, used.** `steps/device.py` now calls `Protocol.consume_cancel()` and reads
`Protocol.cancel_requested`; no private attribute any more.

**coreA → coreB 1 (SAMPLE_SPEC file message): done.** `fixtures/make_store.swift` gives the seeded own file message a
blob id and an encryption key (new `make_store` cache tag, stores are rebuilt on first use).

**coreA → coreB 2 (`password.txt`): done.** `fixtures/gen_android_backup.py` takes `--out` (required) or
`generate(out, split=False, format_version="27")`, writes no password file and reads `read_csv` from
`tmcore.lib.validate_android`. Canary contact/group/message values are carried by the virtual iPhone's Threema store
(`fake/scenario.py default_store_spec`); the Android side stays the format fixture (android_canary.py stays coreA's).

**coreA → coreB 3 (canary scan): `scripts/canary_scan.py` written** (docs request): UTF-8, UTF-16 LE/BE, Base64 in
three alignments; scope = engine output only (events `*.jsonl`, `*.log`, `reports/`, `session.json`, `engine.json`,
`diag/`), never `ios/`, `work/`, `android/`, `fake-iphone/` of a session or the fixture inputs. It does not import
`core/tests/support/canary.py` (scripts must run without the test tree); if you prefer one implementation, make
`support/canary.py` the library and I switch the CLI to it.

**coreA → coreB 4 (backup_diff): done.** `steps/postcheck.py` calls `compare()` without `show_apps/deep/waivers`.

**coreA → coreB 5 (`lib/restore_engine.py`): coreB agrees** to delete it together with
`tests/unit/test_restore_wrapper.py` and its `collect_ignore` entry: `steps/restore.py` (`send_set`) +
`guards/` + `tmcore/fake` replace it, the fake device already rejects any option set but the proven one. Deletion is
coreA's/the maintainer's commit (coreA owns `lib/`; pack: `packaging/tests/import_set.py`).

**mypy: done.** `mypy tmcore` is clean for every coreB file; `ruff check --select E9,F` clean incl.
`fixtures/gen_android_backup.py`.

### coreB → coreA

1. `host-check` ignores `--fake-device` and reports the machine it runs on. The scenario recorder therefore replaces
   the host-check values of a recording with the canonical example Mac (`devtools/record_scenarios.py HOST`). Request:
   with `--fake-device`, report a fixed virtual Mac (macOS 15.1, arm64, apfs, 220 GB free, ac, FileVault on) -- then
   the recorder substitution can go.
2. `redact.py`: the phone-number pattern also matches ISO dates, e.g. `2026-10-01T16:23:24Z` in debug.log becomes
   `<phone>T16:23:24Z` (seen in stderr of every step that logs a timestamp). Request: require a leading `+`/`00` or
   exclude `\d{4}-\d{2}-\d{2}`.
3. `cleanup --what work` after a green postcheck removes `work/` incl. `work/postcheck/` (gate reports, structure and
   counts only). Fine for v1; if the diag report should carry them after cleanup, copy counts into `reports/` first
   (postcheck already writes `reports/postcheck.json`).

### coreB → app

The 27 recordings in `app/Tests/Scenarios/` replace the drafts (header `"draft": false`; `make record-scenarios`,
check: `make check-scenarios`). Differences to the drafts the UI should know:

| Scenario | Now |
|---|---|
| all | `hello` carries `fake_device: true`; counts are the canonical example values, sizes those of the fixtures |
| happy | encryption off on the virtual iPhone: S10a (`{"mock":"user","screen":"S10a","answer":"password_confirmed"}`) + `encryption-enable` |
| find_my_on | `find_my` not readable live (`findmy` warn at S03/S08), the device answers MBError 211: `E_GUARD_FINDMY {source: mberror_211}` after `critical` on/off, `device_modified: no` |
| ios_unknown | S03 answer `prepare_android`, Android part runs, the second `device-status` (S08) ends on F-IOS-UNKNOWN; no backup |
| wrong_backup_password | encryption on, first password wrong: `backup` → `E_BACKUP_PASSWORD {attempt 1, max 5}`; user line `F-PW-WRONG/password_entered`; the second `backup --role pre` re-checks the SAME backup (no progress events) |
| freshness_expired, dcim_changed | `restore` refused (`E_GUARD_FRESHNESS` / `E_GUARD_DCIM_CHANGED`), user line `F-FRESHNESS`/`F-DCIM` `new_backup`, then `backup --role pre` + `prepare` + `restore` again → S20 |
| link_lost_after_send | `restore` → `E_RESTORE_INTERRUPTED {last_progress: 97}`, `device_modified: unknown`; user line `F-RESTORE-MID/iphone_restarted`; continues S16 … S20 |
| app_crash_after_send | crash block, `relaunch`, `session-status` → S16, user line `S23/continue`, then POST backup, postcheck, S20 |
| rollback_threema_ok | `postcheck` threema_only, user line `S21/reset_threema`, `rollback-threema` (phases build/guards/send/reboot), POST backup, postcheck ok, S20 |
| setup_full | user answer `full_setup` at S16 |
| new | happy_with_notes, first_backup_dropped (`retry` W_BACKUP_RETRY), threema_missing, threema_model_unknown, iphone_space_low, photos_limit, threema_only_fail, keychain_fail, restore_state, android_two_backups, android_incomplete, android_format_new, duplicate_chat |

`postcheck` `areas[].area` tokens: see the earlier coreB entry; `p3_payload`/`p4_strict` checks carry
`{alerts, notes}`, `purplebuddy` carries `{buddy_class, setup_done}`.

### coreB → docs / pack

- E2E: `core/tests/e2e/test_scenarios.py` (27 scenarios: end screen, code, verdict, events contract via
  `scripts/validate_schemas.py`, `reports/*.json` + `engine.json` against the schemas, canary scan, password never
  in any file, iPhone untouched when nothing was sent) and `core/tests/e2e/test_restore_guards.py` (17 guard tests on
  real artifacts). Importer: `TMCORE_IMPORTER` or `tests/support.importer()`; Android fixture needs Pillow, ffmpeg,
  afconvert (cached in `build/test-cache/android-<tag>/`). Run time ≈ 2 min. No device, no network.
- `make record-scenarios` / `make check-scenarios` work with the existing Makefile targets.

### coreB → maintainer (OWNERSHIP.md)

New coreB files: `core/tmcore/fake/scenarios/*.json` (27), `core/tests/e2e/flow.py`, `devtools/record_scenarios.py`,
`scripts/canary_scan.py`, `core/tests/unit/test_{guards_pure,verdict,fake_device}.py`. `fake/image.py` from the
earlier list does not exist (the image lives in `fixtures/gen_ios_backup.DeviceImage`).

## 2026-10-01 app → coreA (schema custodian): area labels, `x-area-tokens`

The app now has `area.<token>` texts (DE/EN) for every token of coreB's list above plus the draft tokens `settings`
and `sms_imessage`; unknown tokens are shown as "nicht näher bestimmte Bereiche" (never as raw tokens). If
`x-area-tokens` lands in `codes.v1.json`, please let `gen_codes.py` manage `area.*` like `code.*` — the app keys will
then be removed in the same commit.

## 2026-10-01 app → coreB: scenario recordings and the app flow

- The recordings place the S08 Find-My `device-status` before the `S08` directive; the app runs it on S08 "Weiter"
  (DESIGN §8.3 S08). Harmless for the mock (blocks are taken per command), noted for readers of the files.
- The file choice on S05 has no directive (android-inspect follows "S04 done"); both app drivers choose the
  scenario's files right after "S04 done". If you add a `{"mock":"user","screen":"S05","answer":"files_chosen"}`
  line, both drivers already accept a following `password_entered`.
- `ios_unknown`: the app shows S03 red (no F-screen) with "Android-Teil vorbereiten"; after the Android part, S07
  "Weiter" re-runs `device-status` and ends on F-IOS-UNKNOWN — this matches the recording.
- `docs/images/screenshots.json` takes S10a from `happy` (✓ encryption switched on there) and S10b from
  `wrong_backup_password` (✓). S22 is reached from `data_fail` via S21 "Anleitung".

## 2026-10-01 app → docs: CI app job

- Scheme `ThreemaChatTransfer` runs Unit (incl. snapshot/size checks) and UI tests; `app/scripts/test.sh unit|ui` wraps it.
  `ScenarioUITests` runs **every** scenario in DE and EN itself (`TM_LANG`), so `-testLanguage` does not change
  what is tested; you may drop the matrix or split by `-only-testing:ThreemaChatTransferTests` / `ThreemaChatTransferUITests`.
- XCUITests need macOS UI automation mode (granted on GitHub's runners). Locally they cannot run without an
  administrator enabling it; the unit target replays every scenario through the store with the same directives.
- Snapshot PNGs: set `TEST_RUNNER_TM_SNAPSHOT_DIR` to collect them (review only; they carry no `tm-demo` marker
  and must not be committed).
- `DemoScreenshotTests` implements your contract (`TM_SCREENSHOT_LIST`, `TM_SCREENSHOT_DIR`, `TM_DEMO_RESOURCES`,
  language from `TM_LANG` or the last path component `de`/`en` of the output dir). Items whose scenario is missing
  or whose screen is not reached are listed in an attachment, not failed.

## 2026-10-01 app → pack: bundle layout used by LiveEngine

`Contents/Resources/core/python/bin/python3` (`-I -B -m tmcore`), `Contents/Resources/engine-sandbox.sb` (shipped by
the app target), `TMCORE_RESOURCES=Contents/Resources`, optional `Contents/Resources/legal/NOTICE` (About window).
The app target also bundles `Resources/Scenarios/*.jsonl` (synthetic mock scenarios for `TM_ENGINE=mock:`; a few
hundred KB). Drop them from Release in `build-app.sh` if the size budget needs it — the app then refuses `mock:`.

---

## 2026-10-01 integrator (P5): resolution of every open entry above

| Entry | Resolution |
|---|---|
| coreB → coreA: session paths | accepted by coreA, implemented by both; unchanged. |
| coreB → coreA: `consume_cancel` | done (coreA), used by `steps/device.py` (coreB). Closed. |
| coreB → maintainer: OWNERSHIP | `devtools/OWNERSHIP.md` lists the P3–P5 files per owner (new table "Files added in P3–P5"). |
| coreB → app / coreA: `x-area-tokens` | **done.** `core/schema/codes.v1.json` has `x-area-tokens` (27 tokens, DE/EN); `scripts/gen_codes.py` manages `area.*` like `code.*` (the app keys were replaced in the same commit; the unused draft tokens `settings`, `sms_imessage` are gone); `scripts/validate_schemas.py` validates the block; `core/tests/unit/test_verdict.py::test_every_area_token_has_de_en_words` fails when the verdict can emit a token without words. Unknown tokens still show "nicht näher bestimmte Bereiche". |
| docs → maintainer: `SECURITY.md`, allowlist block | "(in preparation)" removed; `scripts/scrub-allowlist.txt` has the managed block `demo screenshots` (now filled by `devtools/demo_screenshots.py --install`). |
| docs → maintainer: OWNERSHIP additions | added (table above), incl. `CI_STRICT`. |
| docs → pack: workflow interfaces | all scripts run without arguments from the repo root (checked with `make dmg OFFLINE=1`); `check-scenarios` target exists. |
| docs → coreB: e2e job | the variable is `TMCORE_IMPORTER` (not `THREEMA_IMPORT_BIN`): `ci.yml` e2e job fixed; `brew install ffmpeg` added to the contract and e2e jobs, `pillow==12.3.0` to `.github/ci-requirements.txt`. `scripts/canary_scan.py` exists. |
| docs → app: project and demo screenshots | names as requested. The demo screenshots no longer need UI automation: `devtools/demo_screenshots.py` starts the signed app with `TM_ENGINE=fake:<scenario>` and the in-app demo autopilot (`app/Sources/System/DemoAutopilot.swift`, demo engines only) and renders the window in-process; `make demo-screenshots` and `demo-screenshots.yml` call it. `DemoScreenshotTests` stays as a MockEngine review aid (its UI driver types test passwords the virtual iPhone would refuse). |
| docs → coreA/coreB: lint findings | `ruff check --select E9,F` and `mypy tmcore` clean on the integrated tree (see `docs/INTEGRATION-REPORT.md`). |
| pack → app: `TMEngineVersion` | done (app: `AppInfo.engineVersion`); verified in the signed bundle (`verify-bundle.sh`: TMEngineVersion = engine = importer). |
| pack → coreA: `bundle-manifest.json` | accepted (coreA); `selftest` passes from the signed app. |
| pack → coreB: `record_scenarios.py` interface | as documented; `make check-scenarios` passes. |
| pack → docs: Xcode pin vs. model bytes | `model/build-momd.sh`: the byte check is hard only with the Xcode build named in `model/V56/SOURCE` (`27A266a`); with another momc a byte difference is a NOTE and the canonical dump + identity checks decide. CI keeps its pin. |
| coreA → coreB 1–4 | done (see coreB's answers). |
| coreA → coreB 5 / coreB agrees: `lib/restore_engine.py` | **deleted** with `core/tests/unit/test_restore_wrapper.py`; `collect_ignore` is empty; `packaging/tests/import_set.py` imports the steps, guards, verdict and fake device instead. |
| coreA → docs/pack: Pillow, ffmpeg | done (see docs → coreB). |
| coreB → coreA 1: virtual Mac under `--fake-device` | **done.** `host-check --fake-device` reports `VIRTUAL_MAC` (macOS 15.1, arm64, APFS, 220 GB free, AC, FileVault on; `need_bytes` = the real estimate). The recorder substitution stays (harmless, same values except `need_bytes`). |
| coreB → coreA 2: ISO dates became `<phone>` | **fixed** in `redact.py` (a phone match may not start inside a digit run nor at `YYYY-MM-DD`); test `test_redactor_keeps_iso_timestamps_but_not_phone_numbers`. |
| coreB → coreA 3: `cleanup --what work` removes `work/postcheck` | accepted for v1 (counts are in `reports/postcheck.json`). |
| coreB → app: recordings | used by the unit tests, the XCUITests and the demo autopilot (bundled `Resources/Scenarios`, which therefore stay in the Release bundle). |
| app → coreB: S05 `files_chosen` | not needed: both drivers and the autopilot choose the files after "S04 done". |
| app → pack: bundle layout | as described; LiveEngine verified against the signed bundle (`devtools/synthetic_prepare_run.py`, demo runs). New: for `fake:` runs only, LiveEngine passes `TMCORE_FIXTURES` (from `TM_DEMO_FIXTURES`), because the virtual iPhone builds its backups with `fixtures/gen_ios_backup.py`, which the bundle does not ship; `tmcore.fake.scenario.canaries()` reads the same folder. |
