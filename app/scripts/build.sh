#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Build the SwiftUI app (arm64, ad-hoc signature, no team). The engine is not bundled here (packaging/build-app.sh).
#   app/scripts/build.sh            Debug build into build/app/DerivedData
#   CONFIG=Release app/scripts/build.sh
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "${here}/.." && pwd)"
config="${CONFIG:-Debug}"
derived="${DERIVED:-${repo}/build/app/DerivedData}"
command -v xcodegen >/dev/null 2>&1 || { echo "build.sh: xcodegen missing (brew install xcodegen)" >&2; exit 2; }
python3 "${repo}/scripts/gen_codes.py" --check
xcodegen generate --spec "${here}/project.yml" --quiet
exec xcodebuild -project "${here}/ThreemaChatTransfer.xcodeproj" -scheme ThreemaChatTransfer -configuration "${config}" \
    -destination 'platform=macOS,arch=arm64' -derivedDataPath "${derived}" \
    CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= build
