#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# make-dmg.sh [App.app] -- the release DMG (DESIGN §14.1 step 7) -> build/dist/<slug>-<version>.dmg + SHA256SUMS
# (slug = threema-chat-transfer; volume name and app keep the display name).
# Owner: pack.
#
# Content: the signed app, an "Applications" link, "Zuerst lesen.pdf" + "Read me first.pdf" (typeset from
# packaging/readme-first/{de,en}.md by tools/render_readme.swift) and a background with the four Gatekeeper steps and
# the disclaimer (tools/render_dmg_background.swift, 1x + 2x as one HiDPI TIFF). Layout: packaging/dmg/layout.json.
# Built with dmgbuild (writes .DS_Store without scripting Finder; drives hdiutil; UDZO), hash-pinned in
# packaging/build-tools.lock and installed into build/tools-site (never into the bundle). The DMG is ad-hoc signed.
#
# Privacy of the image (no build-machine traces; verify-bundle.sh --dmg checks every point):
#   * TZ=UTC: HFS+ stores the volume creation date in LOCAL time, which would reveal the builder's time zone
#   * no extended attributes on any item: the app is staged with `ditto --noextattr` and dmgbuild's own ditto copy is
#     run with --noextattr too. com.apple.provenance cannot be removed once a file has it, and macOS attaches it to
#     every file written by a provenance-tracked process: build release DMGs from Terminal or CI, not from inside a
#     downloaded third-party app (verify-bundle.sh --dmg fails if any attribute made it onto the volume)
#   * file times on the volume, the PDF dates and the PDF document id come from SOURCE_DATE_EPOCH (default: commit
#     time of HEAD); the PDF /Producer is neutral (tools/neutralize_pdf.py); intermediate files have neutral names
#
# Environment: TM_REPO_URL  release page printed in the PDFs (default: the repository's release page)
source "$(dirname "$0")/lib.sh"
require_arm64_macos
export TZ=UTC
SOURCE_DATE_EPOCH="$(tm_source_date_epoch)"; export SOURCE_DATE_EPOCH
STAMP="$(date -u -r "$SOURCE_DATE_EPOCH" +%Y%m%d%H%M.%S)"
APP="${1:-$BUILD/dist/$APP_NAME.app}"
[ -d "$APP/Contents" ] || die "no app bundle at $APP (build-app.sh + sign-adhoc.sh first)"
codesign --verify --strict --deep "$APP" 2>/dev/null || die "app is not signed (sign-adhoc.sh first)"
VERSION="$(tm_version)"
REPO_URL="${TM_REPO_URL:-https://github.com/Toubster/threema-chat-transfer/releases}"
DIST="$BUILD/dist"; WORK="$BUILD/dmg"; TOOLS="$BUILD/tools"
DMG="$DIST/$APP_SLUG-$VERSION.dmg"
rm -rf "$WORK"; mkdir -p "$WORK" "$TOOLS" "$DIST"

# --- build tools: Swift renderers + dmgbuild ---
for t in render_readme render_dmg_background; do
  if [ ! -x "$TOOLS/$t" ] || [ "$PKG_DIR/tools/$t.swift" -nt "$TOOLS/$t" ]; then
    xcrun swiftc -O -swift-version 5 -target arm64-apple-macosx14.0 -o "$TOOLS/$t" "$PKG_DIR/tools/$t.swift" \
      >"$WORK/$t.log" 2>&1 || { cat "$WORK/$t.log" >&2; die "cannot compile $t"; }
  fi
done
RUNTIME="$BUILD/python-runtime/python/bin/python3"
[ -x "$RUNTIME" ] || "$PKG_DIR/fetch-python.sh"
SITE="$BUILD/tools-site"
if [ ! -f "$SITE/.lock-sha256" ] || [ "$(cat "$SITE/.lock-sha256")" != "$(sha256_of "$PKG_DIR/build-tools.lock")" ]; then
  rm -rf "$SITE"
  "$RUNTIME" -I -m pip install --quiet --disable-pip-version-check --require-hashes --only-binary :all: --no-deps \
    --target "$SITE" -r "$PKG_DIR/build-tools.lock" || die "cannot install dmgbuild"
  sha256_of "$PKG_DIR/build-tools.lock" > "$SITE/.lock-sha256"
fi

# --- PDFs ---
fill() { sed -e "s#{App}#$APP_NAME#g" -e "s#{version}#$VERSION#g" -e "s#{repo_url}#$REPO_URL#g" "$1"; }
fill "$PKG_DIR/readme-first/de.md" > "$WORK/de.md"
fill "$PKG_DIR/readme-first/en.md" > "$WORK/en.md"
"$TOOLS/render_readme" "$WORK/de.md" "$WORK/Zuerst lesen.pdf" "$APP_NAME – Zuerst lesen" >/dev/null
"$TOOLS/render_readme" "$WORK/en.md" "$WORK/Read me first.pdf" "$APP_NAME – Read me first" >/dev/null
/usr/bin/python3 -I -B "$PKG_DIR/tools/neutralize_pdf.py" "$WORK/Zuerst lesen.pdf" "$WORK/Read me first.pdf" \
  || die "cannot neutralise the PDF metadata"

# --- background (1x + 2x -> one HiDPI TIFF) ---
# (tiffutil records the input file names in the TIFF ImageDescription: keep them neutral)
"$TOOLS/render_dmg_background" "$PKG_DIR/dmg/steps.json" "$PKG_DIR/dmg/layout.json" "$APP_NAME" "$WORK/background-1x.png" 1 >/dev/null
"$TOOLS/render_dmg_background" "$PKG_DIR/dmg/steps.json" "$PKG_DIR/dmg/layout.json" "$APP_NAME" "$WORK/background-2x.png" 2 >/dev/null
tiffutil -cathidpicheck "$WORK/background-1x.png" "$WORK/background-2x.png" -out "$WORK/background.tiff" >/dev/null 2>&1 \
  || die "tiffutil failed"

# --- staging: the app without extended attributes, every file time = SOURCE_DATE_EPOCH ---
STAGE="$WORK/stage"; mkdir -p "$STAGE"
ditto --noextattr --noqtn --norsrc "$APP" "$STAGE/$APP_NAME.app" || die "cannot stage the app"
cp -X "$WORK/Zuerst lesen.pdf" "$WORK/Read me first.pdf" "$STAGE/" || die "cannot stage the PDFs"
find "$STAGE" -exec touch -h -t "$STAMP" {} + || die "cannot set the file times"
codesign --verify --strict --deep "$STAGE/$APP_NAME.app" 2>/dev/null || die "staged app does not verify"

# --- dmgbuild settings ---
"$RUNTIME" -I - "$PKG_DIR/dmg/layout.json" "$WORK/settings.py" "$STAGE/$APP_NAME.app" "$STAGE" "$WORK" "$APP_NAME" <<'PY'
import json, sys
layout, out, app, stage, work, name = sys.argv[1:]
L = json.load(open(layout))
w, h = L["window"]["width"], L["window"]["height"]
p = L["positions"]
s = {
    "format": "UDZO", "compression_level": 9, "filesystem": "HFS+",
    "files": [app, f"{stage}/Zuerst lesen.pdf", f"{stage}/Read me first.pdf"],
    "symlinks": {"Applications": "/Applications"},
    "background": f"{work}/background.tiff",
    "window_rect": ((200, 120), (w, h)), "icon_size": L["icon_size"], "text_size": L["text_size"],
    "show_status_bar": False, "show_tab_view": False, "show_toolbar": False, "show_pathbar": False,
    "show_sidebar": False, "default_view": "icon-view", "show_icon_preview": False, "arrange_by": None,
    "icon_locations": {f"{name}.app": tuple(p["app"]), "Applications": tuple(p["Applications"]),
                       "Zuerst lesen.pdf": tuple(p["readme_de"]), "Read me first.pdf": tuple(p["readme_en"])},
}
with open(out, "w") as f:
    for k, v in s.items():
        f.write(f"{k} = {v!r}\n")
PY

log "dmgbuild (UDZO, TZ=UTC)"
rm -f "$DMG"
# dmgbuild copies every file with a plain `/usr/bin/ditto src dst`, which carries extended attributes over: add
# --noextattr --noqtn --norsrc (the code signatures are embedded in the Mach-O files and _CodeSignature, not in xattrs)
cat > "$WORK/run_dmgbuild.py" <<'PY'
import subprocess, sys
sys.path.insert(0, sys.argv[1])
_call = subprocess.call
def _call_noxattr(cmd, *a, **kw):
    if isinstance(cmd, (list, tuple)) and list(cmd[:1]) == ["/usr/bin/ditto"]:
        cmd = ["/usr/bin/ditto", "--noextattr", "--noqtn", "--norsrc", *cmd[1:]]
    return _call(cmd, *a, **kw)
subprocess.call = _call_noxattr
import dmgbuild
dmgbuild.build_dmg(sys.argv[2], sys.argv[3], sys.argv[4])
PY
PYTHONPATH="$SITE" "$RUNTIME" -I "$WORK/run_dmgbuild.py" "$SITE" "$DMG" "$APP_NAME $VERSION" "$WORK/settings.py" \
  >"$WORK/dmgbuild.log" 2>&1 || { tail -20 "$WORK/dmgbuild.log" >&2; die "dmgbuild failed"; }
codesign --force --sign - --timestamp=none "$DMG" || die "cannot sign the DMG"
(cd "$DIST" && shasum -a 256 "$(basename "$DMG")" > SHA256SUMS)
size=$(( $(stat -f %z "$DMG") / 1024 / 1024 ))
[ "$size" -le 90 ] || die "DMG is $size MB (> 90 MB budget, DESIGN §12)"
log "built build/dist/$(basename "$DMG") ($size MB) + SHA256SUMS"
