#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
record_scenarios.py -- record the mock scenarios of the app from the real engine on the virtual iPhone
(DESIGN §13.3). Owner: coreB. No device, synthetic data only.

    python3 devtools/record_scenarios.py                 # record every scenario -> app/Tests/Scenarios/<name>.jsonl
    python3 devtools/record_scenarios.py happy find_my_on
    python3 devtools/record_scenarios.py --check         # record into a temp dir, compare STRUCTURALLY with the
                                                         # checked-in files; exit 1 on drift (CI contract job)
    python3 devtools/record_scenarios.py --list

Each scenario of core/tmcore/fake/scenarios/<name>.json runs as one wizard flow (core/tests/e2e/flow.py): every
tmcore process the app would start, with --fake-device <name>. The recording is then made reproducible and free of
machine facts:
  * timestamps are shifted so the run starts at 2026-01-01T09:00:00Z (all offsets stay as recorded, so countdowns
    and fresh_until stay consistent);
  * session hashes (random salt per session) become fixed values: device h:5c0ffee1, own Threema ID h:0a1b2c3d,
    anything else h:0000000n (n = 1, 2, ...) in order of appearance;
  * `host-check` describes the Mac the recording ran on -- its values are replaced by the canonical example Mac
    (macOS 15.1, arm64, APFS, 220 GB free, AC power, FileVault on); structure and codes stay as recorded;
  * paths are placeholders (<session>, <workdir>, <android-backup-N>); secrets are field names only;
  * chat counts are the canonical example values of docs/ENGINE-PROTOCOL.md §9 (18 chats, 4 groups, 12345 messages,
    1234 media, 2 polls; a partial media set shows 1200 of 1234), so screenshots and texts never show fixture
    numbers; sizes stay those of the synthetic fixtures;
  * any other number >= 100 that the local scrub deny list (scripts/scrub_check.py layer 2, maintainer machine)
    would flag is moved to the next number that it does not flag -- the recording must pass the pre-push scrub.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORE = REPO / "core"
OUT = REPO / "app" / "Tests" / "Scenarios"
sys.path.insert(0, str(CORE))

T0 = dt.datetime(2026, 1, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
TS = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z$")
HASH = re.compile(r"^h:[0-9a-f]{8}$")
FIXED_HASHES = {"device": "h:5c0ffee1", "own_id": "h:0a1b2c3d"}
# canonical chat counts per command (docs/ENGINE-PROTOCOL.md §9, DESIGN §10.4)
COUNTS = {"android-normalize": {"chats": 18, "groups": 4, "messages": 12345, "polls": 2, "media_total": 1234},
          "prepare": {"messages": 12345, "media": 1234}}
HOST = {"macos": "15.1", "arch": "arm64", "fs": "apfs", "free_bytes": 220_000_000_000, "need_bytes": 40_000_000_000,
        "power": "ac", "battery_pct": None, "filevault": True}


def _parse(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt(t: dt.datetime) -> str:
    t = t.astimezone(dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Canon:
    def __init__(self, first_ts: str):
        self.delta = T0 - _parse(first_ts)
        self.hashes: dict[str, str] = {}

    def h(self, key: str | None, v: str) -> str:
        if v not in self.hashes:
            self.hashes[v] = FIXED_HASHES.get(key or "") or f"h:{len(self.hashes) + 1:08x}"
        return self.hashes[v]

    def value(self, v, key: str | None = None):
        if isinstance(v, str):
            if TS.match(v):
                return _fmt(_parse(v) + self.delta)
            if HASH.match(v) and key != "compat_digest":
                return self.h(key, v)
            return v
        if isinstance(v, list):
            return [self.value(x, key) for x in v]
        if isinstance(v, dict):
            return {k: self.value(x, k) for k, x in v.items()}
        return v


def _host(ev: dict) -> dict:
    """host-check: the canonical example Mac instead of the recording machine."""
    ev = dict(ev)
    if ev.get("type") == "check":
        ev["status"] = "pass"
        ev.pop("code", None)
        if "data" in ev:
            ev["data"] = {k: HOST.get(k, v) for k, v in ev["data"].items()}
    elif ev.get("type") == "result" and ev.get("ok"):
        ev["data"] = {k: HOST.get(k, v) for k, v in ev["data"].items()}
    return ev


def _counts(ev: dict) -> dict:
    table = COUNTS.get(ev.get("cmd") or "")
    if not table or ev.get("type") not in ("check", "result", "note") or not isinstance(ev.get("data"), dict):
        return ev
    ev = dict(ev)
    d = dict(ev["data"])
    partial = isinstance(d.get("media_present"), int) and isinstance(d.get("media_total"), int) and \
        d["media_present"] < d["media_total"]
    for k, v in table.items():
        if isinstance(d.get(k), int) and not isinstance(d.get(k), bool):
            d[k] = v
    if isinstance(d.get("media_present"), int) and "media_total" in table:
        d["media_present"] = 1200 if partial else table["media_total"]
    ev["data"] = d
    return ev


class Scrub:
    """Layer-2 safety net of the recordings: numbers the local deny list flags are nudged (maintainer machine)."""

    def __init__(self):
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            import scrub_check
            self.sc = scrub_check
            self.deny = scrub_check.DenyList.load()
        except Exception:  # noqa: BLE001 -- no scrub tooling: nothing to nudge against
            self.sc, self.deny = None, None
        self.nudged = 0

    def _denied(self, n: int) -> bool:
        return bool(self.deny) and self.sc.hmac_hex(self.deny.key, self.sc.number_key(n)) in self.deny.digests

    def number(self, v):
        if self.deny is None or isinstance(v, bool) or not isinstance(v, int) or v < 100:
            return v
        n = v
        while self._denied(n):
            n += 1
        if n != v:
            self.nudged += 1
        return n

    def value(self, v):
        if isinstance(v, dict):
            return {k: self.value(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        return self.number(v)


def canonical(lines: list[dict], scrub: Scrub | None = None) -> list[dict]:
    first = next(e["ts"] for e in lines if "ts" in e)
    c = Canon(first)
    out = []
    for e in lines:
        if "mock" in e:
            out.append(e)
            continue
        if e.get("cmd") == "host-check":
            if e.get("type") == "note":
                continue                                   # W_FILEVAULT_OFF etc. describe the recording Mac
            e = _host(e)
        e = c.value(_counts(e))
        if scrub is not None:
            e = {k: (scrub.value(v) if k not in ("v", "seq", "ts", "cmd", "type") else v) for k, v in e.items()}
        out.append(e)
    _renumber(out)
    return out


def _renumber(lines: list[dict]) -> None:
    """seq stays gapless after dropping host-check notes."""
    seq = 0
    for e in lines:
        if "mock" in e:
            if e["mock"] == "invoke":
                seq = 0
            continue
        seq += 1
        e["seq"] = seq


def structure(lines: list[dict]) -> list[tuple]:
    """What the app reacts to: directives, event types, ids, codes, states -- not values, not progress counts."""
    out: list[tuple] = []
    for e in lines:
        if "mock" in e:
            k = e["mock"]
            if k == "scenario":
                out.append(("scenario", e["name"], e["expect_screen"]))
            elif k == "invoke":
                out.append(("invoke", e["cmd"], tuple(a for a in e["args"] if a.startswith("--")),
                            tuple(e["secrets"])))
            elif k == "user":
                out.append(("user", e["screen"], e["answer"]))
            elif k == "exit":
                out.append(("exit", e["code"], bool(e.get("crash"))))
            else:
                out.append((k,))
            continue
        t = e["type"]
        if t == "progress":
            if out and out[-1][:2] == ("progress", e["phase"]):
                continue
            out.append(("progress", e["phase"]))
        elif t == "check":
            out.append(("check", e["id"], e["status"], e.get("code")))
        elif t == "phase":
            out.append(("phase", e["phase"], e["index"], e["count"]))
        elif t in ("note", "retry"):
            out.append((t, e.get("code") or e.get("reason")))
        elif t == "device":
            out.append(("device", e["state"]))
        elif t == "prompt":
            out.append(("prompt", e["kind"], e["active"]))
        elif t == "critical":
            out.append(("critical", e["on"]))
        elif t == "result":
            out.append(("result", e["code"], e["ok"], e["device_modified"],
                        (e["data"] or {}).get("verdict"), tuple(sorted((e["data"] or {}).get("notes") or []))))
        else:
            out.append((t,))
    return out


def record(name: str, base: Path, scrub: Scrub | None = None) -> tuple[list[dict], object]:
    from tests.e2e.flow import Flow
    f = Flow(name, base).run()
    if f.end_screen != f.sc.expect_screen:
        raise SystemExit(f"{name}: the flow ended on {f.end_screen}, the scenario expects {f.sc.expect_screen}")
    return canonical(f.lines, scrub), f


def write(name: str, lines: list[dict], out_dir: Path) -> Path:
    p = out_dir / f"{name}.jsonl"
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for e in lines:
            fh.write(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n")
    return p


def load(p: Path) -> list[dict]:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    from tmcore.fake import scenario as S
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--check", action="store_true", help="compare fresh recordings with the checked-in files")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    names = a.names or S.names()
    if a.list:
        print("\n".join(names))
        return 0
    drift = 0
    scrub = None if a.check else Scrub()
    if scrub is not None and scrub.deny is None:
        print("note: no local scrub deny list -- numbers are not checked against layer 2 (run the pre-push scrub)")
    with tempfile.TemporaryDirectory(prefix="record-scenarios-") as tmp:
        for name in names:
            lines, f = record(name, Path(tmp) / name, scrub)
            if a.check:
                ref = a.out / f"{name}.jsonl"
                if not ref.is_file():
                    print(f"DRIFT {name}: no recording checked in")
                    drift += 1
                    continue
                want, got = structure(load(ref)), structure(lines)
                if want != got:
                    drift += 1
                    i = next((i for i, (x, y) in enumerate(zip(want, got)) if x != y), min(len(want), len(got)))
                    print(f"DRIFT {name}: first difference at item {i}: "
                          f"recorded {want[i] if i < len(want) else None} vs engine {got[i] if i < len(got) else None}")
                else:
                    print(f"ok    {name}")
            else:
                p = write(name, lines, a.out)
                print(f"{name}: {len(lines)} lines, ends {f.end_screen} -> {p.relative_to(REPO)}")
                if scrub is not None and scrub.deny is not None:
                    left = scrub.sc.scan_text_layer2(str(p.relative_to(REPO)), p.read_text(encoding="utf-8"),
                                                     scrub.deny)
                    if left:
                        print(f"SCRUB {name}: {len(left)} line(s) still flagged by layer 2 -- re-record")
                        drift += 1
    if scrub is not None and scrub.nudged:
        print(f"layer-2 safety net: {scrub.nudged} number(s) moved off the local deny list")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
