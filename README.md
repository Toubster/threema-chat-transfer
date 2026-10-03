# Chat Transfer for Threema

**Move your Threema chat history from an Android data backup to your iPhone — without erasing the iPhone.**
A free, open-source app for the Mac (Apple silicon, macOS 14 or later), in German and English.
**Unofficial: not made by Threema AG**, and neither Threema nor Apple supports it.

> [!WARNING]
> **Beta (0.9.0-beta.1) — please read this box before you try it.**
>
> **Proven on a real iPhone (iOS 27.0, build 24A437):**
> - *How the transfer reaches the iPhone:* a test restore that left everything unchanged ("no-op canary") was
>   accepted by the iPhone; afterwards Threema, settings and photos were as before.
> - *The app's whole flow up to the transfer:* reading the Android backup, backing up the iPhone, preparing the
>   transfer and all 15 safety checks passed. The iPhone then refused the transfer itself because Find My was still
>   on — and nothing on the iPhone was changed.
>
> **Not proven yet:** a transfer that the app itself carries out to the end on a real iPhone.
>
> **Please wait for a later version if** you rely on this iPhone every day and cannot do without it for an evening,
> if you have no fresh Apple backup (iCloud or Finder) and do not know how to restore one, or if you are not
> comfortable with beta software. Other iOS versions are refused by the app anyway.

Deutsch: [README.de.md](README.de.md) · Full user guide: [English](docs/user/en/README.md) ·
[Deutsch](docs/user/de/README.md)

## What it does

You switched from Android to iPhone and want your old Threema chats on the iPhone. Threema Safe moves your Threema ID
and contacts, but not the chat history. Chat Transfer for Threema fills that gap:

1. You make a data backup in Threema on the Android phone and copy the file to your Mac.
2. You set up Threema on the iPhone with Threema Safe, as usual.
3. Chat Transfer for Threema makes a backup of the iPhone, adds your old messages, pictures, voice messages, files and polls to a copy
   of the iPhone's Threema data, checks everything twice and sends it back to the iPhone in one restore.
4. After the iPhone restarts, Chat Transfer for Threema makes a second backup and compares it with the first, to confirm that nothing
   other than Threema changed.

Everything happens on your Mac, connected to the iPhone with a USB cable. **Your chats are never sent to the
internet.** The app opens no network connection at all; this is enforced technically.

## What it does not do

- It is **not** made by Threema or Apple, and neither of them supports it.
- It does not move your Threema ID or contacts (that is Threema Safe's job).
- It does not erase your iPhone or set it up as new.
- It does not work with Threema Work or OnPrem, with iPhones managed by a company or school, on Intel Macs or Windows,
  or from iPhone to Android.
- It does not transfer anything on an iOS version it has not tested on a device. It then stops before the iPhone is
  changed.
- It has no override switch. If a safety check fails, it stops; nobody can tell it to "continue anyway".
- It does not replace an Apple backup. You make one on the way, as a safety net.

## What you need

- A Mac with Apple silicon (M1 or newer), macOS 14 or later, connected to power, with enough free space
  (the app tells you how much).
- Your iPhone on an iOS version that Chat Transfer for Threema has tested, with the regular Threema app.
- Your Android phone with Threema, and a USB cable for the iPhone.
- Your Threema Safe password, iPhone passcode and Apple Account password, and the password of the Android backup.
  If already turned on, also the password for iPhone backups on the Mac (see [FAQ](#frequently-asked-questions)).
- About 1½ to 3 hours. For 30 to 60 minutes of that, your iPhone is offline (airplane mode) and must not be used.
- Version 1 limits: at most 20 GB of photos stored on the iPhone; Find My must be turned off during the transfer.

## Risks — please read

At the end, Chat Transfer for Threema restores data to your iPhone. The iPhone then resets some areas to how they were a few minutes
earlier: **settings, notifications, photos stored on the iPhone, SMS/iMessage, call history, Wallet passes and
keyboard**. Because that backup is only minutes old, these areas look practically the same afterwards; the iPhone is
not erased and not reset. Anything you do on the iPhone during that time is lost — that is why it stays in airplane
mode.
Afterwards you sign in to your Apple Account again and add your Apple Pay cards again. Mail stored only "On My iPhone"
(POP, local folders) may be lost. Your other apps and saved passwords stay as they are.

Chat Transfer for Threema checks very carefully before and after, and stops whenever something is unclear. Some risk remains: iOS can
change how restores work, and software can have bugs. That is why you also make a backup with Apple (iCloud or
Finder) before the transfer, and the guide explains how to go back to it. How the restore works and why it is built
this way: [docs/RESTORE-MECHANISM.md](docs/RESTORE-MECHANISM.md).

## Download

Releases appear on the [Releases page](../../releases) as `threema-chat-transfer-X.Y.Z.dmg`, together with
`SHA256SUMS` (checksums). Versions 0.9.x are betas (see the box at the top); a minisign signature and a build provenance
attestation are planned from version 1.0.

## Opening the app the first time ("Open Anyway")

Chat Transfer for Threema is free and not registered with Apple (that costs 99 USD per year), so macOS cannot confirm the developer.
You allow it once:

1. Open `threema-chat-transfer-X.Y.Z.dmg` and drag Chat Transfer for Threema into the "Applications" folder. Do not start Chat Transfer for Threema directly from the
   DMG.
2. Open Chat Transfer for Threema in "Applications". macOS says *"Chat Transfer for Threema" Not Opened*. Click **Done** (not "Move to Trash").
3. Open System Settings → Privacy & Security and scroll down to "Security". Next to *"Chat Transfer for Threema" was blocked …* click
   **Open Anyway**.
4. Confirm with **Open Anyway** and your Mac password or Touch ID. This is only needed the first time.

On macOS 14 the old way also works: right-click the app → **Open** → **Open**.

If macOS says the app **"is damaged"**, the download is broken: download it again and compare the checksum. Never
paste commands from the internet into Terminal to "fix" an app. Pictures for every step:
[user guide, chapter 3](docs/user/en/README.md#3-installing).

## Frequently asked questions

**Which "password for iPhone backups" does the app mean?** A separate password only for backups of your iPhone on a
computer. It is **not** your iPhone passcode, **not** your Apple Account password, and has **nothing** to do with
iCloud backups: iCloud backups never ask for it. Chat Transfer for Threema needs encrypted backups on the Mac, because only those
contain all data.

**I never set such a password.** Then it is usually off, and Chat Transfer for Threema sets one up for you. You write it down, the
iPhone asks for its passcode to confirm, and nothing on the iPhone is deleted. The setting stays on afterwards; future
backups on a computer use this password.

**The app says it is already on, but I don't know it.** It was usually set some time ago in iTunes, Finder or another
iPhone program, often for an earlier iPhone: the setting can carry over when you move to a new iPhone. It may be in
your Mac's keychain (Keychain Access, search for "backup"). Step by step:
[user guide, "The iPhone backup password"](docs/user/en/README.md#the-iphone-backup-password).

## Privacy in short

Offline, no telemetry, no account. During the move, readable copies of your chats are in a private folder on your
Mac; the app offers to delete them at the end. A diagnostic report, if you choose to save one, contains codes and
counts only. Details: [docs/PRIVACY.md](docs/PRIVACY.md).

## For developers

- How the restore works: [docs/RESTORE-MECHANISM.md](docs/RESTORE-MECHANISM.md) · security model:
  [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md) · supported versions: [docs/COMPAT-POLICY.md](docs/COMPAT-POLICY.md)
- Engine contract: [docs/ENGINE-PROTOCOL.md](docs/ENGINE-PROTOCOL.md) · maintainer handbook:
  [docs/MAINTAINER.md](docs/MAINTAINER.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) (no real data, ever) · security reports: [SECURITY.md](SECURITY.md)
- Build from source: `git clone https://github.com/Toubster/threema-chat-transfer`, then `make app` (Xcode,
  XcodeGen; see [packaging/README.md](packaging/README.md)).

## Licence

Chat Transfer for Threema is free software under the **GNU Affero General Public License, version 3** (AGPL-3.0,
[LICENSE](LICENSE), [NOTICE](NOTICE)). In short:

- You may use it free of charge, read the source code, change it and pass it on.
- Whoever passes on the app or a changed version — or offers a changed version to others over a network — must do so
  under the same licence and publish the complete source code. Closed-source copies are not allowed.
- There is no warranty (AGPL-3.0 §§ 15–16). Use at your own risk.

Own source files are `AGPL-3.0-or-later`; the bundled Threema data model (`model/`) is `AGPL-3.0-only`. Why not a
"non-commercial only" licence: the app contains that Threema model (AGPL-3.0) and pymobiledevice3 (GPL-3.0), and their
licences forbid adding further restrictions such as "non-commercial use only" (GPL-3.0/AGPL-3.0 §§ 7 and 10). The
AGPL is the standard licence that keeps every copy and every changed version open.

## Disclaimer

Chat Transfer for Threema is an **unofficial**, independent open-source project. It is not affiliated with, endorsed
or reviewed by Threema AG, Apple Inc. or Google LLC. Threema is a trademark of Threema AG. Apple, iPhone, Finder,
iCloud and Apple Pay are trademarks of Apple Inc., registered in the U.S. and other countries; iOS is a trademark of
Cisco, used under licence. Android is a trademark of Google LLC. These names are used only to say what the app works
with. See [TRADEMARKS.md](TRADEMARKS.md).
