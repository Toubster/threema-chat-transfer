#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# stage-resources.sh -- everything below <App>/Contents/Resources that is not the Swift app itself
# (DESIGN §3.1, §11.1): build/stage/Resources/
#   core/            (build-core.sh)      bin/threema-import   (build-importer.sh)
#   models/<id>/ThreemaData.momd          one per entry of compat/threema-ios.json
#   compat/{ios,threema-ios,android}.json
#   legal/           LICENSE NOTICE TRADEMARKS.md THIRD_PARTY_LICENSES MODEL.md SOURCE.txt
#   bundle-manifest.json                  sha256 of engine, importer, models, compat, legal (tools/bundle_manifest.py)
# Runs build-core.sh / build-importer.sh first when their output is missing. Owner: pack.
source "$(dirname "$0")/lib.sh"
RES="$BUILD/stage/Resources"
[ -x "$RES/core/python/bin/python3" ] || "$PKG_DIR/build-core.sh"
[ -x "$RES/bin/threema-import" ] || "$PKG_DIR/build-importer.sh"

rm -rf "$RES/models" "$RES/compat" "$RES/legal"
mkdir -p "$RES/models" "$RES/compat" "$RES/legal"

# models: exactly the ones compat/threema-ios.json names, at the path it names
/usr/bin/python3 -I - "$REPO" "$RES" <<'PY'
import json, os, shutil, sys
repo, res = sys.argv[1:]
for m in json.load(open(os.path.join(repo, "compat", "threema-ios.json")))["models"]:
    src = os.path.join(repo, "model", m["id"], "ThreemaData.momd")
    dst = os.path.join(res, m["momd"])
    if not m["momd"].startswith(f"models/{m['id']}/") or not os.path.isdir(src):
        sys.exit(f"stage-resources: model {m['id']} missing or momd path unexpected")
    shutil.copytree(src, dst)
    print(f"[stage-resources.sh] model {m['id']}")
PY
cp "$REPO"/compat/ios.json "$REPO"/compat/threema-ios.json "$REPO"/compat/android.json "$RES/compat/"
cp "$REPO/LICENSE" "$REPO/NOTICE" "$REPO/TRADEMARKS.md" "$RES/legal/"
cp "$REPO/model/README.md" "$RES/legal/MODEL.md"
SITE="$RES/core/python/lib/python$PY_MINOR/site-packages"
/usr/bin/python3 -I "$PKG_DIR/tools/third_party_licenses.py" "$SITE" \
  "$RES/core/python/lib/python$PY_MINOR/LICENSE.txt" "$RES/legal/THIRD_PARTY_LICENSES" >&2
commit="$(git -C "$REPO" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"   # short: a 40-hex token trips the scrub of the DMG content
dirty="$(git -C "$REPO" status --porcelain 2>/dev/null | head -n1)"
cat > "$RES/legal/SOURCE.txt" <<TXT
$APP_NAME $(tm_version)
Corresponding Source (AGPL-3.0 §6): the git tag v$(tm_version) of the project repository (commit $commit) and source-bundle.tar.gz on its release page$([ -n "$dirty" ] && echo " (built from a modified work tree)")
Python runtime: python-build-standalone $(kv "$PKG_DIR/python.lock" PBS_RELEASE), CPython $(kv "$PKG_DIR/python.lock" PBS_PYTHON)
Threema iOS model source: see MODEL.md
TXT
find "$RES" -name '.DS_Store' -delete
# digests of everything that decides what the engine does, checked by `tmcore selftest` (phase "bundle")
/usr/bin/python3 -I -B "$PKG_DIR/tools/bundle_manifest.py" "$RES"
log "staged $(du -sh "$RES" | cut -f1) in build/stage/Resources"
