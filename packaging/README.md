# packaging/ — from source to a signed DMG (owner: pack)

Builds `Chat Transfer for Threema.app` (arm64, macOS 14+) and the release DMG without an Apple Developer account (DESIGN §3, §11,
§14). Every script runs from any directory, writes only below the git-ignored `build/`, and never talks to an iPhone.
`make help` lists the wrappers.

| Step | Script | Output | Network |
|---|---|---|---|
| 1 | `fetch-python.sh [--offline]` | `build/python-runtime/python`: python-build-standalone per `python.lock` (URL + sha256 of the official release asset), trimmed (`test`, `idlelib`, `tkinter`, `ensurepip`, headers, …) | GitHub release asset, once (cached in `build/cache`) |
| 2 | `build-core.sh [--offline]` | `build/stage/Resources/core/`: runtime + wheels of `core/requirements.lock` (`pip --require-hashes --only-binary :all: --no-deps --target <runtime site-packages>`) + vendored single-module sdists (`sdist-vendor.lock`, no `setup.py` executed) + `tmcore` + `schema/`; universal binaries thinned to arm64; `.pyc` once (`compileall --invalidation-mode unchecked-hash`); tests, caches, `pip` removed; import test under `python3 -I -B` (`tests/check-core.sh`) | PyPI wheels, once (cached in `build/cache/wheels`) |
| 3 | `build-importer.sh` | `build/stage/Resources/bin/threema-import` (`swift build -c release --arch arm64`; checks arm64-only, `minos 14.0`, `--version` = engine version), then `stage-resources.sh` | – |
| – | `stage-resources.sh` | `models/<id>/ThreemaData.momd` (every model of `compat/threema-ios.json`), `compat/*.json`, `legal/` (LICENSE, NOTICE, TRADEMARKS, MODEL.md, SOURCE.txt, THIRD_PARTY_LICENSES with the licence allow-list of `licenses.txt`), `bundle-manifest.json` (`tools/bundle_manifest.py`, read by `tmcore selftest`) | – |
| 4 | `build-app.sh [--prebuilt X.app]` | `build/dist/Chat Transfer for Threema.app`: `xcodegen` (`app/project.yml`) + `xcodebuild archive` Release/arm64, unsigned, no Hardened Runtime, no App Sandbox; `MARKETING_VERSION` = engine version without suffix, `CURRENT_PROJECT_VERSION` = `CFBundleVersion` (this folder), `TMEngineVersion` = full engine version; staged Resources merged into `Contents/Resources` | – |
| 5 | `sign-adhoc.sh [App]` | inside-out ad-hoc signature (see below) | – |
| 6 | `verify-bundle.sh [App] [--dmg F] [--quarantine]` | `codesign --verify --strict --deep`; every Mach-O signed + arm64-only; `minos`; Info.plist versions; forbidden content (data files, pip, tests, writable files); `tmcore version` + `selftest` from the bundle under an empty environment; bundle byte-identical before/after (tree digest); size budget (app ≤ 200 MB, DMG ≤ 90 MB); DMG layout; privacy (app and DMG): no login/host/full name or build path of this Mac, the repository account only inside repository links, no absolute rpath/library path outside `/usr/lib` and `/System`; DMG volume: no extended attribute on any item (`com.apple.provenance`), neutral PDF metadata, HFS+ dates in UTC (`tools/scan_dist.py`, `tools/neutralize_pdf.py --check`); `--quarantine`: copy with `com.apple.quarantine` in a path with spaces and umlauts | – |
| 7 | `make-dmg.sh [App]` | `build/dist/threema-chat-transfer-<version>.dmg` + `SHA256SUMS`: app, link „Programme“/Applications, `Zuerst lesen.pdf` + `Read me first.pdf` (typeset from `readme-first/{de,en}.md` by `tools/render_readme.swift`), background with the four Gatekeeper steps + disclaimer (`dmg/steps.json`, `tools/render_dmg_background.swift`, 1x + 2x HiDPI TIFF), layout `dmg/layout.json`; written by dmgbuild (hash-pinned in `build-tools.lock`, build-time only) as UDZO with `TZ=UTC`, from a staged copy without extended attributes (dmgbuild's ditto runs with `--noextattr`), file times and PDF dates = `SOURCE_DATE_EPOCH` (commit time of HEAD), neutral PDF `/Producer` (`tools/neutralize_pdf.py`); the DMG is ad-hoc signed. Build release DMGs from Terminal or CI: macOS tags every file written by a provenance-tracked app with `com.apple.provenance`, which cannot be removed afterwards (`verify-bundle.sh --dmg` fails) | dmgbuild wheels, once |
| release | `make-sbom.sh`, `make-source-bundle.sh [--offline]` | `build/dist/sbom.cdx.json` (CycloneDX 1.5, from the assembled app), `THIRD_PARTY_LICENSES.txt`, `source-bundle.tar.gz` (git archive of HEAD + sha256-pinned sdists of every GPL component, `source-bundle.lock` + `sdist-vendor.lock`, + runtime/model references; deterministic) | PyPI sdists, once |

`tests/smoke.sh [--offline]` runs steps 4–7 with a stub `.app` (one tiny arm64 executable) — the packaging chain is
testable without the Xcode project. `make dmg` runs everything with the real app.

## Signing without a team (DESIGN §14.1 step 5)

`codesign --force --sign - --timestamp=none` on every Mach-O (deepest path first: `libpython`, every `.so`/`.dylib`,
`python3.13`, `threema-import`, the app executable), then nested bundles, then — after `bundle-manifest.json` was
refreshed, because signing rewrites the Mach-O bytes — the app bundle itself. Never `--deep` when signing: it signs
in the wrong order and hides unsigned code (`--deep` is used only to *verify*).

| Choice | Why |
|---|---|
| ad-hoc (`-s -`) | no paid Apple Developer account (DESIGN, fixed decision); users open the app once with „Trotzdem öffnen“ (§14.3) |
| **no** Hardened Runtime (`--options runtime`) | without a Team ID, library validation would refuse every bundled `.so`/`.dylib` the interpreter loads (ad-hoc signatures have no team to match); without notarization it adds nothing for Gatekeeper |
| **no** App Sandbox, no entitlements | the sandbox would block `/var/run/usbmuxd`; the engine runs under its own `sandbox-exec` profile instead (`app/Resources/engine-sandbox.sb`, network denied except usbmuxd) plus the in-process socket guard |
| no timestamp | ad-hoc signatures cannot carry one |
| the bundle is never written at run time | `.pyc` compiled at build time with `unchecked-hash`, engine started with `-I -B`, `PYTHONDONTWRITEBYTECODE=1`; writing into the bundle would break the seal („ist beschädigt“). `verify-bundle.sh` proves it with a tree digest |

## Pins and inventories

| File | Content |
|---|---|
| `python.lock` | python-build-standalone release, file, URL, sha256 (cross-checked with the release's `SHA256SUMS`) |
| `../core/requirements.lock` | hash-pinned wheels (generated from `runtime-pins.txt` by `tools/lock_wheels.py`; co-owned with coreA) |
| `runtime-pins.txt` | the curated `--no-deps` list: exactly what the engine's import set loads (`tests/import_set.py`) |
| `sdist-vendor.lock` | single-module packages without a wheel (hexdump) |
| `source-bundle.lock` | sdists of the GPL components for the Corresponding Source (`tools/lock_sdists.py`) |
| `build-tools.lock` | dmgbuild + deps (build machine only, never bundled) |
| `licenses.txt` | SPDX expression per bundled distribution + runtime component, and the licence allow-list |
| `CFBundleVersion` | monotonic build number (DESIGN §14.2), bump with every release |

## What waits for other owners

- **app**: `build-app.sh` drives `app/project.yml` (scheme `ThreemaChatTransfer`; the bundle keeps the display name). The app must compare `hello.engine_version`
  with `TMEngineVersion` from its Info.plist (not `CFBundleShortVersionString`, which cannot carry `-dev`), see
  `devtools/CONTRACT-REQUESTS.md`. `tests/smoke.sh` keeps a stub app so packaging stays testable without Xcode.
- **demo screenshots**: `make demo-screenshots` needs the app's `ThreemaChatTransferUITests/DemoScreenshotTests`.
- **scenarios**: `make record-scenarios` / `check-scenarios` call coreB's `devtools/record_scenarios.py` once it exists.
