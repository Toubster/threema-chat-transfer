#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# make-source-bundle.sh [--offline] -- build/dist/source-bundle.tar.gz, the "Corresponding Source" attached to every
# release next to the git tag (DESIGN §11.1, AGPL-3.0 §6):
#   threema-chat-transfer-<version>-source/project/       git archive of HEAD (tracked files only = what the scrub check covered)
#   threema-chat-transfer-<version>-source/sdists/        the sdists of every GPL component (packaging/source-bundle.lock) and the
#                                             vendored single-module sdists (packaging/sdist-vendor.lock), sha256-checked
#   threema-chat-transfer-<version>-source/REFERENCES.txt python-build-standalone release + CPython source, Threema iOS model commit
#   threema-chat-transfer-<version>-source/SHA256SUMS
# Refuses a dirty work tree (the bundle must equal a commit) unless TM_ALLOW_DIRTY_SOURCE=1 (local tests only; the
# bundle then still contains HEAD, never uncommitted changes) and a GPL set that differs from source-bundle.lock.
# Network: files.pythonhosted.org only, and only for sdists missing from build/cache/sdists. Owner: pack.
source "$(dirname "$0")/lib.sh"
OFFLINE=0; [ "${1:-}" = "--offline" ] && OFFLINE=1
VERSION="$(tm_version)"
DIST="$BUILD/dist"; CACHE="$BUILD/cache/sdists"; WORK="$BUILD/source-bundle"
LOCK="$PKG_DIR/source-bundle.lock"; VLOCK="$PKG_DIR/sdist-vendor.lock"
mkdir -p "$DIST" "$CACHE"; rm -rf "$WORK"; mkdir -p "$WORK/sdists"

if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
  [ "${TM_ALLOW_DIRTY_SOURCE:-0}" = 1 ] || die "work tree has uncommitted changes: commit first (the source bundle is HEAD)"
  log "WARNING: uncommitted changes are NOT in the bundle (TM_ALLOW_DIRTY_SOURCE=1)"
fi

# the GPL set of the lock must still match licenses.txt + requirements.lock (no network: names only)
/usr/bin/python3 -I - "$REPO/core/requirements.lock" "$PKG_DIR/licenses.txt" "$LOCK" <<'PY' || die "source-bundle.lock is out of date (packaging/tools/lock_sdists.py)"
import re, sys
canon = lambda n: re.sub(r"[-_.]+", "-", n).lower()
pins = {canon(m.group(1)): m.group(2) for l in open(sys.argv[1]) if (m := re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", l))}
gpl = set()
for raw in open(sys.argv[2]):
    l = raw.split("#", 1)[0].strip()
    if l and not l.startswith(("ALLOW ", "@runtime ")):
        n, e = re.split(r"\s+", l, maxsplit=1)
        if "GPL" in e and canon(n) in pins:
            gpl.add((canon(n), pins[canon(n)]))
locked = {(l.split()[0], l.split()[1]) for l in open(sys.argv[3]) if l.strip() and not l.startswith("#")}
sys.exit(0 if gpl == locked else 1)
PY

fetch() {  # name version sha256 url
  local f; f="$CACHE/$(basename "$4")"
  if [ ! -f "$f" ] || [ "$(sha256_of "$f")" != "$3" ]; then
    [ "$OFFLINE" = 1 ] && die "offline and $(basename "$4") not in build/cache/sdists"
    case "$4" in https://files.pythonhosted.org/*) ;; *) die "unexpected sdist host for $1" ;; esac
    curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error --retry 3 -o "$f.part" "$4"
    mv "$f.part" "$f"
  fi
  [ "$(sha256_of "$f")" = "$3" ] || { rm -f "$f"; die "sha256 mismatch for $1 $2"; }
  cp "$f" "$WORK/sdists/"
}
n=0
while read -r name ver sha url; do fetch "$name" "$ver" "$sha" "$url"; n=$((n + 1)); done \
  < <(grep -vE '^\s*(#|$)' "$LOCK")
while read -r name ver sha url _module _lic; do
  # sdist-vendor.lock caches next to the wheels (build-core.sh); reuse that copy when present
  [ -f "$BUILD/cache/$(basename "$url")" ] && [ ! -f "$CACHE/$(basename "$url")" ] && cp "$BUILD/cache/$(basename "$url")" "$CACHE/"
  fetch "$name" "$ver" "$sha" "$url"; n=$((n + 1))
done < <(grep -vE '^\s*(#|$)' "$VLOCK")
log "$n sdists (sha256 ok)"

commit="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" archive --format=tar -o "$WORK/project.tar" HEAD
SRC_FILE="$REPO/model/V56/SOURCE"
cat > "$WORK/REFERENCES.txt" <<TXT
$APP_NAME $VERSION -- Corresponding Source (GNU AGPL-3.0 section 6)

project/   the complete source of $APP_NAME at commit $commit (git tag v$VERSION)
sdists/    source distributions of the bundled GPL components and of the vendored single-module packages,
           exactly the versions in the app bundle (core/requirements.lock, packaging/sdist-vendor.lock)

Python runtime: python-build-standalone release $(kv "$PKG_DIR/python.lock" PBS_RELEASE), CPython $(kv "$PKG_DIR/python.lock" PBS_PYTHON)
  binary   $(kv "$PKG_DIR/python.lock" PBS_URL)
  sha256   $(kv "$PKG_DIR/python.lock" PBS_SHA256)
  build    https://github.com/astral-sh/python-build-standalone/tree/$(kv "$PKG_DIR/python.lock" PBS_RELEASE)
  CPython  https://www.python.org/ftp/python/$(kv "$PKG_DIR/python.lock" PBS_PYTHON)/Python-$(kv "$PKG_DIR/python.lock" PBS_PYTHON).tar.xz

Threema iOS Core Data model (model/V56, AGPL-3.0-only): $(kv "$SRC_FILE" REPO_URL | sed 's/\.git$//')
  tag $(kv "$SRC_FILE" TAG), commit $(kv "$SRC_FILE" COMMIT | awk '{print $1}')
  rebuild and compare: model/build-momd.sh --verify
TXT
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$REPO" log -1 --format=%ct HEAD)}" \
  /usr/bin/python3 -I -B "$PKG_DIR/tools/source_bundle.py" "$DIST/source-bundle.tar.gz" \
  "$APP_SLUG-$VERSION-source" "$WORK/project.tar" "$WORK/sdists" "$WORK/REFERENCES.txt"
log "build/dist/source-bundle.tar.gz (commit ${commit:0:12})"
