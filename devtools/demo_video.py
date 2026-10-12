#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
demo_video.py -- the README walkthrough video: the REAL app in demo mode (virtual iPhone, synthetic data, "DEMO"
watermark) clicked through from the first screen to the success screen, in German and English (DESIGN §13.1 level 7).
Owner: integrator. No device, no network, synthetic data only.

    python3 devtools/demo_video.py                          # DE + EN -> build/demo-video/<lang>/walkthrough.{mp4,gif}
    python3 devtools/demo_video.py --langs de --review DIR  # + one PNG per screen state and a contact sheet in DIR
    python3 devtools/demo_video.py --install                # + docs/images/demo/<lang>/, allowlist block "demo video"
    python3 devtools/demo_video.py --engine mock            # MockEngine replay instead of the virtual iPhone

Same run as devtools/demo_screenshots.py (whose helpers it imports): the signed app (`make app`) starts with
TM_ENGINE=fake:<scenario>, the bundled tmcore works on the virtual iPhone, and the app's demo autopilot plays the user
directives of app/Tests/Scenarios/<scenario>.jsonl. With TM_VIDEO_DIR the autopilot records
(app/Sources/System/DemoVideo.swift): the window is rendered in-process at twice its size into a PNG sequence at a fixed
rate (20 fps; the GIF keeps every second frame), a pointer glides to every control before it is used, entries are typed
visibly (short ones at reading speed), every screen gets a reading pause, notices the app shows by itself stay long
enough to be read and the long waits (backup, preparation, sending, control backup) are shown shortened with a
"fast-forward" badge. The window is the app's default width and taller than its default (--size, a size the user can
give the resizable window too), so that every screen of the run fits without scrolling: the in-process rendering cannot
draw the blur macOS puts under the title bar, so a scrolled frame would show the content over it. Scenario and end
screen come from the "demo" group of docs/images/screenshots.json. The sessions folder and the synthetic Android backup
live in a fresh temporary folder, so nothing of this Mac can appear in a frame.

Checks per language: the run ended on the scenario's expected screen and the recording on the end screen, every control
was located, no frame failed to write, the window had the asked size and no screen's content was taller than it, the
app bundle is byte-identical afterwards, the engine logs show no network attempt and the canary scan finds nothing.
Frames drawn while the window was not in front (another app, or the screen locked: title bar inactive) fail the run
unless --allow-inactive. Then ffmpeg (H.264 MP4 <= 15 MB, palette GIF <= 8 MB, metadata stripped, comment tm-demo=1;
checked with .github/scripts/mark_png.py --check). Output: build/demo-video/<lang>/ and build/demo-video/report.json
(counts and screen ids only).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_screenshots as ds  # noqa: E402  -- the run, its safety checks and the fixtures are shared

REPO = ds.REPO
DEST = REPO / "docs" / "images" / "demo"
MARK = REPO / ".github" / "scripts" / "mark_png.py"
MARKER = "tm-demo=1"
MP4_LIMIT = 15 * 1024 * 1024
GIF_LIMIT = 8 * 1024 * 1024          # GitHub's limit for images in a README
# GIF settings, tried in this order until the file fits GIF_LIMIT (frame rate, colours)
GIF_STEPS = ((10, 256), (8, 256), (8, 160), (6, 128))


def demo_item(lst: dict) -> dict:
    items = lst["groups"]["demo"]["items"]
    return next(it for it in items if it["id"] == "walkthrough")


def ffmpeg_bin() -> str:
    for c in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if c and Path(c).is_file():
            return c
    raise SystemExit("demo_video: ffmpeg not found (brew install ffmpeg)")


def ff(ffmpeg: str, *args: str) -> None:
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args], check=True)


def encode_mp4(ffmpeg: str, frames: Path, fps: int, out: Path) -> None:
    """H.264 yuv420p (BT.709 throughout: the PNGs' sRGB tag would otherwise end up as the transfer function),
    +faststart, no audio, all metadata stripped (no encoder name in the sample entry, no creation time, no SEI with
    the encoder settings) except the comment marker."""
    for crf in (20, 24, 28):
        ff(ffmpeg, "-framerate", str(fps), "-i", str(frames / "f%06d.png"),
           "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                  "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
           "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-tune", "stillimage",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           "-bsf:v", "filter_units=remove_types=6", "-an", "-sn", "-dn",
           "-map_metadata", "-1", "-map_chapters", "-1", "-metadata", f"comment={MARKER}",
           "-metadata:s:v:0", "encoder=",
           "-fflags", "+bitexact", "-flags:v", "+bitexact", "-movflags", "+faststart", str(out))
        if out.stat().st_size <= MP4_LIMIT:
            return


def encode_gif(ffmpeg: str, frames: Path, fps: int, width: int, out: Path) -> tuple[int, int]:
    """Looping GIF with one palette for the whole video (palettegen stats_mode=diff, paletteuse diff_mode=rectangle:
    only the changed part of a frame is stored). Returns the frame rate and colours that fit GIF_LIMIT."""
    for gif_fps, colors in GIF_STEPS:
        graph = (f"fps={min(gif_fps, fps)},scale={width}:-2:flags=lanczos,split[a][b];"
                 f"[a]palettegen=max_colors={colors}:stats_mode=diff[p];"
                 "[b][p]paletteuse=dither=none:diff_mode=rectangle")
        ff(ffmpeg, "-framerate", str(fps), "-i", str(frames / "f%06d.png"), "-filter_complex", graph,
           "-loop", "0", "-map_metadata", "-1", "-fflags", "+bitexact", str(out))
        subprocess.run([sys.executable, str(MARK), "--marker", MARKER, str(out)], check=True, capture_output=True)
        if out.stat().st_size <= GIF_LIMIT:
            return gif_fps, colors
    return GIF_STEPS[-1]


def review(ffmpeg: str, lang_dir: Path, manifest: dict, width: int, dest: Path) -> int:
    """One PNG per key frame (every reading pause, click, state the app showed by itself, wait, end) in full size and
    at GIF size, plus a contact sheet of the screen states (without the clicks). Outside the repository (reviewers
    only)."""
    full, small = dest / "full", dest / "gif-size"
    for d in (full, small):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    names, sheet = [], []
    for n, k in enumerate(manifest["keyframes"], 1):
        src = lang_dir / "video" / "frames" / f"f{k['frame']:06d}.png"
        if not src.is_file():
            continue
        name = f"{n:03d}-{k['screen']}-{k['kind']}" + (f"-{k['control']}" if k.get("control") else "") + ".png"
        shutil.copy2(src, full / name)
        ff(ffmpeg, "-i", str(src), "-vf", f"scale={width}:-2:flags=lanczos", str(small / name))
        names.append(name)
        if k["kind"] != "click":
            sheet.append(name)
    cols = 5
    rows = max(1, -(-len(sheet) // cols))
    lst = dest / "contact-sheet.txt"
    lst.write_text("".join(f"file '{(small / nm).as_posix()}'\nduration 1\n" for nm in sheet), encoding="utf-8")
    ff(ffmpeg, "-f", "concat", "-safe", "0", "-i", str(lst),
       "-vf", f"scale=470:-2:flags=lanczos,tile={cols}x{rows}:padding=8:margin=8:color=white", "-frames:v", "1",
       str(dest / "contact-sheet.png"))
    lst.unlink()
    (dest / "contact-sheet-order.txt").write_text("\n".join(sheet) + "\n", encoding="utf-8")
    return len(names)


def demo_clock_tz(now: float | None = None, local: int = 10 * 60) -> str:
    """A fixed UTC offset ("GMT+hhmm", Foundation's sign) under which the run starts at about 10:00: the clock times
    the app shows (backup time, transfer deadline) then tell neither the time of day nor the time zone of the
    recording."""
    t = time.gmtime(time.time() if now is None else now)
    off = (local - (t.tm_hour * 60 + t.tm_min)) % 1440
    if off > 720:
        off -= 1440
    sign = "+" if off >= 0 else "-"
    return f"GMT{sign}{abs(off) // 60:02d}{abs(off) % 60:02d}"


def run_lang(a: argparse.Namespace, app: Path, lang: str, scenario: str, end: str, out: Path, files: dict) -> dict:
    lang_dir = out / lang
    ds.rmtree(lang_dir)
    (lang_dir / "video").mkdir(parents=True)
    # neutral temporary folder (no user or project name in any path): sessions, temp files, the Android backup
    work = Path(tempfile.mkdtemp(prefix="tm-demo-video-"))
    try:
        sessions = work / "sessions"
        sessions.mkdir()
        rep = work / "report.json"
        env = {"HOME": os.environ.get("HOME", ""), "USER": os.environ.get("USER", ""),
               "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TMPDIR": str(work),
               "TM_ENGINE": f"{a.engine}:{scenario}", "TM_DEMO": "1", "TM_LANG": lang, "TM_APPEARANCE": "light",
               "TM_MOCK_SPEED": "fast", "TM_SESSIONS_DIR": str(sessions), "TM_AUTOPILOT": "1",
               "TM_AUTOPILOT_REPORT": str(rep), "TM_SCREENSHOT_SIZE": a.size,
               "TM_VIDEO_DIR": str(lang_dir / "video"), "TM_VIDEO_FPS": str(a.fps), "TM_VIDEO_END": end,
               "TZ": demo_clock_tz()}
        if a.engine == "fake":
            env["TM_DEMO_FIXTURES"] = str(REPO / "fixtures")
            if a.resources:
                env["TM_DEMO_RESOURCES"] = str(a.resources.resolve())
            android = work / "Android"
            android.mkdir()
            copies = []
            for f in files[ds.scenario_android_kind(scenario)]:
                shutil.copy2(f, android / Path(f).name)        # synthetic fixture names (fixture-backup.zip)
                copies.append(str(android / Path(f).name))
            env["TM_AUTOPILOT_ANDROID_FILES"] = json.dumps(copies)
        t0 = time.monotonic()
        rc = ds.launch(app, env, a.timeout)
        r = json.loads(rep.read_text(encoding="utf-8")) if rep.is_file() else {}
        probs = ds.engine_output_problems(sessions) if a.engine == "fake" else []
        canary_rc = ds.canary_scan(sessions, sys.executable) if a.engine == "fake" else 0
    finally:
        ds.rmtree(work)
    expect = json.loads((ds.SCEN / f"{scenario}.jsonl").read_text(encoding="utf-8").splitlines()[0])["expect_screen"]
    v = r.get("video") or {}
    problems = []
    if rc != 0 or r.get("error"):
        problems.append(f"exit {rc} {r.get('error') or ''}".strip())
    if r.get("end_screen") != expect:
        problems.append(f"run ended on {r.get('end_screen')}, expected {expect}")
    if v.get("end_screen") != end:
        problems.append(f"recording ended on {v.get('end_screen')}, expected {end}")
    if v.get("missing_controls"):
        problems.append(f"controls not located: {', '.join(v['missing_controls'])}")
    if v.get("write_failures"):
        problems.append(f"{v['write_failures']} frame(s) not written")
    w, h = (int(x) for x in a.size.split("x"))
    scale = v.get("scale") or 2
    if v and (v.get("width"), v.get("height")) != (w * scale, h * scale):
        problems.append(f"window was {v.get('width', 0) / scale:.0f}x{v.get('height', 0) / scale:.0f} pt, asked for "
                        f"{a.size} (screen too small?)")
    over = {k: x for k, x in (v.get("overflow") or {}).items() if x > 0.5}
    if over:
        problems.append("content taller than the window (it would scroll under the title bar): "
                        + ", ".join(f"{k} +{x:.0f} pt" for k, x in sorted(over.items()))
                        + f"; --size {w}x{h + int(max(over.values()) + 0.999)} fits")
    if v.get("inactive_frames") and not a.allow_inactive:
        problems.append(f"{v['inactive_frames']} frame(s) with the window in the background (another app in front "
                        "or the screen locked)")
    problems += probs
    if canary_rc != 0:
        problems.append("canary scan")
    if not 60 <= (v.get("seconds") or 0) <= 100:
        problems.append(f"video is {v.get('seconds') or 0:.1f} s long, expected 60-100 s (README walkthrough)")
    res = {"lang": lang, "scenario": scenario, "exit": rc, "seconds_run": round(time.monotonic() - t0, 1),
           "end": r.get("end_screen"), "expect": expect, "video_end": v.get("end_screen"),
           "frames": v.get("frames"), "fps": v.get("fps"), "video_seconds": v.get("seconds"),
           "fast_forward_frames": v.get("fast_forward_frames"), "inactive_frames": v.get("inactive_frames"),
           "missing_controls": v.get("missing_controls"), "network": probs, "canary_scan_rc": canary_rc,
           "room_pt": min((-x for x in (v.get("overflow") or {}).values()), default=None)}
    if problems:
        res.update(ok=False, problems=problems)
        return res
    manifest = json.loads((lang_dir / "video" / "video.json").read_text(encoding="utf-8"))
    ffmpeg = ffmpeg_bin()
    frames = lang_dir / "video" / "frames"
    mp4, gif = lang_dir / "walkthrough.mp4", lang_dir / "walkthrough.gif"
    encode_mp4(ffmpeg, frames, manifest["fps"], mp4)
    gif_fps, colors = encode_gif(ffmpeg, frames, manifest["fps"], a.gif_width, gif)
    chk = subprocess.run([sys.executable, str(MARK), "--check", str(mp4), str(gif)], capture_output=True, text=True)
    res.update(mp4_bytes=mp4.stat().st_size, gif_bytes=gif.stat().st_size, gif_fps=gif_fps, gif_colors=colors,
               width=manifest["width"], height=manifest["height"], marker_check_rc=chk.returncode)
    if mp4.stat().st_size > MP4_LIMIT:
        problems.append("MP4 larger than 15 MB")
    if gif.stat().st_size > GIF_LIMIT:
        problems.append("GIF larger than 8 MB")
    if chk.returncode != 0:
        problems.append("marker/metadata check: " + chk.stderr.strip().replace("\n", "; "))
    if a.review:
        res["review_pictures"] = review(ffmpeg, lang_dir, manifest, a.gif_width, a.review.resolve() / lang)
    if not a.keep_frames:
        shutil.rmtree(frames, ignore_errors=True)
    res.update(ok=not problems, problems=problems)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", type=Path, default=REPO / "build" / "dist" / "Chat Transfer for Threema.app")
    ap.add_argument("--out", type=Path, default=REPO / "build" / "demo-video")
    ap.add_argument("--langs", nargs="+", default=["de", "en"])
    ap.add_argument("--engine", choices=("fake", "mock"), default="fake")
    ap.add_argument("--resources", type=Path, help="staged Resources/ with the engine (TM_DEMO_RESOURCES), for an "
                                                    "app built without packaging (e.g. the Debug build)")
    ap.add_argument("--size", default="1000x950", help="window content size: the app's default width, tall enough for "
                                                       "every screen of the run without scrolling (S14 needs 939 pt)")
    ap.add_argument("--fps", type=int, default=20, help="recording and MP4 frame rate (GIF: at most 10)")
    ap.add_argument("--gif-width", type=int, default=1000, help="GIF width in pixels (1000 = the window at 1x)")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--review", type=Path, help="folder for the review pictures (keep it outside the repository)")
    ap.add_argument("--keep-frames", action="store_true", help="keep build/demo-video/<lang>/video/frames")
    ap.add_argument("--allow-inactive", action="store_true",
                    help="accept frames drawn with the window not in front (another app, locked screen: grey "
                         "title bar; drafts only)")
    ap.add_argument("--install", action="store_true", help="copy to docs/images/demo/<lang>/, allowlist block")
    a = ap.parse_args(argv)

    app = a.app.resolve()
    if not (app / "Contents" / "MacOS" / app.stem).is_file():
        print(f"demo_video: no app at {app.name} (run `make app`)", file=sys.stderr)
        return 2
    if len(a.size.split("x")) != 2 or not all(x.isdigit() for x in a.size.split("x")):
        print(f"demo_video: --size {a.size}: expected WIDTHxHEIGHT in points", file=sys.stderr)
        return 2
    if a.review and a.review.resolve().is_relative_to(REPO):
        print("demo_video: --review must be outside the repository", file=sys.stderr)
        return 2
    item = demo_item(json.loads(ds.LIST.read_text(encoding="utf-8")))
    scenario, end = item["scenario"], item["end"]
    if any(json.loads(ln).get("mock") == "relaunch"
           for ln in (ds.SCEN / f"{scenario}.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()):
        print(f"demo_video: {scenario} restarts the app; a recording covers one app start", file=sys.stderr)
        return 2
    files = ds.android_files() if a.engine == "fake" else {}
    before = ds.tree_digest(app)
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"engine": a.engine, "scenario": scenario, "end": end, "runs": [], "bundle_unchanged": None}
    for lang in a.langs:
        res = run_lang(a, app, lang, scenario, end, out, files)
        report["runs"].append(res)
        print(f"{lang} {scenario} {'ok ' if res['ok'] else 'BAD'} frames={res.get('frames')} "
              f"video={res.get('video_seconds')}s mp4={res.get('mp4_bytes')} gif={res.get('gif_bytes')} "
              f"{'; '.join(res.get('problems') or [])}", flush=True)
    report["bundle_unchanged"] = ds.tree_digest(app) == before
    failures = sum(not r["ok"] for r in report["runs"]) + (0 if report["bundle_unchanged"] else 1)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"demo_video: {len(report['runs'])} run(s), {failures} problem(s), bundle unchanged: "
          f"{report['bundle_unchanged']}", flush=True)
    if a.install and failures == 0:
        for lang in a.langs:
            (DEST / lang).mkdir(parents=True, exist_ok=True)
            for ext in ("mp4", "gif"):
                shutil.copy2(out / lang / f"walkthrough.{ext}", DEST / lang / f"walkthrough.{ext}")
        media = sorted(DEST.glob("*/walkthrough.*"))
        subprocess.run([sys.executable, str(MARK), "--check", *map(str, media)], check=True)
        subprocess.run([sys.executable, str(REPO / ".github" / "scripts" / "allowlist_block.py"), "--block",
                        "demo video", *map(str, media)], check=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
