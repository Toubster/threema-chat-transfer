# fixtures/ — synthetic test data only (owner: coreB)

- `gen_android_backup.py` — synthetic Threema Android data backup (format v27), IDs `ZZ…`.
- `gen_ios_backup.py` — synthetic encrypted iOS backup (Home, CameraRoll, Keyboard, Keychain, Threema domains).
- `build_normalizer_fixture.py` — Android backup pair for the normalizer tests.
- `canaries.json` — canary values for the privacy scan (DESIGN §10.2); the only file where the password rule of the
  scrub check is waived.
Assembled from the proof of concept; porting notes in `devtools/OWNERSHIP.md`.
