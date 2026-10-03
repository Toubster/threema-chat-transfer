#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Build the simulator-only inject dylib (keychain identity, group defaults, DB copy, network kill-switch)
set -euo pipefail
ROOT="${TCT_SIM_ROOT:-$HOME/.cache/threema-chat-transfer-sim}"   # simulator workspace (ref/, build/, sim/)
cd "$(dirname "$0")"
mkdir -p "$ROOT/sim"
xcrun -sdk iphonesimulator clang -arch arm64 -mios-simulator-version-min=17.0 -fobjc-arc -dynamiclib \
  -framework Foundation -framework UIKit -framework CoreData -framework Security -framework UserNotifications -o "$ROOT/sim/libThreemaSimInject.dylib" ThreemaSimInject.m
codesign -f -s - "$ROOT/sim/libThreemaSimInject.dylib"
echo "built $ROOT/sim/libThreemaSimInject.dylib"
