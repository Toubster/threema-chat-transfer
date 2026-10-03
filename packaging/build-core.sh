#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# build-core.sh -- the bundle's "core" folder (DESIGN §3.1, §14.1 step 2), laid out like the repository's core/:
#   build/stage/Resources/core/python/   python-build-standalone runtime (fetch-python.sh); site-packages = the
#                                        hash-pinned wheels of core/requirements.lock + the vendored single-module
#                                        sdists of packaging/sdist-vendor.lock + tmcore.pth
#   build/stage/Resources/core/tmcore/   the engine (no tests, no caches)
#   build/stage/Resources/core/schema/   core/schema/*.v1.json (contract; tmcore.protocol reads <tmcore>/../schema)
# tmcore.pth (one relative line, resolved by site.py even under `python3 -I`) puts Resources/core on sys.path, so
# `python3 -I -B -m tmcore` works without PYTHONPATH and tmcore finds its schemas and (via core/..) the Resources
# folder exactly as in the repository (core/.. = repo root <-> core/.. = Contents/Resources).
# Then: compileall (unchecked-hash, so the signed bundle never needs to write .pyc), strip tests/caches/pip,
# import test of every module the engine uses (packaging/tests/import_set.py) under `python3 -I -B`, and a
# no-write check (the tree digest must not change while the import test runs).
#
#   packaging/build-core.sh [--offline]      # --offline: wheels only from build/cache/wheels (no network)
# Owner: pack.
source "$(dirname "$0")/lib.sh"
require_arm64_macos

OFFLINE=0; [ "${1:-}" = "--offline" ] && OFFLINE=1
RUNTIME="$BUILD/python-runtime/python"
[ -x "$RUNTIME/bin/python3" ] || "$PKG_DIR/fetch-python.sh" $([ "$OFFLINE" = 1 ] && echo --offline)
STAGE="$BUILD/stage/Resources"
CORE="$STAGE/core"
PY="$CORE/python"
SITE="$PY/lib/python$PY_MINOR/site-packages"
WHEELS="$BUILD/cache/wheels"
LOCK="$REPO/core/requirements.lock"

# a new core invalidates the selftest manifest of the staged Resources (rewritten by stage-resources.sh)
rm -rf "$CORE" "$STAGE/bundle-manifest.json"; mkdir -p "$CORE" "$WHEELS"
cp -R "$RUNTIME" "$PY"
BPY="$PY/bin/python3"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1

# --- 1. wheels: download (hash-checked) into the cache, then install from the cache only ---
if [ "$OFFLINE" = 0 ]; then
  log "downloading wheels (hash-pinned) to build/cache/wheels"
  "$BPY" -I -m pip download --quiet --require-hashes --only-binary :all: --no-deps \
      --platform macosx_14_0_arm64 --python-version "$PY_MINOR" --implementation cp \
      --dest "$WHEELS" -r "$LOCK"
fi
log "installing wheels into the runtime's site-packages"
"$BPY" -I -m pip install --quiet --require-hashes --only-binary :all: --no-deps --no-index --find-links "$WHEELS" \
    --no-compile --target "$SITE" -r "$LOCK"

# --- 2. single-module sdists (no wheel on PyPI), never executing setup.py ---
grep -vE '^\s*(#|$)' "$PKG_DIR/sdist-vendor.lock" | while read -r name ver sha url module license; do
  f="$BUILD/cache/$(basename "$url")"
  if [ ! -f "$f" ] || [ "$(sha256_of "$f")" != "$sha" ]; then
    [ "$OFFLINE" = 1 ] && die "offline and $(basename "$url") not cached"
    curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error --retry 3 -o "$f" "$url"
  fi
  [ "$(sha256_of "$f")" = "$sha" ] || die "sha256 mismatch for $name $ver"
  "$BPY" -I - "$f" "$module" "$SITE" "$name" "$ver" "$license" <<'PYEOF'
import sys, zipfile, tarfile, os, posixpath
src, module, site, name, ver, lic = sys.argv[1:]
def pick(names):
    hits = [n for n in names if posixpath.basename(n) == module and n.count("/") <= 1]
    if len(hits) != 1:
        sys.exit(f"vendor: {module} not unique in {os.path.basename(src)}")
    return hits[0]
if src.endswith(".zip"):
    with zipfile.ZipFile(src) as z:
        data = z.read(pick(z.namelist()))
else:
    with tarfile.open(src) as t:
        data = t.extractfile(pick(t.getnames())).read()
open(os.path.join(site, module), "wb").write(data)
di = os.path.join(site, f"{name}-{ver}.dist-info"); os.makedirs(di, exist_ok=True)
open(os.path.join(di, "METADATA"), "w").write(f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\nLicense: {lic}\n")
open(os.path.join(di, "INSTALLER"), "w").write("threema-chat-transfer-vendor\n")
open(os.path.join(di, "top_level.txt"), "w").write(module.removesuffix(".py") + "\n")
PYEOF
  log "vendored $name $ver ($module)"
done

# --- 3. tmcore itself (no tests, no caches) + the contract schemas, found through tmcore.pth ---
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'tests' "$REPO/core/tmcore/" "$CORE/tmcore/"
mkdir -p "$CORE/schema"
cp "$REPO"/core/schema/*.v1.json "$CORE/schema/"
printf '../../../..\n' > "$SITE/tmcore.pth"      # site-packages/../../../.. = Resources/core

# --- 4. strip: pip/setuptools (never shipped), tests, caches, type stubs, console scripts ---
rm -rf "${SITE:?}"/pip "$SITE"/pip-*.dist-info "$SITE"/setuptools* "$SITE"/_distutils_hack "$SITE"/distutils-precedence.pth \
       "${SITE:?}"/bin "$PY"/bin/pip* "$SITE/README.txt"
find "$SITE" -depth -type d \( -name tests -o -name test -o -name testing -o -name '__pycache__' \) -exec rm -rf {} +
find "$PY" \( -name '*.pyi' -o -name 'py.typed' -o -name '*.pyc' \) -delete
find "$SITE" -name '*.dist-info' -type d -exec rm -f {}/RECORD {}/INSTALLER {}/REQUESTED {}/direct_url.json \;

# --- 4b. arm64 only (DESIGN §3.3): thin universal2 wheels' binaries; every Mach-O must contain arm64 ---
thinned=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  archs="$(lipo -archs "$f")"
  case " $archs " in *" arm64 "*) ;; *) die "no arm64 slice: ${f#$CORE/}" ;; esac
  if [ "$archs" != arm64 ]; then
    lipo -thin arm64 "$f" -output "$f.thin" || die "lipo -thin failed: ${f#$CORE/}"
    chmod "$(stat -f '%Lp' "$f")" "$f.thin"; mv "$f.thin" "$f"; thinned=$((thinned + 1))
  fi
done < <(/usr/bin/python3 -I -B "$PKG_DIR/tools/find_macho.py" "$PY")
log "thinned $thinned universal binaries to arm64"

# --- 5. bytecode: compiled once at build time, never written at run time (-B, read-only bundle) ---
# co_filename of every .pyc is bundle-relative ("/Chat Transfer for Threema.app/Contents/Resources/..."), never the build path
# (it would ship the build machine's home folder, DESIGN §10.3); -f also rewrites the timestamp .pyc that pip and
# the import test left behind; the import system fixes co_filename up at load time anyway
"$BPY" -I -m compileall -q -f -j 0 --invalidation-mode unchecked-hash -s "$STAGE" -p "/Chat Transfer for Threema.app/Contents/Resources" \
  "$PY/lib/python$PY_MINOR" "$CORE/tmcore" >/dev/null || die "compileall failed"
if grep -rlq -F "$BUILD" "$CORE" 2>/dev/null || grep -rlq -F "$HOME" "$CORE" 2>/dev/null; then
  die "a staged core file still contains a build path"
fi

# --- 6. import test + no-write check ---
"$PKG_DIR/tests/check-core.sh" "$STAGE"
log "core ready: $CORE ($(du -sh "$CORE" | cut -f1))"
