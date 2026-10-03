#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# build-momd.sh -- rebuild the bundled Threema Core Data model from the pinned Threema iOS source and compare it
# with the committed copy (DESIGN §4.2, §6.3, §12 job "importer"). Owner: pack. Needs full Xcode (momc).
#
#   model/build-momd.sh [--model V56] [--source DIR] [--verify]
#
#   --source DIR   use an existing threema-ios checkout (must be at the pinned commit, model folder unmodified);
#                  default: shallow clone of the pinned tag into build/model/src (network: github.com only)
#   --verify       exit 1 unless everything matches (CI); without it the script only reports
#
# Checks (all must hold):
#   1. the checkout is exactly COMMIT from <model>/SOURCE and the .xcdatamodeld is unmodified
#   2. same file set as the committed <model>/ThreemaData.momd
#   3. every *.mom and VersionInfo.plist byte-identical (sha256) to the committed files -- hard only with the Xcode
#      build named in COMPILED_WITH of <model>/SOURCE; another momc may serialise differently, then a byte difference
#      is reported as NOTE and checks 4 + 5 (canonical dump, model identity) decide alone
#   4. *.omo (momc's optimized model, NOT byte-reproducible: hash-table order changes per run) equal through the
#      canonical dump (model/tools/model_dump.swift) -- built .omo == committed .omo == committed current .mom
#   5. model identity = compat/threema-ios.json: sha256(json.dumps(NSStoreModelVersionHashes, sort_keys=True)) ==
#      version_hashes_sha256 and the version identifiers match
# Output: file names, digests and OK/FAIL only.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
BUILD="${TM_BUILD_DIR:-$REPO/build}/model"
MODEL=V56; SRC=""; VERIFY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --source) SRC="$2"; shift 2 ;;
    --verify) VERIFY=1; shift ;;
    -h|--help) sed -n 2,22p "$0"; exit 0 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
done
log() { printf '[build-momd] %s\n' "$*" >&2; }
fail=0
check() { if [ "$1" = ok ]; then log "OK   $2"; else log "FAIL $2"; fail=1; fi; }
kv() { grep -E "^$2=" "$1" | head -n1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//'; }

SOURCE_FILE="$HERE/$MODEL/SOURCE"
COMMITTED="$HERE/$MODEL/ThreemaData.momd"
[ -f "$SOURCE_FILE" ] && [ -d "$COMMITTED" ] || { echo "no model $MODEL" >&2; exit 2; }
URL="$(kv "$SOURCE_FILE" REPO_URL)"; TAG="$(kv "$SOURCE_FILE" TAG)"; COMMIT="$(kv "$SOURCE_FILE" COMMIT)"
XCD="$(kv "$SOURCE_FILE" XCDATAMODELD)"; VID="$(kv "$SOURCE_FILE" VERSION_IDENTIFIER)"
xcrun --find momc >/dev/null 2>&1 || { echo "momc not found (full Xcode needed)" >&2; exit 2; }
mkdir -p "$BUILD"

# --- source at the pinned commit ---
if [ -z "$SRC" ]; then
  SRC="$BUILD/src-$MODEL"
  if [ ! -d "$SRC/.git" ]; then
    log "cloning $URL at tag $TAG (shallow)"
    git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$TAG" "$URL" "$SRC"
  fi
fi
HEAD="$(git -C "$SRC" rev-parse HEAD)"
[ "$HEAD" = "$COMMIT" ] && check ok "source commit = pinned commit" || check fail "source commit = pinned commit"
[ -z "$(git -C "$SRC" status --porcelain -- "$XCD")" ] && check ok "model source unmodified" \
  || check fail "model source unmodified"

# --- compile ---
OUT="$BUILD/$MODEL"; rm -rf "$OUT"; mkdir -p "$OUT"
xcrun momc "$SRC/$XCD" "$OUT/" >"$BUILD/momc-$MODEL.log" 2>&1 || { tail -5 "$BUILD/momc-$MODEL.log" >&2; exit 1; }
BUILT="$OUT/ThreemaData.momd"
log "momc: $(xcrun momc --version 2>/dev/null | head -n1 || true) $(xcodebuild -version 2>/dev/null | tr '\n' ' ')"

# --- 2./3. file set and deterministic files ---
( cd "$COMMITTED" && ls | LC_ALL=C sort ) > "$BUILD/files-committed.txt"
( cd "$BUILT" && ls | LC_ALL=C sort ) > "$BUILD/files-built.txt"
cmp -s "$BUILD/files-committed.txt" "$BUILD/files-built.txt" && check ok "file set ($(wc -l < "$BUILD/files-built.txt" | tr -d ' ') files)" \
  || check fail "file set"
nbad=0; nsame=0
while read -r f; do
  case "$f" in *.omo) continue ;; esac
  if [ -f "$BUILT/$f" ] && [ "$(shasum -a 256 < "$BUILT/$f")" = "$(shasum -a 256 < "$COMMITTED/$f")" ]; then
    nsame=$((nsame + 1)); else nbad=$((nbad + 1)); log "     differs: $f"; fi
done < "$BUILD/files-committed.txt"
XCODE_BUILD="$(xcodebuild -version 2>/dev/null | awk '/Build version/ {print $3}')"
PINNED_BUILD="$(kv "$SOURCE_FILE" COMPILED_WITH | grep -Eo '[0-9]{2}[A-Z][0-9]+[a-z]?' | tail -n1 || true)"
if [ "$nbad" = 0 ]; then
  check ok "byte-identical .mom/.plist ($nsame files)"
elif [ -n "$PINNED_BUILD" ] && [ "$XCODE_BUILD" != "$PINNED_BUILD" ]; then
  log "NOTE byte-identical .mom/.plist: $nbad differ with Xcode build ${XCODE_BUILD:-unknown} (committed with" \
      "$PINNED_BUILD); informational, the canonical dump and identity checks below decide"
else
  check fail "byte-identical .mom/.plist ($nbad differ)"
fi

# --- 4./5. canonical dumps and model identity ---
DUMP="$BUILD/model_dump"
if [ ! -x "$DUMP" ] || [ "$HERE/tools/model_dump.swift" -nt "$DUMP" ]; then
  xcrun swiftc -O -swift-version 5 -target arm64-apple-macosx14.0 -o "$DUMP" "$HERE/tools/model_dump.swift" \
    -framework CoreData >/dev/null 2>&1 || { echo "cannot compile model_dump" >&2; exit 2; }
fi
CUR="$(kv "$SOURCE_FILE" CURRENT_VERSION)"
"$DUMP" "$COMMITTED" > "$BUILD/dump-committed-momd.json"
"$DUMP" "$COMMITTED/$CUR.mom" > "$BUILD/dump-committed-mom.json"
"$DUMP" "$BUILT" > "$BUILD/dump-built-momd.json"
for omo in "$COMMITTED"/*.omo; do
  n="$(basename "$omo")"
  "$DUMP" "$omo" > "$BUILD/dump-committed-$n.json"
  "$DUMP" "$BUILT/$n" > "$BUILD/dump-built-$n.json"
  if cmp -s "$BUILD/dump-committed-$n.json" "$BUILD/dump-built-$n.json" \
     && cmp -s "$BUILD/dump-committed-$n.json" "$BUILD/dump-committed-mom.json"; then
    check ok "$n equal by canonical dump (not byte-reproducible)"; else check fail "$n canonical dump"; fi
done
cmp -s "$BUILD/dump-committed-momd.json" "$BUILD/dump-built-momd.json" && check ok "momd canonical dump" \
  || check fail "momd canonical dump"
python3 - "$BUILD/dump-built-momd.json" "$REPO/compat/threema-ios.json" "$MODEL" "$VID" <<'PY' && check ok "identity = compat/threema-ios.json" || check fail "identity = compat/threema-ios.json"
import hashlib, json, sys
dump = json.load(open(sys.argv[1]))
compat = {m["id"]: m for m in json.load(open(sys.argv[2]))["models"]}[sys.argv[3]]
digest = hashlib.sha256(json.dumps(dump["version_hashes"], sort_keys=True).encode()).hexdigest()
print(f"[build-momd]      version_hashes_sha256 {digest}", file=sys.stderr)
ok = digest == compat["version_hashes_sha256"] and dump["version_identifiers"] == [sys.argv[4]] \
     and compat.get("version_identifiers") == [sys.argv[4]]
sys.exit(0 if ok else 1)
PY

if [ "$fail" = 0 ]; then log "RESULT OK ($MODEL)"; exit 0; fi
log "RESULT MISMATCH ($MODEL)"
[ "$VERIFY" = 1 ] && exit 1 || exit 0
