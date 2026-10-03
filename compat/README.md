# compat/ — bundled allow-lists (schema core/schema/compat.v1.json)

- `ios.json` — iOS builds. v1 restores only on status `verified`. **24A437 is `verified` under the interim rule D6
  with `comment_code: acceptance_partial`:** the restore-set mechanism is proven by a no-op canary (2026-10-01), and
  an app run on the device (2026-10-03) passed every step and guard up to the send, where the device itself refused
  (Find My on, `MBError 211`) and nothing changed. A restore executed by the app itself is still pending.
- `threema-ios.json` — Threema iOS Core Data models (exact model hash; the engine never migrates).
- `android.json` — verified Android backup format versions.
- `records/ios/<build>.json` — device-run evidence, structural counts only: no identifiers, no counts or sizes from
  personal data (devtools/make_compat_record.py, to be added). Run kinds: `C0`, `C1a`, `C3`, `R1`, `R3` (COMPAT-POLICY.md
  §1.1) and `app_run` (an app build on the device; may stop before the restore).
Never fetched over the network; changes ship only with a new app release.
