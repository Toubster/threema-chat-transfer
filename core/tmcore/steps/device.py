# SPDX-License-Identifier: AGPL-3.0-or-later
"""device-watch / device-status (S03, S08): read only, pymobiledevice3 over usbmuxd or the fake device.

device-watch streams `device` events (none / locked / untrusted / ready / multiple / disconnected) plus the matching
`prompt` hints until SIGTERM, then ends with R_OK {events}. It may show the iPhone's "Trust this computer?" dialog
(pairing is the one host-side record the transfer needs); it never changes a device setting.

device-status reads the facts of DESIGN §5.4 and reports every S03/S08 check as an event (compat_ios, managed,
threema_variant, iphone_space estimate, battery, findmy, photos_limit estimate). The result is R_OK whenever the
facts could be read -- red checks are shown by the app, the hard decisions come after the PRE backup and right
before the send. A device that cannot be read ends with its E_DEV_* code.

Owner: coreB (devtools/OWNERSHIP.md).
"""
from __future__ import annotations

import time

from tmcore.cli import Context, StepResult
from tmcore.guards import battery as g_battery
from tmcore.guards import compat as g_compat
from tmcore.guards import emit
from tmcore.guards import findmy as g_findmy
from tmcore.guards import managed as g_managed
from tmcore.guards import photos_limit as g_photos
from tmcore.guards import space as g_space
from tmcore.guards import threema as g_threema
from tmcore.protocol import EngineError
from tmcore.steps import iphone as I

POLL_S = 0.5
FAKE_POLL_S = 0.2
PAIR_TIMEOUT_S = 1.0


def _consume_cancel(ctx: Context) -> bool:
    """SIGTERM is the normal end of device-watch (DESIGN §5.4): consumed, so the stream ends with R_OK."""
    return bool(ctx.proto.consume_cancel())


def _requested(ctx: Context) -> bool:
    return bool(ctx.proto.cancel_requested)


def watch(ctx: Context) -> StepResult:
    """Stream device events until SIGTERM (no result data except counts)."""
    gw = I.gateway(ctx)
    last: tuple | None = None
    prompts: dict[str, bool] = {"unlock_device": False, "trust_device": False}
    events = 0

    def prompt(kind: str, on: bool) -> None:
        nonlocal events
        if prompts[kind] != on:
            prompts[kind] = on
            ctx.proto.prompt(kind, on)
            events += 1

    poll = FAKE_POLL_S if gw.fake else POLL_S
    while not _requested(ctx):
        try:
            seen = gw.scan(watch=True) if gw.fake else gw.scan(pair_timeout=PAIR_TIMEOUT_S)
        except I.DeviceError:
            seen = []
        if not seen:
            state, dev_hash, product = ("disconnected" if last and last[0] not in ("none", "disconnected")
                                        else "none"), None, None
        elif len(seen) > 1:
            state, dev_hash, product = "multiple", None, None
        else:
            s = seen[0]
            state, dev_hash, product = s.state, ctx.session.hasher.h(s.udid), s.product_type
        if state == "locked":
            prompt("trust_device", False)
            prompt("unlock_device", True)
        elif state == "untrusted":
            prompt("unlock_device", False)
            prompt("trust_device", True)
        else:
            prompt("unlock_device", False)
            prompt("trust_device", False)
        cur = (state, dev_hash, product)
        if cur != last:
            ctx.proto.device(state, dev_hash, product if product and I._PRODUCT.match(product) else None)
            events += 1
            last = cur
        deadline = time.monotonic() + poll
        while time.monotonic() < deadline and not _requested(ctx):
            time.sleep(0.05)
    _consume_cancel(ctx)
    return StepResult(data={"events": events})


def _estimate_need(ctx: Context, photos: int | None) -> int:
    """S03 estimate before any backup: photos + Home/Keyboard estimate + the Android media, x 1.5."""
    android = 0
    try:
        a = ctx.session.engine_state().get("android") or {}
        if isinstance(a.get("media_bytes"), int):
            android = a["media_bytes"]
    except Exception:  # noqa: BLE001
        android = 0
    if not android:
        p = ctx.session.path("android/media")
        if p.is_dir():
            android = I.tree_bytes(p)[1]
    return g_space.need_for((photos or 0) + I.HOME_ESTIMATE_BYTES + android)


def connect(ctx: Context, gw, *, expect: str | None = None):
    """select + open with E_DEV_* mapping (shared by the device steps)."""
    try:
        udid, _seen = I.select_device(ctx, gw, expect=expect)
    except I.DeviceError as e:
        raise I.engine_error(e) from None
    return udid


def status(ctx: Context) -> StepResult:
    """Read-only facts: product type, iOS build + compat stage, Threema install/variant/version, encryption, Find My,
    space, battery, managed."""
    gw = I.gateway(ctx)
    expect = getattr(ctx.args, "expect", None)
    est = ctx.session.engine_state()
    expect = expect or est.get("device")
    ctx.proto.phase("read", 1, 1)
    udid = connect(ctx, gw, expect=expect)
    try:
        with gw.open(udid) as dev:
            f = I.read_facts(dev)
    except I.DeviceError as e:
        raise I.engine_error(e) from None
    dev_hash = ctx.session.hasher.h(udid)
    with ctx.session.update_engine() as st:
        if st.get("device") is None:
            st["device"] = dev_hash
            st["device_product_type"] = f.product_type
    stage = I.compat_stage(ctx, f.ios_build)
    emit(ctx, g_compat.check(stage=stage, ios_version=f.ios_version, ios_build=f.ios_build))
    emit(ctx, g_managed.check(managed=f.managed))
    emit(ctx, g_threema.variant(installed=f.threema_installed, variant=f.threema_variant, hard=False))
    emit(ctx, g_space.check(free_bytes=f.free_bytes, need_bytes=_estimate_need(ctx, f.photos_bytes_estimate),
                            estimate=True))
    emit(ctx, g_battery.check(battery_pct=f.battery_pct, charging=f.charging))
    emit(ctx, g_findmy.check(find_my=f.find_my))
    emit(ctx, g_photos.check(photos_bytes=f.photos_bytes_estimate, estimate=True))
    data = {"device": dev_hash, "product_type": f.product_type, "ios_version": f.ios_version,
            "ios_build": f.ios_build, "compat": stage,
            "threema": {"installed": f.threema_installed, "variant": f.threema_variant,
                        "version": f.threema_version},
            "encryption": "on" if f.encryption else "off", "find_my": f.find_my,
            "free_bytes": f.free_bytes if f.free_bytes is not None else 0,
            "photos_bytes_estimate": f.photos_bytes_estimate, "battery_pct": f.battery_pct,
            "charging": f.charging, "managed": f.managed}
    return StepResult(data=data)


def require_session_device(ctx: Context) -> str:
    """The device hash of the session (set by device-status / the first backup)."""
    d = ctx.session.engine_state().get("device")
    if not d:
        raise EngineError("E_PROTOCOL", sub="device_unknown")
    return d
