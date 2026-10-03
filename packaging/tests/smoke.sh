#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# smoke.sh -- end-to-end test of the packaging chain WITHOUT the real app (until app/project.yml exists):
# a stub .app (one tiny arm64 executable + Info.plist) goes through build-app.sh --prebuilt, sign-adhoc.sh,
# verify-bundle.sh, make-dmg.sh and verify-bundle.sh --dmg. Everything below build/ (git-ignored). Owner: pack.
#
#   packaging/tests/smoke.sh [--offline]
source "$(dirname "$0")/../lib.sh"
require_arm64_macos
OFF=""; [ "${1:-}" = "--offline" ] && OFF="--offline"
VERSION="$(tm_version)"
STUB="$BUILD/smoke/$APP_NAME.app"
rm -rf "$BUILD/smoke"; mkdir -p "$STUB/Contents/MacOS" "$STUB/Contents/Resources"
cat > "$BUILD/smoke/main.swift" <<'SWIFT'
// stub executable for packaging/tests/smoke.sh -- not the app
print("stub")
SWIFT
xcrun swiftc -O -target arm64-apple-macosx14.0 -o "$STUB/Contents/MacOS/$APP_NAME" "$BUILD/smoke/main.swift" >/dev/null
cat > "$STUB/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>org.example.threema-chat-transfer.stub</string>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleExecutable</key><string>$APP_NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${VERSION%%-*}</string>
  <key>CFBundleVersion</key><string>$(tr -d '[:space:]' < "$PKG_DIR/CFBundleVersion")</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
PLIST
[ -x "$BUILD/stage/Resources/core/python/bin/python3" ] || "$PKG_DIR/build-core.sh" $OFF
"$PKG_DIR/build-app.sh" --prebuilt "$STUB"
"$PKG_DIR/sign-adhoc.sh"
"$PKG_DIR/verify-bundle.sh"
TM_REPO_URL="https://example.org/threema-chat-transfer/releases" "$PKG_DIR/make-dmg.sh"
"$PKG_DIR/verify-bundle.sh" --dmg "$BUILD/dist/$APP_SLUG-$VERSION.dmg"
log "SMOKE OK (stub app; the real app replaces the stub once app/project.yml exists)"
