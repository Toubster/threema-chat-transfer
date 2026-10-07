#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# verify-bundle.sh -- checks of a signed app (DESIGN §14.1 step 6, §12 package-smoke). Owner: pack.
#
#   packaging/verify-bundle.sh [App.app] [--dmg FILE] [--quarantine]
#
#   1. codesign --verify --strict --deep; every Mach-O signed and arm64-only; minimum macOS 14.0 on our binaries
#   2. Info.plist (LSMinimumSystemVersion 14.0, versions), Resources layout, nothing that must not ship
#      (sqlite/zip data, pip, tests, .DS_Store, group/world-writable files)
#   3. packaging/tests/check-core.sh on Contents/Resources: every engine module imports under python3 -I -B from
#      the bundle only, `tmcore version` is schema-shaped, nothing is written into the bundle or HOME
#   4. the whole bundle is byte-identical before and after (tree digest) and the signature still verifies
#   5. size budget: app <= 200 MB; with --dmg: DMG <= 90 MB, hdiutil verify, signature, expected content, and the
#      privacy of the image: no extended attribute on any item (com.apple.provenance fingerprints the build Mac), no
#      build-machine name or path anywhere on the volume (.DS_Store included), the repository account only inside
#      repository links, neutral PDF metadata (SOURCE_DATE_EPOCH), HFS+ volume dates in UTC (no time-zone hint)
#   6. --quarantine (CI/VM only): ditto copy into a path with spaces and umlauts, com.apple.quarantine on every
#      file, then step 3 from there. Off by default: on a desktop Mac Gatekeeper may show a dialog.
source "$(dirname "$0")/lib.sh"
APP="$BUILD/dist/$APP_NAME.app"; DMG=""; QUAR=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dmg) DMG="$2"; shift 2 ;;
    --quarantine) QUAR=1; shift ;;
    *) APP="$1"; shift ;;
  esac
done
[ -d "$APP/Contents" ] || die "no app bundle at $APP"
RES="$APP/Contents/Resources"
fails=0
ok()  { log "OK   $*"; }
bad() { log "FAIL $*"; fails=$((fails + 1)); }

# --- 1. signatures and architectures ---
codesign --verify --strict --deep "$APP" 2>/dev/null && ok "codesign --verify --strict --deep" || bad "codesign --verify --strict --deep"
nm=0; unsigned=0; nonarm=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  nm=$((nm + 1))
  codesign --verify --strict "$f" 2>/dev/null || { unsigned=$((unsigned + 1)); log "     unsigned: ${f#$APP/}"; }
  [ "$(lipo -archs "$f" 2>/dev/null)" = arm64 ] || { nonarm=$((nonarm + 1)); log "     not arm64-only: ${f#$APP/}"; }
done < <(/usr/bin/python3 -I -B "$PKG_DIR/tools/find_macho.py" "$APP/Contents")
[ "$unsigned" = 0 ] && ok "all $nm Mach-O files signed" || bad "$unsigned of $nm Mach-O files unsigned"
[ "$nonarm" = 0 ] && ok "all Mach-O files arm64-only" || bad "$nonarm Mach-O files not arm64-only"
for f in "$RES/bin/threema-import" "$APP/Contents/MacOS/"*; do
  minos="$(otool -l "$f" | awk '/LC_BUILD_VERSION/{x=1} x&&$1=="minos"{print $2; exit}')"
  [ "$minos" = "14.0" ] && ok "minos 14.0: ${f#$APP/}" || bad "minos '$minos': ${f#$APP/}"
done
flags="$(codesign -dv "$APP" 2>&1 | sed -nE 's/.*flags=0x[0-9a-f]+\(([^)]*)\).*/\1/p')"
case ",$flags," in *,runtime,*) bad "hardened runtime set (breaks ad-hoc library loading)" ;; *) ok "ad-hoc, no hardened runtime ($flags)" ;; esac

# --- 2. Info.plist, layout, forbidden content ---
PL="$APP/Contents/Info.plist"
pb() { /usr/libexec/PlistBuddy -c "Print :$1" "$PL" 2>/dev/null || true; }
VERSION="$(tm_version)"
[ "$(pb LSMinimumSystemVersion)" = "14.0" ] && ok "LSMinimumSystemVersion 14.0" || bad "LSMinimumSystemVersion"
[ "$(pb CFBundleShortVersionString)" = "${VERSION%%-*}" ] && ok "CFBundleShortVersionString ${VERSION%%-*}" || bad "CFBundleShortVersionString"
[ "$(pb TMEngineVersion)" = "$VERSION" ] && ok "TMEngineVersion $VERSION = engine version" || bad "TMEngineVersion '$(pb TMEngineVersion)' != $VERSION"
ev="$(env -i HOME=/var/empty PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
      "$RES/core/python/bin/python3" -I -B -m tmcore version 2>/dev/null | head -n1 \
      | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("engine_version",""))' 2>/dev/null || true)"
iv="$("$RES/bin/threema-import" --version 2>/dev/null | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["importer_version"])' 2>/dev/null || true)"
[ "$ev" = "$VERSION" ] && [ "$iv" = "$VERSION" ] && ok "engine_version = importer_version = $VERSION" \
  || bad "versions differ: engine '$ev', importer '$iv', expected $VERSION"
for p in core/python/bin/python3 core/tmcore/__main__.py core/schema/codes.v1.json bin/threema-import compat/ios.json compat/threema-ios.json \
         compat/android.json legal/LICENSE legal/NOTICE legal/TRADEMARKS.md legal/THIRD_PARTY_LICENSES legal/SOURCE.txt \
         legal/MODEL.md; do
  [ -e "$RES/$p" ] || bad "missing Resources/$p"
done
/usr/bin/python3 -I - "$RES" <<'PY' && ok "every compat model is bundled" || bad "compat model missing"
import json, os, sys
res = sys.argv[1]
models = json.load(open(os.path.join(res, "compat", "threema-ios.json")))["models"]
sys.exit(0 if models and all(os.path.isdir(os.path.join(res, m["momd"])) for m in models) else 1)
PY
forbidden="$(find "$APP" \( -name '*.sqlite' -o -name '*.sqlite-wal' -o -name '*.db' -o -name '*.zip' -o -name '.DS_Store' \
              -o -name 'pip' -o -name 'ensurepip' -o -name 'idlelib' -o -name 'tkinter' \
              -o \( -type d \( -name tests -o -name test \) \) -o \( ! -type l \( -perm -o+w -o -perm -g+w \) \) \) \
              -print | head -n 20)"
[ -z "$forbidden" ] && ok "no data files, installers, tests or writable files" \
  || { bad "forbidden content:"; echo "$forbidden" | sed "s#^$APP/#     #" >&2; }
# no build-machine path in any file (DESIGN §10.3 "Home-Pfade"): .pyc co_filename, debug stabs, #file literals
# on GitHub Actions HOME is the generic runner home folder, which upstream wheels built on GitHub runners embed themselves;
# there only the checkout path is a leak of this build
if [ "${GITHUB_ACTIONS:-}" = true ]; then
  leak="$( (grep -rl -F -e "$REPO" "$APP" 2>/dev/null || true) | head -n 5)"
else
  leak="$( (grep -rl -F -e "$REPO" -e "$HOME/" "$APP" 2>/dev/null || true) | head -n 5)"
fi
[ -z "$leak" ] && ok "no build path or home folder in any file" \
  || { bad "build path in:"; echo "$leak" | sort -u | sed "s#^$APP/#     #" >&2; }
# login, full name and host names of this Mac; the repository account only inside repository links (bundle id!)
/usr/bin/python3 -I -B "$PKG_DIR/tools/scan_dist.py" "$APP" 2>/dev/null \
  && ok "no build-machine names; account name only in repository links" \
  || { bad "build-machine names or account name outside a link:"; { /usr/bin/python3 -I -B "$PKG_DIR/tools/scan_dist.py" "$APP" 2>&1 || true; } | sed 's/^/     /' >&2; }
# load commands: no absolute rpath or library path outside the system (e.g. the Xcode toolchain of the build Mac)
abs_lc=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if otool -l "$f" 2>/dev/null | awk '/cmd LC_RPATH/{r=1; next} /cmd LC_LOAD(_WEAK)?_DYLIB|cmd LC_REEXPORT_DYLIB/{d=1; next}
       r&&$1=="path"{print $2; r=0} d&&$1=="name"{print $2; d=0}' \
     | grep -E '^/' | grep -qvE '^/(usr/lib|System/Library)/'; then
    abs_lc=$((abs_lc + 1)); log "     absolute load path: ${f#$APP/}"
  fi
done < <(/usr/bin/python3 -I -B "$PKG_DIR/tools/find_macho.py" "$APP/Contents")
[ "$abs_lc" = 0 ] && ok "no absolute rpath/library path outside /usr/lib and /System" || bad "$abs_lc Mach-O files with absolute load paths"

# --- 3./4. engine smoke test, no writes ---
before="$(tree_digest "$APP")"
"$PKG_DIR/tests/check-core.sh" "$RES" && ok "engine smoke test (check-core.sh)" || bad "engine smoke test"
after="$(tree_digest "$APP")"
[ "$before" = "$after" ] && ok "bundle unchanged after running the engine" || bad "bundle changed while running"
codesign --verify --strict --deep "$APP" 2>/dev/null && ok "signature still valid" || bad "signature broken after run"

# --- 5. size budget ---
kb="$(du -sk "$APP" | cut -f1)"
[ "$kb" -le $((200 * 1024)) ] && ok "app size $((kb / 1024)) MB <= 200 MB" || bad "app size $((kb / 1024)) MB > 200 MB"
if [ -n "$DMG" ]; then
  [ -f "$DMG" ] || die "no DMG at $DMG"
  dk="$(( $(stat -f %z "$DMG") / 1024 ))"
  [ "$dk" -le $((90 * 1024)) ] && ok "dmg size $((dk / 1024)) MB <= 90 MB" || bad "dmg size $((dk / 1024)) MB > 90 MB"
  hdiutil verify -quiet "$DMG" && ok "hdiutil verify" || bad "hdiutil verify"
  codesign --verify "$DMG" 2>/dev/null && ok "dmg signature" || bad "dmg signature"
  MNT="$(mktemp -d "${TMPDIR:-/tmp}/tm-dmg.XXXXXX")"
  if hdiutil attach -quiet -readonly -nobrowse -noautoopen -mountpoint "$MNT" "$DMG"; then
    for e in "$APP_NAME.app" "Applications" "Zuerst lesen.pdf" "Read me first.pdf" ".background.tiff" ".DS_Store"; do
      [ -e "$MNT/$e" ] || bad "dmg lacks $e"
    done
    [ "$(readlink "$MNT/Applications")" = /Applications ] && ok "dmg layout (app, Applications link, PDFs, background)" \
      || bad "Applications link"
    codesign --verify --strict --deep "$MNT/$APP_NAME.app" 2>/dev/null && ok "app inside the dmg verifies" || bad "app inside the dmg"
    /usr/bin/python3 -I -B "$PKG_DIR/tools/scan_dist.py" "$MNT" --xattrs 2>/dev/null \
      && ok "dmg volume: no extended attributes, no build-machine names, account only in links" \
      || { bad "dmg volume privacy:"; { /usr/bin/python3 -I -B "$PKG_DIR/tools/scan_dist.py" "$MNT" --xattrs 2>&1 || true; } | tail -n 12 | sed 's/^/     /' >&2; }
    SOURCE_DATE_EPOCH="$(tm_source_date_epoch)" /usr/bin/python3 -I -B "$PKG_DIR/tools/neutralize_pdf.py" --check \
      "$MNT/Zuerst lesen.pdf" "$MNT/Read me first.pdf" && ok "PDF metadata neutral (Producer, SOURCE_DATE_EPOCH dates, content id)" \
      || bad "PDF metadata"
    dev="$(df "$MNT" | awk 'NR==2{print $1}')"
    /usr/bin/python3 -I - "${dev/\/dev\/disk//dev/rdisk}" <<'PY' && ok "HFS+ volume dates in UTC (no time-zone hint)" || bad "HFS+ volume creation date is not UTC (build with TZ=UTC)"
import struct, sys
with open(sys.argv[1], "rb") as f:
    hdr = f.read(1024 + 64)[1024:]
if hdr[:2] not in (b"H+", b"HX"):
    sys.exit(1)
create_local, modify_gmt = struct.unpack(">II", hdr[16:24])
sys.exit(0 if abs(create_local - modify_gmt) < 1500 else 1)
PY
    hdiutil detach -quiet "$MNT" || hdiutil detach -quiet -force "$MNT"
  else
    bad "hdiutil attach"
  fi
  rmdir "$MNT" 2>/dev/null || true
fi

# --- 6. quarantined copy in an awkward path ---
if [ "$QUAR" = 1 ]; then
  Q="$(mktemp -d "${TMPDIR:-/tmp}/tm-quar.XXXXXX")/Prüfung mit Leerzeichen äöü"
  mkdir -p "$Q"; ditto "$APP" "$Q/$APP_NAME.app"
  qv="0083;$(printf '%x' "$(date +%s)");Safari;"
  find "$Q/$APP_NAME.app" -exec xattr -w com.apple.quarantine "$qv" {} \;
  "$PKG_DIR/tests/check-core.sh" "$Q/$APP_NAME.app/Contents/Resources" && ok "quarantined copy in a path with spaces/umlauts" \
    || bad "quarantined copy"
  rm -rf "$(dirname "$Q")"
fi

[ "$fails" = 0 ] && { log "RESULT OK"; exit 0; }
log "RESULT $fails failure(s)"; exit 1
