# Security

Chat Transfer for Threema is an unofficial project; please do not send reports about this app to Threema AG or Apple.

## Reporting a vulnerability

Please report security problems **privately** through GitHub: *Security → Report a vulnerability* (private
security advisory) on this repository,
<https://github.com/Toubster/threema-chat-transfer/security/advisories/new>. Do not open a public issue, and never attach backups, logs with personal
data, screenshots of chats or device identifiers. A diagnostic report saved by the app contains codes and counts
only and may be attached.

We aim to acknowledge reports within 7 days. There is no bug bounty.

## Security model in short

- **Offline.** The app and its engine (`tmcore`) open no network connection: an in-process socket guard allows only
  the local usbmuxd Unix socket, and the engine runs under a `sandbox-exec` profile that denies network access.
  No telemetry, no crash reporter, no update check.
- **Guards in the engine, no overrides.** Every safety check (iOS build allow-list, Threema model, airplane mode,
  backup freshness, Find My, photo-library change, identity match, restore-set integrity, …) runs in `tmcore`. Neither
  the app nor the shipped command line has an option to skip a guard.
- **One restore, fixed flags.** The device receives exactly one restore of a frozen, verified restore set.
- **Secrets on stdin only.** Passwords are passed as one JSON line on stdin, kept in memory, never written to files,
  logs, reports, argv or the environment.
- **No personal data in outputs.** Engine events, reports and diagnostic reports carry codes, counts, public version
  numbers and salted hashes only; the debug log is redacted and never part of a diagnostic report.
- **Local data hygiene.** The session folder is `0700`, excluded from Time Machine and Spotlight; the app offers to
  delete readable chat copies at the end and warns when FileVault is off.
- **Supply chain.** Pinned dependencies with hashes, pinned Python runtime (sha256), ad-hoc signed bundle, release
  checksums signed offline with minisign, build provenance attestation, draft releases only from CI.
- **Repository hygiene.** `scripts/scrub_check.py` blocks real identifiers, home paths, secrets and unreviewed
  images (layer 1 in CI and hooks, layer 2 with a private deny list before every push).

Details: `docs/SECURITY-MODEL.md` and `docs/PRIVACY.md`.

## Supported versions

Only the latest release is supported. Betas (0.9.x) are pre-releases: see the beta box in the README for what is
proven on a real iPhone and who should wait. Older development builds (0.3.x and earlier) were never released and are
not supported.
