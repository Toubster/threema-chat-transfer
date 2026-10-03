#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# fetch-python.sh -- download the pinned python-build-standalone runtime, verify sha256 against python.lock,
# unpack it to build/python-runtime/python and drop what the engine never needs (DESIGN §14.1 step 1).
#
#   packaging/fetch-python.sh [--offline]     # --offline: use build/cache only, never download
#
# The only network access of the whole build besides build-core.sh's wheel download. Owner: pack.
source "$(dirname "$0")/lib.sh"
require_arm64_macos

OFFLINE=0; [ "${1:-}" = "--offline" ] && OFFLINE=1
LOCK="$PKG_DIR/python.lock"
FILE="$(pbs_file)"; URL="$(kv "$LOCK" PBS_URL)"; SHA="$(kv "$LOCK" PBS_SHA256)"
PYV="$(kv "$LOCK" PBS_PYTHON)"
[ "$(basename "$URL" | sed 's/%2B/+/g')" = "$FILE" ] || die "PBS_URL does not name $FILE"
CACHE="$BUILD/cache"; OUT="$BUILD/python-runtime"
mkdir -p "$CACHE"
TARBALL="$CACHE/$FILE"

if [ ! -f "$TARBALL" ] || [ "$(sha256_of "$TARBALL")" != "$SHA" ]; then
  [ "$OFFLINE" = 1 ] && die "offline and $FILE not in $CACHE"
  log "downloading $FILE"
  rm -f "$TARBALL.part"
  curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error --retry 3 -o "$TARBALL.part" "$URL"
  mv "$TARBALL.part" "$TARBALL"
fi
GOT="$(sha256_of "$TARBALL")"
[ "$GOT" = "$SHA" ] || { rm -f "$TARBALL"; die "sha256 mismatch for $FILE (expected $SHA, got $GOT)"; }
log "sha256 ok ($SHA)"

rm -rf "$OUT"; mkdir -p "$OUT"
tar -xzf "$TARBALL" -C "$OUT"
PY="$OUT/python"
[ -x "$PY/bin/python3" ] || die "unexpected tarball layout (no python/bin/python3)"
"$PY/bin/python3" -c "import sys; assert sys.version.split()[0] == '$PYV', sys.version" || die "version != $PYV"

# --- trim: modules and files the engine never uses (tests, GUI, installer, headers, static lib) ---
STD="$PY/lib/python$PY_MINOR"
rm -rf "$STD/test" "$STD/idlelib" "$STD/tkinter" "$STD/turtledemo" "$STD/ensurepip" "$STD/turtle.py" \
       "$STD"/lib-dynload/_tkinter*.so "$STD"/lib-dynload/_test*.so "$STD"/lib-dynload/xxlimited*.so \
       "$STD"/lib-dynload/_xxtestfuzz*.so "$STD"/lib-dynload/_ctypes_test*.so \
       "$STD"/config-"$PY_MINOR"-darwin \
       "$PY"/lib/tcl* "$PY"/lib/tk* "$PY"/lib/itcl* "$PY"/lib/thread* "$PY"/lib/libtcl* "$PY"/lib/libtk* \
       "$PY/include" "$PY/share" "$PY/lib/pkgconfig" \
       "$PY"/bin/idle3* "$PY"/bin/pydoc3* "$PY"/bin/2to3*
# pip stays for build-core.sh and is removed there after the wheel install (never shipped).
find "$PY" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$PY" -name '*.pyc' -delete

"$PY/bin/python3" -I -B -c "import ssl, sqlite3, hashlib, zlib, lzma, bz2, ctypes, plistlib, json; print('runtime ok', ssl.OPENSSL_VERSION, 'sqlite', sqlite3.sqlite_version)"
du -sh "$PY" | awk '{print "[fetch-python.sh] runtime size " $1}' >&2
