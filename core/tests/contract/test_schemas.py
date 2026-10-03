# SPDX-License-Identifier: AGPL-3.0-or-later
"""The frozen contract validates itself: schemas, code catalog, compat lists, mock scenarios, string catalog."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _run(script):
    return subprocess.run([sys.executable, str(REPO / "scripts" / script)], capture_output=True, text=True)


def test_validate_schemas():
    p = _run("validate_schemas.py")
    assert p.returncode == 0, p.stderr[-3000:]


def test_check_strings():
    p = _run("check_strings.py")
    assert p.returncode == 0, p.stderr[-3000:]
