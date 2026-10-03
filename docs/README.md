# docs/ (owner: docs)

| Document | For | Content |
|---|---|---|
| [user/de/README.md](user/de/README.md) · [user/en/README.md](user/en/README.md) | users | full guide (DESIGN §15): overview, requirements, "Open Anyway", step by step, after the move, if something goes wrong, FAQ, privacy, uninstall, advanced |
| [PRIVACY.md](PRIVACY.md) | users, reviewers | what is processed and stored where, diagnostic report, technical notes (DE + EN) |
| [RESTORE-MECHANISM.md](RESTORE-MECHANISM.md) | reviewers, developers | the iOS 27 restore change (public ipsw-diff strings), the restore set, fixed options, gate, evidence status |
| [SECURITY-MODEL.md](SECURITY-MODEL.md) | reviewers | assets, threats, principles, trust boundaries, every guard, secrets, offline enforcement, supply chain |
| [COMPAT-POLICY.md](COMPAT-POLICY.md) | users, maintainers | which iOS builds, Threema models and Android formats are allowed, and how they get in |
| [MAINTAINER.md](MAINTAINER.md) | maintainers | hard rules, local setup, CI, per-build canary procedure, compat records, simulator harness, release, screenshots |
| [ENGINE-PROTOCOL.md](ENGINE-PROTOCOL.md) | developers | the engine contract, protocol v1 (owner: coreA, kept in sync with `core/schema/`) |
| [images/screenshots.json](images/screenshots.json) | everyone producing pictures | the one list of screenshot names (app via demo mode, Gatekeeper via VMs, device photos) |

Rules for this folder:

- Example numbers only from the canonical set (DESIGN §10.4): 18 chats, 4 groups, 12 345 messages, 1 234 media,
  2 polls, 6.4 GB, iOS 27.0 (24A437), Threema 7.4, device "iPhone (model)" without a name, Threema IDs `ZZ…`. The scrub
  check flags other counts in `docs/`.
- Images: only demo-mode screenshots (`tm-demo=1`) or reviewed device photos (`tm-reviewed=1`), named as in
  `images/screenshots.json` and stored as `images/<group>/<lang>/<id>.png`. Until the demo mode exists, the guides
  reference the planned files.
- User-facing pages use the product name "Chat Transfer for Threema" literally; UI strings use `{App}`. The first
  mention on a page says "unofficial" (TRADEMARKS.md).
- Menu paths marked † are not yet checked on a device in both languages (DESIGN §8.2).
- Check before committing: `python3 docs/tools/check_docs.py` and `make scrub`.
- Still to write: a public rewrite of the design document (`DESIGN.md`, without names).
