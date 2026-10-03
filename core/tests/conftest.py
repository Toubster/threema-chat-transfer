# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared test setup: no Time Machine calls from tests, schema dir of this checkout."""
import os
from pathlib import Path

os.environ.setdefault("TMCORE_NO_TMUTIL", "1")
os.environ.setdefault("TMCORE_SCHEMA_DIR", str(Path(__file__).resolve().parents[1] / "schema"))
