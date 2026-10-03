# SPDX-License-Identifier: AGPL-3.0-or-later
"""postcheck (S18): gate v2 on the POST backup -> verdict (DESIGN §7). Owner: coreB (devtools/OWNERSHIP.md).

Preconditions: a restore was sent (engine.json restore_sent_at) and a POST backup of the same iPhone exists that was
STARTED after the restore finished (E_POST_TOO_EARLY otherwise: back up again after the reboot).

  threema  P.2  final restore: lib.verify_import --post-launch (normalized.sqlite + the PRE store as baseline against
                the store of the POST backup: the imported history landed, the app state is consistent);
                rollback (no-op set): the Threema store counts of POST are >= those of PRE
  compare  P.3  lib.backup_diff PRE -> POST with the restore set as payload (payload landing; Home/CameraRoll/Keyboard
                as identity domains, every other domain with thresholds and sentinels)
           P.4  lib.backup_diff PRE -> POST strict, without any payload knowledge (independent second view)
           both in process, the password from memory, NO waivers (the product has none)
  verdict  tmcore.verdict.decide(P.2, P.3, P.4, purplebuddy class, --buddy-answer, expected_notes of the device's
           build from compat/ios.json): setup_full > data_keychain > data > restore_state > threema_only >
           needs_answer > ok_with_notes > ok

Answers of the user (the app passes them; both optional):
  --buddy-answer   S16 account_only | full_setup | none. full_setup = the iPhone shows the full Setup Assistant
                   (language/country). That alone is setup_full (DESIGN §7, first rule), and an iPhone in Setup
                   Assistant can neither open Threema (S17) nor be expected to make a POST backup (S18), so postcheck
                   then decides without a POST backup (REVIEW M2): verdict setup_full, P.2-P.4 not run.
  --threema-answer S17 ok | problem. "problem" with every system check green -> threema_only (R1), REVIEW M1.

Result data: verdict, notes[] (N_ codes), areas[] {area, severity}, threema_ok. A red verdict is a normal result
(R_OK): the app shows S21 with the matching rollback step (DESIGN §8.5). The full gate reports (structure and counts
only) stay in work/postcheck/ for the diagnostic report; reports/postcheck.json has counts and codes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tmcore import verdict as V
from tmcore.cli import Context, StepResult
from tmcore.guards import facts as F
from tmcore.guards import threema as g_threema
from tmcore.protocol import EngineError
from tmcore.steps import iphone as I

STORE_TABLES = ("ZMESSAGE", "ZCONVERSATION", "ZCONTACT", "ZGROUP")
GATE = {"alert_min": 20, "alert_frac": 0.10, "collapse_max": 3, "depth": 2, "top": 25}


def _set_dev(paths: I.Paths, kind: str, udid: str) -> Path:
    root = paths.rollbackset if kind == "rollback" else paths.restoreset
    return root / udid


def _model_id(ctx: Context, rep: dict) -> str | None:
    ef = F.extract_facts(rep)
    if not ef["model_hashes"]:
        return None
    _res, mid = g_threema.model(digest=g_threema.model_digest(ef["model_hashes"]), app_version=ef["app_version"],
                                compat=ctx.compat("threema-ios"))
    return mid


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _p2_final(ctx: Context, paths: I.Paths, model_id: str) -> tuple[bool, dict]:
    vi = I.lib("verify_import")
    out = ctx.session.path("work/postcheck")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    rep_p = out / "verify-import-post.json"
    rep_p.unlink(missing_ok=True)
    argv = ["--normalized", str(paths.normalized), "--work-dir", str(paths.android),
            "--store", str(paths.extract_post / "store"), "--store-in", str(paths.extract / "store"),
            "--post-launch", "--no-hash", "--momd", str(ctx.momd(model_id)), "--importer", str(ctx.importer),
            "--report", str(rep_p)]
    with I.quiet():
        rc = vi.main(argv, quiet=True)
    rep = _read_json(rep_p)
    fails = rep.get("failures") or {}
    return rc == 0, {"checks": int(sum((rep.get("checks_passed") or {}).values())),
                     "failures": int(sum(v if isinstance(v, int) else 1 for v in fails.values()) if
                                     isinstance(fails, dict) else len(fails))}


def _p2_noop(paths: I.Paths) -> tuple[bool, dict]:
    a = (_read_json(paths.extract / "report.json").get("store") or {}).get("counts") or {}
    b = (_read_json(paths.extract_post / "report.json").get("store") or {}).get("counts") or {}
    lower = sum(1 for t in STORE_TABLES if int(b.get(t) or 0) < int(a.get(t) or 0))
    return lower == 0, {"tables": len(STORE_TABLES), "lower": lower}


def _diff(ctx: Context, pre_dev: Path, post_dev: Path, payload_dev: Path | None, pw: str) -> dict:
    bd = I.lib("backup_diff")
    I._use_tmp(ctx)  # noqa: SLF001 -- decrypted Manifest copies of the library stay in <session>/work/tmp
    try:
        with I.quiet():
            pre = bd.Backup(pre_dev, pw, "pre")
            post = bd.Backup(post_dev, pw, "post")
            payload = bd.Backup(payload_dev, pw, "payload") if payload_dev is not None else None
            marks = [m for m in (("pre_backup", pre.date), ("post_backup", post.date)) if m[1]]
            return bd.compare(pre, post, payload=payload, expect=list(bd.DEFAULT_EXPECT), marks=marks,
                              depth=GATE["depth"], content_re=None, baseline=None, alert_min=GATE["alert_min"],
                              alert_frac=GATE["alert_frac"], top=GATE["top"], collapse_max=GATE["collapse_max"])
    except Exception as e:  # noqa: BLE001 -- DiffError, PipelineError, sqlite: the gate cannot judge = internal
        raise EngineError("E_INTERNAL", sub="gate") from e


def _view(rep: dict) -> V.GateView:
    kc = rep.get("keychain_items") or {}
    a, b = kc.get("pre"), kc.get("post")
    added = isinstance(a, dict) and isinstance(b, dict) and "error" not in a and "error" not in b and any(
        isinstance(b.get(k), int) and isinstance(a.get(k), int) and b[k] > a[k] for k in b)
    pb = rep.get("purplebuddy") or {}
    return V.GateView(alert_ids=list(rep.get("alert_ids") or []),
                      note_classes=[n.get("class") for n in rep.get("notes") or [] if n.get("class")],
                      buddy_class=pb.get("class"), buddy_setup_done=bool(pb.get("setup_done_before_and_after")),
                      keychain_added=added)


def _write_private(p: Path, obj: dict) -> None:
    tmp = p.with_name(f".{p.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, default=str)
    os.replace(tmp, p)


def _setup_full(ctx: Context, kind: str) -> StepResult:
    """S16 "also language, country or Apps & data": setup_full from the answer alone, no POST backup (REVIEW M2)."""
    ctx.proto.phase("verdict", 1, 1)
    ctx.proto.check("buddy_answer", "fail", None, answer="full_setup")
    res = V.decide(p2_ok=True, views=[], buddy_answer="full_setup", expected_notes=[])
    with ctx.session.update_engine() as w:
        w["postcheck"] = {"at": I.iso(I.now(ctx)), "verdict": res.verdict, "notes": []}
        ctx.session.advance_phase(w, "postcheck_done")
    ctx.session.write_report("postcheck", "postcheck", "R_OK",
                             counts={"alerts_payload_view": 0, "alerts_strict_view": 0, "notes": 0,
                                     "areas": len(res.areas), "p2_ok": 0, "p2_checks": 0, "p2_failures": 0},
                             codes=[],
                             data={"verdict": res.verdict, "kind": kind, "buddy_class": "not_checked",
                                   "areas": res.areas, "alert_kinds": []})
    return StepResult(data={"verdict": res.verdict, "notes": [], "areas": res.areas, "threema_ok": False})


def postcheck(ctx: Context) -> StepResult:
    """postcheck -> verdict, notes[], areas[], threema_ok."""
    answer = getattr(ctx.args, "buddy_answer", None)
    threema_answer = getattr(ctx.args, "threema_answer", None)
    st = ctx.session.engine_state()
    paths = I.Paths(ctx.session)
    rest = st.get("restore") or {}
    if not st.get("restore_sent_at") or not rest.get("kind"):
        raise EngineError("E_PROTOCOL", sub="no_restore_sent")
    if answer == "full_setup":
        return _setup_full(ctx, rest["kind"])       # no backup is read: no password needed
    pw = ctx.secrets.require("backup_password")
    pre_dev, post_dev = paths.backup_dev("pre"), paths.backup_dev("post")
    post = st.get("post") or {}
    if pre_dev is None:
        raise EngineError("E_PROTOCOL", sub="pre_backup_missing")
    if post_dev is None or not post.get("backup_id") or not post.get("password_ok"):
        raise EngineError("E_POST_TOO_EARLY")
    if post_dev.name != pre_dev.name:
        raise EngineError("E_DEV_OTHER")
    restored = I.parse_ts(rest.get("finished_at")) or I.parse_ts(rest.get("sent_at")) or \
        I.parse_ts(st.get("restore_sent_at"))
    post_start = I.parse_ts(post.get("started_at")) or I.parse_ts(post.get("finished_at"))
    ok_time = restored is not None and post_start is not None and post_start > restored
    ctx.proto.check("post_after_restore", "pass" if ok_time else "fail", None if ok_time else "E_POST_TOO_EARLY")
    if not ok_time:
        raise EngineError("E_POST_TOO_EARLY")
    kind = rest["kind"]
    set_dev = _set_dev(paths, kind, pre_dev.name)
    if not (set_dev / "Manifest.db").is_file():
        raise EngineError("E_GUARD_SET_INTEGRITY", sub="layout")
    if not (paths.extract_post / "report.json").is_file() or not (paths.extract / "report.json").is_file():
        raise EngineError("E_INTERNAL", sub="extract")

    # -- P.2 Threema --------------------------------------------------------------------------------------------
    ctx.proto.phase("threema", 1, 3)
    if kind == "final":
        model_id = _model_id(ctx, _read_json(paths.extract_post / "report.json")) or \
            _model_id(ctx, _read_json(paths.extract / "report.json"))
        if model_id is None or not paths.normalized.is_file():
            p2_ok, p2 = False, {"checks": 0, "failures": 1}
        else:
            p2_ok, p2 = _p2_final(ctx, paths, model_id)
    else:
        p2_ok, p2 = _p2_noop(paths)
    ctx.proto.check("p2_verify_import", "pass" if p2_ok else "fail", None, **{k: int(v) for k, v in p2.items()})
    ctx.proto.check_cancel()

    # -- P.3 / P.4 ----------------------------------------------------------------------------------------------
    ctx.proto.phase("compare", 2, 3)
    rep3 = _diff(ctx, pre_dev, post_dev, set_dev, pw)
    ctx.proto.check_cancel()
    rep4 = _diff(ctx, pre_dev, post_dev, None, pw)
    gate_dir = ctx.session.path("work/postcheck")
    gate_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private(gate_dir / "diff-payload.json", rep3)
    _write_private(gate_dir / "diff-strict.json", rep4)
    views = [_view(rep3), _view(rep4)]
    for cid, rep, v in (("p3_payload", rep3, views[0]), ("p4_strict", rep4, views[1])):
        n_alerts = len([a for a in v.alert_ids if a != "purplebuddy"])
        ctx.proto.check(cid, "pass" if not n_alerts else "fail", None, alerts=n_alerts, notes=len(v.note_classes))
    kc = rep4.get("keychain_items") or {}
    kc_v = str(kc.get("verdict") or "n/a")
    ctx.proto.check("keychain_items", "fail" if kc_v.startswith("ALERT") else "pass" if kc_v == "ok" else "skip")
    pb_class = views[1].buddy_class or "unchanged"
    pb_class = pb_class if pb_class in ("unchanged", "apple_account_rerun", "restore_state", "setup_reset") \
        else "setup_reset"
    ctx.proto.check("purplebuddy", "pass" if pb_class == "unchanged" else "warn"
                    if pb_class == "apple_account_rerun" else "fail", buddy_class=pb_class,
                    setup_done=views[1].buddy_setup_done)

    # -- verdict ------------------------------------------------------------------------------------------------
    ctx.proto.phase("verdict", 3, 3)
    build = (st.get("pre") or {}).get("ios_build") or ""
    expected = list((I.compat_entry(ctx, build) or {}).get("expected_notes") or [])
    res = V.decide(p2_ok=p2_ok, views=views, buddy_answer=answer, expected_notes=expected,
                   threema_answer=threema_answer)
    if threema_answer is not None:
        ctx.proto.check("threema_answer", "pass" if threema_answer == "ok" else "fail", None, answer=threema_answer)
    for n in res.notes:
        ctx.proto.note(n)
    at = I.iso(I.now(ctx))
    with ctx.session.update_engine() as w:
        w["postcheck"] = {"at": at, "verdict": res.verdict, "notes": list(res.notes)}
        ctx.session.advance_phase(w, "postcheck_done")
    alerts = sorted({a for v in views for a in v.alert_ids})
    ctx.session.write_report("postcheck", "postcheck", "R_OK",
                             counts={"alerts_payload_view": len(rep3.get("alert_ids") or []),
                                     "alerts_strict_view": len(rep4.get("alert_ids") or []),
                                     "notes": len(res.notes), "areas": len(res.areas), "p2_ok": int(p2_ok),
                                     "p2_checks": p2.get("checks", 0), "p2_failures": p2.get("failures", 0)},
                             codes=list(res.notes),
                             data={"verdict": res.verdict, "kind": kind, "buddy_class": pb_class,
                                   "areas": res.areas, "alert_kinds": sorted({a.split(":", 1)[0] for a in alerts})})
    return StepResult(data={"verdict": res.verdict, "notes": list(res.notes), "areas": res.areas,
                            "threema_ok": p2_ok})
