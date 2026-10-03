# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guards 'threema_variant', 'threema_setup', 'threema_retention', 'threema_model' (DESIGN §6.1, §6.3).

* variant: the regular app ch.threema.iapp (Work/OnPrem refused); S03 informs, after PRE hard.
* setup: AppSetupState == 40, no APP_SETUP_NOT_COMPLETED marker, store integrity ok.
* retention: KeepMessagesDays not > 0 ("Keep messages: Forever"), otherwise Threema deletes the imported history.
* model: the store's NSStoreModelVersionHashes digest is EXACTLY a model of compat/threema-ios.json (never migrate);
  app versions are a soft check (blocked_app_versions refused)."""
from __future__ import annotations

import base64
import hashlib
import json

from tmcore.guards import GuardResult


def variant(*, installed: bool, variant: str, hard: bool) -> GuardResult:
    if installed and variant == "regular":
        return GuardResult("threema_variant", "pass")
    if variant in ("work", "onprem", "other"):
        return GuardResult("threema_variant", "fail", "E_THREEMA_VARIANT", data={"variant": variant})
    if not hard:
        return GuardResult("threema_variant", "skip", data={"variant": "none"})    # installed in S07
    return GuardResult("threema_variant", "fail", "E_THREEMA_MISSING", data={"variant": "none"}, error_data={})


def setup(*, app_setup_state, setup_marker: bool, integrity_ok: bool) -> GuardResult:
    ok = app_setup_state == 40 and not setup_marker and integrity_ok
    if ok:
        return GuardResult("threema_setup", "pass")
    sub = "marker" if setup_marker else "integrity" if not integrity_ok else "state"
    return GuardResult("threema_setup", "fail", "E_THREEMA_NOT_SET_UP", data={"sub": sub}, error_data={})


def retention(*, keep_messages_days) -> GuardResult:
    if isinstance(keep_messages_days, int) and not isinstance(keep_messages_days, bool) and keep_messages_days > 0:
        return GuardResult("threema_retention", "fail", "E_THREEMA_RETENTION",
                           data={"keep_days": keep_messages_days}, error_data={})
    return GuardResult("threema_retention", "pass")


def model_digest(model_hashes_b64: dict) -> str:
    """compat/threema-ios.json version_hashes_sha256 = sha256 of json.dumps(base64 hashes, sort_keys=True)."""
    return hashlib.sha256(json.dumps(model_hashes_b64, sort_keys=True).encode()).hexdigest()


def model_hashes_b64(raw: dict) -> dict:
    return {k: (base64.b64encode(v).decode() if isinstance(v, (bytes, bytearray)) else v) for k, v in raw.items()}


def model(*, digest: str | None, app_version: str | None, compat: dict) -> tuple[GuardResult, str | None]:
    """-> (result, model id). Unknown digest = refused before anything happens on the iPhone."""
    for m in compat.get("models") or []:
        if digest and m.get("version_hashes_sha256") == digest and m.get("status") == "verified":
            if app_version and app_version in (m.get("blocked_app_versions") or []):
                return (GuardResult("threema_model", "fail", "E_THREEMA_MODEL_UNKNOWN", data={"model": m["id"]},
                                    error_data={"app_version": app_version}), m["id"])
            return GuardResult("threema_model", "pass", data={"model": m["id"]}), m["id"]
    err = {"app_version": app_version} if app_version else {}
    return GuardResult("threema_model", "fail", "E_THREEMA_MODEL_UNKNOWN", data={"model": "unknown"},
                       error_data=err), None
