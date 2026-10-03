#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""mark_png.py -- strip metadata from PNG screenshots and set the scrub marker (DESIGN §10.3 rule "Bilder").

    mark_png.py --marker tm-demo=1 FILE...       demo-mode screenshots (demo-screenshots.yml)
    mark_png.py --marker tm-reviewed=1 FILE...   device / VM photos, ONLY after the maintainer looked at every picture
    mark_png.py --check FILE...                  exit 1 unless every file carries exactly one known marker

Colour: a screenshot carries the ICC profile of the display it was taken on, and that profile names the display model
(a hardware fingerprint of the maintainer's desk). Files with an embedded profile are therefore converted to sRGB first
(`sips --matchTo` the system sRGB profile, macOS), then every colour chunk (iCCP, gAMA, cHRM, sRGB) is replaced by one
standard `sRGB` chunk. Keeps the image data (IHDR, PLTE, IDAT, IEND, tRNS, sBIT, pHYs); drops every other ancillary
chunk (tEXt/iTXt/zTXt, eXIf, tIME, ...), then writes one tEXt chunk "Comment" = marker after IHDR.
`--check` also fails on an embedded ICC profile. Prints counts only.
"""
from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

SIG = b"\x89PNG\r\n\x1a\n"
KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"sBIT", b"pHYs"}
SRGB_ICC = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")
MARKERS = ("tm-demo=1", "tm-reviewed=1")


def chunks(data: bytes):
    if not data.startswith(SIG):
        raise ValueError("not a PNG")
    pos = len(SIG)
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        yield ctype, body
        pos += 12 + length
        if ctype == b"IEND":
            break


def chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def mark(data: bytes, marker: str) -> bytes:
    out = [SIG]
    for ctype, body in chunks(data):
        if ctype not in KEEP:
            continue
        out.append(chunk(ctype, body))
        if ctype == b"IHDR":
            out.append(chunk(b"sRGB", b"\x00"))          # rendering intent: perceptual
            out.append(chunk(b"tEXt", b"Comment\x00" + marker.encode("ascii")))
    return b"".join(out)


def has_icc(data: bytes) -> bool:
    return any(ctype == b"iCCP" for ctype, _ in chunks(data))


def to_srgb(path: Path) -> bool:
    """Convert the pixels of a file with an embedded (display) profile to sRGB. False if that is not possible here."""
    if not shutil.which("sips") or not SRGB_ICC.exists():
        return False
    return subprocess.run(["sips", "--matchTo", str(SRGB_ICC), str(path)], capture_output=True).returncode == 0


def markers_of(data: bytes) -> list[str]:
    found = []
    for ctype, body in chunks(data):
        if ctype in (b"tEXt", b"iTXt", b"zTXt"):
            found += [m for m in MARKERS if m.encode() in body]
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--marker", choices=MARKERS)
    g.add_argument("--check", action="store_true")
    ap.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args(argv)
    bad = 0
    for f in a.files:
        data = f.read_bytes()
        try:
            if a.check:
                if len(markers_of(data)) != 1:
                    bad += 1
                    print(f"{f}: missing or ambiguous marker", file=sys.stderr)
                if has_icc(data):
                    bad += 1
                    print(f"{f}: embedded ICC profile (run mark_png.py --marker again)", file=sys.stderr)
            else:
                if has_icc(data):
                    if not to_srgb(f):
                        bad += 1
                        print(f"{f}: has an ICC profile and cannot be converted to sRGB here (macOS sips)",
                              file=sys.stderr)
                        continue
                    data = f.read_bytes()
                f.write_bytes(mark(data, a.marker))
        except (ValueError, struct.error):
            bad += 1
            print(f"{f}: not a readable PNG", file=sys.stderr)
    print(f"mark_png: files={len(a.files)} problems={bad}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
