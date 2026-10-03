#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""neutralize_pdf.py FILE.pdf [...] -- neutral, reproducible metadata for the PDFs on the DMG. Owner: pack.

Quartz writes the build machine's macOS version and build into /Producer, the build time into /CreationDate and
/ModDate, and a time-based document /ID. This rewrites exactly those values IN PLACE with values of the same byte
length (the cross-reference offsets stay valid, nothing else moves):

  /Producer      "Quartz PDFContext" (padded with spaces after the closing parenthesis)
  /CreationDate  /ModDate  SOURCE_DATE_EPOCH (UTC; default: the commit time of HEAD, set by make-dmg.sh)
  /ID            md5 over the file with both ID strings blanked (deterministic for the same content)

Refuses (exit 1) when the Info dictionary carries any key other than Title, Creator, Producer, CreationDate and
ModDate (Author, Subject, Keywords, ...), when an XMP metadata stream is present, or when a value cannot be replaced
without changing the file length. `--check` only verifies a PDF that was neutralised before.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import sys

ALLOWED_KEYS = {b"Title", b"Creator", b"Producer", b"CreationDate", b"ModDate"}
PRODUCER = b"Quartz PDFContext"


def literal_end(data: bytes, start: int) -> int:
    """index just past the PDF literal string that starts with "(" at `start` (nested parens, backslash escapes)"""
    depth, i = 0, start
    while i < len(data):
        c = data[i]
        if c == 0x5C:            # backslash: skip the escaped byte
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unterminated string")


def dict_end(data: bytes, start: int) -> int:
    """index just past the dictionary that starts with "<<" at `start` (nested dicts, strings skipped)"""
    i, depth = start, 0
    while i < len(data):
        two = data[i:i + 2]
        if two == b"<<":
            depth, i = depth + 1, i + 2
        elif two == b">>":
            depth, i = depth - 1, i + 2
            if depth == 0:
                return i
        elif data[i] == 0x28:
            i = literal_end(data, i)
        elif data[i] == 0x3C:
            i = data.index(b">", i) + 1
        else:
            i += 1
    raise ValueError("unterminated dictionary")


def info_dict(data: bytes) -> tuple[int, int]:
    """(start, end) of the document Info dictionary, found via the trailer /Info reference"""
    refs = set(re.findall(rb"/Info\s+(\d+)\s+(\d+)\s+R", data))
    if len(refs) != 1:
        raise ValueError("no unique /Info reference")
    num, gen = refs.pop()
    m = re.search(rb"(?<![0-9])" + num + rb"\s+" + gen + rb"\s+obj\s*<<", data)
    if not m:
        raise ValueError("Info object not found")
    start = m.end() - 2
    return start, dict_end(data, start)


def entries(data: bytes, start: int, end: int):
    """yield (key, value_start, value_end) for the literal-string and hex-string values of the dictionary"""
    i = start + 2
    while True:
        m = re.compile(rb"/([A-Za-z]+)\s*").search(data, i, end)
        if not m:
            return
        key, v = m.group(1), m.end()
        if data[v:v + 1] == b"(":
            ve = literal_end(data, v)
        elif data[v:v + 1] == b"<":
            ve = data.index(b">", v) + 1
        else:
            raise ValueError(f"unexpected value type for /{key.decode()}")
        yield key, v, ve
        i = ve


def replace_same_length(buf: bytearray, v: int, ve: int, new: bytes) -> None:
    if len(new) > ve - v:
        raise ValueError("replacement longer than the original value")
    buf[v:ve] = new + b" " * ((ve - v) - len(new))


def pdf_date(epoch: int) -> bytes:
    t = dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
    return t.strftime("(D:%Y%m%d%H%M%SZ00'00')").encode()


def neutralize(path: str, epoch: int, check: bool) -> list[str]:
    with open(path, "rb") as f:
        data = f.read()
    problems: list[str] = []
    if b"/Metadata" in data or b"<x:xmpmeta" in data:
        problems.append("XMP metadata present")
    start, end = info_dict(data)
    buf = bytearray(data)
    date = pdf_date(epoch)
    for key, v, ve in list(entries(data, start, end)):
        if key not in ALLOWED_KEYS:
            problems.append(f"Info key /{key.decode()} not allowed")
            continue
        val = bytes(data[v:ve])
        if key == b"Producer":
            want = b"(" + PRODUCER + b")"
            if check:
                if val != want:
                    problems.append("Producer is not neutral")
            else:
                replace_same_length(buf, v, ve, want)
        elif key in (b"CreationDate", b"ModDate"):
            if check:
                if val != date:
                    problems.append(f"/{key.decode()} is not SOURCE_DATE_EPOCH")
            else:
                if len(val) != len(date):
                    raise ValueError(f"/{key.decode()} has an unexpected format")
                replace_same_length(buf, v, ve, date)
    m = re.search(rb"/ID\s*\[\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\]", bytes(buf))
    if m:
        blank = bytearray(buf)
        for g in (1, 2):
            blank[m.start(g):m.end(g)] = b"0" * (m.end(g) - m.start(g))
        want_id = hashlib.md5(bytes(blank)).hexdigest().encode()[: m.end(1) - m.start(1)]
        if check:
            if m.group(1) != want_id or m.group(2) != want_id:
                problems.append("document /ID is not the content hash")
        else:
            buf[m.start(1):m.end(1)] = want_id
            buf[m.start(2):m.end(2)] = want_id
    if not check and not problems:
        if len(buf) != len(data):
            raise ValueError("file length changed")
        with open(path, "wb") as f:
            f.write(bytes(buf))
    return problems


def main(argv: list[str]) -> int:
    check = "--check" in argv
    files = [a for a in argv if a != "--check"]
    if not files:
        print(__doc__, file=sys.stderr)
        return 2
    epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0") or 0)
    if epoch <= 0:
        print("neutralize_pdf: SOURCE_DATE_EPOCH must be set", file=sys.stderr)
        return 2
    rc = 0
    for p in files:
        try:
            problems = neutralize(p, epoch, check)
        except (OSError, ValueError) as e:
            problems = [str(e)]
        for pr in problems:
            print(f"neutralize_pdf: {os.path.basename(p)}: {pr}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
