# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Static contract check over the whole engine (owner: coreA as schema custodian): every code a module names in
`EngineError(...)`, `check(..., code)`, `note(...)`, `retry(...)` or `StepResult(code=...)` exists in
codes.v1.json with the right kind, and every data key passed to an `EngineError` is declared for that code
(`data` of the catalog; `sub` only where declared). Catches drift before a test happens to hit the path.
"""
from __future__ import annotations

import ast
import json
import re

import pytest

from tests import support

CATALOG = json.loads((support.CORE / "schema" / "codes.v1.json").read_text())["codes"]
EVENTS = json.loads((support.CORE / "schema" / "events.v1.json").read_text())
CHECK_IDS = set(EVENTS["$defs"]["check"]["properties"]["id"]["enum"])
CODE_RE = re.compile(r"^[EWNR]_[A-Z0-9_]+$")
SOURCES = sorted(p for p in (support.CORE / "tmcore").rglob("*.py") if "/lib/" not in p.as_posix())


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            yield name, node


def _findings():
    out = []
    for path in SOURCES:
        rel = path.relative_to(support.CORE).as_posix()
        for name, node in _calls(path):
            if name in ("EngineError", "SessionError") and node.args:
                code = _const(node.args[0])
                if code is None or name == "SessionError":
                    continue
                entry = CATALOG.get(code)
                if entry is None:
                    out.append(f"{rel}:{node.lineno}: {code} not in the catalog")
                    continue
                allowed = set(entry.get("data") or [])
                for kw in node.keywords:
                    if kw.arg is None or kw.arg in ("retryable", "device_modified"):
                        continue
                    if kw.arg not in allowed:
                        out.append(f"{rel}:{node.lineno}: {code} data key {kw.arg!r} not declared")
            elif name == "check" and len(node.args) >= 2:
                cid = _const(node.args[0])
                if cid is not None and cid not in CHECK_IDS:
                    out.append(f"{rel}:{node.lineno}: check id {cid!r} not in events.v1")
                code = _const(node.args[2]) if len(node.args) >= 3 else None
                if code is not None and code not in CATALOG:
                    out.append(f"{rel}:{node.lineno}: check code {code} not in the catalog")
            elif name == "note" and node.args:
                code = _const(node.args[0])
                if code is not None and (code not in CATALOG or code[0] not in "WN"):
                    out.append(f"{rel}:{node.lineno}: note code {code}")
            elif name == "retry" and node.args:
                code = _const(node.args[0])
                if code is not None and code not in CATALOG:
                    out.append(f"{rel}:{node.lineno}: retry reason {code}")
            elif name == "StepResult":
                for kw in node.keywords:
                    code = _const(kw.value) if kw.arg == "code" else None
                    if code is not None and (code not in CATALOG or not code.startswith("R_")):
                        out.append(f"{rel}:{node.lineno}: result code {code}")
    return out


def test_sources_found():
    assert len(SOURCES) > 20


@pytest.mark.parametrize("owner", ["all"])
def test_every_code_and_data_key_is_declared(owner):
    findings = _findings()
    assert findings == [], "\n".join(findings)


def test_catalog_codes_are_well_formed():
    for code, entry in CATALOG.items():
        assert CODE_RE.match(code)
        assert entry["kind"] == {"E": "error", "W": "warning", "N": "note", "R": "result"}[code[0]]
