#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Build Threema (consumer scheme, Debug) for the iOS Simulator, ad-hoc signed ("Sign to Run Locally", no team/profile).
# Ad-hoc signing matters: only then Xcode links Threema.app-Simulated.xcent into __TEXT,__entitlements, which the
# simulator uses for app groups (group.ch.threema) + keychain access group. A CODE_SIGNING_ALLOWED=NO build compiles
# fine but has no entitlements, and re-signing with entitlements via codesign makes launchd refuse to spawn (EPERM).
# NOTE: do NOT pass -sdk iphonesimulator: it forces SDKROOT on the swift-syntax macro host tool (ThreemaMacrosMacros)
# which then gets built as a simulator binary => "produced malformed response" errors everywhere.
# Prereq: a threema-ios checkout in $ROOT/ref/threema-ios with its binary dependencies built as described there
# (libthreema.xcframework, SaltyRTC FFI, WebRTC.xcframework).
set -o pipefail
ROOT="${TCT_SIM_ROOT:-$HOME/.cache/threema-chat-transfer-sim}"   # simulator workspace (ref/, build/, sim/)
cd "$ROOT/ref/threema-ios" || { echo "no threema-ios checkout in $ROOT/ref/threema-ios"; exit 1; }
xcodebuild build \
  -project Threema.xcodeproj -scheme Threema -configuration Debug \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath "$ROOT/build/DerivedData" \
  -clonedSourcePackagesDirPath "$ROOT/build/SourcePackages" \
  -skipPackagePluginValidation -skipMacroValidation \
  ARCHS=arm64 CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= \
  PROVISIONING_PROFILE_SPECIFIER= "$@"
echo "XCODEBUILD EXIT $?"
