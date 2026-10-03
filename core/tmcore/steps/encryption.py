# SPDX-License-Identifier: AGPL-3.0-or-later
"""encryption-enable (S10a): turns on backup encryption with ctx.secrets.require('new_backup_password'); critical
while the device dialog is open (the iPhone asks for its passcode). This is one of the only two things the tool
changes on the iPhone (DESIGN §2.8); it is never switched off again by the tool (encrypted local backups are
better). Already on -> nothing is changed, R_OK {encryption: on, changed: false}: the backup password is then the one
the user chose earlier (for example in Finder for the S09 safety net), NOT the one the app offered, so the app must
not keep it (REVIEW B1). Turned on now -> {encryption: on, changed: true}.

Turned on now while a PRE backup is kept for another password attempt (F-PW-WRONG): that backup was made under a
password the iPhone no longer has (the user did "Alle Einstellungen zurücksetzen", S10b help), so it is dropped from
engine.json and the next `backup --role pre` makes a new backup instead of re-checking the old one with the new
password.

Owner: coreB (devtools/OWNERSHIP.md).
"""
from __future__ import annotations

from tmcore.cli import Context, StepResult
from tmcore.protocol import EngineError
from tmcore.steps import iphone as I
from tmcore.steps.device import connect

MIN_LEN = 10          # S10a "Eigenes Passwort wählen (>= 10 Zeichen)"; the generated one has 6 x 4 characters


def enable(ctx: Context) -> StepResult:
    """encryption-enable -> {encryption: on}."""
    pw = ctx.secrets.require("new_backup_password")
    if len(pw) < MIN_LEN:
        raise EngineError("E_PROTOCOL", sub="password_too_short")
    gw = I.gateway(ctx)
    expect = ctx.session.engine_state().get("device")
    udid = connect(ctx, gw, expect=expect)
    ctx.proto.phase("confirm_on_device", 1, 1)
    try:
        with gw.open(udid) as dev:
            if dev.will_encrypt():
                ctx.proto.check("encryption", "pass", state="already_on")
                return StepResult(data={"encryption": "on", "changed": False}, device_modified="no")
            ctx.proto.check_cancel()
            with ctx.proto.critical():
                ctx.proto.prompt("passcode_on_device", True)
                try:
                    dev.change_password(pw, ctx.session.tmp)
                finally:
                    ctx.proto.prompt("passcode_on_device", False)
            on = dev.will_encrypt()
    except I.DeviceError as e:
        raise EngineError(e.code, device_modified="unknown") from None
    if not on:
        raise EngineError("E_BACKUP_ENCRYPTION_OFF", device_modified="no")
    ctx.proto.check("encryption", "pass", state="enabled")
    with ctx.session.update_engine() as st:
        st["device"] = st.get("device") or ctx.session.hasher.h(udid)
        if (st.get("pre") or {}).get("password_ok") is False:
            st["pre"] = None              # made under the password the reset removed: never re-checked, a new PRE
    return StepResult(data={"encryption": "on", "changed": True}, device_modified="yes")
