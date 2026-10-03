#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Offline verification of a converted Threema iOS database in the iOS Simulator.
#
#   sim-verify.sh <db-dir> [identity-json]
#
#   <db-dir>        directory containing ThreemaData.sqlite (+ optional -wal/-shm, .ThreemaData_SUPPORT/)
#                   i.e. exactly what goes into the app-group container root (group.ch.threema) on the phone
#   identity-json   default $TCT_SIM_ROOT/sim/test-identity.json (gen_test_identity.py; throwaway key)
#
# What it does (only touches a DEDICATED simulator device "Threema-Verify"; never the host keychain/MobileSync):
#   1. create/erase sim device, boot it
#   2. install the self-built Threema.app (Debug, consumer scheme)
#   3. DB is copied into the app-group container by the dylib BEFORE preLaunchSetup (so no APP_SETUP_NOT_COMPLETED marker)
#   4. launch with ThreemaSimInject.dylib (keychain identity + AppSetupState=40 + AppMigratedToVersion)
#      and -isRunningForScreenshots (ServerConnector._connect returns early => no chat-server login, no contact sync)
#   5. screenshot + collect app log lines
set -euo pipefail
DB_DIR="${1:?usage: sim-verify.sh <db-dir> [identity-json]}"
ROOT="${TCT_SIM_ROOT:-$HOME/.cache/threema-chat-transfer-sim}"   # simulator workspace (ref/, build/, sim/)
ID_JSON="${2:-$ROOT/sim/test-identity.json}"
APP="${APP:-$ROOT/sim/Threema.app}"   # from prepare-sim-app.sh
DYLIB="$ROOT/sim/libThreemaSimInject.dylib"
DEV_NAME="${DEV_NAME:-Threema-Verify}"
DEV_TYPE="${DEV_TYPE:-com.apple.CoreSimulator.SimDeviceType.iPhone-18-Pro-Max}"
RUNTIME="${RUNTIME:-com.apple.CoreSimulator.SimRuntime.iOS-27-0}"
BUNDLE_ID="ch.threema.iapp"
GROUP_ID="group.ch.threema"
OUT="$ROOT/sim/run-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

[ -f "$DB_DIR/ThreemaData.sqlite" ] || { echo "no ThreemaData.sqlite in $DB_DIR"; exit 1; }
[ -d "$APP" ] || { echo "app not built: $APP"; exit 1; }
[ -f "$DYLIB" ] || { echo "dylib missing: $DYLIB"; exit 1; }

UDID=$(xcrun simctl list devices -j | /usr/bin/python3 -c "import json,sys;d=json.load(sys.stdin)['devices'];print(next((x['udid'] for r in d.values() for x in r if x['name']=='$DEV_NAME'),''))")
if [ -z "$UDID" ]; then
  UDID=$(xcrun simctl create "$DEV_NAME" "$DEV_TYPE" "$RUNTIME")
else
  xcrun simctl shutdown "$UDID" 2>/dev/null || true
  xcrun simctl erase "$UDID"
fi
echo "device $DEV_NAME $UDID"
xcrun simctl boot "$UDID"
xcrun simctl bootstatus "$UDID" -b >/dev/null

xcrun simctl install "$UDID" "$APP"
# NOTE: on the simulator the group container does not exist until the entitled app asks for it, so the DB is
# copied by ThreemaSimInject.dylib (THREEMA_SIM_DB_DIR) inside the app process before preLaunchSetup runs.
DB_DIR_ABS="$(cd "$DB_DIR" && pwd)"

# stream app logs (Threema uses CocoaLumberjack -> os_log)
xcrun simctl spawn "$UDID" log stream --style compact --level debug \
  --predicate 'process == "Threema"' > "$OUT/app.log" 2>&1 &
LOGPID=$!
sleep 1

SIMCTL_CHILD_DYLD_INSERT_LIBRARIES="$DYLIB" \
SIMCTL_CHILD_THREEMA_SIM_IDENTITY_JSON="$ID_JSON" \
SIMCTL_CHILD_THREEMA_SIM_DB_DIR="$DB_DIR_ABS" \
  xcrun simctl launch --terminate-running-process "$UDID" "$BUNDLE_ID" -isRunningForScreenshots

sleep "${WAIT_SECS:-25}"
xcrun simctl io "$UDID" screenshot "$OUT/chatlist.png"
# optional: OPEN_IDS="ABCDEFGH IJKLMNOP" opens those 1:1 chats via URL scheme (URLHandler: threema://compose?id=)
for ID in ${OPEN_IDS:-}; do
  xcrun simctl openurl "$UDID" "threema://compose?id=$ID"; sleep 6
  xcrun simctl io "$UDID" screenshot "$OUT/chat-$ID.png"
done
kill $LOGPID 2>/dev/null || true
GROUP_DIR=$(xcrun simctl get_app_container "$UDID" "$BUNDLE_ID" "$GROUP_ID" 2>/dev/null || true)
echo "group container: $GROUP_DIR"
[ -n "$GROUP_DIR" ] && ls -la "$GROUP_DIR" > "$OUT/group-container-after.txt"
[ -n "$GROUP_DIR" ] && cp -p "$GROUP_DIR"/ThreemaData.sqlite* "$OUT/" 2>/dev/null || true   # post-launch DB (after any migration)
grep -E "ThreemaSimInject|BLOCKED|AppSetup|Launch|migrat|Migrat|error|Error|fatal" "$OUT/app.log" | head -200 > "$OUT/app-important.log" || true
echo "results in $OUT (chatlist.png, app.log, post-launch DB copy)"
echo "simulator left booted: $UDID  (xcrun simctl shutdown $UDID)"
