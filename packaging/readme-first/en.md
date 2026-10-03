<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- Source of "Read me first.pdf" in the DMG (packaging/make-dmg.sh, DESIGN §14.3). Placeholders: {App}, {version}, {repo_url}. -->
# Opening {App} – read me first

{App} {version} · for Threema chats from Android to iPhone

## How to open {App} the first time

1. Drag {App} into the "Applications" folder. Do not start {App} directly from this window.
2. Open {App} in "Applications". macOS says "{App}" Not Opened. Click **Done** (not "Move to Trash").
3. Open System Settings → Privacy & Security and scroll down to "Security". Next to "{App}" was blocked … click **Open Anyway**.
4. Confirm with **Open Anyway** and your Mac password or Touch ID. This is only needed the first time.

On macOS 14 the older way also works: in "Applications", right-click {App} → "Open" → "Open".

## Why this warning?

{App} is free and not registered with Apple (that costs 99 USD per year), so macOS cannot confirm the developer. The source code is public on GitHub, and every version has a checksum.

## If macOS says the app "is damaged"

The download is broken. Please download {App} again and compare the checksum with the file "SHA256SUMS" on the release page: {repo_url}

## What you need

- a Mac with Apple silicon and macOS 14 or later, connected to power
- the Android phone with Threema, your iPhone and a USB cable
- your Threema Safe password, your iPhone passcode and your Apple Account password
- about 1½ to 3 hours; for 30 to 60 minutes of that your iPhone is offline

## Beta

Versions 0.9.x are betas. Proven on a real iPhone (iOS 27.0, 24A437): the restore mechanism (a test restore that changed nothing) and the app's whole flow up to the transfer. Not proven yet: a transfer carried out to the end by the app itself. If you depend on your iPhone every day or have no fresh Apple backup, please wait for a later version.

## Notice

{App} is an unofficial, independent open-source project. It is not affiliated with, endorsed or reviewed by Threema AG, Apple Inc. or Google LLC. Threema is a trademark of Threema AG. Apple, iPhone, Finder, iCloud and Apple Pay are trademarks of Apple Inc.; iOS is a trademark of Cisco, used under licence. Android is a trademark of Google LLC. These names are used only to describe compatibility. Licence: AGPL-3.0. Use at your own risk, without warranty (AGPL-3.0 §§ 15–16).
