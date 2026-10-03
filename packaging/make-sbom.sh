#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# make-sbom.sh [App.app] -- release metadata next to the DMG (DESIGN §11.1, .github/workflows/release.yml):
#   build/dist/sbom.cdx.json             CycloneDX 1.5 SBOM read from the assembled app (tools/make_sbom.py)
#   build/dist/THIRD_PARTY_LICENSES.txt  copy of the bundle's legal/THIRD_PARTY_LICENSES (licence allow-list enforced
#                                        when it was generated, stage-resources.sh)
# No network. Owner: pack.
source "$(dirname "$0")/lib.sh"
APP="${1:-$BUILD/dist/$APP_NAME.app}"
RES="$APP/Contents/Resources"
[ -x "$RES/core/python/bin/python3" ] || die "no assembled app at $APP (build-app.sh first)"
DIST="$BUILD/dist"; mkdir -p "$DIST"
/usr/bin/python3 -I -B "$PKG_DIR/tools/make_sbom.py" "$RES" "$DIST/sbom.cdx.json" || die "SBOM failed"
/usr/bin/python3 -I -c 'import json,sys; b=json.load(open(sys.argv[1])); assert b["bomFormat"]=="CycloneDX" and b["components"]' \
  "$DIST/sbom.cdx.json" || die "SBOM malformed"
cp "$RES/legal/THIRD_PARTY_LICENSES" "$DIST/THIRD_PARTY_LICENSES.txt"
log "build/dist/sbom.cdx.json + THIRD_PARTY_LICENSES.txt"
