# app/ — SwiftUI wizard (owner: app)

SwiftUI app for macOS 14+ (arm64, ad-hoc signed, no team): screens S00–S23 and the F-screens of DESIGN §8,
`WizardStore` (state machine, back-lock, cancel/quit rules, freshness countdown, resume), `EngineClient`
(`LiveEngine` + `MockEngine`), session folder, power assertion, keychain. **No safety logic here** — every check
lives in `core/tmcore`; the app shows what the engine decided and maps codes to screens via `codes.v1.json`.

## Build and test

```sh
brew install xcodegen                 # once
app/scripts/build.sh                  # xcodegen + xcodebuild (Debug, arm64, CODE_SIGN_IDENTITY=-)
app/scripts/test.sh unit              # unit + snapshot/size tests (MockEngine only)
app/scripts/test.sh ui                # XCUITests: every scenario in DE + EN (needs macOS UI automation, see below)
```

The `.xcodeproj` is generated from `project.yml` and never committed. Snapshot PNGs of the size checks land in
`build/app/snapshots/<lang>/<light|dark>/<screen>.png` for review (not committed; no `tm-demo` marker).

XCUITests need macOS "automation mode". GitHub's macOS runners have it; on a workstation macOS asks for an
administrator once (or `automationmodetool`). The unit target replays every scenario through the same directives
against the store, so the flows are covered without UI automation too.

## Runtime switches (`AppEnvironment`)

| Variable | Meaning |
|---|---|
| `TM_ENGINE` | `live` (default), `fake:<scenario>` (real engine + virtual iPhone, demo), `mock:<scenario>` (replays `Tests/Scenarios/<scenario>.jsonl`) |
| `TM_SCENARIOS_DIR` | folder with the `.jsonl` files (default: the copy bundled as `Resources/Scenarios`) |
| `TM_MOCK_SPEED` | `instant`, `fast`, `demo` (default) |
| `TM_SESSIONS_DIR` | sessions root; default `~/Library/Application Support/Chat Transfer for Threema/sessions`, demo runs use `…/demo-sessions` |
| `TM_LANG` | `de` / `en` (default: macOS language; switch on S00) |
| `TM_DEMO` | `1`: DEMO watermark (always on for `mock:`/`fake:`) |
| `TM_DEMO_RESOURCES` | staged `Resources/` with the real engine, only for `fake:` (e.g. a Debug build without packaging) |
| `TM_DEMO_FIXTURES` | `fixtures/` of a checkout, only for `fake:`: passed to the engine as `TMCORE_FIXTURES` (the virtual iPhone's backup generator) |
| `TM_AUTOPILOT` | `1`: the demo autopilot plays the scenario's user directives and saves screenshots (`TM_SCREENSHOT_DIR`, `TM_SCREENSHOT_IDS`, `TM_SCREENSHOT_SIZE`, `TM_AUTOPILOT_ANDROID_FILES`, `TM_AUTOPILOT_REPORT`); refuses the live engine. Driver: `devtools/demo_screenshots.py` |
| `TM_APPEARANCE` | `light` / `dark` for demo runs (screenshots) |
| `TM_PYTHON` | Debug builds only: interpreter for `live` from a source checkout |

Every demo run (`mock:`/`fake:`) shows the DEMO watermark, uses an in-memory keychain and its own sessions folder.

## Engine bundle layout the app expects (P5, pack)

`LiveEngine` starts `<App>/Contents/Resources/core/python/bin/python3 -I -B -m tmcore <command> …` with the
environment of ENGINE-PROTOCOL §1 (nothing inherited), secrets as one stdin line, stdout parsed line by line and
copied to `<session>/logs/events.jsonl`, stderr to `<session>/logs/debug.log`. When `/usr/bin/sandbox-exec` exists
it runs under `Resources/engine-sandbox.sb` (no network except usbmuxd). The About window shows
`Resources/legal/NOTICE` when the bundle has it.

## Files

- Generated, do not edit: `Sources/Engine/Codes.generated.swift` and the `code.*`/`action.*` keys of
  `Resources/Localizable.xcstrings` (`python3 scripts/gen_codes.py`). All other keys belong to the app.
- Mock scenarios: `Tests/Scenarios/*.jsonl` (content: coreB; format: `docs/ENGINE-PROTOCOL.md` §9).
- Illustrations are SF Symbols in tinted squares (`Illustration`), no device photos.
