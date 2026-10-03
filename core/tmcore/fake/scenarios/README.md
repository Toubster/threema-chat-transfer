# Fake-device scenarios (owner: coreB)

One `<name>.json` per scenario of DESIGN §13.3 (plus `wrong_android_password` and `threema_problem_reported`, the S17
"Problem" path of docs/REVIEW.md M1), names identical to
`app/Tests/Scenarios/<name>.jsonl`. A file lists only what differs from the defaults in `tmcore/fake/scenario.py`:

- `device`: what the virtual iPhone is (build, encryption and its password — `"canary"` = the canary password of
  `fixtures/canaries.json` —, battery, free space, Find My live answer, managed, Threema variant/model/store,
  airplane mode, photo sizes, device-watch sequence);
- `behaviour`: what it does (`drop_first_backup`, `find_my_on` = MBError 211, `dcim_change_before_send`,
  `restore_end` link_lost | clean | link_lost_early | crash, `clock_skip_min`, `effects[]`, `damage[]`);
- `flow`: how the recorded wizard run behaves (`core/tests/e2e/flow.py`): `android`, `backup_password`,
  `buddy_answer` (S16), `threema_answer` (S17 of the first check: `ok` | `problem`), `after_postcheck`;
- `expect`, `expect_screen`: what the run must end with (checked by `core/tests/e2e/test_scenarios.py`).

The compat list of a fake run comes from the scenario (`compat_ios`, default: 24A437 verified with the four expected
notes); the shipped engine has no switch for it.
