# SPDX-License-Identifier: AGPL-3.0-or-later
"""Session folder rules (DESIGN §5.8): 0700, owner, schema, atomic engine.json, path escapes refused."""
import json
import os
import stat

import pytest

from tmcore.protocol import EngineError
from tmcore.session import Session, SessionError


def test_create_and_open(tmp_path):
    s = Session.create(tmp_path)
    assert stat.S_IMODE(s.root.stat().st_mode) == 0o700
    assert (s.root / ".metadata_never_index").exists()
    for d in ("android", "ios/pre", "ios/post", "work/restoreset", "reports", "logs", "diag"):
        assert (s.root / d).is_dir()
    s2 = Session.open(s.root)
    assert s2.app_state()["schema"] == "session.v1"


def test_wrong_mode_or_schema_is_refused(tmp_path):
    s = Session.create(tmp_path)
    os.chmod(s.root, 0o755)
    with pytest.raises(SessionError) as e:
        Session.open(s.root)
    assert e.value.code == "E_PROTOCOL" and e.value.data["sub"] == "session_mode"
    os.chmod(s.root, 0o700)
    (s.root / "session.json").write_text(json.dumps({"schema": "other"}))
    with pytest.raises(SessionError):
        Session.open(s.root)
    with pytest.raises(SessionError):
        Session.open(tmp_path / "missing")


def test_engine_state_atomic_and_salted(tmp_path):
    s = Session.create(tmp_path)
    with s.update_engine() as st:
        st["phase"] = "prepared"
    data = json.loads((s.root / "engine.json").read_text())
    assert data["phase"] == "prepared" and len(data["hash_salt_hex"]) == 64
    assert stat.S_IMODE((s.root / "engine.json").stat().st_mode) == 0o600
    assert s.hasher.h("ZZCANARY").startswith("h:")


def test_paths_stay_inside(tmp_path):
    s = Session.create(tmp_path)
    assert s.key(s.path("android/missing-senders.json")) == "android/missing-senders.json"
    with pytest.raises(EngineError):
        s.path("../../etc/passwd")
