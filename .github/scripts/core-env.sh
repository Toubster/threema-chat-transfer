#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# core-env.sh -- used by .github/actions/core-env. BUNDLED=true: build the bundle's core (runtime + pinned wheels) and
# test with that interpreter; otherwise use the setup-python interpreter with the pinned wheels. Test tools (pytest,
# jsonschema) always go into a separate --target folder on PYTHONPATH, never into the bundle.
set -euo pipefail
devtools="${RUNNER_TEMP:?}/devtools"
python -m pip install --quiet --target "$devtools" -r .github/ci-requirements.txt
if [ "${BUNDLED:-}" = "true" ]; then
  packaging/fetch-python.sh
  packaging/build-core.sh
  py="$PWD/build/stage/Resources/core/python/bin/python3"
else
  .github/scripts/install-runtime-wheels.sh python
  py="$(command -v python)"
fi
"$py" --version
{
  echo "PY=$py"
  echo "PYTHONPATH=$devtools"
} >> "${GITHUB_ENV:?}"
