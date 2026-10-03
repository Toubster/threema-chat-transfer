# Engine protocol v1 (`tmcore`)

Status: **frozen** for 0.3.0-dev (P2.5). The app (SwiftUI) and the engine (`tmcore`, Python) are built in
parallel against this contract. Machine-readable sources of truth:

| File | Content |
|---|---|
| `core/schema/events.v1.json` | every stdout line (envelope, event types, `result.data` per command) |
| `core/schema/codes.v1.json` | every code: kind, screen, exit code, device_modified, retryable, needs_new_backup, actions, data keys, DE + EN texts |
| `core/schema/session.v1.json` | `session.json` (app) and `engine.json` (engine) in the session folder |
| `core/schema/compat.v1.json` | bundled allow-lists `compat/*.json` and device-run records |
| `core/schema/report.v1.json` | `reports/*.json` and the diagnostic report |

Any incompatible change raises `protocol`. New **optional** fields are compatible: the engine (producer) is strict
and emits only declared fields; the app (consumer) ignores unknown fields and maps unknown codes to `F-INTERNAL`
with the code in the diagnostic report.

## 1. Invocation

```
<App>/Contents/Resources/core/python/bin/python3 -I -B -m tmcore <command> --session <dir> [options]
    [--secrets-stdin] [--fake-device <scenario>]
environment (set completely by the app, nothing inherited):
    PYTHONDONTWRITEBYTECODE=1  PYTHONNOUSERSITE=1  LANG=C.UTF-8  TMPDIR=<session>/work/tmp  TMCORE_PROTOCOL=1
    TMCORE_RESOURCES=<App>/Contents/Resources
```

- **argv** never contains secrets or personal data. The only exception: paths of the Android backup files the user
  chose (`android-inspect`, `android-normalize`).
- **stdin**: only with `--secrets-stdin`, exactly **one** JSON line, then EOF; otherwise `/dev/null`.
  `{"backup_password":"…","new_backup_password":"…","android_passwords":{"0":"…","1":"…"}}` — unused fields are
  absent; `android_passwords` is keyed by the argv index of the Android file. Unknown fields, more than one line or
  wrong types → `E_PROTOCOL`; a secret the step needs but did not get → `E_SECRETS_MISSING`.
- **stdout**: JSON lines only (UTF-8, one object per line, ≤ 64 KiB, flushed per line). At process start the event
  stream gets a private copy of fd 1 and fd 1 itself points to stderr, so a stray `print()`, a C library or an
  inherited child stdout can never corrupt the stream (it lands in the debug log, redacted).
- **stderr**: debug log, redacted by `tmcore.redact` (patterns: home paths, UDIDs, ECIDs, serials, e-mail, phone,
  Threema-ID shapes, Android backup file names; by value: every secret of the stdin line and every Threema ID of
  `android/normalized.sqlite`); the app stores it as `<session>/logs/debug.log` and never shows it. The app also
  stores the stdout copy as `<session>/logs/events.jsonl`.
- `--fake-device <scenario>`: virtual iPhone (CI, demo mode). The network guard then refuses even `AF_UNIX`, so a
  fake run can never reach a real device. `hello.fake_device` is `true`. `host-check` then reports a fixed virtual
  Mac (macOS 15.1, arm64, APFS, 220 GB free, AC power, FileVault on) instead of the machine it runs on. The virtual
  iPhone builds its backups with `fixtures/gen_ios_backup.py` of a checkout; a bundle run gets that folder as
  `TMCORE_FIXTURES` (the app passes it only for `fake:` runs, from `TM_DEMO_FIXTURES`).
- There is **no** override option (`--allow-*`, `--force`, `--waive`, …) in any command, and there never will be.

## 2. Envelope

```json
{"v":1,"seq":17,"ts":"2026-10-01T13:43:40.120Z","cmd":"restore","type":"progress", …}
```

| Field | Rule |
|---|---|
| `v` | protocol version, `1` |
| `seq` | gapless from 1 per process |
| `ts` | RFC 3339 UTC with `Z` (`YYYY-MM-DDTHH:MM:SS.mmmZ`) |
| `cmd` | the command |
| `type` | `hello`, `phase`, `progress`, `check`, `prompt`, `retry`, `device`, `critical`, `note`, `result` |

**Allowed values** anywhere in an event: numbers, booleans, `null`, and *safe strings* only — enum tokens
(`^[a-z][a-z0-9_]*$`), codes (`E_`/`W_`/`N_`/`R_`), salted hashes (`h:` + 8 hex, HMAC-SHA256 with the session salt),
timestamps, public version numbers, iOS builds (`24A437`), device model identifiers (`iPhone17,1`), model IDs
(`V56`), screen IDs (`S16`, `F-INTERNAL`), session file keys (`android/missing-senders.json`) and `sha256:` digests
of public bundle files. Object keys are snake_case.

**Forbidden**: file names from backups, paths, names, Threema IDs, device names, phone numbers, e-mail addresses,
message content, free text. `tmcore.protocol` refuses such a value *before* anything is written. Personal data the
UI must show (the missing Threema IDs of S07) is written to a `0600` file in the session folder; the event only names
the file key.

## 3. Event types

| `type` | Fields | App |
|---|---|---|
| `hello` | `engine_version`, `protocol`, `pymobiledevice3`, `importer_version`, `models[]`, `compat_digest`, (`fake_device`) | first event of every process; app requires `protocol == 1` and `engine_version == app version`, else `F-INTERNAL` |
| `phase` | `phase`, `index`, `count` | step list (phase names per command: `events.v1.json` → `x-phases`) |
| `progress` | `phase`, `pct` (0–100), `done`, `total`, `unit` (`bytes`/`files`/`items`), (`eta_s`) | progress bar |
| `check` | `id`, `status` (`pass`/`warn`/`fail`/`skip`), (`code`), (`data`: numbers/enums) | check row with tick |
| `prompt` | `kind` (`unlock_device`/`trust_device`/`passcode_on_device`/`keep_cable`), `active` | hint overlay |
| `retry` | `reason` (code), `attempt`, `max`, `wait_s` | "That is normal, trying again …" |
| `device` | `state` (`none`/`locked`/`untrusted`/`ready`/`multiple`/`disconnected`), (`device` hash), (`product_type`) | `device-watch` and device steps |
| `critical` | `on` | `on:true` locks Cancel and Quit; power assertion stays |
| `note` | `code` (`W_`/`N_`), (`data`) | hint without stopping |
| `result` | `ok`, `code`, `retryable`, `device_modified` (`no`/`yes`/`unknown`), `data` | **exactly once, always the last event** |

`result`: `ok:true` ⇔ an `R_` code; `ok:false` ⇔ an `E_` code. For `ok:true` the `data` object must match
`events.v1.json#/$defs/result_ok_<command>`. For `ok:false`, `data` carries the keys listed for the code in the
catalog (`codes.v1.json` → `data`, enums in `data_enums`).

## 4. Commands

| Command | Device | Secrets | Options | `result.data` on success | Phases |
|---|---|---|---|---|---|
| `version` | – | – | – | versions, `models`, `model_sha256[]`, `importer_sha256`, `compat_sha256`, `compat_digest` | – |
| `selftest` | – | – | – | `modules_ok`, `bundle_ok`, `netguard`, `python` | imports, bundle, netguard |
| `host-check` | – | – | `--workdir` | `macos`, `arch`, `fs`, `free_bytes`, `need_bytes`, `power`, `battery_pct`, `filevault` | checks |
| `android-inspect` | – | – | `FILE…` | `files[]` {`ref`, `kind`, `format_version`, `created_at`, `bytes`, `has_media`, `encrypted`}, `plan`, `text_ref`, `media_refs[]` | inspect |
| `android-normalize` | – | `android_passwords` | `--plan JSON FILE…` | `chats`, `groups`, `messages`, `media_present`, `media_total`, `polls`, `own_unsent_as_sent`, `missing_key_senders`, `missing_key_messages`, `missing_key_groups`, `missing_senders_file`, `own_id` | read, media, verify |
| `device-watch` | read | – | – | `events` (stream ends at SIGTERM) | – |
| `device-status` | read | – | `--expect HASH` | `device`, `product_type`, `ios_version`, `ios_build`, `compat`, `threema` {`installed`, `variant`, `version`}, `encryption`, `find_my`, `free_bytes`, `photos_bytes_estimate`, `battery_pct`, `charging`, `managed` | read |
| `encryption-enable` | **writes a setting** | `new_backup_password` | – | `encryption:"on"`, `changed` (`false` = it was already on) | confirm_on_device |
| `backup` | read | `backup_password` | `--role pre\|post` | `role`, `bytes`, `files`, `password_ok`, `airplane`, `threema` {`setup_ok`, `retention_ok`, `model`, `app_version`}, `id_match` (pre), `photos_bytes`, `finished_at`, `fresh_until` (pre), `ios_build` | backup, checks |
| `prepare` | – | `backup_password` | – | `messages`, `media`, `skipped`, `payload_bytes`, `iphone_required_bytes`, `fresh_until` | extract, import, count, build, verify |
| `restore` | **writes** | `backup_password` | – | `last_progress`, `finished_at` | guards, send, reboot |
| `postcheck` | read | `backup_password` (not with `full_setup`) | `--buddy-answer account_only\|full_setup\|none`, `--threema-answer ok\|problem` | `verdict`, `notes[]`, `areas[]` {`area`, `severity`}, `threema_ok` | threema, compare, verdict |
| `rollback-threema` | **writes** | `backup_password` | – | like `restore` | build, guards, send, reboot |
| `session-status` | – | – | – | `phase`, `resume_at` (screen), `restore_sent_at`, `fresh_until`, `verdict` | – |
| `diag-report` | – | – | – | `file` (session key), `bytes` | collect |
| `cleanup` | – | – | `--what work\|pre\|post\|all` | `freed_bytes`, `what` | delete |

Rules:
- Every command except `version`, `selftest` and `host-check` requires `--session`; the folder must be owned by the
  user, mode `0700`, with a `session.json` of schema `session.v1` — otherwise `E_PROTOCOL` (exit 2).
- `backup --role pre` runs right after the backup: keybag password check, extract checks, `airplane`,
  `threema_variant`/`threema_setup`/`threema_retention`/`threema_model`, `identity`, `photos_limit`. A failing check
  keeps the backup; `needs_new_backup` of the code tells the app whether a new backup is needed.
- `restore` runs all guards (DESIGN §6.1) in a fixed order, then exactly one restore with
  `system=True reboot=True copy=False settings=False remove=False skip_apps=True`.
- `postcheck` needs a post backup made after `restore_finished` on the same device. A red verdict is **not** an
  engine error: the result is `ok:true`, `R_OK`, and `data.verdict` tells the app where to go (S19/S21/S16 question).
  Without `--buddy-answer`, an Apple-account re-run ends with `verdict:"needs_answer"`. `account_only` and `none`
  both rule out the full Setup Assistant; with the backups' proof (SetupDone before and after, only re-run keys
  changed) the re-run is the note `N_APPLE_ACCOUNT_RERUN`. `full_setup` is `setup_full` from the answer alone:
  postcheck then reads no backup and needs no password (an iPhone in Setup Assistant cannot open Threema or be
  expected to make a POST backup). `--threema-answer problem` (S17) with every system check green is `threema_only`,
  the one verdict `rollback-threema` accepts; `threema_ok` stays the P.2 machine check.
- `encryption-enable` with encryption already on changes nothing and returns `changed:false`: the backups are then
  protected by the password chosen elsewhere (e.g. Finder), never by `new_backup_password`. The app must not keep or
  store that password. `changed:true` while a PRE backup is kept for another password attempt (`E_BACKUP_PASSWORD`)
  drops that backup from `engine.json`: it was made under a password the iPhone no longer has (the user did "Alle
  Einstellungen zurücksetzen", S10b help), so the next `backup --role pre` makes a new backup.
- `android-inspect` reads only the zip directories (no password): `files[].format_version` is `null` there; the
  format of the chat backup is checked by `android-normalize` (`E_ANDROID_FORMAT_NEW`/`_UNVERIFIED`) right after
  the password check. Plans: `single` (one file with chats; media from the same file if it has any),
  `text_plus_media` (chats from the newest file with chats, media from the others), `none` (no file with chats:
  `E_ANDROID_NO_TEXT` if a media-only file was given, `E_ANDROID_INCOMPLETE` if a cut-off file was given, otherwise
  `R_OK` with `plan:"none"` and the file kinds).
- `prepare` needs `android-normalize` and a PRE backup of this session whose checks all passed (else `E_PROTOCOL`
  `sub` `no_android`/`no_pre_backup`/`pre_checks_failed`); it re-checks freshness at the start and at the end (`E_GUARD_FRESHNESS`), reuses
  `work/extract` only when it belongs to `engine.json pre.backup_id`, and writes `engine.json prepared` only after
  the frozen set verified. Any failure leaves `prepared: null` (nothing for `restore`).
- `cleanup` empties the chosen folders (the layout stays) and is refused with `E_PROTOCOL` `sub:"cleanup_blocked"`
  while a sent restore has no postcheck verdict yet, and for `pre`/`all` while R1 is still possible (verdict
  `threema_only`, rollback not used, PRE ≤ 6 h old). Before a restore the phase falls back to what is left.
- `diag-report` writes a new `diag/diag-<utc>[-n].json` each time (never overwrites), re-salts every `h:` value and
  drops every event or report that does not pass the safe-value rules; `debug.log` is never read.
- Check IDs: `events.v1.json#/$defs/check` (host, device, Android, backup, prepare, guard and postcheck checks).

## 5. Codes

`codes.v1.json` holds every code with:

| Key | Meaning |
|---|---|
| `kind` | `error` (E_), `warning` (W_), `note` (N_), `result` (R_) |
| `screen` | target screen (`S05`, `F-FINDMY`, …); empty for `R_OK` |
| `exit` | process exit code for this result code (0 for W_/N_/R_) |
| `device_modified` | default for the result: `no`/`yes`/`unknown`/`dynamic` (`dynamic`: the step decides) |
| `retryable` | the app may run the same command again after the user action of the screen |
| `needs_new_backup` | a new PRE backup (and `prepare`) is required before going on |
| `actions` | buttons the app offers (labels: `action.<id>`) |
| `data` / `data_enums` / `enum_strings` | data keys the code may carry, their enum values and localized enum texts |
| `strings` | `title`, `body`, `action`, each `de` (source, formal "Sie") + `en` |

`scripts/gen_codes.py` writes `app/Sources/Engine/Codes.generated.swift` (`EngineCode`, `CodeAction`,
`EngineCommand`, `EnginePhase`, `CheckID`, `Verdict`, …) and the managed keys of
`app/Resources/Localizable.xcstrings`: `code.<CODE>.title|body|action`, `code.<CODE>.<key>.<value>`, `action.<id>`,
and `area.<token>` from `x-area-tokens` (the closed token set of postcheck `areas[].area` with the DE/EN words of
S21 R2 `{Bereiche}`; `core/tests/unit/test_verdict.py` checks that every token the verdict can emit is listed).
Placeholders stay literal `{name}` tokens in both languages: `{App}` = product name, `{x_gb}` = the data key
`x_bytes` formatted in GB, `{date}` = an app-side value, every other `{key}` = a data key of the event.
`scripts/check_strings.py` fails on a missing or empty DE/EN text, different placeholder sets or "du" in German.

Retry reasons are codes too (`W_BACKUP_RETRY`, `W_DEVICE_RETRY`).

## 6. Exit codes, cancel, `critical`

| Exit | Meaning |
|---|---|
| 0 | `result.ok = true` |
| 1 | refused by a guard or user error; **nothing** changed on the iPhone |
| 2 | invocation or program error |
| 3 | device error; for `restore` possibly after sending (`device_modified` tells) |
| 4 | cancelled (SIGTERM outside `critical`) |

- The `result` event decides, not the exit code. **No result** (crash): if `critical` was on, the app treats it as
  "restore possibly sent" → **S16**; without `critical` → `F-INTERNAL`.
- SIGTERM outside `critical`: the engine stops at the next safe point → `E_CANCELLED`. During `critical` the engine
  defers SIGTERM until sending is over; the app locks Cancel and ⌘Q meanwhile. A step that returned keeps its result
  (a SIGTERM that arrived while it was sending never turns a sent restore into `E_CANCELLED`). `device-watch` ends
  on SIGTERM by design with `R_OK` (`Protocol.consume_cancel()`).
- Any network attempt in the process ends the command with `E_NETWORK_BLOCKED`, also when a library caught the
  refusal and carried on (the guard counts violations). `SystemExit` or any other exception inside a step ends as
  `E_INTERNAL` with `exc` and `where` only.
- `critical` is on in `restore`/`rollback-threema` from the first byte sent to the device until just before the
  result, and in `encryption-enable` while the device dialog is open. The result is never emitted while `critical`
  is on (the engine closes it first). An error after `critical` was on reports `device_modified:"unknown"` unless
  the step knows better.

## 7. Session folder

```
~/Library/Application Support/Chat Transfer for Threema/sessions/<uuid>/   0700, Time Machine exclusion, .metadata_never_index, APFS
  session.json   app state (schema session.v1)            written by the app only
  engine.json    engine state (session.v1#/$defs/engine)   written by the engine only (locked, atomic, 0600)
  android/       normalized.sqlite, media/, missing-senders.json (0600)
  ios/pre/ ios/post/   backups (pymobiledevice3 layout), frozen
  work/          extract/, store_out/, restoreset/ (APFS clone), tmp/
  reports/       *.json (counts only, report.v1)
  logs/          events.jsonl (stdout copy), debug.log (redacted)     written by the app
  diag/          diagnostic reports (report.v1 kind diag, fresh salt)
```

`engine.json` holds the session salt for `h:` hashes, the device hash, the PRE/POST backup references with
`fresh_until`, the prepared set, the restore (`kind`, `sent_at`, `finished_at`, `critical_seen`),
`restore_sent_at`, the postcheck verdict and a short command history (code + exit per command). The app reads it via
`session-status`, which returns the screen to resume at (DESIGN §8.1): once `restore_sent_at` is set — also after a
missing result with `critical` on — it is always **S16**, never "start over".

## 8. Engine-side API (Python)

```python
from tmcore import protocol as P
from tmcore.cli import Context, StepResult
from tmcore.protocol import EngineError

def restore(ctx: Context) -> StepResult:
    P.phase("guards", 1, 3)
    P.check("freshness", "pass", age_min=7, limit_min=60)       # data: numbers/enums only
    ctx.proto.check_cancel()                                     # safe point for SIGTERM
    pw = ctx.secrets.require("backup_password")                  # memory only
    with ctx.proto.critical():                                   # critical on … off, SIGTERM deferred
        P.phase("send", 2, 3)
        P.progress("send", done, total, unit="bytes")
    return StepResult({"last_progress": 100}, code="R_RESTORE_SENT_LINK_LOST", device_modified="yes")

raise EngineError("E_GUARD_FRESHNESS", age_min=71, limit_min=60)  # -> result + exit code from the catalog
```

| Module | Use |
|---|---|
| `tmcore.protocol` | `emit(type, **fields)`, typed helpers, `critical()`, `check_cancel()`, `EngineError`, `catalog()` |
| `tmcore.cli` | `COMMANDS`, `Context` (`proto`, `session`, `secrets`, `fake_device`, `resources`, `compat(name)`), `StepResult` |
| `tmcore.session` | `Session.open/create`, `path(key)`, `key(path)`, `update_engine()`, `hasher` |
| `tmcore.secrets` | `Secrets.require(name)`, `android(ref)`, `wipe()` |
| `tmcore.hashing` | `SessionHasher.h(value)`, `same(a, b)`, `rehash(h)` |
| `tmcore.redact` | `Redactor`, `install_stderr`, `exception_summary(exc)` (class + module_line, never the message) |
| `tmcore.netguard` | `install(allow_unix=…)`, `NetworkBlocked` |
| `tmcore.guards` | `GuardResult`, `report(ctx, result)` |

Steps never print, never write to stdout, never emit `hello` or `result` themselves. Unhandled exceptions become
`E_INTERNAL` with `exc` and `where` only. `ctx.session` is set for every command except `version`, `selftest` and
`host-check`. Library ports live in `tmcore.lib` and take passwords as arguments (no password files):
`backup_pipeline.run(cmd, password=…, …)`, `android_normalize.normalize(…, passwords=…)`,
`verify_import.main(argv, quiet=True)`, `verify_normalized.verify(dir)`, `validate_android.classify(path)`/`plan()`,
`backup_diff.Backup(dev, password, label)` + `compare(…)` (no waivers, no deep paths, no app names).
The importer is the bundled `Resources/bin/threema-import` (`TMCORE_IMPORTER` for development builds and tests).

## 9. Mock scenarios (`app/Tests/Scenarios/*.jsonl`)

One file per scenario; names identical to the fake-device scenarios (DESIGN §13.3). Each line is either an event
(`events.v1`) or a mock directive:

| Line | Meaning |
|---|---|
| `{"mock":"scenario","name","protocol","lang","expect_screen","summary","draft"}` | header, first line; `name` = file name |
| `{"mock":"invoke","cmd","args","secrets"}` | a tmcore process starts; `args` use placeholders (`<session>`, `<android-backup-0>`); `secrets` lists the stdin field names, never values |
| events | the stdout of that process, `seq` from 1 |
| `{"mock":"exit","code"}` | the process exits; `{"crash":true,"code":-9}` = killed without a result |
| `{"mock":"user","screen","answer"}` | what the user does in the app (for UI tests) |
| `{"mock":"relaunch"}` | the app is quit and started again (resume) |

Timestamps are relative to a fixed start (`2026-01-01T09:00:00Z`). Values are the canonical examples only:
18 chats, 4 groups, 12345 messages, 1234 media, 2 polls, 6.4 GB, iOS 27.0 (24A437), Threema 7.4, hashed IDs.
`scripts/validate_schemas.py` checks every file: schema-valid events, `hello` first, gapless `seq`, exactly one
`result` last (except crash blocks), balanced `critical`, exit code = catalog exit of the result code, every code in
the catalog. `make record-scenarios` (P3) replaces the drafts with recordings from `tmcore --fake-device`; CI then
compares drafts and recordings structurally.

## 10. Example: `restore`

```json
{"v":1,"seq":1,"ts":"…","cmd":"restore","type":"hello","engine_version":"0.9.0","protocol":1,"pymobiledevice3":"11.19.4","importer_version":"0.9.0","models":["V56"],"compat_digest":"h:3f2a91c0"}
{"v":1,"seq":2,"ts":"…","cmd":"restore","type":"phase","phase":"guards","index":1,"count":3}
{"v":1,"seq":3,"ts":"…","cmd":"restore","type":"check","id":"freshness","status":"pass","data":{"age_min":7,"limit_min":60}}
{"v":1,"seq":4,"ts":"…","cmd":"restore","type":"check","id":"dcim_unchanged","status":"pass","data":{"files":1234}}
{"v":1,"seq":5,"ts":"…","cmd":"restore","type":"critical","on":true}
{"v":1,"seq":6,"ts":"…","cmd":"restore","type":"phase","phase":"send","index":2,"count":3}
{"v":1,"seq":7,"ts":"…","cmd":"restore","type":"progress","phase":"send","pct":41.1,"done":2630400000,"total":6400000000,"unit":"bytes"}
{"v":1,"seq":8,"ts":"…","cmd":"restore","type":"phase","phase":"reboot","index":3,"count":3}
{"v":1,"seq":9,"ts":"…","cmd":"restore","type":"critical","on":false}
{"v":1,"seq":10,"ts":"…","cmd":"restore","type":"result","ok":true,"code":"R_RESTORE_SENT_LINK_LOST","retryable":false,"device_modified":"yes","data":{"last_progress":100}}
```

## 11. Tooling

```
python3 scripts/validate_schemas.py   # schemas, code catalog, compat lists, DESIGN example, mock scenarios
python3 scripts/gen_codes.py          # Swift codes + string catalog keys (--check in CI)
python3 scripts/check_strings.py      # DE + EN complete
cd core && python3 -m pytest          # unit + contract tests + fixture E2E of the Android/prepare path
                                      # (dev deps: pytest, jsonschema; the E2E builds threema-import with swiftc
                                      # unless TMCORE_IMPORTER is set, runs every process under sandbox-exec
                                      # (deny network*) and ends with the canary scan of DESIGN §10.2)
```
