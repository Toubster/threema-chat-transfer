#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Build the synthetic Android backup pair used by tests/test_normalize.py (with decodable JPEG/PNG media and
thumbnails instead of random bytes) under build/normalizer-fixture/src/ and normalize it into build/normalizer-fixture/
(NOT work/fixture: that directory belongs to the importer / fixture builders).

  .venv/bin/python tests/build_fixture.py

Everything is synthetic: fake identities (own = ZZOWN345), fake keys, fixture password in build/normalizer-fixture/src/pw.txt.
"""
import io
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import test_normalize as t  # noqa: E402
import android_normalize as an  # noqa: E402


def img(fmt, size, color):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, format=fmt)
    return b.getvalue()


def main():
    src = os.path.join(ROOT, "build", "normalizer-fixture", "src")
    out = os.path.join(ROOT, "build", "normalizer-fixture")
    os.makedirs(src, exist_ok=True)
    # decodable media / thumbnails (deterministic)
    t.MEDIA[t.uid(6)] = img("JPEG", (640, 480), (200, 40, 40))
    t.MEDIA[t.uid(7)] = b"\xff\xf1" + bytes(range(256)) * 4          # fake ADTS-ish audio bytes
    t.MEDIA[t.uid(12)] = img("JPEG", (320, 240), (40, 200, 40))
    t.MEDIA[t.uid(105)] = b"%PDF-1.4\n%fixture\n" + b"0" * 150_000    # > Core Data external-storage threshold
    t.THUMBS[t.uid(6)] = img("JPEG", (160, 120), (200, 40, 40))
    t.THUMBS[t.uid(106)] = img("PNG", (120, 160), (40, 40, 200))
    text = os.path.join(src, f"threema-backup_{t.TEXT_TS}_1")
    media = os.path.join(src, f"threema-backup_{t.MEDIA_TS}_1")
    pwf = os.path.join(src, "pw.txt")
    with open(pwf, "w") as f:
        f.write(t.PW + "\n")
    t.build_text_backup(text)
    t.build_media_backup(media)
    return an.main(["--text-backup", text, "--media-backup", media, "--password-file", pwf, "--out-dir", out])


if __name__ == "__main__":
    sys.exit(main())
