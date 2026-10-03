# SPDX-License-Identifier: AGPL-3.0-or-later
# packaging/lib.sh -- shared helpers for the packaging scripts (sourced, never executed). Owner: pack.
# Layout (all under the git-ignored build/ at the repo root):
#   build/cache/            downloads (python-build-standalone tarball)
#   build/python-runtime/   fetched + trimmed runtime (fetch-python.sh)
#   build/stage/core/       bundle "core" folder: python/ with tmcore + pinned wheels in site-packages (build-core.sh)
#   build/stage/bin/        threema-import (build-importer.sh)
#   build/app/              Xcode archive / exported .app (build-app.sh)
#   build/dist/             signed .app, .dmg, SHA256SUMS (sign-adhoc.sh, make-dmg.sh)
set -euo pipefail
PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$PKG_DIR/.." && pwd)"
BUILD="${TM_BUILD_DIR:-$REPO/build}"
APP_NAME="${TM_APP_NAME:-Chat Transfer for Threema}"   # display name = bundle name ({App}); "<App>.app"
APP_SLUG="${TM_APP_SLUG:-threema-chat-transfer}"       # file names: DMG, source bundle, repository
XC_NAME="${TM_XC_NAME:-ThreemaChatTransfer}"           # Xcode project, scheme and Swift module (no spaces)
PY_MINOR="3.13"

log()  { printf '[%s] %s\n' "$(basename "$0")" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(basename "$0")" "$*" >&2; exit 1; }

# kv FILE KEY -> value of KEY=... (files are parsed, never sourced)
kv() {
  local v
  v="$(grep -E "^$2=" "$1" | head -n1 | cut -d= -f2-)" || true
  [ -n "$v" ] || die "$2 missing in $1"
  printf '%s' "$v"
}

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }

# version from tmcore (one number for app, core and importer, DESIGN §5.7)
tm_version() { sed -nE 's/^__version__ = "([^"]+)".*/\1/p' "$REPO/core/tmcore/__init__.py"; }

# reproducible timestamps (PDF dates, file times on the DMG, SBOM, source bundle): SOURCE_DATE_EPOCH, else the commit
# time of HEAD -- never the wall clock of the build machine
tm_source_date_epoch() {
  if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then printf '%s' "$SOURCE_DATE_EPOCH"; return; fi
  git -C "$REPO" log -1 --format=%ct HEAD 2>/dev/null || die "SOURCE_DATE_EPOCH unset and no git HEAD"
}

require_arm64_macos() {
  [ "$(uname -s)" = Darwin ] || die "macOS only"
  [ "$(uname -m)" = arm64 ] || die "arm64 (Apple silicon) only"
}

# tree_digest DIR -> one sha256 over every file, symlink and directory below DIR (path, mode, content)
tree_digest() { /usr/bin/python3 -I -B "$PKG_DIR/tools/tree_digest.py" "$1"; }

# python-build-standalone asset name from python.lock (composed: a literal "+<release>" reads like a phone number to
# the scrub check)
pbs_file() {
  local l="$PKG_DIR/python.lock"
  printf 'cpython-%s+%s-%s-%s.tar.gz' "$(kv "$l" PBS_PYTHON)" "$(kv "$l" PBS_RELEASE)" "$(kv "$l" PBS_TRIPLE)" "$(kv "$l" PBS_FLAVOR)"
}
