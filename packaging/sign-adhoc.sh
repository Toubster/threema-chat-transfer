#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# sign-adhoc.sh [App.app] -- ad-hoc signature from the inside out (DESIGN §14.1 step 5). Owner: pack.
#
#   1. every Mach-O inside the bundle (python3.13, libpython, every .so/.dylib, threema-import, ...), deepest first
#   2. nested bundles (*.framework, *.xpc, *.appex, *.bundle, *.app below Contents), deepest first
#   3. Resources/bundle-manifest.json refreshed (signing changed the Mach-O bytes), then the app bundle itself
# each with `codesign --force --sign - --timestamp=none`. Never --deep when signing (it would sign in the wrong order
# and hide unsigned code).
#
# Choices without an Apple Developer team (documented in packaging/README.md):
#   * no Hardened Runtime (--options runtime): with an ad-hoc signature there is no Team ID, so library validation
#     would refuse every bundled .so/.dylib the interpreter loads; the runtime also gives nothing for Gatekeeper
#     without notarization.
#   * no App Sandbox entitlement: it would block /var/run/usbmuxd (DESIGN §14.1 step 4).
#   * no entitlements at all; no timestamp (ad-hoc signatures cannot carry one).
source "$(dirname "$0")/lib.sh"
APP="${1:-$BUILD/dist/$APP_NAME.app}"
[ -d "$APP/Contents" ] || die "no app bundle at $APP"
SIGN=(codesign --force --sign - --timestamp=none)

n=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  "${SIGN[@]}" "$f" 2>/dev/null || die "codesign failed: ${f#$APP/}"
  n=$((n + 1))
done < <(/usr/bin/python3 -I -B "$PKG_DIR/tools/find_macho.py" "$APP/Contents")
log "signed $n Mach-O files"

b=0
while IFS= read -r d; do
  [ -n "$d" ] || continue
  "${SIGN[@]}" "$d" 2>/dev/null || die "codesign failed: ${d#$APP/}"
  b=$((b + 1))
done < <(find "$APP/Contents" -depth -type d \( -name '*.framework' -o -name '*.xpc' -o -name '*.appex' \
                                               -o -name '*.bundle' -o -name '*.app' \) | awk '{print length, $0}' \
         | sort -rn | cut -d' ' -f2-)
log "signed $b nested bundles"

# signing rewrote every Mach-O (threema-import included): refresh the selftest digests now, so the bundle signature
# below seals the final manifest (tmcore selftest, phase "bundle")
[ ! -d "$APP/Contents/Resources/core" ] || /usr/bin/python3 -I -B "$PKG_DIR/tools/bundle_manifest.py" "$APP/Contents/Resources" \
  || die "bundle manifest failed"

"${SIGN[@]}" "$APP" || die "codesign failed for the app bundle"
codesign --verify --strict --deep "$APP" || die "signature does not verify"
log "ad-hoc signed and verified: $(basename "$APP")"
