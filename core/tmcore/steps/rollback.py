# SPDX-License-Identifier: AGPL-3.0-or-later
"""rollback-threema (R1, DESIGN §8.5): Threema back to the state of the PRE backup with the no-op restore set.

  build   guard rollback_allowed: only after the FINAL restore, only for the verdict threema_only, only with the PRE
          backup of that restore, only once (E_GUARD_ROLLBACK_NOT_ALLOWED); guard rollback_window: the PRE backup is
          at most 6 h old (E_GUARD_ROLLBACK_WINDOW) -- the only built-in loosening of the 60 min freshness, decided
          by the engine, never by an option. Then lib.backup_pipeline restoreset --noop from the PRE backup
          (Threema domains unchanged + zero-length -wal/-shm, complete Home/CameraRoll/Keyboard) -> work/rollback,
          frozen.
  guards / send / reboot   exactly as `restore` (tmcore.steps.restore.send_set), with the 6 h window instead of
          the 60 min freshness.

Not for data / data_keychain / restore_state / setup_full verdicts: R1 uses the same mechanism as the restore that
caused them (DESIGN §8.5 R2). Afterwards the wizard runs S15-S19 again (POST backup + postcheck).
Owner: coreB (devtools/OWNERSHIP.md).
"""
from __future__ import annotations

from tmcore.cli import Context, StepResult
from tmcore.guards import report
from tmcore.guards import rollback as g_rollback
from tmcore.protocol import EngineError
from tmcore.steps import iphone as I
from tmcore.steps.restore import SendPlan, send_set


def rollback_threema(ctx: Context) -> StepResult:
    """rollback-threema -> like restore."""
    pw = ctx.secrets.require("backup_password")
    st = ctx.session.engine_state()
    paths = I.Paths(ctx.session)
    pre = st.get("pre") or {}
    rest = st.get("restore") or {}
    verdict = (st.get("postcheck") or {}).get("verdict")
    pre_dev = paths.backup_dev("pre")
    ctx.proto.phase("build", 1, 4)
    report(ctx, g_rollback.allowed(last_kind=rest.get("kind"), verdict=verdict,
                                   pre_matches=bool(pre.get("backup_id")) and pre_dev is not None
                                   and rest.get("pre_backup_id") == pre.get("backup_id"),
                                   used=bool(st.get("rollback_used"))))
    started = I.parse_ts(pre.get("started_at")) or I.parse_ts(pre.get("finished_at"))
    report(ctx, g_rollback.window(age_min=I.minutes(I.now(ctx), started) if started else 10**6))
    ctx.proto.check_cancel()
    if pre_dev is None:                                   # unreachable: rollback_allowed needs the PRE backup
        raise EngineError("E_PROTOCOL", sub="pre_backup_missing")
    out = paths.rollbackset
    if out.exists():
        I.remove_tree(out)
    out.mkdir(mode=0o700, parents=True)
    try:
        rc, _summary = I.bp_run(ctx, "restoreset", pw, backup_udid_dir=str(pre_dev), out_root=str(out), noop=True,
                                report=None)
    except Exception as e:  # noqa: BLE001 -- PipelineError & friends: no set, nothing sent
        ctx.proto.check("restoreset", "fail", "E_GUARD_SET_INTEGRITY", sub="structure")
        raise EngineError("E_GUARD_SET_INTEGRITY", sub="structure") from e
    if rc != 0 or not (out / pre_dev.name / "Manifest.db").is_file():
        ctx.proto.check("restoreset", "fail", "E_GUARD_SET_INTEGRITY", sub="structure")
        raise EngineError("E_GUARD_SET_INTEGRITY", sub="structure")
    I.freeze(out)
    ctx.proto.check("restoreset", "pass", noop=True)
    plan = SendPlan("rollback", out, pre_dev, pre, g_rollback.WINDOW_MIN, "rollback_window", "E_GUARD_ROLLBACK_WINDOW",
                    phase_offset=1, phase_count=4)
    return send_set(ctx, plan, pw)
