#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
validate_schemas.py -- self-validation of the frozen engine contract (DESIGN §5, §13.3).

  1. every core/schema/*.v1.json is a valid JSON Schema (draft 2020-12) and all cross-schema refs resolve
  2. the code catalog core/schema/codes.v1.json is consistent (kinds, exits, screens, DE+EN texts, placeholders,
     actions, data enums)
  3. compat/*.json and compat/records/ios/*.json validate against compat.v1
  4. every mock scenario app/Tests/Scenarios/*.jsonl follows the scenario format: each event validates against
     events.v1 and every process block obeys the contract (hello first, seq gapless from 1, exactly one result as
     the last event, critical on/off balanced, exit code matches the result code, codes exist in the catalog)
  5. the contract examples embedded below (DESIGN §5.3) validate

Exit 0 = all good. Needs the 'jsonschema' package (dev dependency, not shipped).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import jsonschema
    from referencing import Registry, Resource
except ImportError:  # pragma: no cover
    print("validate_schemas.py needs 'jsonschema' (pip install jsonschema)", file=sys.stderr)
    sys.exit(2)

REPO = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO / "core" / "schema"
SCENARIO_DIR = REPO / "app" / "Tests" / "Scenarios"
SCHEMA_FILES = ["events.v1.json", "session.v1.json", "compat.v1.json", "report.v1.json"]

CATALOG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema", "protocol", "actions", "codes", "exit_codes"],
    "properties": {
        "schema": {"const": "codes.v1"},
        "protocol": {"const": 1},
        "actions": {"type": "object", "additionalProperties": {
            "type": "object", "required": ["de", "en"], "additionalProperties": False,
            "properties": {"de": {"type": "string", "minLength": 1}, "en": {"type": "string", "minLength": 1}}}},
        "x-area-tokens": {"type": "object", "propertyNames": {"pattern": "^[a-z][a-z0-9_]{0,47}$"},
                          "additionalProperties": {
                              "type": "object", "required": ["de", "en"], "additionalProperties": False,
                              "properties": {"de": {"type": "string", "minLength": 1},
                                             "en": {"type": "string", "minLength": 1}}}},
        "codes": {
            "type": "object",
            "propertyNames": {"pattern": "^[EWNR]_[A-Z0-9_]{2,60}$"},
            "additionalProperties": {
                "type": "object",
                "required": ["kind", "screen", "exit", "device_modified", "retryable", "needs_new_backup",
                             "actions", "data", "strings"],
                "additionalProperties": False,
                "properties": {
                    "kind": {"enum": ["error", "warning", "note", "result"]},
                    "screen": {"type": "string", "pattern": r"^(|S[0-9]{2}[a-z]?|F-[A-Z0-9-]{2,40})$"},
                    "exit": {"enum": [0, 1, 2, 3, 4]},
                    "device_modified": {"enum": ["no", "yes", "unknown", "dynamic"]},
                    "retryable": {"type": "boolean"},
                    "needs_new_backup": {"type": "boolean"},
                    "actions": {"type": "array", "items": {"type": "string"}},
                    "data": {"type": "array", "items": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,47}$"}},
                    "data_enums": {"type": "object", "additionalProperties": {
                        "type": "array", "items": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,47}$"}}},
                    "enum_strings": {"type": "object"},
                    "strings": {
                        "type": "object", "required": ["title", "body", "action"], "additionalProperties": False,
                        "properties": {k: {"type": "object", "required": ["de", "en"], "additionalProperties": False,
                                           "properties": {"de": {"type": "string", "minLength": 1},
                                                          "en": {"type": "string", "minLength": 1}}}
                                       for k in ("title", "body", "action")}},
                },
            },
        },
    },
}
RX_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.ok: list[str] = []

    def err(self, msg: str):
        self.errors.append(msg)

    def good(self, msg: str):
        self.ok.append(msg)


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def build_registry() -> tuple[Registry, dict]:
    schemas = {}
    resources = []
    for name in SCHEMA_FILES:
        s = load_json(SCHEMA_DIR / name)
        schemas[name] = s
        resources.append((s["$id"], Resource.from_contents(s)))
    return Registry().with_resources(resources), schemas


def validator(schema: dict, registry: Registry):
    return jsonschema.Draft202012Validator(schema, registry=registry,
                                           format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER)


def check_schemas(r: Report, registry: Registry, schemas: dict):
    for name, s in schemas.items():
        try:
            jsonschema.Draft202012Validator.check_schema(s)
            # resolve every $ref once (catches dangling refs early)
            resolver = registry.resolver(base_uri=s["$id"])
            for ref in set(_refs(s)):
                resolver.lookup(ref)
            r.good(f"schema {name}: valid draft 2020-12, refs resolve")
        except Exception as e:  # noqa: BLE001
            r.err(f"schema {name}: {type(e).__name__}: {str(e)[:200]}")


def _refs(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "$ref" and isinstance(v, str):
                yield v
            else:
                yield from _refs(v)
    elif isinstance(o, list):
        for v in o:
            yield from _refs(v)


def check_catalog(r: Report, events_schema: dict) -> dict:
    cat = load_json(SCHEMA_DIR / "codes.v1.json")
    errs = list(jsonschema.Draft202012Validator(CATALOG_SCHEMA).iter_errors(cat))
    for e in errs[:20]:
        r.err(f"codes.v1.json: {'/'.join(map(str, e.absolute_path))}: {e.message[:160]}")
    if errs:
        return cat
    kind_prefix = {"error": "E_", "warning": "W_", "note": "N_", "result": "R_"}
    n_strings = 0
    for code, c in cat["codes"].items():
        if not code.startswith(kind_prefix[c["kind"]]):
            r.err(f"codes.v1.json: {code}: kind {c['kind']} does not match the prefix")
        if c["kind"] == "error" and c["exit"] == 0:
            r.err(f"codes.v1.json: {code}: an error code needs exit != 0")
        if c["kind"] != "error" and c["exit"] != 0:
            r.err(f"codes.v1.json: {code}: only error codes may have exit != 0")
        if c["kind"] == "error" and not c["screen"]:
            r.err(f"codes.v1.json: {code}: error without target screen")
        for a in c["actions"]:
            if a not in cat["actions"]:
                r.err(f"codes.v1.json: {code}: unknown action {a!r}")
        for k in c.get("data_enums", {}):
            if k not in c["data"]:
                r.err(f"codes.v1.json: {code}: data_enums key {k!r} not listed in data")
        for k, values in c.get("enum_strings", {}).items():
            for val, tr in values.items():
                if val not in c.get("data_enums", {}).get(k, []):
                    r.err(f"codes.v1.json: {code}: enum_strings {k}.{val} not in data_enums")
                if not tr.get("de") or not tr.get("en"):
                    r.err(f"codes.v1.json: {code}: enum_strings {k}.{val} needs DE and EN")
        for key, tr in c["strings"].items():
            n_strings += 2
            de, en = set(RX_PLACEHOLDER.findall(tr["de"])), set(RX_PLACEHOLDER.findall(tr["en"]))
            if de != en:
                r.err(f"codes.v1.json: {code}.{key}: placeholders differ DE {sorted(de)} vs EN {sorted(en)}")
            for ph in de - {"App", "date"}:
                base = ph[:-3] + "_bytes" if ph.endswith("_gb") else ph
                if base not in c["data"] and ph not in c["data"]:
                    r.err(f"codes.v1.json: {code}.{key}: placeholder {{{ph}}} has no data key")
            if re.search(r"\bdu\b|\bdein", tr["de"], re.I):
                r.err(f"codes.v1.json: {code}.{key}: German text must use 'Sie' (DESIGN §8.2)")
    # every code pattern used by the events schema must be representable
    rx = re.compile(events_schema["$defs"]["code"]["pattern"])
    bad = [c for c in cat["codes"] if not rx.match(c)]
    if bad:
        r.err(f"codes.v1.json: codes not matching the events code pattern: {bad}")
    r.good(f"codes.v1.json: {len(cat['codes'])} codes, {n_strings} DE/EN strings, {len(cat['actions'])} actions")
    return cat


def check_compat(r: Report, registry: Registry, schemas: dict):
    v = validator(schemas["compat.v1.json"], registry)
    files = sorted((REPO / "compat").glob("*.json")) + sorted((REPO / "compat" / "records" / "ios").glob("*.json"))
    for p in files:
        errs = list(v.iter_errors(load_json(p)))
        if errs:
            best = jsonschema.exceptions.best_match(errs)
            r.err(f"{p.relative_to(REPO)}: {best.message[:200]}")
        else:
            r.good(f"{p.relative_to(REPO)}: valid compat.v1")
    ios = load_json(REPO / "compat" / "ios.json")
    cat = load_json(SCHEMA_DIR / "codes.v1.json")["codes"]
    for b in ios["builds"]:
        for n in b["expected_notes"]:
            if n not in cat:
                r.err(f"compat/ios.json: {b['build']}: expected note {n} not in the code catalog")


# ------------------------------------------------------------------------------------------- scenario JSONL
def parse_scenario(p: Path):
    """Yield (lineno, kind, obj). kind = 'mock' | 'event'."""
    for no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > 65536:
            yield no, "error", "line longer than 64 KiB"
            continue
        try:
            obj = json.loads(line)
        except ValueError as e:
            yield no, "error", f"not JSON: {e}"
            continue
        yield no, ("mock" if "mock" in obj else "event"), obj


def check_scenario(r: Report, p: Path, ev_validator, catalog: dict, events_schema: dict) -> int:
    name = p.relative_to(REPO)
    codes = catalog["codes"]
    phases = events_schema["x-phases"]
    header = None
    proc = None          # current process block
    blocks = 0
    errors_before = len(r.errors)

    def close_block(no):
        nonlocal proc, blocks
        if proc is None:
            return
        blocks += 1
        evs, exit_ = proc["events"], proc["exit"]
        if not evs:
            r.err(f"{name}:{no}: process {proc['cmd']} has no events")
        else:
            if evs[0]["type"] != "hello":
                r.err(f"{name}:{proc['line']}: first event of {proc['cmd']} is not hello")
            results = [e for e in evs if e["type"] == "result"]
            crashed = proc.get("crash", False)
            if crashed:
                if results:
                    r.err(f"{name}:{no}: crash block must not contain a result")
            elif len(results) != 1 or evs[-1]["type"] != "result":
                r.err(f"{name}:{no}: {proc['cmd']}: exactly one result, as the last event, required")
            elif exit_ is not None:
                res = results[0]
                want = 0 if res["ok"] else codes.get(res["code"], {}).get("exit")
                if want is not None and exit_ != want:
                    r.err(f"{name}:{no}: exit {exit_} does not match result code {res['code']} (expects {want})")
                c = codes.get(res["code"])
                critical_seen = any(e["type"] == "critical" for e in evs)
                if c and not critical_seen and c["device_modified"] not in ("dynamic", res["device_modified"]):
                    r.err(f"{name}:{no}: device_modified {res['device_modified']} contradicts catalog "
                          f"({c['device_modified']}) for {res['code']}")
            on = False
            for e in evs:
                if e["type"] == "critical":
                    if e["on"] == on:
                        r.err(f"{name}: {proc['cmd']}: critical toggled to {e['on']} twice")
                    on = e["on"]
            if on and not proc.get("crash"):
                r.err(f"{name}: {proc['cmd']}: critical still on at the end of the process")
        proc = None

    for no, kind, obj in parse_scenario(p):
        if kind == "error":
            r.err(f"{name}:{no}: {obj}")
            continue
        if kind == "mock":
            m = obj["mock"]
            if m == "scenario":
                header = obj
                for k in ("name", "protocol", "lang", "expect_screen"):
                    if k not in obj:
                        r.err(f"{name}:{no}: scenario header lacks {k!r}")
                if obj.get("name") != p.stem:
                    r.err(f"{name}:{no}: scenario name must equal the file name")
            elif m == "invoke":
                close_block(no)
                if obj.get("cmd") not in events_schema["x-commands"]:
                    r.err(f"{name}:{no}: unknown command {obj.get('cmd')!r}")
                proc = {"cmd": obj.get("cmd"), "line": no, "events": [], "exit": None, "seq": 0}
            elif m == "exit":
                if proc is None:
                    r.err(f"{name}:{no}: exit without invoke")
                    continue
                proc["exit"] = obj.get("code")
                proc["crash"] = bool(obj.get("crash"))
                if proc["crash"] and obj.get("code") not in (None, -6, -9, 134, 137):
                    r.err(f"{name}:{no}: a crash exit uses a signal code (-9/-6) or null")
                close_block(no)
            elif m in ("app", "relaunch", "user"):
                pass  # app-side steps (user answers, relaunch) -- informational for the MockEngine/UI tests
            else:
                r.err(f"{name}:{no}: unknown mock directive {m!r}")
            continue
        # event
        if proc is None:
            r.err(f"{name}:{no}: event outside an invoke block")
            continue
        errs = list(ev_validator.iter_errors(obj))
        if errs:
            best = jsonschema.exceptions.best_match(errs)
            r.err(f"{name}:{no}: events.v1: {best.message[:180]}")
        if obj.get("cmd") != proc["cmd"]:
            r.err(f"{name}:{no}: event cmd {obj.get('cmd')} inside a {proc['cmd']} block")
        proc["seq"] += 1
        if obj.get("seq") != proc["seq"]:
            r.err(f"{name}:{no}: seq {obj.get('seq')} expected {proc['seq']} (gapless from 1 per process)")
        if obj.get("type") == "phase" and obj.get("phase") not in phases.get(proc["cmd"], []):
            r.err(f"{name}:{no}: phase {obj.get('phase')!r} not declared for {proc['cmd']}")
        for key in ("code", "reason"):
            c = obj.get(key)
            if isinstance(c, str) and c not in codes:
                r.err(f"{name}:{no}: code {c} not in the catalog")
        if proc["events"] and proc["events"][-1]["type"] == "result":
            r.err(f"{name}:{no}: event after the result")
        proc["events"].append(obj)
    close_block("eof")
    if header is None:
        r.err(f"{name}: missing {{\"mock\":\"scenario\"}} header line")
    if len(r.errors) == errors_before:
        r.good(f"{name}: {blocks} process blocks valid")
    return blocks


def check_scenarios(r: Report, registry: Registry, schemas: dict, catalog: dict):
    events_schema = schemas["events.v1.json"]
    ev_validator = validator(events_schema, registry)
    files = sorted(SCENARIO_DIR.glob("*.jsonl"))
    if not files:
        r.err("no scenario files in app/Tests/Scenarios")
    for p in files:
        check_scenario(r, p, ev_validator, catalog, events_schema)


DESIGN_EXAMPLE = [
    {"v": 1, "seq": 1, "ts": "2026-10-01T13:43:40.120Z", "cmd": "restore", "type": "hello", "engine_version": "0.9.0",
     "protocol": 1, "pymobiledevice3": "11.19.4", "importer_version": "0.9.0", "models": ["V56"],
     "compat_digest": "h:3f2a91c0"},
    {"v": 1, "seq": 2, "ts": "2026-10-01T13:43:40.130Z", "cmd": "restore", "type": "phase", "phase": "guards",
     "index": 1, "count": 3},
    {"v": 1, "seq": 3, "ts": "2026-10-01T13:43:40.140Z", "cmd": "restore", "type": "check", "id": "freshness",
     "status": "pass", "data": {"age_min": 7, "limit_min": 60}},
    {"v": 1, "seq": 4, "ts": "2026-10-01T13:43:40.150Z", "cmd": "restore", "type": "check", "id": "dcim_unchanged",
     "status": "pass", "data": {"files": 1234}},
    {"v": 1, "seq": 9, "ts": "2026-10-01T13:43:41.000Z", "cmd": "restore", "type": "critical", "on": True},
    {"v": 1, "seq": 11, "ts": "2026-10-01T13:44:40.000Z", "cmd": "restore", "type": "progress", "phase": "send",
     "pct": 41.1, "done": 4210000000, "total": 10240000000, "unit": "bytes"},
    {"v": 1, "seq": 31, "ts": "2026-10-01T13:47:28.000Z", "cmd": "restore", "type": "result", "ok": True,
     "code": "R_RESTORE_SENT_LINK_LOST", "retryable": False, "device_modified": "yes",
     "data": {"last_progress": 100}},
]
MUST_FAIL = [
    ("free text in data", {"v": 1, "seq": 2, "ts": "2026-10-01T13:43:40.000Z", "cmd": "restore", "type": "note",
                           "code": "W_USB2_SLOW", "data": {"name": "Some Person"}}),
    ("path in data", {"v": 1, "seq": 2, "ts": "2026-10-01T13:43:40.000Z", "cmd": "backup", "type": "check",
                      "id": "extract", "status": "pass", "data": {"file": "/tmp/x/Manifest.db"}}),
    ("unknown field", {"v": 1, "seq": 2, "ts": "2026-10-01T13:43:40.000Z", "cmd": "restore", "type": "critical",
                       "on": True, "message": "x"}),
    ("ok with E_ code", {"v": 1, "seq": 9, "ts": "2026-10-01T13:43:40.000Z", "cmd": "cleanup", "type": "result",
                         "ok": True, "code": "E_INTERNAL", "retryable": False, "device_modified": "no",
                         "data": {"freed_bytes": 1}}),
    ("local time", {"v": 1, "seq": 1, "ts": "2026-10-01T15:43:40+02:00", "cmd": "version", "type": "critical",
                    "on": False}),
]


def check_examples(r: Report, registry: Registry, schemas: dict):
    v = validator(schemas["events.v1.json"], registry)
    for e in DESIGN_EXAMPLE:
        errs = list(v.iter_errors(e))
        if errs:
            r.err(f"DESIGN §5.3 example seq {e['seq']}: {jsonschema.exceptions.best_match(errs).message[:160]}")
    for label, e in MUST_FAIL:
        if v.is_valid(e):
            r.err(f"negative example accepted by events.v1: {label}")
    r.good(f"events.v1: DESIGN §5.3 example valid, {len(MUST_FAIL)} negative examples rejected")


def main() -> int:
    r = Report()
    registry, schemas = build_registry()
    check_schemas(r, registry, schemas)
    catalog = check_catalog(r, schemas["events.v1.json"])
    check_compat(r, registry, schemas)
    check_examples(r, registry, schemas)
    check_scenarios(r, registry, schemas, catalog)
    for m in r.ok:
        print("ok   ", m)
    for m in r.errors:
        print("FAIL ", m, file=sys.stderr)
    print(f"validate_schemas: {'PASS' if not r.errors else 'FAIL'} ({len(r.ok)} ok, {len(r.errors)} errors)")
    return 0 if not r.errors else 1


if __name__ == "__main__":
    sys.exit(main())
