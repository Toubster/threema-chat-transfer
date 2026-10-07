#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# build-importer.sh -- threema-import, release, arm64 only (DESIGN §14.1 step 3) -> build/stage/Resources/bin/.
# Checks: Mach-O arm64 only, minimum macOS 14.0, `--version` equals the engine version (DESIGN §5.7). Then, if the
# core is staged, stage-resources.sh (build/stage/Resources complete).
# Owner: pack.
source "$(dirname "$0")/lib.sh"
require_arm64_macos
PKG="$REPO/importer"
# no build-machine paths in the shipped binary (DESIGN §10.3 "Home-Pfade"): source paths mapped to ".", and the
# debug symbols (object-file stabs) stripped below
SB=(swift build -c release --arch arm64 --package-path "$PKG" --product threema-import
    -Xswiftc -file-prefix-map -Xswiftc "$REPO=." -Xswiftc -debug-prefix-map -Xswiftc "$REPO=.")
log "swift build (release, arm64)"
"${SB[@]}" >"$BUILD/build-importer.log" 2>&1 || { tail -30 "$BUILD/build-importer.log" >&2; die "swift build failed"; }
BIN="$("${SB[@]}" --show-bin-path | tail -n1)/threema-import"
[ -x "$BIN" ] || die "no threema-import in the build products"

archs="$(lipo -archs "$BIN")"
[ "$archs" = arm64 ] || die "threema-import architectures: $archs (want arm64 only)"
minos="$(otool -l "$BIN" | awk '/LC_BUILD_VERSION/{f=1} f&&$1=="minos"{print $2; exit}')"
[ "$minos" = "14.0" ] || die "threema-import minimum macOS $minos (want 14.0)"
want="$(tm_version)"
got="$("$BIN" --version | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["importer_version"])')"
[ "$got" = "$want" ] || die "importer_version $got != engine_version $want"

mkdir -p "$BUILD/stage/Resources/bin"
install -m 0755 "$BIN" "$BUILD/stage/Resources/bin/threema-import"
strip -S -x "$BUILD/stage/Resources/bin/threema-import" || die "strip failed"
# SwiftPM adds an LC_RPATH into the Xcode toolchain (an absolute build-machine path, never needed on the Macs of users:
# every Swift library comes from /usr/lib/swift). Drop every absolute rpath outside /usr/lib and /System, then renew
# the ad-hoc linker signature that install_name_tool invalidates (sign-adhoc.sh signs again inside the app).
while IFS= read -r rp; do
  [ -n "$rp" ] || continue
  install_name_tool -delete_rpath "$rp" "$BUILD/stage/Resources/bin/threema-import" 2>/dev/null \
    || die "cannot delete an rpath from threema-import"
done < <(otool -l "$BUILD/stage/Resources/bin/threema-import" \
         | awk '/cmd LC_RPATH/{r=1; next} r&&$1=="path"{print $2; r=0}' | grep -E '^/' | grep -vE '^/(usr/lib|System)/' || true)
codesign --force --sign - --timestamp=none "$BUILD/stage/Resources/bin/threema-import" 2>/dev/null \
  || die "cannot re-sign threema-import after the rpath change"
[ "$("$BUILD/stage/Resources/bin/threema-import" --version | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["importer_version"])')" = "$want" ] \
  || die "threema-import does not run after the rpath change"
if grep -q -F "$REPO" "$BUILD/stage/Resources/bin/threema-import" \
   || grep -q -F "Xcode.app/" "$BUILD/stage/Resources/bin/threema-import" \
   || { [ "${GITHUB_ACTIONS:-}" != true ] && grep -q -F "$HOME" "$BUILD/stage/Resources/bin/threema-import"; }; then
  die "threema-import still contains a build path"
fi
log "threema-import $got (arm64, macOS >= $minos) -> build/stage/Resources/bin/"
# With the core already staged, complete build/stage/Resources (models, compat, legal, bundle-manifest.json) so that
# fetch-python.sh + build-core.sh + build-importer.sh leave a runnable engine tree for the demo mode and the e2e tests
# (TMCORE_RESOURCES=build/stage/Resources) -- the app adds only its own files on top (build-app.sh).
if [ -x "$BUILD/stage/Resources/core/python/bin/python3" ]; then "$PKG_DIR/stage-resources.sh"; fi
