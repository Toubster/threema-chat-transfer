#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""mark_png.py -- strip metadata from PNG screenshots and set the scrub marker (DESIGN §10.3 rule "Bilder").

    mark_png.py --marker tm-demo=1 FILE...       demo-mode screenshots (demo-screenshots.yml), the demo video's GIF
    mark_png.py --marker tm-reviewed=1 FILE...   device / VM photos, ONLY after the maintainer looked at every picture
    mark_png.py --check FILE...                  exit 1 unless every file carries exactly one known marker

The README walkthrough (devtools/demo_video.py) is a GIF and an MP4. GIF: the marker is one comment extension right
after the colour table; every other extension except the graphic control and the NETSCAPE2.0 loop block is dropped.
MP4: ffmpeg writes the marker as the only metadata item (comment) when it encodes; `--check` walks the boxes and also
fails on any other metadata item, an encoder name in the sample entry, a location box (©xyz, loci), XMP/uuid boxes and
non-zero creation or modification times.

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


GIF_SIGS = (b"GIF87a", b"GIF89a")
LOOP_APPS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")


def _gif_subblocks_end(data: bytes, pos: int) -> int:
    while True:
        n = data[pos]
        pos += 1 + n
        if n == 0:
            return pos


def gif_blocks(data: bytes):
    """(kind, bytes) of a GIF after the header and global colour table: ("head", ...), ("ext", label, ...),
    ("image", ...), ("trailer", ...)."""
    if data[:6] not in GIF_SIGS:
        raise ValueError("not a GIF")
    flags = data[10]
    pos = 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0)
    yield "head", None, data[:pos]
    while pos < len(data):
        b = data[pos]
        if b == 0x3B:
            yield "trailer", None, data[pos:pos + 1]
            return
        if b == 0x21:
            end = _gif_subblocks_end(data, pos + 2)
            yield "ext", data[pos + 1], data[pos:end]
        elif b == 0x2C:
            fl = data[pos + 9]
            end = _gif_subblocks_end(data, pos + 10 + (3 * 2 ** ((fl & 7) + 1) if fl & 0x80 else 0) + 1)
            yield "image", None, data[pos:end]
        else:
            raise ValueError("not a readable GIF")
        pos = end
    raise ValueError("GIF without trailer")


def mark_gif(data: bytes, marker: str) -> bytes:
    out = []
    for kind, label, raw in gif_blocks(data):
        if kind == "ext" and not (label == 0xF9 or (label == 0xFF and raw[3:14] in LOOP_APPS)):
            continue                                   # comments, plain text, XMP and other application blocks
        out.append(raw)
        if kind == "head":
            m = marker.encode("ascii")
            out.append(b"\x21\xfe" + bytes([len(m)]) + m + b"\x00")
    return b"".join(out)


def gif_comments(data: bytes) -> list[bytes]:
    return [raw[2:] for kind, label, raw in gif_blocks(data) if kind == "ext" and label == 0xFE]


def mp4_boxes(data: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", data[pos:pos + 8])
        head = 8
        if size == 1:
            size, head = struct.unpack(">Q", data[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            raise ValueError("not a readable MP4")
        yield typ, pos + head, pos + size
        pos += size


def mp4_problems(data: bytes) -> list[str]:
    """Metadata of an MP4 beyond the one comment marker (see the module docstring)."""
    if data[4:8] != b"ftyp":
        raise ValueError("not an MP4")
    probs: list[str] = []
    items: dict[bytes, bytes] = {}

    def walk(start: int, end: int, depth: int) -> None:
        for typ, a, b in mp4_boxes(data, start, end):
            if typ in (b"\xa9xyz", b"loci", b"XMP_", b"uuid"):
                probs.append(f"box {typ.decode('latin-1')}")
            if typ in (b"mvhd", b"tkhd", b"mdhd"):
                v = data[a]
                times = struct.unpack(">QQ", data[a + 4:a + 20]) if v == 1 else struct.unpack(">II", data[a + 4:a + 12])
                if any(times):
                    probs.append(f"{typ.decode()} creation/modification time")
            if typ == b"stsd":
                for et, ea, eb in mp4_boxes(data, a + 8, b):     # full box + entry count, then the sample entries
                    name = data[ea + 43:ea + 43 + min(31, data[ea + 42])] if eb - ea > 74 else b""
                    if et in (b"avc1", b"hvc1", b"hev1", b"mp4v") and name.strip(b" \x00"):
                        probs.append("encoder name in the sample entry")
                continue
            if typ == b"ilst":
                for it, ia, ib in mp4_boxes(data, a, b):
                    for dt, da, db in mp4_boxes(data, ia, ib):
                        if dt == b"data":
                            items[it] = data[da + 8:db]
                continue
            if typ in (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts") and depth < 8:
                walk(a, b, depth + 1)
            elif typ == b"meta" and depth < 8:
                walk(a + 4, b, depth + 1)              # full box: version and flags first

    walk(0, len(data), 0)
    others = sorted(k.decode("latin-1") for k in items if k != b"\xa9cmt")
    if others:
        probs.append("metadata items " + ",".join(others))
    if items.get(b"\xa9cmt", b"").decode("latin-1") not in MARKERS:
        probs.append("missing or ambiguous marker")
    return probs


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
            if data[:6] in GIF_SIGS:
                if a.check:
                    found = [m for c in gif_comments(data) for m in MARKERS if m.encode() in c]
                    if len(found) != 1 or len(gif_comments(data)) != 1:
                        bad += 1
                        print(f"{f}: missing or ambiguous marker", file=sys.stderr)
                else:
                    f.write_bytes(mark_gif(data, a.marker))
                continue
            if data[4:8] == b"ftyp":
                if not a.check:
                    bad += 1
                    print(f"{f}: MP4 -- the marker is written by the encoder (ffmpeg -metadata comment=...)",
                          file=sys.stderr)
                    continue
                for p in mp4_problems(data):
                    bad += 1
                    print(f"{f}: {p}", file=sys.stderr)
                continue
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
        except (ValueError, struct.error, IndexError):
            bad += 1
            print(f"{f}: not a readable PNG, GIF or MP4", file=sys.stderr)
    print(f"mark_png: files={len(a.files)} problems={bad}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
