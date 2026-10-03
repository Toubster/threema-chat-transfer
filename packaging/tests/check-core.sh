#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# check-core.sh RESOURCES -- smoke test of a staged or bundled Resources folder (core part), used by build-core.sh
# and verify-bundle.sh:
#   1. every engine module imports under `python3 -I -B` with an empty environment, only from inside the bundle
#   2. `tmcore version` prints schema-shaped JSON lines and exits 0; with bundle-manifest.json present (fully staged
#      Resources) also `tmcore selftest` in bundle mode (modules, manifest digests, importer version, netguard)
#   3. nothing was written: the tree digest of Resources/ and the scratch HOME are unchanged/empty
# Owner: pack.
source "$(dirname "$0")/../lib.sh"
RES="$(cd "${1:?usage: check-core.sh <Resources>}" && pwd)"
PY="$RES/core/python/bin/python3"
[ -x "$PY" ] || die "no bundled python in $RES"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/tm-check-core.XXXXXX")"
trap 'chmod -R u+w "$SCRATCH" 2>/dev/null; rm -rf "$SCRATCH"' EXIT
mkdir -p "$SCRATCH/home" "$SCRATCH/tmp"
before="$(tree_digest "$RES")"

# exactly the environment the app sets (DESIGN §5.1 + TMCORE_RESOURCES, app/Sources/Engine/LiveEngine.swift);
# nothing inherited. No TMCORE_SCHEMA_DIR: the bundle must find its schemas by itself (core/schema next to tmcore).
run_env=(env -i HOME="$SCRATCH/home" TMPDIR="$SCRATCH/tmp" PATH=/usr/bin:/bin LANG=C.UTF-8
         PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 TMCORE_PROTOCOL=1 TMCORE_RESOURCES="$RES")

out="$("${run_env[@]}" "$PY" -I -B "$PKG_DIR/tests/import_set.py" "$RES")" || { echo "$out" >&2; die "import test failed"; }
log "import test: $out"

ver="$(cd "$SCRATCH" && "${run_env[@]}" "$PY" -I -B -m tmcore version)" || { echo "$ver" >&2; die "tmcore version failed"; }
"$PY" -I -B - "$ver" <<'PYEOF' || die "tmcore version output malformed"
import json, sys
lines = [json.loads(l) for l in sys.argv[1].splitlines() if l.strip()]
assert lines[0]["type"] == "hello" and lines[-1]["type"] == "result" and lines[-1]["ok"] is True, lines[-1]
assert [l["seq"] for l in lines] == list(range(1, len(lines) + 1))
assert sum(l["type"] == "result" for l in lines) == 1
print("[check-core.sh] tmcore version: hello+result ok, engine", lines[0]["engine_version"], "models", lines[0]["models"])
PYEOF

# `tmcore selftest` in bundle mode needs the fully staged Resources (importer, models, bundle-manifest.json), i.e.
# not yet inside build-core.sh: run it whenever the manifest exists (stage-resources.sh, build-app.sh, the app)
if [ -f "$RES/bundle-manifest.json" ]; then
  st="$(cd "$SCRATCH" && "${run_env[@]}" "$PY" -I -B -m tmcore selftest)" || { printf '%s\n' "$st" | tail -n 3 >&2; die "tmcore selftest failed"; }
  "$PY" -I -B - "$st" <<'PYEOF' || die "tmcore selftest output malformed"
import json, sys
lines = [json.loads(l) for l in sys.argv[1].splitlines() if l.strip()]
checks = {l["id"]: l for l in lines if l["type"] == "check"}
assert lines[-1]["type"] == "result" and lines[-1]["ok"] is True, lines[-1]
assert all(c["status"] == "pass" for c in checks.values()), checks
b = checks["bundle_hashes"]["data"]
print(f"[check-core.sh] tmcore selftest: ok (modules {checks['modules']['data']['modules']}, manifest files {b['files']}, "
      f"models {b['models']}, importer {b['importer']})")
PYEOF
fi

after="$(tree_digest "$RES")"
[ "$before" = "$after" ] || die "the bundle changed while running (something wrote into Resources/)"
leftover="$(find "$SCRATCH/home" -mindepth 1 | wc -l | tr -d ' ')"
[ "$leftover" = 0 ] || die "running the engine wrote $leftover entries into HOME"
log "no writes into the bundle or HOME"

# without TMCORE_RESOURCES too: tmcore must locate Contents/Resources from its own position (core/..)
ver2="$(cd "$SCRATCH" && env -i HOME="$SCRATCH/home" TMPDIR="$SCRATCH/tmp" PATH=/usr/bin:/bin LANG=C.UTF-8 \
        PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 TMCORE_PROTOCOL=1 "$PY" -I -B -m tmcore version)" \
  || die "tmcore version without TMCORE_RESOURCES failed"
[ "$(printf '%s\n' "$ver" | head -n1 | sed -E 's/"ts":"[^"]*",//')" = "$(printf '%s\n' "$ver2" | head -n1 | sed -E 's/"ts":"[^"]*",//')" ] \
  || die "hello differs without TMCORE_RESOURCES (resources not found from the bundle layout)"
log "resources found with and without TMCORE_RESOURCES"
