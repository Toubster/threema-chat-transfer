# SPDX-License-Identifier: AGPL-3.0-or-later
"""restore (S14/S15): every guard of DESIGN §6.1 in a fixed order, then EXACTLY ONE
Mobilebackup2Service.restore(system=True, reboot=True, copy=False, settings=False, remove=False, skip_apps=True)
of the frozen restore set, inside `critical`. There is no override of any kind (DESIGN §6.1, §19).

Guard order (fail-fast; any failure = nothing is sent, the iPhone is unchanged):
  guards  1 freshness        PRE backup <= 60 min old (engine clock)                       E_GUARD_FRESHNESS
          2 set_integrity    layout, marker, source, plists, structure, verify (frozen set == what prepare built
                             from THIS session's PRE backup; Threema + complete Home/CameraRoll/Keyboard only)
                                                                                           E_GUARD_SET_INTEGRITY
          3 airplane         radios.plist AirplaneMode = true in the PRE backup             E_GUARD_AIRPLANE
          -- one lockdown connection from here to the send --
          4 device           same iPhone as the session (hash) and as the set (UDID)        E_DEV_OTHER
          5 compat_ios       build verified in compat/ios.json                              E_IOS_UNKNOWN/BLOCKED
          6 ios_unchanged    build == build of the PRE backup                               E_IOS_CHANGED
          7 managed          not supervised / no MDM                                        E_DEV_MANAGED
          8 battery, power   iPhone >= 50 % or charging; Mac on AC (real runs)              E_DEV_BATTERY / E_HOST_POWER
          9 findmy           live lockdown com.apple.fmip (unknown = warn; MBError 211 below is the hard stop)
         10 iphone_space     min(TotalDataAvailable, AmountDataAvailable) >= 1.5 x payload  E_GUARD_IPHONE_SPACE
         11 dcim_unchanged   AFC /DCIM listing == CameraRollDomain DCIM rows of PRE          E_GUARD_DCIM_CHANGED
         12 freshness        again, right before the first byte
         13 set_integrity    'unchanged': sha256 of Manifest.db / marker / report as checked
  send    critical on; engine.json restore_sent_at is written BEFORE the first byte (a crash resumes at S16)
  reboot  the device reboots by itself after the commit; the link drop at 100 % is the normal end

Results: R_RESTORE_SENT_LINK_LOST (normal) | R_OK (device answered after the commit) | E_GUARD_FINDMY
(MBError 211 before staging, nothing changed) | E_RESTORE_NOT_STARTED (refused before staging) |
E_RESTORE_INTERRUPTED (link lost while sending; device_modified unknown) | E_RESTORE_DEVICE_ERROR (after sending).

`send_set()` is shared with rollback-threema (R1), which sends the no-op set under the 6 h rollback window.
Owner: coreB (devtools/OWNERSHIP.md).
"""
from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import NoReturn

from tmcore.cli import Context, StepResult
from tmcore.guards import GuardResult, report
from tmcore.guards import airplane as g_airplane
from tmcore.guards import battery as g_battery
from tmcore.guards import compat as g_compat
from tmcore.guards import dcim as g_dcim
from tmcore.guards import device as g_device
from tmcore.guards import facts as F
from tmcore.guards import findmy as g_findmy
from tmcore.guards import freshness as g_fresh
from tmcore.guards import managed as g_managed
from tmcore.guards import setintegrity as g_set
from tmcore.guards import space as g_space
from tmcore.protocol import EngineError
from tmcore.steps import iphone as I

SENT_AT_100 = 99.5                     # progress at which "everything was sent" (the device reboots afterwards)
RETRY_AFTER = ("E_RESTORE_INTERRUPTED",)   # a restore may be repeated after these (same frozen set, still fresh)


@dataclasses.dataclass
class SendPlan:
    kind: str                          # final | rollback
    set_root: Path                     # work/restoreset or work/rollback
    pre_dev: Path                      # ios/pre/<UDID>
    pre: dict                          # engine.json pre
    limit_min: int                     # freshness limit (60) or rollback window (360)
    fresh_id: str                      # check id: freshness | rollback_window
    fresh_code: str                    # E_GUARD_FRESHNESS | E_GUARD_ROLLBACK_WINDOW
    phase_offset: int = 0              # rollback-threema has a 'build' phase first
    phase_count: int = 3


def _age_min(ctx: Context, pre: dict) -> int:
    started = I.parse_ts(pre.get("started_at")) or I.parse_ts(pre.get("finished_at"))
    if started is None:
        raise EngineError("E_PROTOCOL", sub="pre_backup_missing")
    return I.minutes(I.now(ctx), started)


def _fresh(ctx: Context, plan: SendPlan) -> GuardResult:
    return g_fresh.check(age_min=_age_min(ctx, plan.pre), limit_min=plan.limit_min, check_id=plan.fresh_id,
                         code=plan.fresh_code)


def _progress(ctx: Context, total: int):
    state = {"pct": 0.0, "t": 0.0, "sent": False, "called": False}

    def cb(pct) -> None:
        try:
            p = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            return
        state["sent"] = True
        now = time.monotonic()
        if p >= 100.0 or p - state["pct"] >= 2.0 or (now - state["t"] >= 2.0 and p > state["pct"]):
            ctx.proto.progress("send", int(total * p / 100.0), total, unit="bytes")
            state["t"] = now
        state["pct"] = max(state["pct"], p)
    return cb, state


def _record(ctx: Context, **fields) -> None:
    with ctx.session.update_engine() as st:
        rec = dict(st.get("restore") or {})
        rec.update(fields)
        st["restore"] = rec


def send_set(ctx: Context, plan: SendPlan, pw: str) -> StepResult:
    """Guards 1-13, then exactly one restore of plan.set_root. Returns the step result or raises EngineError."""
    P = ctx.proto
    est = ctx.session.engine_state()
    P.phase("guards", plan.phase_offset + 1, plan.phase_count)

    # -- offline guards ------------------------------------------------------------------------------------------
    report(ctx, _fresh(ctx, plan))
    P.check_cancel()
    snap = g_set.check(ctx, plan.set_root, plan.pre_dev, pw)
    if isinstance(snap, GuardResult):
        report(ctx, snap)
        raise EngineError("E_GUARD_SET_INTEGRITY", sub="layout")      # unreachable: report() raised
    report(ctx, snap.result())
    if snap.noop != (plan.kind == "rollback"):
        report(ctx, g_set.fail("marker"))                              # a final never sends the no-op set and back
    P.check_cancel()
    view = F.open_view(plan.pre_dev, pw)
    if view is None:
        P.check("password", "fail", "E_BACKUP_PASSWORD", attempt=1, max=I.PASSWORD_ATTEMPTS)
        raise EngineError("E_BACKUP_PASSWORD", attempt=1, max=I.PASSWORD_ATTEMPTS)
    report(ctx, g_airplane.check(airplane=F.airplane(view)))
    pre_dcim = F.dcim_rows(view)
    del view
    P.check_cancel()

    # -- device guards, one connection up to the send ------------------------------------------------------------
    gw = I.gateway(ctx)
    try:
        udid, _seen = I.select_device(ctx, gw, expect=est.get("device"))
    except I.DeviceError as e:
        raise I.engine_error(e) from None
    total = int(snap.payload_bytes)
    cb, st = _progress(ctx, total)
    started_at = I.iso(I.now(ctx))
    try:
        with gw.open(udid) as dev:
            f = I.read_facts(dev)
            report(ctx, g_device.check(session_device=est.get("device"), connected=ctx.session.hasher.h(udid)))
            if udid != snap.udid:
                report(ctx, GuardResult("device", "fail", "E_DEV_OTHER", error_data={}))
            report(ctx, g_compat.check(stage=I.compat_stage(ctx, f.ios_build), ios_version=f.ios_version,
                                       ios_build=f.ios_build))
            report(ctx, g_compat.ios_unchanged(device_build=f.ios_build,
                                               pre_build=plan.pre.get("ios_build") or snap.lockdown.get("BuildVersion")))
            report(ctx, g_managed.check(managed=f.managed))
            report(ctx, g_battery.check(battery_pct=f.battery_pct, charging=f.charging))
            if not ctx.fake_device:                      # the virtual iPhone does not simulate the Mac
                report(ctx, g_battery.mac_check(power=g_battery.mac_power()))
            report(ctx, g_findmy.check(find_my=f.find_my))
            report(ctx, g_space.check(free_bytes=f.free_bytes, need_bytes=g_space.need_for(total)))
            report(ctx, g_dcim.check(pre_rows=pre_dcim, device_rows=dev.dcim()))
            report(ctx, _fresh(ctx, plan))
            report(ctx, g_set.unchanged(snap))
            P.check_cancel()                             # last safe point: from here on SIGTERM waits

            with P.critical():
                sent_at = I.iso(I.now(ctx))
                with ctx.session.update_engine() as w:      # BEFORE the first byte: a crash resumes at S16
                    w["restore_sent_at"] = sent_at
                    w["restore"] = {"kind": plan.kind, "pre_backup_id": plan.pre["backup_id"],
                                    "started_at": started_at, "sent_at": sent_at, "finished_at": None,
                                    "result_code": None, "critical_seen": True}
                    w["post"] = None                   # PRE/POST of an earlier restore no longer apply
                    w["postcheck"] = None
                    if plan.kind == "rollback":
                        w["rollback_used"] = True
                    ctx.session.advance_phase(w, "rollback_sent" if plan.kind == "rollback" else "restore_sent")
                P.phase("send", plan.phase_offset + 2, plan.phase_count)
                P.progress("send", 0, total, unit="bytes")
                code = "R_OK"
                st["called"] = True                          # from here on the device may have received data
                try:
                    dev.restore(snap.root, pw, expect_build=f.ios_build, progress=cb)
                    if st["pct"] < 100.0:
                        P.progress("send", total, total, unit="bytes")
                        st["pct"] = 100.0
                except I.LinkLost:
                    if st["pct"] < SENT_AT_100:
                        raise _Interrupted(st["pct"]) from None
                    code = "R_RESTORE_SENT_LINK_LOST"
                P.phase("reboot", plan.phase_offset + 3, plan.phase_count)
    except I.RestoreRefused as e:
        return _refused(ctx, e, st)
    except _Interrupted as e:
        _record(ctx, finished_at=I.iso(I.now(ctx)), result_code="E_RESTORE_INTERRUPTED")
        raise EngineError("E_RESTORE_INTERRUPTED", last_progress=round(e.pct, 1), device_modified="unknown") \
            from None
    except I.DeviceError as e:
        if st.get("called"):
            return _device_error(ctx, st)
        if ctx.proto.critical_seen:
            return _refused(ctx, I.RestoreRefused(None, "connection"), st)
        raise I.engine_error(e) from None
    except EngineError:
        raise
    except Exception:  # noqa: BLE001 -- unknown failure inside the restore call: fail-safe = "maybe sent" (S16)
        if st.get("called"):
            return _device_error(ctx, st)
        if not ctx.proto.critical_seen:
            raise
        return _refused(ctx, I.RestoreRefused(None, "other"), st)

    finished = I.iso(I.now(ctx))
    with ctx.session.update_engine() as w:
        rec = dict(w.get("restore") or {})
        rec.update(finished_at=finished, result_code=code)
        w["restore"] = rec
        ctx.session.advance_phase(w, "rollback_sent" if plan.kind == "rollback" else "restore_finished")
    I.write_report(ctx, "restore" if plan.kind == "final" else "rollback", "restore" if plan.kind == "final"
                   else "rollback", code, {"payload_bytes": total, "home_rows": snap.counts.get("home_rows", 0),
                                           "cameraroll_rows": snap.counts.get("cameraroll_rows", 0),
                                           "keyboard_rows": snap.counts.get("keyboard_rows", 0),
                                           "threema_rows": snap.counts.get("threema_rows", 0)})
    return StepResult(data={"last_progress": round(max(st["pct"], 100.0 if code == "R_OK" else st["pct"]), 1),
                            "finished_at": finished}, code=code, device_modified="yes")


def _device_error(ctx: Context, st: dict) -> NoReturn:
    """The restore call failed in an unknown way: the iPhone may have received (part of) the set. restore_sent_at
    stays, the app goes to S16 and the postcheck decides (never a second exposure on a guess)."""
    _record(ctx, finished_at=I.iso(I.now(ctx)), result_code="E_RESTORE_DEVICE_ERROR")
    raise EngineError("E_RESTORE_DEVICE_ERROR", last_progress=round(st["pct"], 1), device_modified="unknown")


class _Interrupted(Exception):
    def __init__(self, pct: float):
        super().__init__("interrupted")
        self.pct = pct


def _refused(ctx: Context, e: I.RestoreRefused, st: dict) -> NoReturn:
    """The device refused before staging: nothing changed. restore_sent_at goes back (no restore happened)."""
    code = "E_GUARD_FINDMY" if e.mberror == I.MBERROR_FINDMY else "E_RESTORE_NOT_STARTED"
    with ctx.session.update_engine() as w:
        rec = dict(w.get("restore") or {})
        if rec:
            rec.update(finished_at=I.iso(I.now(ctx)), result_code=code, sent_at=None)
            w["restore"] = rec
        w["restore_sent_at"] = None
        if rec.get("kind") == "rollback":
            w["rollback_used"] = False
    if code == "E_GUARD_FINDMY":
        res = g_findmy.refused_211()
        ctx.proto.check(res.id, res.status, res.code, **res.data)
        raise EngineError(code, source="mberror_211", device_modified="no")
    reason = e.reason if e.reason in ("device_refused", "connection", "password", "other") else "other"
    raise EngineError(code, reason=reason, device_modified="no")


def _plan_final(ctx: Context) -> SendPlan:
    est = ctx.session.engine_state()
    paths = I.Paths(ctx.session)
    pre = est.get("pre") or {}
    rest = est.get("restore") or {}
    if est.get("restore_sent_at") and rest.get("result_code") not in RETRY_AFTER:
        raise EngineError("E_PROTOCOL", sub="restore_already_sent")    # one exposition per session (DESIGN §2.1)
    if not pre.get("backup_id") or not pre.get("password_ok") or not pre.get("fresh_until"):
        raise EngineError("E_PROTOCOL", sub="pre_backup_missing")
    prepared = est.get("prepared") or {}
    if prepared.get("pre_backup_id") != pre["backup_id"]:
        raise EngineError("E_PROTOCOL", sub="not_prepared")
    pre_dev = paths.backup_dev("pre")
    if pre_dev is None:
        raise EngineError("E_PROTOCOL", sub="pre_backup_missing")
    return SendPlan("final", paths.restoreset, pre_dev, pre, I.FRESH_LIMIT_MIN, "freshness", "E_GUARD_FRESHNESS")


def restore(ctx: Context) -> StepResult:
    """restore -> R_RESTORE_SENT_LINK_LOST (normal) or R_OK; data last_progress, finished_at."""
    pw = ctx.secrets.require("backup_password")
    return send_set(ctx, _plan_final(ctx), pw)
