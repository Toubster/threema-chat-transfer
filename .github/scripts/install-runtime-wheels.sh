#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# install-runtime-wheels.sh <python> -- the bundle's runtime wheels from core/requirements.lock (hash-pinned, binary
# only, no dependency resolution: DESIGN §3.3) into <python>'s site-packages. Fallback when the bundled runtime cannot
# be built; with CI_STRICT=1 an empty lock is an error.
set -euo pipefail
py="${1:?usage: install-runtime-wheels.sh <python>}"
if grep -qE '^[A-Za-z0-9_.-]+==' core/requirements.lock; then
  "$py" -m pip install --require-hashes --only-binary :all: --no-deps -r core/requirements.lock
elif [ "${CI_STRICT:-}" = "1" ]; then
  echo "::error title=runtime wheels::core/requirements.lock has no pinned wheels"
  exit 1
else
  echo "::notice title=runtime wheels not pinned yet::core/requirements.lock is empty; tests that need pymobiledevice3 & co. are skipped"
fi
