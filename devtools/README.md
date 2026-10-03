# devtools/ — maintainer only

- `OWNERSHIP.md` — who owns which module in the next phase.
- `draft_scenarios.py` — writes the draft mock scenarios (replaced by recordings).
- `sim-harness/` — simulator gallery and store diff (needs Xcode and a Threema build from source).
- `record_scenarios.py` — records the mock scenarios from the real engine on the virtual iPhone (`make record-scenarios`,
  `make check-scenarios`).
- `demo_screenshots.py` — the signed app in demo mode (real engine, virtual iPhone, demo autopilot) end to end; writes
  the user-guide screenshots (`make demo-screenshots`, `INSTALL=1` puts them into `docs/images/app/`).
- `synthetic_prepare_run.py` — the bundled engine without `--fake-device` on synthetic data: Android fixture →
  `android-normalize` → synthetic PRE backup → `prepare` → independent `verify --restoreset`, with privacy checks.
- To be added: `device-run.md`, `make_compat_record.py`. The layer-2 deny list generator lives in the private
  maintainer repository (it reads private data).
