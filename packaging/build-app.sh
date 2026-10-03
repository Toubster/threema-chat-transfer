#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# build-app.sh -- the unsigned .app (DESIGN §14.1 step 4) -> build/dist/<App>.app. Owner: pack.
#
#   packaging/build-app.sh                    xcodegen (app/project.yml -> app/ThreemaChatTransfer.xcodeproj) + xcodebuild archive
#   packaging/build-app.sh --prebuilt X.app   skip Xcode, take X.app (smoke tests with a stub app)
#
# Xcode settings forced here (app/project.yml must not fight them): CODE_SIGNING_ALLOWED=NO (sign-adhoc.sh signs
# afterwards, inside out), ENABLE_HARDENED_RUNTIME=NO, no App Sandbox (would block /var/run/usbmuxd), ARCHS=arm64,
# MARKETING_VERSION = engine version without suffix, CURRENT_PROJECT_VERSION = packaging/CFBundleVersion.
# Afterwards the staged Resources (core, bin, models, compat, legal: stage-resources.sh) are merged into
# Contents/Resources. Then: sign-adhoc.sh, verify-bundle.sh, make-dmg.sh.
source "$(dirname "$0")/lib.sh"
require_arm64_macos
PREBUILT=""
[ "${1:-}" = "--prebuilt" ] && PREBUILT="${2:?--prebuilt needs a path}"
VERSION="$(tm_version)"; MARKETING="${VERSION%%-*}"
BUNDLE_VERSION="$(tr -d '[:space:]' < "$PKG_DIR/CFBundleVersion")"
[[ "$BUNDLE_VERSION" =~ ^[0-9]+$ ]] || die "packaging/CFBundleVersion must be an integer"
DIST="$BUILD/dist"; APP="$DIST/$APP_NAME.app"
mkdir -p "$DIST"

"$PKG_DIR/stage-resources.sh"

if [ -n "$PREBUILT" ]; then
  [ -d "$PREBUILT/Contents" ] || die "not an app bundle: $PREBUILT"
  rm -rf "$APP"; ditto "$PREBUILT" "$APP"
else
  [ -f "$REPO/app/project.yml" ] || die "app/project.yml does not exist yet (owner: app, DESIGN §16 P4) -- use --prebuilt"
  command -v xcodegen >/dev/null || die "xcodegen missing (brew install xcodegen)"
  XC="$BUILD/app"; rm -rf "$XC"; mkdir -p "$XC"
  # generated next to project.yml (app/ThreemaChatTransfer.xcodeproj, git-ignored) exactly like CI does: XcodeGen keeps folder
  # references and path build settings (INFOPLIST_FILE, ...) relative to the project, so generating it elsewhere
  # breaks them. Build products and logs stay in build/app.
  log "xcodegen"
  (cd "$REPO/app" && xcodegen generate --quiet --spec project.yml) || die "xcodegen failed"
  PROJ="$REPO/app/$XC_NAME.xcodeproj"
  [ -d "$PROJ" ] || die "xcodegen did not write app/$XC_NAME.xcodeproj"
  log "xcodebuild archive (Release, arm64, unsigned) -> build/app/"
  xcodebuild -project "$PROJ" -scheme "$XC_NAME" -configuration Release -destination 'generic/platform=macOS' \
    -derivedDataPath "$XC/DerivedData" -archivePath "$XC/$XC_NAME.xcarchive" archive \
    ARCHS=arm64 ONLY_ACTIVE_ARCH=NO MACOSX_DEPLOYMENT_TARGET=14.0 \
    CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=- ENABLE_HARDENED_RUNTIME=NO \
    MARKETING_VERSION="$MARKETING" CURRENT_PROJECT_VERSION="$BUNDLE_VERSION" \
    > "$XC/xcodebuild.log" 2>&1 || { tail -40 "$XC/xcodebuild.log" >&2; die "xcodebuild failed"; }
  rm -rf "$APP"; ditto "$XC/$XC_NAME.xcarchive/Products/Applications/$APP_NAME.app" "$APP"
fi

# --- merge the staged Resources; the Xcode product must not ship any of these folders itself ---
RES="$APP/Contents/Resources"; mkdir -p "$RES"
for d in core bin models compat legal; do
  [ ! -e "$RES/$d" ] || die "the app bundle already contains Resources/$d (packaging owns it)"
done
ditto "$BUILD/stage/Resources" "$RES"
# (bundle-manifest.json is rewritten by sign-adhoc.sh after the Mach-O files are signed: it then also covers the
#  app's own engine-sandbox.sb)

# --- Info.plist sanity (DESIGN §3.3: arm64, macOS 14+; §14.2: one version number) ---
PL="$APP/Contents/Info.plist"
pb() { /usr/libexec/PlistBuddy -c "Print :$1" "$PL" 2>/dev/null || true; }
# TMEngineVersion = the full engine version incl. a pre-release suffix ("0.3.0-dev"). CFBundleShortVersionString can
# only carry the numeric part, but the app compares hello.engine_version with its own version (DESIGN §5.3/§5.7), so
# it reads this key first (devtools/CONTRACT-REQUESTS.md, pack -> app). Written before signing.
/usr/libexec/PlistBuddy -c "Delete :TMEngineVersion" "$PL" >/dev/null 2>&1 || true
/usr/libexec/PlistBuddy -c "Add :TMEngineVersion string $VERSION" "$PL" || die "cannot write TMEngineVersion"
[ "$(pb LSMinimumSystemVersion)" = "14.0" ] || die "LSMinimumSystemVersion must be 14.0 (is '$(pb LSMinimumSystemVersion)')"
[ "$(pb CFBundleShortVersionString)" = "$MARKETING" ] || die "CFBundleShortVersionString '$(pb CFBundleShortVersionString)' != $MARKETING"
[ "$(pb CFBundleVersion)" = "$BUNDLE_VERSION" ] || die "CFBundleVersion '$(pb CFBundleVersion)' != $BUNDLE_VERSION"
EXE="$APP/Contents/MacOS/$(pb CFBundleExecutable)"
[ -x "$EXE" ] && [ "$(lipo -archs "$EXE")" = arm64 ] || die "main executable missing or not arm64-only"
log "app assembled: build/dist/$APP_NAME.app ($MARKETING, build $BUNDLE_VERSION, $(du -sh "$APP" | cut -f1))"
