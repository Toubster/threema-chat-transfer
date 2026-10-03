# Contributing

Thank you for helping! Please read this first — the project handles private chat histories and modifies iPhones.

## Ground rules

1. **No real data, ever.** Never commit or attach real backups, chat content, names, Threema IDs, phone numbers,
   e-mail addresses, device names, UDIDs/ECIDs/serial numbers, home-folder paths or screenshots of real devices.
   Use the synthetic fixtures (`fixtures/`) and the canonical example values: 18 chats, 4 groups, 12 345 messages,
   1 234 media, 2 polls, 6.4 GB, iOS 27.0 (24A437), Threema 7.4, device "iPhone (model)" without a name, Threema IDs
   starting with `ZZ`.
2. **Scrub check must pass.** Install the hooks once: `git config core.hooksPath scripts/hooks`. The pre-commit hook
   runs `scripts/scrub_check.py --staged`; CI runs it on every push and pull request. Screenshots must come from the
   app's demo mode (marker `tm-demo=1`).
3. **No overrides.** Pull requests that add a way to skip or weaken a safety guard (`--allow-*`, `--force`,
   waivers, environment switches) will not be merged. Experts have the source code.
4. **Contract first.** The engine protocol is frozen in `core/schema/` (see `docs/ENGINE-PROTOCOL.md`). Adding an
   optional field is compatible; anything else raises the protocol version. New codes go into
   `core/schema/codes.v1.json` with German and English texts, then run `python3 scripts/gen_codes.py` and
   `python3 scripts/check_strings.py`.
5. **Language.** German UI texts use the formal "Sie" (as Threema does); English uses a neutral "you". The product
   name is always the placeholder `{App}`.
6. **Licence headers.** Every new source file starts with `SPDX-License-Identifier: AGPL-3.0-or-later`
   (files under `model/` are AGPL-3.0-only).

## Developer Certificate of Origin

All commits must be signed off (`git commit -s`), certifying the
[Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote the change or have the right to
submit it under this project's licence.

## Before you open a pull request

```
make scrub          # privacy scan (layer 1; layer 2 runs only for the maintainer)
make validate       # schemas, code catalog, compat lists, mock scenarios
make strings        # DE + EN strings complete
make test           # core tests
```

Real-device testing happens only on the maintainer's dedicated test hardware, never in CI.
