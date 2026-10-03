#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Run the app tests with the MockEngine only (never a device, never the network).
#   app/scripts/test.sh unit     unit + snapshot/size tests (ThreemaChatTransferTests)
#   app/scripts/test.sh ui       XCUITests: every scenario in DE + EN, VoiceOver labels (ThreemaChatTransferUITests)
#   app/scripts/test.sh          both
# Snapshot PNGs of the size checks land in build/app/snapshots/ (review only, never committed).
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "${here}/.." && pwd)"
derived="${DERIVED:-${repo}/build/app/DerivedData}"
what="${1:-all}"
python3 "${repo}/scripts/gen_codes.py" --check
xcodegen generate --spec "${here}/project.yml" --quiet
set -- -project "${here}/ThreemaChatTransfer.xcodeproj" -scheme ThreemaChatTransfer -destination 'platform=macOS,arch=arm64' \
    -derivedDataPath "${derived}" CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM=
case "${what}" in
    unit) set -- "$@" -only-testing:ThreemaChatTransferTests ;;
    ui) set -- "$@" -only-testing:ThreemaChatTransferUITests ;;
    all) ;;
    *) echo "usage: test.sh [unit|ui|all]" >&2; exit 2 ;;
esac
export TM_SNAPSHOT_DIR="${TM_SNAPSHOT_DIR:-${repo}/build/app/snapshots}"
exec env TEST_RUNNER_TM_SNAPSHOT_DIR="${TM_SNAPSHOT_DIR}" xcodebuild test "$@"
