#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# fresh_copy_check.sh -- the DMG as a user gets it (DESIGN §13.1 levels 9/10, §14.3). Owner: integrator.
#
#   devtools/fresh_copy_check.sh [build/dist/threema-chat-transfer-<version>.dmg]
#
# 1. mounts the DMG read-only and copies the app into a path with spaces and umlauts (as from "Programme")
# 2. the copy: strict signature, then the REAL app starts from it in demo mode on the virtual iPhone and runs the
#    happy path end to end (devtools/demo_screenshots.py --scenarios happy), the bundle stays byte-identical
# 3. a second copy gets com.apple.quarantine like a browser download; it is only ASSESSED (spctl), never opened, so no
#    Gatekeeper dialog appears on this Mac. Expected: rejected (ad-hoc signature, not notarized) -> the user needs
#    "Trotzdem öffnen" / "Open Anyway", which README, user guide, "Zuerst lesen.pdf" and the DMG background explain.
# The real "Open Anyway" click-through is a manual check on clean macOS VMs (DESIGN §13.1 level 10).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(sed -nE 's/^__version__ = "([^"]+)".*/\1/p' "$REPO/core/tmcore/__init__.py")"
DMG="${1:-$REPO/build/dist/threema-chat-transfer-$VERSION.dmg}"
OUT="$REPO/build/fresh-copy"
PY="${PY:-$REPO/.venv/bin/python}"
fail=0
ok() { printf 'OK   %s\n' "$*"; }
bad() { printf 'FAIL %s\n' "$*"; fail=1; }

[ -f "$DMG" ] || { echo "no DMG (make dmg)" >&2; exit 2; }
chmod -R u+w "$OUT" 2>/dev/null || true
rm -rf "$OUT"; mkdir -p "$OUT"
size=$(stat -f %z "$DMG"); mb=$(( (size + 999999) / 1000000 ))
[ "$mb" -le 90 ] && ok "DMG ${mb} MB <= 90 MB" || bad "DMG ${mb} MB > 90 MB"
hdiutil verify -quiet "$DMG" && ok "hdiutil verify" || bad "hdiutil verify"
codesign --verify "$DMG" 2>/dev/null && ok "DMG signature (ad-hoc)" || bad "DMG signature"

mnt="$(mktemp -d "$OUT/mnt.XXXXXX")"
hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mnt" "$DMG" >/dev/null
trap 'hdiutil detach -quiet "$mnt" 2>/dev/null || true' EXIT
ls "$mnt" | sed 's/^/     DMG: /'
dest="$OUT/Programme mit Leerzeichen und Ümläuten"
mkdir -p "$dest" "$OUT/quarantined"
ditto "$mnt/Chat Transfer for Threema.app" "$dest/Chat Transfer for Threema.app"
ditto "$mnt/Chat Transfer for Threema.app" "$OUT/quarantined/Chat Transfer for Threema.app"
hdiutil detach -quiet "$mnt"; trap - EXIT

codesign --verify --strict --deep "$dest/Chat Transfer for Threema.app" && ok "fresh copy: codesign --verify --strict --deep" \
  || bad "fresh copy signature"
if "$PY" "$REPO/devtools/demo_screenshots.py" --app "$dest/Chat Transfer for Threema.app" --scenarios happy --langs de \
     --out "$OUT/run" >"$OUT/run.log" 2>&1; then
  ok "fresh copy starts and runs the happy path on the virtual iPhone (bundle unchanged)"
else
  bad "fresh copy demo run (see build/fresh-copy/run.log)"
fi

q="$OUT/quarantined/Chat Transfer for Threema.app"
xattr -w com.apple.quarantine "0081;$(printf '%x' "$(date +%s)");Safari;" "$q"
xattr -p com.apple.quarantine "$q" >/dev/null && ok "quarantine attribute set on the second copy"
assess="$(spctl --assess --type execute -vv "$q" 2>&1 || true)"
case "$assess" in
  *rejected*) ok "Gatekeeper assessment of the quarantined copy: rejected (expected; needs \"Open Anyway\")" ;;
  *accepted*) bad "quarantined ad-hoc copy unexpectedly accepted" ;;
  *) bad "spctl: unexpected answer" ;;
esac
printf '%s\n' "$assess" | sed -n 's/^.*source=/     spctl source=/p' | head -n 1

docs_ok=1
for f in README.de.md docs/user/de/README.md packaging/readme-first/de.md; do
  /usr/bin/grep -q "Trotzdem öffnen" "$REPO/$f" || { docs_ok=0; echo "     missing in $f"; }
done
for f in README.md docs/user/en/README.md packaging/readme-first/en.md; do
  /usr/bin/grep -q "Open Anyway" "$REPO/$f" || { docs_ok=0; echo "     missing in $f"; }
done
/usr/bin/grep -q "Trotzdem öffnen" "$REPO/packaging/dmg/steps.json" && /usr/bin/grep -q "Open Anyway" "$REPO/packaging/dmg/steps.json" \
  || { docs_ok=0; echo "     missing in packaging/dmg/steps.json"; }
[ "$docs_ok" = 1 ] && ok "\"Trotzdem öffnen\" / \"Open Anyway\" documented (READMEs, guides, read-me-first PDFs, DMG background)" \
  || bad "Open Anyway path not documented everywhere"
exit "$fail"
