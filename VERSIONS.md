# Versions

One number for app, `tmcore` and importer (`engine_version` = app version = importer version, DESIGN §5.7).
`CFBundleVersion` increases monotonically. Every tag gets a row with its commit (12-character short hash) so going back
needs no searching. A commit cannot name its own hash: the row of a release commit names its tag.

The public history starts with the first public beta. Development before it (0.3.x, under a working title) happened
in a private repository and is not part of this history; `CHANGELOG.md` describes those versions.

| Version | Date | Commit | Core (`core/`) | App (`app/`) | Importer (`importer/`) | Model | CFBundleVersion | Behaviour |
|---|---|---|---|---|---|---|---|---|
| 0.9.0-beta.1 | 2026-10-03 | initial public commit, tag `v0.9.0-beta.1` | version 0.9.0-beta.1, pyproject 0.9.0b1 | rename to "Chat Transfer for Threema": bundle, module `ThreemaChatTransfer`, bundle id `org.threema-chat-transfer.app`, Application Support folder, keychain item; S00 unofficial line; passcode hint S11/S12/S18; S14 rewritten + "nothing new" case; `MARKETING_VERSION` 0.9.0, `TM_ENGINE_VERSION` 0.9.0-beta.1, `CURRENT_PROJECT_VERSION` 4; demo screenshots regenerated, sRGB without display profile; `mark_png.py` + `scrub_check.py` rule `image_icc_profile` | version 0.9.0-beta.1; no rpath into the build toolchain | V56 @ threema-ios `35b749536bbb` | 4 (`packaging/CFBundleVersion`) | **behaviour**: app texts/layout, name, paths and bundle id; DMG `threema-chat-transfer-0.9.0-beta.1.dmg`; `compat/ios.json` 24A437 `verified` → comment `acceptance_partial`, `min_app` 0.9.0-beta.1, record + `app_run` 2026-10-03; S10b decision screen ("Erneut prüfen" → S10a after "Alle Einstellungen zurücksetzen"), `encryption-enable` drops a PRE backup rejected by the password check; restore set, guards, verdicts unchanged; DMG without extended attributes, UTC volume dates, neutral PDF metadata |

Planned (DESIGN §16): 0.3.0 `tmcore` · 0.4.0 SwiftUI app · 0.5.0 integration + package · 0.9.x beta · 1.0.0 release.
