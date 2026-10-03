#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Snapshot the ad-hoc signed simulator build (build-threema-sim.sh) to sim/Threema.app and check it carries the
# simulated entitlements section (needed for group.ch.threema container + keychain access group).
# Do NOT re-sign with `codesign --entitlements`: launchd_sim then refuses to spawn the app (EPERM, "Launchd job spawn failed").
set -euo pipefail
ROOT="${TCT_SIM_ROOT:-$HOME/.cache/threema-chat-transfer-sim}"   # simulator workspace (ref/, build/, sim/)
SRC="$ROOT/build/DerivedData/Build/Products/Debug-iphonesimulator/Threema.app"
DST="$ROOT/sim/Threema.app"
otool -l "$SRC/Threema" | grep -q __entitlements || { echo "no __entitlements section: rebuild with build-threema-sim.sh"; exit 1; }
rm -rf "$DST"; cp -Rp "$SRC" "$DST"
echo "ready: $DST"
