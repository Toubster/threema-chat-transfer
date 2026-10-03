#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Screenshot gallery of a Threema iOS store in the Simulator -- no system prompts, no manual taps.
#
#   sim-gallery.sh <store_dir> <out_dir> <target> [<target> ...]
#
#   <store_dir>  dir with ThreemaData.sqlite (+ -wal/-shm, .ThreemaData_SUPPORT/) = app-group container root content
#   <out_dir>    created; gets NN-<slug>-pK.png screenshots, app.log, errors.log, crash reports, summary.tsv
#   <target>     list                       conversation list
#                contact:<ID|name>           1:1 chat (identity, "first last", first name or nickname)
#                group:<name|16-hex id>      group chat
#                name:<any>                  either
#                optional suffix @N          also capture N more screens scrolled upwards (handshake per page)
#                optional suffix ^N          scroll N screens up BEFORE the first screenshot
#                e.g.  contact:ZZFIXR01@2   "group:Fixture Group^1@1"
#
# How: one boot of a DEDICATED device (DEV_NAME, default Threema-Verify; erased first), install sim/Threema.app, then for
# every target a fresh app launch (--terminate-running-process) with ThreemaSimInject.dylib, which
#   - injects the THROWAWAY identity (ID_JSON from gen_test_identity.py) into the simulator keychain,
#   - keeps the network kill-switch ON (THREEMA_SIM_ALLOW_NET is never passed),
#   - copies the store into group.ch.threema on the first launch only,
#   - opens the target in-process via kNotificationShowConversation (no URL scheme => no "Open in Threema?" alert),
#   - reports "ready <page>" through <out_dir>/ctrl-NN/state; this script screenshots and acks each page.
# Env: DEV_NAME, ID_JSON, APP, WAIT_READY (s per page, default 120), SETTLE (s, default 3), KEEP_BOOTED=1, NO_ERASE=1
#      PROBE_ONLINE=1  online simulation (review appsafety M1, see ThreemaSimInject.m): connectionState=LoggedIn with the
#                      kill-switch ON, chat send/reflect/ack intercepted; target "probe" drives BlobManager for every
#                      FileMessage + TaskManager.spool and writes <out>/probe.json
#      COPY_BACK=<dir> after the run copy the app's group-container DB (+WAL, folded) and _EXTERNAL_DATA listing to <dir>
set -uo pipefail
STORE="${1:?usage: sim-gallery.sh <store_dir> <out_dir> <target...>}"; OUT="${2:?out_dir}"; shift 2
[ $# -ge 1 ] || { echo "need at least one target"; exit 2; }
ROOT="${TCT_SIM_ROOT:-$HOME/.cache/threema-chat-transfer-sim}"   # simulator workspace (ref/, build/, sim/)
APP="${APP:-$ROOT/sim/Threema.app}"
DYLIB="$ROOT/sim/libThreemaSimInject.dylib"
ID_JSON="${ID_JSON:-$ROOT/sim/test-identity.json}"
DEV_NAME="${DEV_NAME:-Threema-Verify}"
WAIT_READY="${WAIT_READY:-120}"
SETTLE="${SETTLE:-3}"
BUNDLE_ID="ch.threema.iapp"; GROUP_ID="group.ch.threema"

[ -f "$STORE/ThreemaData.sqlite" ] || { echo "no ThreemaData.sqlite in $STORE"; exit 1; }
[ -d "$APP" ] && [ -f "$DYLIB" ] && [ -f "$ID_JSON" ] || { echo "missing app/dylib/identity"; exit 1; }
# never a real identity: the key file must be one produced by gen_test_identity.py
grep -q '"note": "throwaway key' "$ID_JSON" || { echo "ID_JSON is not a gen_test_identity.py throwaway identity"; exit 1; }
STORE="$(cd "$STORE" && pwd)"; mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
ID_JSON="$(cd "$(dirname "$ID_JSON")" && pwd)/$(basename "$ID_JSON")"   # dylib runs with another cwd
START_EPOCH=$(date +%s)
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/gallery.log"; }

UDID=$(xcrun simctl list devices -j | /usr/bin/python3 -c "import json,sys;d=json.load(sys.stdin)['devices'];print(next((x['udid'] for r in d.values() for x in r if x['name']=='$DEV_NAME'),''))")
[ -n "$UDID" ] || { echo "device $DEV_NAME not found (create it once with sim-verify.sh)"; exit 1; }
log "device $DEV_NAME $UDID store=$STORE"
xcrun simctl shutdown "$UDID" >/dev/null 2>&1 || true
chflags -R nouchg "$HOME/Library/Developer/CoreSimulator/Devices/$UDID/data/Containers/Bundle" 2>/dev/null || true
[ "${NO_ERASE:-0}" = 1 ] || xcrun simctl erase "$UDID"
xcrun simctl boot "$UDID" || true
xcrun simctl bootstatus "$UDID" -b >/dev/null
xcrun simctl install "$UDID" "$APP"
# On a device the app bundle is read-only. In the simulator it is writable, and MessageVoiceMessageWaveformView
# deletes the bundled silent.mp3 after rendering a voice placeholder (FileUtility.delete(at: audioURL)); the next
# voice cell then hits assertionFailure (Debug build) -> crash. Emulate the read-only bundle for that file.
APPDIR=$(xcrun simctl get_app_container "$UDID" "$BUNDLE_ID" app 2>/dev/null || true)
[ -n "$APPDIR" ] && [ -f "$APPDIR/silent.mp3" ] && chflags uchg "$APPDIR/silent.mp3"
log "booted + installed"

xcrun simctl spawn "$UDID" log stream --style compact --level debug --predicate 'process == "Threema"' > "$OUT/app.log" 2>&1 &
LOGPID=$!
trap 'kill $LOGPID 2>/dev/null; [ -n "${APPDIR:-}" ] && chflags nouchg "$APPDIR/silent.mp3" 2>/dev/null' EXIT
sleep 2

printf "idx\ttarget\tresult\tscreenshots\n" > "$OUT/summary.tsv"
i=0
for RAW in "$@"; do
  i=$((i+1)); NN=$(printf "%02d" $i)
  T="$RAW"; PAGES=0; UP=0
  if [[ "$T" =~ ^(.*)@([0-9]+)$ ]]; then T="${BASH_REMATCH[1]}"; PAGES="${BASH_REMATCH[2]}"; fi
  if [[ "$T" =~ ^(.*)\^([0-9]+)$ ]]; then T="${BASH_REMATCH[1]}"; UP="${BASH_REMATCH[2]}"; fi
  SLUG=$(echo "$T" | tr -c 'A-Za-z0-9_-' '_' | cut -c1-40)
  CTRL="$OUT/ctrl-$NN"; rm -rf "$CTRL"; mkdir -p "$CTRL"
  TAG="g$NN-$$"
  LAUNCH=$(SIMCTL_CHILD_DYLD_INSERT_LIBRARIES="$DYLIB" \
    SIMCTL_CHILD_THREEMA_SIM_IDENTITY_JSON="$ID_JSON" \
    SIMCTL_CHILD_THREEMA_SIM_DB_DIR="$STORE" \
    SIMCTL_CHILD_THREEMA_SIM_OPEN="$T" \
    SIMCTL_CHILD_THREEMA_SIM_SCROLL_UP="$UP" \
    SIMCTL_CHILD_THREEMA_SIM_PAGES="$PAGES" \
    SIMCTL_CHILD_THREEMA_SIM_CTRL_DIR="$CTRL" \
    SIMCTL_CHILD_THREEMA_SIM_RUN_TAG="$TAG" \
    SIMCTL_CHILD_THREEMA_SIM_SETTLE_SECS="$SETTLE" \
    SIMCTL_CHILD_THREEMA_SIM_PROBE_ONLINE="${PROBE_ONLINE:-0}" \
    xcrun simctl launch --terminate-running-process "$UDID" "$BUNDLE_ID" -isRunningForScreenshots -com.apple.TipKit.HideAllTips 1 2>&1)
  PID=$(echo "$LAUNCH" | sed -nE 's/.*: ([0-9]+)$/\1/p' | tail -1)
  log "[$NN] $T pages=$PAGES up=$UP pid=${PID:-?}"
  RESULT="ok"; SHOTS=""
  for P in $(seq 0 "$PAGES"); do
    STATE=""; t0=$(date +%s)
    while :; do
      STATE=$(grep -h "^$TAG " "$CTRL/state" 2>/dev/null | tail -1)
      [ -z "$STATE" ] && STATE=$(grep -ho "STATE $TAG .*" "$OUT/app.log" 2>/dev/null | tail -1 | sed 's/^STATE //')
      case "$STATE" in *" ready $P"*|*" ready $P top"*|*" error "*|*" done"*) break;; esac
      if [ -n "$PID" ] && ! kill -0 "$PID" 2>/dev/null; then STATE="$TAG error app process $PID exited (crash?)"; break; fi
      if grep -q "invalid identity JSON" "$OUT/app.log" 2>/dev/null; then STATE="$TAG error identity JSON rejected by dylib"; break; fi
      [ $(( $(date +%s) - t0 )) -ge "$WAIT_READY" ] && { STATE="$TAG error timeout waiting for page $P"; break; }
      sleep 1
    done
    case "$STATE" in
      *" ready $P"*)
        F="$NN-$SLUG-p$P.png"; xcrun simctl io "$UDID" screenshot "$OUT/$F" >/dev/null 2>&1 && SHOTS="$SHOTS $F"
        touch "$CTRL/ack-$P"
        [[ "$STATE" == *" top" ]] && log "[$NN] page $P reached top of chat";;
      *) RESULT="${STATE#$TAG }"
         F="$NN-$SLUG-FAIL.png"; xcrun simctl io "$UDID" screenshot "$OUT/$F" >/dev/null 2>&1 && SHOTS="$SHOTS $F"
         log "[$NN] $RESULT"; break;;
    esac
  done
  printf "%s\t%s\t%s\t%s\n" "$NN" "$RAW" "$RESULT" "${SHOTS# }" >> "$OUT/summary.tsv"
done

xcrun simctl terminate "$UDID" "$BUNDLE_ID" >/dev/null 2>&1 || true
# one contact sheet per target (all pages side by side, 30 %) for quick review
"$ROOT/.venv/bin/python" - "$OUT" <<'PY' 2>/dev/null || true
import glob, os, re, sys
from PIL import Image
out = sys.argv[1]
groups = {}
for f in sorted(glob.glob(os.path.join(out, "[0-9][0-9]-*-p*.png"))):
    groups.setdefault(os.path.basename(f)[:2], []).append(f)
for nn, files in groups.items():
    files.sort(key=lambda f: int(re.search(r"-p(\d+)\.png$", f).group(1)))
    ims = [Image.open(f).convert("RGB") for f in files]
    s = 0.3; w, h = int(ims[0].width * s), int(ims[0].height * s)
    sheet = Image.new("RGB", (len(ims) * (w + 10) - 10, h), "white")
    for i, im in enumerate(ims[::-1]):      # oldest (highest page) left, newest right
        sheet.paste(im.resize((w, h)), (i * (w + 10), 0))
    sheet.save(os.path.join(out, nn + "-sheet.png"))
PY
sleep 2; kill $LOGPID 2>/dev/null || true
# error extraction:
#  errors.log  = os_log Error/Fault lines + CocoaLumberjack [Err] lines + anything mentioning crash / Core Data / SQLite /
#                migration / repair / assertion (minus known-benign system noise), + injector STATE/copy lines
#  errors-summary.txt = the same, digits normalised, counted (quick triage without reading the whole log)
NOISE='com\.apple\.UIKit:BackgroundTask|com\.apple\.runningboard|NSException(AllowsInsecure|RequiresForward|MinimumTLS)|coreanimation:API\] cannot add handler|cameracapture|coremedia:\]|app_launch_measurement|audioanalytics|PointerUI|CFNetwork:Default\] Task <.*NSURLErrorDomain Code=-1009|TextKit 1 compatibility|com\.apple\.defaults:|FrontBoard:SceneExtension|BaseBoard:Common|UIKit:KeyWindow|insetAdjustedBottomOffset'
grep -E "^\S+ \S+ +(E|F) |\[Err\]|\[ThreemaSimInject\] (STATE|copy|cannot|scroll: no)" "$OUT/app.log" > "$OUT/.e1" 2>/dev/null || true
grep -iE "crash|fatal|assert|EXC_|NSInternalInconsistency|Core ?Data|NSSQLite|sqlite|migrat|repair|corrupt" "$OUT/app.log" >> "$OUT/.e1" 2>/dev/null || true
grep -vE "$NOISE" "$OUT/.e1" | sort | awk '!seen[$0]++' > "$OUT/errors.log"; rm -f "$OUT/.e1"
sed -E 's/^[0-9-]+ [0-9:.]+ +[A-Za-z]+ +Threema\[[0-9:a-f]+\] //; s/[0-9]+/N/g' "$OUT/errors.log" | cut -c1-160 | sort | uniq -c | sort -rn > "$OUT/errors-summary.txt"
grep -cE "\[ThreemaSimInject\] BLOCKED" "$OUT/app.log" > "$OUT/blocked-requests.count" 2>/dev/null || true
grep -cE "network kill-switch active" "$OUT/app.log" > "$OUT/killswitch-active.count" 2>/dev/null || true
grep -cE "\[ThreemaSimInject\] PROBE WOULD_" "$OUT/app.log" > "$OUT/probe-would.count" 2>/dev/null || true
grep -cE "PROBE online simulation active" "$OUT/app.log" > "$OUT/probe-active.count" 2>/dev/null || true
grep -cE "BACKGROUND_SESSION_REDIRECTED" "$OUT/app.log" > "$OUT/background-sessions.count" 2>/dev/null || true
cat "$OUT"/ctrl-*/probe.json > "$OUT/probe.json" 2>/dev/null || rm -f "$OUT/probe.json"
mkdir -p "$OUT/crash-reports"
find "$HOME/Library/Logs/DiagnosticReports" -maxdepth 1 -name 'Threema*' -newermt "@$START_EPOCH" -exec cp {} "$OUT/crash-reports/" \; 2>/dev/null
NCRASH=$(ls "$OUT/crash-reports" | wc -l | tr -d ' ')
GROUP_DIR=$(xcrun simctl get_app_container "$UDID" "$BUNDLE_ID" "$GROUP_ID" 2>/dev/null || true)
[ -n "$GROUP_DIR" ] && ls -la "$GROUP_DIR" > "$OUT/group-container-after.txt" 2>/dev/null
if [ -n "$GROUP_DIR" ]; then
  # persisted task queue (TaskQueue.queuePath: <appData>/taskQueue): which task types would run once online
  TQ=$(find "$GROUP_DIR" -name taskQueue -type f 2>/dev/null | head -1)
  if [ -n "$TQ" ]; then { echo "bytes $(stat -f %z "$TQ")"; strings "$TQ" | grep -oE "TaskDefinition[A-Za-z]+" | sort | uniq -c; } > "$OUT/taskqueue-after.txt"
  else echo "no taskQueue file" > "$OUT/taskqueue-after.txt"; fi
fi
if [ -n "${COPY_BACK:-}" ] && [ -n "$GROUP_DIR" ]; then
  rm -rf "$COPY_BACK"; mkdir -p "$COPY_BACK"
  for f in ThreemaData.sqlite ThreemaData.sqlite-wal ThreemaData.sqlite-shm; do
    [ -f "$GROUP_DIR/$f" ] && cp -p "$GROUP_DIR/$f" "$COPY_BACK/$f"; done
  /usr/bin/sqlite3 "$COPY_BACK/ThreemaData.sqlite" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1
  rm -f "$COPY_BACK/ThreemaData.sqlite-wal" "$COPY_BACK/ThreemaData.sqlite-shm"
  ls "$GROUP_DIR/.ThreemaData_SUPPORT/_EXTERNAL_DATA" 2>/dev/null | sort > "$COPY_BACK/external-data-after.txt"
  [ -d "$GROUP_DIR/.ThreemaData_SUPPORT" ] && cp -c -R "$GROUP_DIR/.ThreemaData_SUPPORT" "$COPY_BACK/.ThreemaData_SUPPORT"   # APFS clone
  log "copied container DB back to $COPY_BACK"
fi
log "done: $(awk -F'\t' 'NR>1 && $3=="ok"' "$OUT/summary.tsv" | wc -l | tr -d ' ')/$i targets ok, crash reports: $NCRASH, errors.log lines: $(wc -l < "$OUT/errors.log" | tr -d ' ')"
if [ "${KEEP_BOOTED:-0}" != 1 ]; then xcrun simctl shutdown "$UDID" >/dev/null 2>&1 || true; fi
echo "GALLERY_DONE $OUT"
