# importer/ — threema-import (owner: pack)

Swift Core Data importer, `arm64-apple-macosx14.0`: reads the engine's `normalized.sqlite` (Android backup,
normalized by `tmcore`) and writes a **copy** of the iPhone's Threema store with the history added (model V56). Never
touches `--store-in`; writes a self-contained store (`journal_mode=DELETE`, no `-wal`/`-shm`) to `--store-out`.
stdout and the report contain counts only.

```
swift build -c release --arch arm64 --product threema-import      # packaging/build-importer.sh does this + checks
threema-import --version                                           # {"importer_version": "...", "mappings": ["v56"]}
threema-import --normalized F --work-dir D --store-in DIR --store-out DIR --momd PATH [--report F] [--dry-run] ...
threema-import verify --store DIR --momd PATH [--load-media] [--report F]
```

- **No overrides** (DESIGN §2.3): a target store with duplicate 1:1 conversations (`error_code` `duplicate_1to1` →
  `E_IMPORT_DUPLICATE_CHAT`) or an incompatible model (`model_incompatible` → `E_THREEMA_MODEL_UNKNOWN`) always
  aborts with exit 1; the old `--allow-duplicate-1to1` of the proof of concept is gone. `--own-identity` exists only
  for the maintainer's simulator harness; the engine never passes it.
- **Version**: `Sources/ThreemaImport/Version.swift` = `core/tmcore/__init__.py` `__version__` (one number,
  DESIGN §5.7); the check suite and `build-importer.sh` fail on a difference.
- **Models**: `importerMappings` must cover every `importer_mapping` of `compat/threema-ios.json`.

## Check suite

```
python3 importer/Tests/run_checks.py [--no-build] [--bin PATH]          # make test-importer
```

End-to-end checks of the release binary against synthetic inputs only (Python 3.13 with Pillow; ffmpeg optional),
all generated under `build/importer-checks/`: fake `ZZ…` identities and media (`Tests/Fixtures/`), empty V56/V55
stores from `model/V56` (`Tests/Tools/MakeEmptyStore`), „Safe-restored“ target stores (`SeedStore`, `SafeSeed`).
Scenarios: full import with row checks and Core Data verify, idempotency, existing data wins (WAL store), dry-run,
master-data-only, incompatible store aborts, simulator identity, retention, review fixes, duplicate-chat STOP without
override, Safe-shaped store + the engine's independent `verify_import`, version. The last output line is JSON
`{"pass": n, "fail": m}`; exit 0 only without failures. The test tools are package products but never bundled.

The suite runs as a script, not XCTest: it needs fixture generators in Python and the engine's `verify_import.py`,
and it tests the exact release binary that gets bundled (`--bin`).
