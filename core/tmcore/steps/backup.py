# SPDX-License-Identifier: AGPL-3.0-or-later
"""backup --role pre|post (S12, S18): encrypted full backup with retries (W_BACKUP_RETRY), then the keybag password
check and, for PRE, the hard checks of DESIGN §5.4/§6.1: extract, airplane, threema_variant/setup/retention/model,
identity, photos_limit.

PRE: compat_ios, managed, battery and Mac power are checked before the backup starts (S12 starts the window). A new
PRE backup replaces the old one and everything built from it (work/extract, store_out, restoreset): DESIGN §6.1
"Frist abgelaufen -> neues Backup + prepare komplett neu". When only the password was wrong (F-PW-WRONG), the next
call re-checks the password on the SAME backup (no new backup, at most 5 attempts) while it is still fresh.
A failing check leaves the backup in the session; the code says whether a new backup is needed (codes.v1
needs_new_backup). engine.json pre.fresh_until is null unless every check passed, so `prepare`/`restore` can only
use a fully checked PRE backup.

POST (after the restore): same backup + password check + extract; facts are reported, nothing is enforced here --
the postcheck judges.

Owner: coreB (devtools/OWNERSHIP.md).
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import time
import uuid

from tmcore.cli import Context, StepResult
from tmcore.guards import GuardResult, report
from tmcore.guards import airplane as g_airplane
from tmcore.guards import battery as g_battery
from tmcore.guards import compat as g_compat
from tmcore.guards import facts as F
from tmcore.guards import identity as g_identity
from tmcore.guards import managed as g_managed
from tmcore.guards import photos_limit as g_photos
from tmcore.guards import space as g_space
from tmcore.guards import threema as g_threema
from tmcore.protocol import EngineError
from tmcore.steps import iphone as I
from tmcore.steps.device import connect

REUSABLE_SENT_CODES = ("E_RESTORE_NOT_STARTED", "E_RESTORE_INTERRUPTED")


def _progress(ctx: Context):
    last = [-1.0, 0.0]

    def cb(pct) -> None:
        try:
            p = max(0.0, min(100.0, float(pct)))
        except (TypeError, ValueError):
            return
        t = time.monotonic()
        if p >= 100.0 or p - last[0] >= 5.0 or (t - last[1] >= 2.0 and p > last[0]):
            last[0], last[1] = p, t
            ctx.proto.progress("backup", int(round(p * 10)), 1000, unit="items")
    return cb


def _notify(ctx: Context):
    state = {"passcode_on_device": False}

    def cb(kind: str, active: bool) -> None:
        if kind in state and state[kind] != active:
            state[kind] = active
            ctx.proto.prompt(kind, active)
    return cb


def _password_attempt(ctx: Context, role: str, since: str | None) -> int:
    hist = ctx.session.engine_state().get("history") or []
    n = sum(1 for h in hist if h.get("cmd") == "backup" and h.get("code") == "E_BACKUP_PASSWORD"
            and (since is None or str(h.get("at", "")) >= since))
    return n + 1


def _clear_pre(ctx: Context, paths: I.Paths) -> None:
    for p in (paths.backup_root("pre"), paths.extract, paths.store_out, paths.restoreset, paths.rollbackset):
        if p.exists():
            I.remove_tree(p)
    paths.backup_root("pre").mkdir(mode=0o700, parents=True, exist_ok=True)
    with ctx.session.update_engine() as st:
        st["pre"] = None
        st["prepared"] = None


def _pre_preflight(ctx: Context, f: I.Facts) -> None:
    """Before the window starts (S12): build allowed, not managed, iPhone battery, Mac on power."""
    report(ctx, g_compat.check(stage=I.compat_stage(ctx, f.ios_build), ios_version=f.ios_version,
                               ios_build=f.ios_build))
    report(ctx, g_managed.check(managed=f.managed))
    report(ctx, g_battery.check(battery_pct=f.battery_pct, charging=f.charging))
    if not ctx.fake_device:                        # the virtual iPhone does not simulate the Mac
        report(ctx, g_battery.mac_check(power=g_battery.mac_power()))


def _mac_space(ctx: Context, f: I.Facts, role: str, paths: I.Paths) -> None:
    """The Mac can hold the backup (PRE is replaced, so its old size counts as free). Real runs only: the virtual
    iPhone's claimed photo sizes have nothing to do with the Mac it runs on."""
    if ctx.fake_device:
        return
    free = shutil.disk_usage(ctx.session.root).free
    if role == "pre" and paths.backup_root("pre").exists():
        free += I.tree_bytes(paths.backup_root("pre"))[1]
    need = g_space.mac_need_for_backup(photos_bytes=f.photos_bytes_estimate, home_estimate=I.HOME_ESTIMATE_BYTES)
    report(ctx, g_space.mac_check(free_bytes=free, need_bytes=need))


def _make_backup(ctx: Context, gw, udid: str, role: str, paths: I.Paths) -> None:
    dest = paths.backup_root(role)
    wait = 0.5 if gw.fake else I.BACKUP_RETRY_WAIT_S
    for attempt in range(1, I.BACKUP_ATTEMPTS + 1):
        ctx.proto.check_cancel()
        try:
            with gw.open(udid) as dev:
                dev.backup(dest, _progress(ctx), _notify(ctx))
            return
        except I.BackupDropped:
            for p in dest.iterdir():
                I.remove_tree(p)
            if attempt == I.BACKUP_ATTEMPTS:
                raise EngineError("E_BACKUP_FAILED", attempts=attempt) from None
            ctx.proto.retry("W_BACKUP_RETRY", attempt + 1, I.BACKUP_ATTEMPTS, wait)
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                ctx.proto.check_cancel()
                time.sleep(0.1)
        except I.DeviceError as e:
            raise I.engine_error(e) from None


def _extract(ctx: Context, pw: str, dev_dir, out) -> dict:
    if out.exists():
        I.remove_tree(out)
    try:
        rc, _summary = I.bp_run(ctx, "extract", pw, backup_udid_dir=str(dev_dir), out_dir=str(out))
    except Exception as e:  # noqa: BLE001 -- PipelineError & friends: the backup is unusable
        raise EngineError("E_INTERNAL", sub="extract") from e
    try:
        rep = json.loads((out / "report.json").read_text())
    except (OSError, ValueError):
        raise EngineError("E_INTERNAL", sub="extract") from None
    rep["_rc"] = rc
    return rep


def _android_known(ctx: Context, paths: I.Paths) -> bool:
    return paths.normalized.is_file() or bool((ctx.session.engine_state().get("android") or {}).get("own_id"))


def _identity(ctx: Context, store_db, paths: I.Paths) -> bool | None:
    """Android ID == iPhone ID, compared as session HMACs in memory. None = iPhone ID unreadable (fail-closed)."""
    iphone_id = F.iphone_identity(store_db)
    if iphone_id is None:
        return None
    android_id = F.android_identity(paths.normalized)
    try:
        if android_id:
            return ctx.session.hasher.same(iphone_id, android_id)
        own = (ctx.session.engine_state().get("android") or {}).get("own_id")
        return bool(own) and ctx.session.hasher.h(iphone_id) == own
    finally:
        del iphone_id, android_id


def backup(ctx: Context) -> StepResult:
    """backup -> data per events.v1 result_ok_backup."""
    role = ctx.args.role
    pw = ctx.secrets.require("backup_password")
    paths = I.Paths(ctx.session)
    est = ctx.session.engine_state()
    rest = est.get("restore") or {}
    if role == "pre" and est.get("restore_sent_at") and rest.get("result_code") not in REUSABLE_SENT_CODES:
        raise EngineError("E_PROTOCOL", sub="restore_already_sent")      # the PRE of a sent restore stays (R1)
    if role == "post" and not est.get("restore_sent_at"):
        raise EngineError("E_PROTOCOL", sub="no_restore_sent")
    if role == "pre" and not _android_known(ctx, paths):
        raise EngineError("E_PROTOCOL", sub="android_not_done")     # the identity guard needs the Android ID
    old = est.get(role) or {}
    dev_dir = paths.backup_dev(role)
    reuse = (old.get("password_ok") is False and dev_dir is not None
             and (role == "post" or (I.parse_ts(old.get("fresh_until")) or I.utc_now()) > I.now(ctx)))
    gw = I.gateway(ctx)
    ctx.proto.phase("backup", 1, 2)
    if reuse:
        started, finished = old["started_at"], old["finished_at"]
        backup_id = old["backup_id"]
        ios_build = old.get("ios_build")
    else:
        udid = connect(ctx, gw, expect=est.get("device"))
        try:
            with gw.open(udid) as dev:
                f = I.read_facts(dev)
        except I.DeviceError as e:
            raise I.engine_error(e) from None
        if role == "pre":
            _pre_preflight(ctx, f)
        _mac_space(ctx, f, role, paths)
        if not f.encryption:
            raise EngineError("E_BACKUP_ENCRYPTION_OFF")
        if role == "pre":
            _clear_pre(ctx, paths)
        else:
            if paths.backup_root("post").exists():
                I.remove_tree(paths.backup_root("post"))
            paths.backup_root("post").mkdir(mode=0o700, parents=True, exist_ok=True)
            with ctx.session.update_engine() as st:
                st["post"] = None
        with ctx.session.update_engine() as st:
            st["device"] = st.get("device") or ctx.session.hasher.h(udid)
            st["device_product_type"] = st.get("device_product_type") or f.product_type
        t0 = I.now(ctx)
        _make_backup(ctx, gw, udid, role, paths)
        started, finished = I.iso(t0), I.iso(I.now(ctx))
        backup_id = str(uuid.uuid4())
        ios_build = f.ios_build
        dev_dir = paths.backup_dev(role)
        if dev_dir is None:
            raise EngineError("E_BACKUP_FAILED", attempts=I.BACKUP_ATTEMPTS)
    ctx.proto.phase("checks", 2, 2)
    if dev_dir is None:
        raise EngineError("E_BACKUP_FAILED", attempts=I.BACKUP_ATTEMPTS)
    started_t = I.parse_ts(started) or I.now(ctx)
    files, nbytes = I.tree_bytes(dev_dir)
    rec = {"backup_id": backup_id, "started_at": started, "finished_at": finished, "fresh_until": None,
           "ios_build": ios_build, "bytes": nbytes, "files": files, "password_ok": None, "frozen": False}
    view = F.open_view(dev_dir, pw)
    if view is None:
        attempt = _password_attempt(ctx, role, finished)
        rec["password_ok"] = False
        if role == "pre":
            rec["fresh_until"] = I.iso(started_t + _dt.timedelta(minutes=I.FRESH_LIMIT_MIN))
        with ctx.session.update_engine() as st:
            st[role] = rec
        ctx.proto.check("password", "fail", "E_BACKUP_PASSWORD", attempt=attempt, max=I.PASSWORD_ATTEMPTS)
        raise EngineError("E_BACKUP_PASSWORD", attempt=attempt, max=I.PASSWORD_ATTEMPTS,
                          retryable=attempt < I.PASSWORD_ATTEMPTS)
    rec["password_ok"] = True
    rec["ios_build"] = str(view.lockdown.get("BuildVersion") or ios_build or "")
    status_date = view.status_date
    if status_date is not None and role == "pre":
        # freshness counts from the earlier of: engine start of the backup, the backup's own Status.plist date
        s = started_t
        if s - _dt.timedelta(minutes=I.FRESH_LIMIT_MIN) <= status_date < s:
            rec["started_at"] = I.iso(status_date)
    ctx.proto.check("password", "pass")
    with ctx.session.update_engine() as st:
        st[role] = dict(rec)

    out = paths.extract if role == "pre" else paths.extract_post
    rep = _extract(ctx, pw, dev_dir, out)
    ef = F.extract_facts(rep)
    ctx.proto.check("extract", "pass" if rep["_rc"] == 0 else "warn", **({"errors": ef["errors"]} if ef["errors"] else {}))
    (out / ".backup_id").write_text(backup_id + "\n")
    apps = view.applications
    installed = "ch.threema.iapp" in apps and ef["group_present"]
    variant = "regular" if installed else next((v for b, v in I.THREEMA_BUNDLES.items() if b in apps), "none")
    model_res, model_id = g_threema.model(
        digest=g_threema.model_digest(ef["model_hashes"]) if ef["model_hashes"] else None,
        app_version=ef["app_version"], compat=ctx.compat("threema-ios"))
    air = F.airplane(view)
    photos = F.photos_bytes(view)
    setup_res = g_threema.setup(app_setup_state=ef["app_setup_state"], setup_marker=ef["setup_marker"],
                                integrity_ok=ef["integrity_ok"])
    ret_res = g_threema.retention(keep_messages_days=ef["keep_messages_days"])
    data = {"role": role, "bytes": nbytes, "files": files, "password_ok": True, "airplane": air is True,
            "threema": {"setup_ok": installed and not setup_res.failed, "retention_ok": not ret_res.failed,
                        "model": model_id or "unknown", "app_version": ef["app_version"]},
            "photos_bytes": photos, "finished_at": rec["finished_at"], "fresh_until": None,
            "ios_build": rec["ios_build"]}
    counts = {"files": files, "bytes": nbytes, "photos_bytes": photos, "store_messages": ef["messages"],
              "manifest_rows": len(view.rows)}
    checks: list[dict] = []

    def run(res: GuardResult) -> None:
        checks.append({"id": res.id, "status": res.status, **({"code": res.code} if res.code else {})})
        report(ctx, res)

    try:
        if role == "pre":
            run(g_airplane.check(airplane=air))
            run(g_threema.variant(installed=installed, variant=variant, hard=True))
            run(setup_res)
            run(ret_res)
            run(model_res)
            same = _identity(ctx, out / "store" / "ThreemaData.sqlite", paths)
            run(g_identity.check(same=same, iphone_readable=same is not None, android_known=True))
            data["id_match"] = bool(same)
            run(g_photos.check(photos_bytes=photos))
    except EngineError as e:
        I.write_report(ctx, f"backup-{role}", "backup", e.code, counts, checks)
        raise
    I.freeze(dev_dir)
    rec["frozen"] = True
    if role == "pre":
        rec["fresh_until"] = I.iso((I.parse_ts(rec["started_at"]) or started_t)
                                   + _dt.timedelta(minutes=I.FRESH_LIMIT_MIN))
        data["fresh_until"] = rec["fresh_until"]
    with ctx.session.update_engine() as st:
        st[role] = rec
        st["phase"] = "pre_backup_done" if role == "pre" else "post_backup_done"
    I.write_report(ctx, f"backup-{role}", "backup", "R_OK", counts, checks)
    return StepResult(data=data)
