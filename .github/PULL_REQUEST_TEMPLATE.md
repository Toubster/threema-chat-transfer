## What and why

<!-- One or two sentences. Link the issue. -->

## Version

<!-- Which version would this go into (VERSIONS.md), and does it change behaviour for users or only lie underneath? -->

## Checklist

- [ ] **No real data**: no backups, chat content, names, Threema IDs (only `ZZ…`), phone numbers, e-mail addresses,
      device names, UDIDs/ECIDs/serial numbers, home-folder paths; only the canonical example values
      (18 chats, 4 groups, 12 345 messages, 1 234 media, 2 polls, 6.4 GB, iOS 27.0 (24A437), Threema 7.4)
- [ ] `make scrub` passes (hooks installed: `git config core.hooksPath scripts/hooks`)
- [ ] Images only from the demo mode (`tm-demo=1`) or reviewed device photos (`tm-reviewed=1`)
- [ ] **No override**: nothing that skips or weakens a guard (`--allow-*`, `--force`, waivers, environment switches)
- [ ] Contract unchanged, or: optional field / new code with DE + EN texts, `scripts/gen_codes.py` and
      `scripts/check_strings.py` run, `make validate` passes; incompatible change = new protocol version (maintainer)
- [ ] Tests added or updated; `make test` passes; no test talks to a real device
- [ ] German texts use "Sie"; the product name is `{App}` in UI strings
- [ ] Docs updated if users see a difference (`docs/user/de` **and** `docs/user/en`; `python3 docs/tools/check_docs.py`)
- [ ] CHANGELOG line (DE + EN) if users notice the change
- [ ] Commits signed off (`git commit -s`, DCO)
