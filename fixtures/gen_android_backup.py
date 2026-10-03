#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
gen_android_backup.py -- generate a SYNTHETIC Threema Android data backup in the exact v27 format.

Everything is fake: identities ZZFIXN01 (own) / ZZFIXR01..ZZFIXR13 / *ZZFIX01, random keys, generated media.
Format source: docs/android-backup-format.md and ref/threema-android BackupService.java (headers, column order),
CSVRow.java (escape: backslash doubling, bool 1/0, Instant -> epoch ms, null -> ""), opencsv CSVWriter
(all cells quoted, never-written cells unquoted empty, "\n" line end), FileDataModelSerializer.kt (FILE body),
LocationDataModel.kt (LOCATION body), StatusDataModel.kt/VoipStatusDataModel.kt/GroupStatusDataModel.kt,
PollDataModel.kt, domain IdentityBackup.kt (identity entry).

Usage:
  python3 fixtures/gen_android_backup.py --out DIR [--split] [--format-version N]
  generate(DIR, split=False, format_version="27")            # the same from Python (e2e tests, recordings)
Outputs:
  DIR/fixture-backup.zip        full backup incl. media + thumbnails (AES-256 zip, password below)
  DIR/fixture-manifest.json     expected content per chat (uids, api ids, kinds) for automated checks
  --split additionally:
  DIR/fixture-text-backup.zip   all rows, no media (like the real 'TEXT' backup)
  DIR/fixture-media-backup.zip  rows created before SPLIT_CUTOFF only, with media, re-generated
                                identity_id/group_uid (like the real older 'MEDIA' backup)
The password is the canary password of fixtures/canaries.json (synthetic; never written to a file here).
Media sources are generated once into DIR/src-media/ (PIL, afconvert, ffmpeg).
"""
import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import uuid
import wave
import math

import pyzipper
from PIL import Image, ImageDraw, ImageFont

CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
PASSWORD = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "canaries.json"), encoding="utf-8"))["password"]  # canary value (DESIGN §10.2)
OWN = "ZZFIXN01"
FORMAT_VERSION = "27"


def ms(y, mo, d, h=0, mi=0, s=0):
    return int(dt.datetime(y, mo, d, h, mi, s, tzinfo=dt.timezone.utc).timestamp() * 1000)


BASE = ms(2026, 9, 1, 8, 0)           # first fixture message
SPLIT_CUTOFF = ms(2026, 9, 20)        # --split: media backup contains only rows created before this

# ----------------------------------------------------------------------------------------------------------------
# deterministic randomness
# ----------------------------------------------------------------------------------------------------------------
R = random.Random(20260928)


def rbytes(n):
    return bytes(R.getrandbits(8) for _ in range(n))


def new_uid():
    return str(uuid.UUID(int=R.getrandbits(128), version=4))


def new_apiid():
    return rbytes(8).hex()


def new_id10():
    return "%010d" % R.randrange(0, 2**31 - 1)


# ----------------------------------------------------------------------------------------------------------------
# CSV writer exactly like Threema's CSVWriter(opencsv 2.3) + CSVRow.escape
# ----------------------------------------------------------------------------------------------------------------
NOT_WRITTEN = object()   # Java null that the writer never filled -> unquoted empty cell


def esc(v):
    if v is NOT_WRITTEN:
        return NOT_WRITTEN
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (list, tuple)):
        return ";".join(esc(x) for x in v)
    return str(v).replace("\\", "\\\\")


def csv_line(cells):
    out = []
    for c in cells:
        c = esc(c)
        if c is NOT_WRITTEN:
            out.append("")
        else:
            out.append('"' + c.replace('"', '""') + '"')
    return ",".join(out) + "\n"


def csv_doc(header, rows):
    """rows: list of dicts (missing key -> NOT_WRITTEN) or lists."""
    s = csv_line(header) if header is not None else ""
    for r in rows:
        if isinstance(r, dict):
            s += csv_line([r.get(h, NOT_WRITTEN) for h in header])
        else:
            s += csv_line(r)
    return s.encode("utf-8")


CONTACT_HDR = ["identity", "publickey", "verification", "acid", "firstname", "lastname", "nick_name", "last_update",
               "hidden", "archived", "identity_id"]
MSG_HDR = ["apiid", "uid", "isoutbox", "isread", "issaved", "messagestae", "posted_at", "created_at", "modified_at",
           "type", "body", "isstatusmessage", "caption", "quoted_message_apiid", "delivered_at", "read_at",
           "g_msg_states", "display_tags", "edited_at", "deleted_at"]
GMSG_HDR = MSG_HDR[:2] + ["identity"] + MSG_HDR[2:]
GROUP_HDR = ["id", "creator", "groupname", "created_at", "last_update", "members", "archived", "groupDesc",
             "groupDescTimestamp", "group_uid", "user_state"]
DL_HDR = ["id", "distribution_list_name", "created_at", "last_update", "distribution_members", "archived"]
POLL_HDR = ["id", "aid", "creator", "ref", "ref_id", "name", "state", "assessment", "type", "choice_type",
            "last_viewed_at", "created_at", "modified_at"]
CHOICE_HDR = ["id", "ballot", "aid", "type", "name", "vote_count", "order", "created_at", "modified_at"]
VOTE_HDR = ["id", "ballot_uid", "choice_uid", "identity", "choice", "created_at", "modified_at"]
CREACT_HDR = ["identity", "api_message_id", "sender_identity", "emoji_sequence", "reacted_at"]
GREACT_HDR = ["api_group_id", "group_creator_identity", "api_message_id", "sender_identity", "emoji_sequence",
              "reacted_at"]


def jdump(x):
    """kotlinx JsonArray.toString(): compact, non-ASCII kept as UTF-8."""
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False)


# ----------------------------------------------------------------------------------------------------------------
# identity entry (IdentityBackup: PBKDF2-HMAC-SHA256 100k + XSalsa20, base32 grouped)
# ----------------------------------------------------------------------------------------------------------------
def _rotl(v, c):
    return ((v << c) & 0xffffffff) | (v >> (32 - c))


def _salsa_core(inp, rounds=20, add=True):
    x = list(inp)
    for _ in range(rounds // 2):
        for a, b, c, d in ((0, 4, 8, 12), (5, 9, 13, 1), (10, 14, 2, 6), (15, 3, 7, 11)):
            x[b] ^= _rotl((x[a] + x[d]) & 0xffffffff, 7)
            x[c] ^= _rotl((x[b] + x[a]) & 0xffffffff, 9)
            x[d] ^= _rotl((x[c] + x[b]) & 0xffffffff, 13)
            x[a] ^= _rotl((x[d] + x[c]) & 0xffffffff, 18)
        for a, b, c, d in ((0, 1, 2, 3), (5, 6, 7, 4), (10, 11, 8, 9), (15, 12, 13, 14)):
            x[b] ^= _rotl((x[a] + x[d]) & 0xffffffff, 7)
            x[c] ^= _rotl((x[b] + x[a]) & 0xffffffff, 9)
            x[d] ^= _rotl((x[c] + x[b]) & 0xffffffff, 13)
            x[a] ^= _rotl((x[d] + x[c]) & 0xffffffff, 18)
    return [(x[i] + inp[i]) & 0xffffffff for i in range(16)] if add else x


SIGMA = struct.unpack("<4I", b"expand 32-byte k")


def xsalsa20_stream(key, nonce24, n):
    k = struct.unpack("<8I", key)
    # HSalsa20 -> subkey
    st = [SIGMA[0], k[0], k[1], k[2], k[3], SIGMA[1]] + list(struct.unpack("<4I", nonce24[:16])) + \
         [SIGMA[2], k[4], k[5], k[6], k[7], SIGMA[3]]
    x = _salsa_core(st, add=False)
    sub = struct.pack("<8I", x[0], x[5], x[10], x[15], x[6], x[7], x[8], x[9])
    k2 = struct.unpack("<8I", sub)
    n2 = struct.unpack("<2I", nonce24[16:24])
    out = b""
    ctr = 0
    while len(out) < n:
        st = [SIGMA[0], k2[0], k2[1], k2[2], k2[3], SIGMA[1], n2[0], n2[1], ctr & 0xffffffff, ctr >> 32,
              SIGMA[2], k2[4], k2[5], k2[6], k2[7], SIGMA[3]]
        out += struct.pack("<16I", *_salsa_core(st))
        ctr += 1
    return out[:n]


def identity_export(identity, private_key, password):
    import base64
    salt = rbytes(8)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000, 32)
    plain = identity.encode("ascii") + private_key
    plain += hashlib.sha256(plain).digest()[:2]
    ks = xsalsa20_stream(key, b"\0" * 24, len(plain))
    enc = salt + bytes(a ^ b for a, b in zip(plain, ks))
    b32 = base64.b32encode(enc).decode().rstrip("=")
    assert len(b32) == 80
    return "-".join(b32[i:i + 4] for i in range(0, 80, 4))


def identity_import_check(s, password):
    """Decrypt again (round-trip self test of our XSalsa20 + format)."""
    import base64
    raw = base64.b32decode(s.replace("-", ""))
    salt, enc = raw[:8], raw[8:]
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000, 32)
    plain = bytes(a ^ b for a, b in zip(enc, xsalsa20_stream(key, b"\0" * 24, len(enc))))
    return plain[:8].decode(), plain[8:40], hashlib.sha256(plain[:40]).digest()[:2] == plain[40:42]


# ----------------------------------------------------------------------------------------------------------------
# media generation (cached in <out>/src-media)
# ----------------------------------------------------------------------------------------------------------------
def font(sz):
    try:
        return ImageFont.load_default(size=sz)
    except TypeError:
        return ImageFont.load_default()


def label_image(w, h, text, c1, c2, mode="RGB", alpha=False):
    img = Image.new("RGBA" if alpha else "RGB", (w, h), (0, 0, 0, 0) if alpha else c1)
    d = ImageDraw.Draw(img)
    if not alpha:
        for y in range(h):
            t = y / max(1, h - 1)
            d.line([(0, y), (w, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
        for x in range(0, w, max(40, w // 12)):
            d.line([(x, 0), (x, h)], fill=(255, 255, 255), width=1)
    else:
        d.ellipse([w * 0.08, h * 0.08, w * 0.92, h * 0.92], fill=c1 + (255,), outline=c2 + (255,), width=max(6, w // 40))
    f = font(max(18, min(w, h) // 9))
    lines = text.split("\n")
    y = h / 2 - len(lines) * (min(w, h) // 9) / 2
    for ln in lines:
        tw = d.textlength(ln, font=f)
        d.text(((w - tw) / 2, y), ln, font=f, fill=(255, 255, 255, 255) if alpha else (20, 20, 20))
        y += min(w, h) // 8
    return img


def save_img(img, path, fmt, **kw):
    img.save(path, fmt, **kw)
    return path


def thumb_of(img, path, fmt="JPEG", maxdim=512):
    t = img.copy()
    if fmt == "JPEG" and t.mode != "RGB":
        bg = Image.new("RGB", t.size, (255, 255, 255))
        bg.paste(t, mask=t.split()[-1] if t.mode == "RGBA" else None)
        t = bg
    t.thumbnail((maxdim, maxdim))
    t.save(path, fmt, **({"quality": 70} if fmt == "JPEG" else {}))
    return path


def make_wav(path, secs, f0, f1):
    rate = 16000
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(secs * rate)):
            t = i / rate
            f = f0 + (f1 - f0) * t / secs
            env = min(1.0, t * 8, (secs - t) * 8)
            frames += struct.pack("<h", int(12000 * env * math.sin(2 * math.pi * f * t)))
        w.writeframes(bytes(frames))


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        raise SystemExit("command failed: %s\n%s" % (" ".join(cmd), r.stdout[-2000:]))


def build_media(src):
    """Returns dict key -> (path, meta). meta: dict with w,h,d,a as applicable."""
    os.makedirs(src, exist_ok=True)
    M = {}

    def p(n):
        return os.path.join(src, n)

    # JPEG landscape + portrait
    img = label_image(1200, 900, "FIXTURE\nJPEG in\n1200x900", (70, 130, 200), (230, 240, 255))
    save_img(img, p("jpeg_in.jpg"), "JPEG", quality=85)
    thumb_of(img, p("jpeg_in_thumb.jpg"))
    M["jpeg_in"] = (p("jpeg_in.jpg"), p("jpeg_in_thumb.jpg"), {"w": 1200, "h": 900})
    img = label_image(900, 1200, "FIXTURE\nJPEG out\n900x1200", (200, 120, 60), (255, 240, 220))
    save_img(img, p("jpeg_out.jpg"), "JPEG", quality=85)
    thumb_of(img, p("jpeg_out_thumb.jpg"))
    M["jpeg_out"] = (p("jpeg_out.jpg"), p("jpeg_out_thumb.jpg"), {"w": 900, "h": 1200})
    img = label_image(1000, 1000, "FIXTURE\nJPEG edited\ncaption", (90, 170, 90), (230, 255, 230))
    save_img(img, p("jpeg_edit.jpg"), "JPEG", quality=85)
    thumb_of(img, p("jpeg_edit_thumb.jpg"))
    M["jpeg_edit"] = (p("jpeg_edit.jpg"), p("jpeg_edit_thumb.jpg"), {"w": 1000, "h": 1000})
    img = label_image(800, 600, "FIXTURE\nJPEG as FILE\n(render 0)", (120, 120, 120), (240, 240, 240))
    save_img(img, p("jpeg_file.jpg"), "JPEG", quality=85)
    thumb_of(img, p("jpeg_file_thumb.jpg"))
    M["jpeg_file"] = (p("jpeg_file.jpg"), p("jpeg_file_thumb.jpg"), {"w": 800, "h": 600})
    img = label_image(1080, 720, "FIXTURE\nGROUP JPEG", (150, 80, 170), (250, 230, 255))
    save_img(img, p("jpeg_group.jpg"), "JPEG", quality=85)
    thumb_of(img, p("jpeg_group_thumb.jpg"))
    M["jpeg_group"] = (p("jpeg_group.jpg"), p("jpeg_group_thumb.jpg"), {"w": 1080, "h": 720})
    img = label_image(720, 1080, "FIXTURE\nOWN GROUP\nJPEG", (40, 150, 150), (220, 255, 255))
    save_img(img, p("jpeg_owngroup.jpg"), "JPEG", quality=85)
    thumb_of(img, p("jpeg_owngroup_thumb.jpg"))
    M["jpeg_owngroup"] = (p("jpeg_owngroup.jpg"), p("jpeg_owngroup_thumb.jpg"), {"w": 720, "h": 1080})
    # thumbnail-only (not downloaded) image
    img = label_image(640, 480, "FIXTURE\nthumb only\n(not downloaded)", (180, 180, 60), (255, 255, 220))
    thumb_of(img, p("thumbonly_thumb.jpg"))
    M["thumbonly"] = (None, p("thumbonly_thumb.jpg"), {"w": 640, "h": 480})
    # PNG (screenshot-like) + png thumb
    img = label_image(750, 1334, "FIXTURE\nPNG\n750x1334", (30, 30, 60), (90, 90, 160))
    save_img(img, p("png_in.png"), "PNG")
    thumb_of(img, p("png_in_thumb.png"), "PNG")
    M["png_in"] = (p("png_in.png"), p("png_in_thumb.png"), {"w": 750, "h": 1334})
    # animated GIF
    frames = [label_image(320, 240, "GIF frame %d" % i, (60 + 40 * i, 90, 200 - 30 * i), (255, 255, 255))
              .convert("P", palette=Image.ADAPTIVE) for i in range(5)]
    frames[0].save(p("anim.gif"), "GIF", save_all=True, append_images=frames[1:], duration=250, loop=0)
    thumb_of(frames[0].convert("RGB"), p("anim_thumb.jpg"))
    M["gif_in"] = (p("anim.gif"), p("anim_thumb.jpg"), {"a": True, "w": 320, "h": 240})
    frames = [label_image(240, 240, "out %d" % i, (200, 60 + 40 * i, 90), (255, 255, 255))
              .convert("P", palette=Image.ADAPTIVE) for i in range(4)]
    frames[0].save(p("anim_out.gif"), "GIF", save_all=True, append_images=frames[1:], duration=300, loop=0)
    thumb_of(frames[0].convert("RGB"), p("anim_out_thumb.jpg"))
    M["gif_out"] = (p("anim_out.gif"), p("anim_out_thumb.jpg"), {"a": True})
    # stickers: transparent PNG 512
    for key, c in (("sticker_in", (240, 90, 120)), ("sticker_out", (80, 180, 240))):
        img = label_image(512, 512, "STICKER\n" + key.split("_")[1], c, (40, 40, 40), alpha=True)
        save_img(img, p(key + ".png"), "PNG")
        thumb_of(img, p(key + "_thumb.png"), "PNG")
        M[key] = (p(key + ".png"), p(key + "_thumb.png"), {"w": 512, "h": 512})
    # voice messages (AAC in MPEG-4, like Android AudioRecorder OutputFormat.MPEG_4, mime audio/aac)
    for key, secs, f0, f1 in (("voice_in", 4.2, 300, 700), ("voice_out", 7.0, 600, 250), ("voice_grp", 3.0, 440, 880),
                              ("audio_file", 5.0, 220, 440)):
        wav = p(key + ".wav")
        make_wav(wav, secs, f0, f1)
        run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "32000", wav, p(key + ".m4a")])
        os.remove(wav)
        M[key] = (p(key + ".m4a"), None, {"d": secs})
    # videos (H.264/AAC mp4) + JPEG thumbnail from frame 1s
    for key, size, secs, src_f in (("video_in", "640x360", 3, "testsrc2"), ("video_out", "360x640", 5, "smptebars"),
                                   ("video_file", "320x240", 2, "rgbtestsrc")):
        mp4 = p(key + ".mp4")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "%s=size=%s:rate=25" % (src_f, size),
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", str(secs),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "main", "-c:a", "aac", "-b:a", "64k",
             "-movflags", "+faststart", "-shortest", mp4])
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", mp4, "-frames:v", "1", "-q:v", "4",
             p(key + "_thumb.jpg")])
        w, h = (int(v) for v in size.split("x"))
        M[key] = (mp4, p(key + "_thumb.jpg"), {"d": float(secs)})
    # PDF (2 pages) + first-page thumbnail
    pages = [label_image(1240, 1754, "FIXTURE PDF\npage %d" % (i + 1), (250, 250, 250), (220, 220, 220)) for i in range(2)]
    pages[0].save(p("doc.pdf"), "PDF", save_all=True, append_images=pages[1:], resolution=150)
    thumb_of(pages[0], p("doc_thumb.jpg"))
    M["pdf"] = (p("doc.pdf"), p("doc_thumb.jpg"), {})
    # vCard, text, binary
    with open(p("contact.vcf"), "w", newline="") as f:
        f.write("BEGIN:VCARD\r\nVERSION:3.0\r\nN:Muster;Max;;;\r\nFN:Max Muster (fixture)\r\n"
                "TEL;TYPE=CELL:+41 00 000 00 00\r\nEMAIL:max@example.invalid\r\nEND:VCARD\r\n")
    M["vcard"] = (p("contact.vcf"), None, {})
    with open(p("notes.txt"), "w") as f:
        f.write("Fixture plain text file.\nZeile 2 mit Umlauten: äöü ÄÖÜ ß\nEmoji: 😀\n")
    M["txt"] = (p("notes.txt"), None, {})
    with open(p("data.bin"), "wb") as f:
        f.write(bytes(range(256)) * 16)
    M["bin"] = (p("data.bin"), None, {})
    # avatars (plain JPEG)
    for key, c in (("avatar_me", (30, 120, 70)), ("avatar_user_ZZFIXR01", (200, 50, 50)),
                   ("profile_ZZFIXR02", (50, 50, 200)), ("profile_ZZFIXR04", (160, 100, 20)),
                   ("group_Fixture", (120, 40, 160)), ("group_Own", (20, 140, 140))):
        img = label_image(512, 512, key.replace("_", "\n"), c, (255, 255, 255))
        save_img(img, p(key + ".jpg"), "JPEG", quality=80)
        M[key] = (p(key + ".jpg"), None, {})
    return M


# ----------------------------------------------------------------------------------------------------------------
# the synthetic world
# ----------------------------------------------------------------------------------------------------------------
class Chat:
    def __init__(self, kind, key, title):
        self.kind, self.key, self.title = kind, key, title   # kind: contact|group
        self.rows = []          # dicts in CSV header names + private '_' keys
        self.t = None


def contact_defs():
    # identity, first, last, nick, verification, hidden, archived, feature title
    return [
        ("ZZFIXR01", "Tina", "Text", "tina", "FULLY_VERIFIED", 0, 0, "Text features"),
        ("ZZFIXR02", "Ida", "Images", "ida", "SERVER_VERIFIED", 0, 0, "Images JPEG/PNG"),
        ("ZZFIXR03", "Gil", "Gif", "gil", "UNVERIFIED", 0, 0, "Animated GIF + stickers"),
        ("ZZFIXR04", "Vera", "Voice", "vera", "SERVER_VERIFIED", 0, 0, "Voice messages + audio file"),
        ("ZZFIXR05", "Vic", "Video", "vic", "UNVERIFIED", 0, 0, "Video"),
        ("ZZFIXR06", "Fred", "Files", "fred", "FULLY_VERIFIED", 0, 0, "Files: PDF, vCard, txt, bin, JPEG-as-file, mp4-as-file"),
        ("ZZFIXR07", "Lou", "Location", "lou", "UNVERIFIED", 0, 0, "Locations"),
        ("ZZFIXR08", "Cal", "Calls", "cal", "SERVER_VERIFIED", 0, 0, "Call statuses"),
        ("ZZFIXR09", "Polly", "Poll", "polly", "FULLY_VERIFIED", 0, 0, "Polls + starred"),
        ("ZZFIXR10", "Pia", "Placeholder", "pia", "UNVERIFIED", 0, 0, "FILE rows without media"),
        ("ZZFIXR11", "Hugo", "Hidden", "hugo", "UNVERIFIED", 1, 0, "group-only contact (hidden=1), no 1:1 chat"),
        ("ZZFIXR12", "Nora", "NoChat", "", "UNVERIFIED", 0, 0, "contact without messages (last_update empty)"),
        ("ZZFIXR13", "", "", "archie", "UNVERIFIED", 0, 1, "archived chat, nickname only"),
        ("*ZZFIX01", "", "", "Fixture Gateway", "SERVER_VERIFIED", 0, 0, "gateway ID chat"),
    ]


class World:
    def __init__(self, media):
        self.M = media
        self.contacts = {}
        for ident, fn, ln, nick, ver, hid, arch, feat in contact_defs():
            self.contacts[ident] = dict(identity=ident, publickey=rbytes(32).hex(), verification=ver, acid="",
                                        firstname=fn, lastname=ln, nick_name=nick, hidden=hid, archived=arch,
                                        _feature=feat)
        self.own_private = rbytes(32)
        self.format_version = FORMAT_VERSION
        self.chats = {}          # key -> Chat
        self.groups = {}         # name -> dict
        self.polls = []
        self.choices = []
        self.votes = []
        self.creactions = []
        self.greactions = []
        self.manifest = {}
        self.call_id = 1000

    # ---- message helpers -----------------------------------------------------------------------------------
    def chat(self, kind, key, title, start):
        c = Chat(kind, key, title)
        c.t = start
        self.chats[(kind, key)] = c
        return c

    def add(self, c, out, mtype, body, *, sender=None, step=60_000, state=None, caption=None, quote=None,
            status=False, edited=False, deleted=False, starred=False, media=None, thumb=None, apiid=True,
            gstates=None, note="", feature=None, delivered=True, read=True):
        c.t += step
        t = c.t
        a = new_apiid() if apiid else ""
        row = {
            "apiid": a, "uid": new_uid(), "isoutbox": out, "isread": True, "issaved": True,
            "posted_at": t if out else t - 1500, "created_at": t, "modified_at": None,
            "type": mtype, "body": body, "isstatusmessage": status, "caption": caption,
            "quoted_message_apiid": quote, "delivered_at": None, "read_at": None,
            "display_tags": 1 if starred else 0,
            "edited_at": t + 120_000 if edited else None, "deleted_at": t + 180_000 if deleted else None,
        }
        if out:
            if state is None:
                state = "READ" if (delivered and read) else ("DELIVERED" if delivered else "SENT")
                if c.kind == "group":
                    state = "SENT"
            if c.kind == "contact" and state in ("READ", "DELIVERED", "USERACK", "USERDEC", "CONSUMED"):
                row["delivered_at"] = t + 2_000
                if state in ("READ", "USERACK", "USERDEC", "CONSUMED"):
                    row["read_at"] = t + 45_000
            row["modified_at"] = row["read_at"] or row["delivered_at"] or t
        else:
            state = state if state is not None else ""
            if not status:
                row["read_at"] = t + 30_000
                row["modified_at"] = t + 30_000
        row["messagestae"] = state
        if c.kind == "group":
            row["identity"] = "" if (out or status) else sender
            row["g_msg_states"] = jdump(gstates) if gstates else ""
        if deleted:
            row["body"] = ""
            row["caption"] = None if mtype != "LOCATION" else None
        if edited and mtype == "FILE":
            pass
        row["_media"] = media
        row["_thumb"] = thumb
        row["_note"] = note
        row["_feature"] = feature or note
        c.rows.append(row)
        return row

    def text(self, c, out, body, **kw):
        return self.add(c, out, "TEXT", body, **kw)

    def file(self, c, out, mkey, mime, name, render, *, caption=None, downloaded=True, blob=True, meta=None,
             thumb_mime="image/jpeg", with_media=True, with_thumb=True, empty_blob=False, **kw):
        src = self.M.get(mkey) if mkey else None
        media_path = src[0] if (src and with_media) else None
        thumb_path = src[1] if (src and with_thumb) else None
        size = os.path.getsize(src[0]) if (src and src[0]) else 123456
        if meta is None:
            meta = dict(src[2]) if src else {}
        if thumb_path is None:
            thumb_mime = thumb_mime if (src and src[1] and not with_thumb) else None
        if empty_blob:
            blob_hex, key_hex = "", ""
        elif blob:
            blob_hex, key_hex = rbytes(16).hex(), rbytes(32).hex()
        else:
            blob_hex, key_hex = None, None
        body = jdump([blob_hex, key_hex, mime, size, name, render, bool(downloaded), caption, thumb_mime, meta])
        return self.add(c, out, "FILE", body, caption=caption, media=media_path, thumb=thumb_path, **kw)

    def location(self, c, out, lat, lon, acc, address=None, name=None, **kw):
        body = jdump([lat, lon, acc, address, name])
        if name:
            cap = "*%s*\n%s" % (name, address or "")
            cap = cap.rstrip("\n")
        else:
            cap = address or ""
        return self.add(c, out, "LOCATION", body, caption=cap, **kw)

    def voip(self, c, out, status, reason=None, duration=None, with_call_id=True, **kw):
        p = {"status": status}
        if with_call_id:
            self.call_id += 7
            p["callId"] = self.call_id
        if reason is not None:
            p["reason"] = reason
        if duration is not None:
            p["duration"] = duration
        return self.add(c, out, "VOIP_STATUS", jdump([1, p]), apiid=False, **kw)

    def gstatus(self, c, stype, identity=None, ballot=None, newname=None, **kw):
        p = {"status": stype}
        if identity:
            p["identity"] = identity
        if ballot:
            p["ballotName"] = ballot
        if newname:
            p["newGroupName"] = newname
        return self.add(c, False, "GROUP_STATUS", jdump([4, p]), status=True, apiid=False, **kw)

    def legacy_status(self, c, text, **kw):
        return self.add(c, False, "TEXT", text, status=True, apiid=False, **kw)

    def poll(self, c, creator, name, state, assessment, ptype, choices, votes, pid):
        aid = new_apiid()
        created = c.t + 60_000
        ref, ref_id = ("GroupBallotModel", self.groups[c.key]["group_uid"]) if c.kind == "group" else \
            ("IdentityBallotModel", c.key)
        self.polls.append({"id": pid, "aid": aid, "creator": creator, "ref": ref, "ref_id": ref_id, "name": name,
                           "state": state, "assessment": assessment, "type": ptype, "choice_type": "TEXT",
                           "last_viewed_at": created + 600_000, "created_at": created,
                           "modified_at": created + 900_000, "_chat": (c.kind, c.key)})
        puid = "%s-%s" % (aid, creator)
        for i, cname in enumerate(choices):
            cnt = sum(1 for v in votes.values() if i in v)
            self.choices.append({"id": 5000 + len(self.choices), "ballot": puid, "aid": i, "type": "Text",
                                 "name": cname, "vote_count": cnt, "order": i, "created_at": created,
                                 "modified_at": created + 900_000})
        for voter, sel in votes.items():
            for i in range(len(choices)):
                self.votes.append({"id": 9000 + len(self.votes), "ballot_uid": puid, "choice_uid": str(i),
                                   "identity": voter, "choice": 1 if i in sel else 0,
                                   "created_at": created + 300_000, "modified_at": created + 300_000})
        return pid

    def creact(self, partner, row, sender, emoji, dt_ms=90_000):
        self.creactions.append({"identity": partner, "api_message_id": row["apiid"], "sender_identity": sender,
                                "emoji_sequence": emoji, "reacted_at": row["created_at"] + dt_ms})

    def greact(self, gname, row, sender, emoji, dt_ms=90_000):
        g = self.groups[gname]
        self.greactions.append({"api_group_id": g["id"], "group_creator_identity": g["creator"],
                                "api_message_id": row["apiid"], "sender_identity": sender, "emoji_sequence": emoji,
                                "reacted_at": row["created_at"] + dt_ms})

    # ---- content ---------------------------------------------------------------------------------------------
    def build(self):
        day = 86_400_000
        H = 3_600_000

        # ZZFIXR01: text features -------------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR01", "Text", BASE)
        self.text(c, False, "Hallo! Plain incoming text message.", note="plain text in")
        m2 = self.text(c, True, "Hi Tina 👋 emoji test 😀🎉🇨🇭 👍🏽 👨‍👩‍👧", note="emoji incl. flag, skin tone, ZWJ")
        self.text(c, False, "Line 1\nLine 2\n\nLine 4 after a blank line\n    indented line 5", note="multiline")
        self.text(c, True, "*bold* _italic_ ~strike~ and a link https://threema.ch/en", note="markup + URL")
        self.text(c, False, "Umlaute äöü ÄÖÜ ß, quotes \"double\" 'single', backslash \\ and \\\\ double, comma, semicolon;",
                  note="CSV escaping: quotes, backslashes, comma")
        self.text(c, False, "Reply with quote v2 (quoted_message_apiid)", quote=m2["apiid"], note="quote v2 in")
        self.text(c, True, "> ZZFIXR01: Hallo! Plain incoming text message.\nReply using legacy quote v1",
                  note="legacy quote v1 in body")
        self.text(c, False, "Hey @[ZZFIXN01], mention of me; and of Ida @[ZZFIXR02]; all: @[@@@@@@@@]",
                  note="mentions")
        self.text(c, True, "This text was edited (current version)", edited=True, note="edited (edited_at)")
        self.text(c, False, "", deleted=True, note="deleted for everyone (deleted_at, empty body)")
        r = self.text(c, True, "React to this message please", note="reactions (contact_reactions.csv)")
        self.creact("ZZFIXR01", r, "ZZFIXR01", "👍")
        self.creact("ZZFIXR01", r, "ZZFIXR01", "❤️", 95_000)
        self.creact("ZZFIXR01", r, OWN, "😂", 120_000)
        self.creact("ZZFIXR01", m2, "ZZFIXR01", "🇨🇭")
        self.text(c, True, "Outgoing msg with legacy USERACK (partner 👍)", state="USERACK",
                  note="legacy USERACK on outgoing -> 👍 by partner")
        self.text(c, False, "Incoming msg with legacy USERDEC (my 👎)", state="USERDEC",
                  note="legacy USERDEC on incoming -> 👎 by me")
        self.text(c, False, "Incoming msg with legacy USERACK (my 👍)", state="USERACK",
                  note="legacy USERACK on incoming -> 👍 by me")
        self.text(c, True, "Delivered but not yet read (state DELIVERED)", read=False, note="state DELIVERED")
        self.text(c, True, "Sent only (state SENT)", delivered=False, read=False, note="state SENT")
        self.text(c, True, "Send failed on Android (SENDFAILED) - must import as sent, never resend",
                  state="SENDFAILED", note="state SENDFAILED")
        self.text(c, False, "Newest message in the text chat ✅ " + "long text " * 25, note="long wrapped text")

        # ZZFIXR02: images -------------------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR02", "Images", BASE + 1 * day)
        self.text(c, False, "Images follow", note="")
        im1 = self.file(c, False, "jpeg_in", "image/jpeg", "IMG_20260902_101500.jpg", 1,
                        caption="Incoming JPEG with caption", note="JPEG in + thumb + caption")
        self.creact("ZZFIXR02", im1, OWN, "😍")
        self.file(c, True, "jpeg_out", "image/jpeg", "IMG_20260902_101800.jpg", 1, blob=False,
                  note="JPEG out, blob/key null (Android local outgoing model)")
        self.file(c, False, "png_in", "image/png", "Screenshot_20260902.png", 1, thumb_mime="image/png",
                  note="PNG in + PNG thumb")
        self.file(c, True, "jpeg_edit", "image/jpeg", "IMG_20260902_103000.jpg", 1, caption="Edited caption (v2)",
                  edited=True, note="JPEG out, caption edited")
        self.file(c, False, "jpeg_in", "image/jpeg", "IMG_20260925_090000.jpg", 1, caption="Newer than SPLIT_CUTOFF",
                  step=23 * day, note="JPEG in created after split cutoff (no media in --split media backup)")

        # ZZFIXR03: GIF + stickers -----------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR03", "GIF+Sticker", BASE + 2 * day)
        self.file(c, False, "gif_in", "image/gif", "anim.gif", 1, note="animated GIF in, meta a,w,h")
        self.file(c, True, "gif_out", "image/gif", "anim_out.gif", 1, note="animated GIF out, meta {a}")
        self.file(c, False, "sticker_in", "image/png", "sticker.png", 2, thumb_mime="image/png",
                  note="sticker in (rendering 2)")
        self.file(c, True, "sticker_out", "image/png", "sticker.png", 2, thumb_mime="image/png",
                  note="sticker out (rendering 2)")

        # ZZFIXR04: voice --------------------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR04", "Voice", BASE + 3 * day)
        self.file(c, False, "voice_in", "audio/aac", "voice_message_20260904.aac", 1, thumb_mime=None,
                  state="CONSUMED", note="voice in (audio/aac render 1), played=CONSUMED")
        self.file(c, True, "voice_out", "audio/aac", "voice_message_20260904b.aac", 1, thumb_mime=None,
                  note="voice out")
        self.file(c, False, "voice_in", "audio/aac", "voice_message_20260904c.aac", 1, thumb_mime=None,
                  note="voice in, not yet played")
        self.file(c, False, "audio_file", "audio/x-m4a", "song.m4a", 0, thumb_mime=None, meta={},
                  note="audio FILE (render 0), not a voice message")

        # ZZFIXR05: video --------------------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR05", "Video", BASE + 4 * day)
        self.file(c, False, "video_in", "video/mp4", "VID_20260905.mp4", 1, caption="Incoming video 3s",
                  note="video in + thumb + caption, meta {d}")
        self.file(c, True, "video_out", "video/mp4", "VID_20260905b.mp4", 1, note="video out portrait + thumb")

        # ZZFIXR06: files --------------------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR06", "Files", BASE + 5 * day)
        self.file(c, False, "pdf", "application/pdf", "Fixture Document.pdf", 0, caption="A PDF with thumbnail",
                  note="PDF in + thumb")
        self.file(c, True, "vcard", "text/x-vcard", "Max Muster.vcf", 0, thumb_mime=None, note="vCard out")
        self.file(c, False, "txt", "text/plain", "notes.txt", 0, thumb_mime=None, note="text/plain in")
        self.file(c, False, "bin", "application/octet-stream", "data.bin", 0, thumb_mime=None,
                  note="octet-stream in")
        self.file(c, True, "jpeg_file", "image/jpeg", "photo-as-file.jpg", 0, meta={},
                  note="JPEG sent as file (render 0)")
        self.file(c, False, "video_file", "video/mp4", "clip-as-file.mp4", 0, meta={},
                  note="mp4 sent as file (render 0)")

        # ZZFIXR07: locations ----------------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR07", "Location", BASE + 6 * day)
        lr = self.location(c, False, 47.378177, 8.540192, 12.0, "Bahnhofplatz, 8001 Zürich", "Zürich HB",
                           note="location in with POI name+address")
        self.creact("ZZFIXR07", lr, OWN, "👍🏽")
        self.location(c, True, 47.5, 9.4, 25.0, note="location out, no POI")
        self.location(c, False, 46.947974, 7.447447, 8.0, "Marktgasse 1, 3011 Bern", None,
                      note="location in, address only")
        self.location(c, True, 46.2044, 6.1432, None, note="location out, accuracy null")
        self.location(c, False, 0.0, 0.0, None, deleted=True, note="deleted location (empty body)")

        # ZZFIXR08: calls --------------------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR08", "Calls", BASE + 7 * day)
        self.voip(c, False, 1, note="missed incoming")
        self.voip(c, True, 2, duration=125, note="finished outgoing 2:05")
        self.voip(c, False, 2, duration=42, note="finished incoming 0:42")
        self.voip(c, True, 3, reason=1, note="rejected BUSY (outgoing call)")
        self.voip(c, True, 3, reason=2, note="rejected TIMEOUT (outgoing call)")
        self.voip(c, False, 3, reason=3, note="rejected REJECTED (incoming, I declined)")
        self.voip(c, True, 3, reason=4, note="rejected DISABLED")
        self.voip(c, True, 3, reason=5, note="rejected OFF_HOURS")
        self.voip(c, False, 3, reason=0, note="rejected UNKNOWN")
        self.voip(c, True, 4, note="aborted outgoing")
        self.voip(c, False, 4, note="aborted incoming")
        self.voip(c, True, 2, duration=30, with_call_id=False, note="finished, no callId")
        self.voip(c, False, 1, with_call_id=False, note="missed, no callId")
        self.text(c, False, "Text after calls", note="text between call rows")
        self.voip(c, False, 2, duration=3725, note="finished incoming 1:02:05 (newest)")

        # ZZFIXR09: polls + starred ----------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR09", "Poll+Star", BASE + 8 * day)
        self.text(c, False, "Starred incoming message ⭐", starred=True, note="starred in (display_tags=1)")
        self.text(c, True, "Starred outgoing message ⭐", starred=True, note="starred out")
        p1 = self.poll(c, OWN, "Fixture poll 1:1 (open, single choice)", "OPEN", "SINGLE_CHOICE", "INTERMEDIATE",
                       ["Yes", "No", "Maybe"], {OWN: {0}, "ZZFIXR09": {2}}, 101)
        self.add(c, True, "BALLOT", jdump([1, p1]), note="poll created by me (OPEN)")
        p2 = self.poll(c, "ZZFIXR09", "Closed multi-choice poll", "CLOSED", "MULTIPLE_CHOICE", "RESULT_ON_CLOSE",
                       ["Mon", "Tue", "Wed", "Thu"], {OWN: {0, 2}, "ZZFIXR09": {2, 3}}, 102)
        self.add(c, False, "BALLOT", jdump([1, p2]), note="poll created by partner")
        self.add(c, False, "BALLOT", jdump([3, p2]), note="poll closed (BALLOT type 3)")
        self.text(c, False, "Newest: text after polls", note="")

        # ZZFIXR10: placeholders -------------------------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR10", "Placeholders", BASE + 9 * day)
        self.file(c, False, "jpeg_in", "image/jpeg", "IMG_missing.jpg", 1, with_media=False, with_thumb=False,
                  caption="Media missing, isDownloaded=true", note="FILE isDownloaded=1 but no media, no thumb")
        self.file(c, False, "thumbonly", "image/jpeg", "IMG_thumbonly.jpg", 1, downloaded=False, with_media=False,
                  note="not downloaded, thumbnail only")
        self.file(c, True, "video_out", "video/mp4", "VID_missing.mp4", 1, blob=False, with_media=False,
                  with_thumb=False, note="outgoing video, blob null, no media/thumb")
        self.file(c, False, "pdf", "application/pdf", "missing.pdf", 0, with_media=False, with_thumb=False,
                  note="pdf without media")
        self.file(c, False, "voice_in", "audio/aac", "voice_missing.aac", 1, thumb_mime=None, with_media=False,
                  note="voice without media")
        self.file(c, False, "jpeg_in", "image/jpeg", "IMG_emptyblob.jpg", 1, empty_blob=True, with_media=False,
                  with_thumb=False, note="blob/key empty strings (33 real rows)")
        self.file(c, True, "jpeg_out", "image/jpeg", "IMG_deleted.jpg", 1, deleted=True, with_media=False,
                  with_thumb=False, note="deleted FILE (empty body)")
        self.text(c, False, "Newest: placeholder chat end", note="")

        # ZZFIXR13: archived, *ZZFIX01 gateway ----------------------------------------------------------------
        c = self.chat("contact", "ZZFIXR13", "Archived", BASE + 10 * day)
        self.text(c, False, "Message in an archived chat", note="archived conversation")
        self.text(c, True, "Reply in archived chat", note="")
        c = self.chat("contact", "*ZZFIX01", "Gateway", BASE + 10 * day + H)
        self.text(c, False, "Automated message from a gateway ID", note="gateway sender")

        # Groups ------------------------------------------------------------------------------------------------
        self.groups["Fixture Group"] = dict(id=new_apiid(), creator="ZZFIXR01", groupname="Fixture Group",
                                            created_at=BASE + 11 * day, members=[OWN, "ZZFIXR01", "ZZFIXR02",
                                                                                 "ZZFIXR03", "ZZFIXR11"],
                                            archived=0, groupDesc="Fixture group description",
                                            groupDescTimestamp=BASE + 11 * day + 5 * H, group_uid=new_id10(),
                                            user_state=0, _avatar="group_Fixture")
        self.groups["Own Group"] = dict(id=new_apiid(), creator=OWN, groupname="Own Group",
                                        created_at=BASE + 12 * day, members=[OWN, "ZZFIXR02", "ZZFIXR04"],
                                        archived=0, groupDesc=None, groupDescTimestamp=None, group_uid=new_id10(),
                                        user_state=0, _avatar="group_Own")
        self.groups["Left Group"] = dict(id=new_apiid(), creator="ZZFIXR06", groupname="Left Group",
                                         created_at=BASE + 13 * day, members=["ZZFIXR06", "ZZFIXR07"],
                                         archived=0, groupDesc=None, groupDescTimestamp=None, group_uid=new_id10(),
                                         user_state=2, _avatar=None)

        G = "Fixture Group"
        c = self.chat("group", G, G, BASE + 11 * day)
        self.gstatus(c, 0, note="status CREATED")
        for ident in ("ZZFIXR02", "ZZFIXR03", "ZZFIXR04", "ZZFIXR05", "ZZFIXR11"):
            self.gstatus(c, 3, identity=ident, step=5_000, note="status MEMBER_ADDED")
        self.legacy_status(c, "Tina Text hat die Gruppe erstellt", note="legacy status text row (TEXT isstatusmessage=1)")
        self.text(c, False, "Welcome to the fixture group!", sender="ZZFIXR01", note="group text in (creator)")
        self.text(c, True, "Hi all, outgoing group text", note="group text out (state SENT)")
        self.gstatus(c, 1, newname="Fixture Group", note="status RENAMED")
        self.gstatus(c, 12, note="status GROUP_DESCRIPTION_CHANGED")
        self.gstatus(c, 2, note="status PROFILE_PICTURE_UPDATED")
        self.gstatus(c, 4, identity="ZZFIXR04", note="status MEMBER_LEFT")
        self.gstatus(c, 5, identity="ZZFIXR05", note="status MEMBER_KICKED")
        self.legacy_status(c, "Vic Video wurde aus der Gruppe entfernt", note="legacy status text row")
        gi = self.file(c, False, "jpeg_group", "image/jpeg", "IMG_group.jpg", 1, sender="ZZFIXR02",
                       caption="Group image", note="group image in + thumb")
        self.text(c, False, "Nice picture (quote v2 of the image)", sender="ZZFIXR03", quote=gi["apiid"],
                  note="group quote v2")
        gp = self.poll(c, "ZZFIXR01", "Group lunch?", "OPEN", "SINGLE_CHOICE", "INTERMEDIATE",
                       ["Pizza", "Sushi", "Salad"], {"ZZFIXR01": {0}, "ZZFIXR02": {1}, OWN: {0}}, 103)
        self.add(c, False, "BALLOT", jdump([1, gp]), sender="ZZFIXR01", note="group poll created by creator")
        self.gstatus(c, 8, identity="ZZFIXR02", ballot="Group lunch?", note="status FIRST_VOTE")
        self.gstatus(c, 9, identity="ZZFIXR01", ballot="Group lunch?", note="status MODIFIED_VOTE")
        self.gstatus(c, 10, ballot="Group lunch?", note="status RECEIVED_VOTE")
        self.gstatus(c, 11, ballot="Group lunch?", note="status VOTES_COMPLETE")
        self.location(c, False, 47.36667, 8.55, 20.0, "Seefeldquai, 8008 Zürich", "Seebad", sender="ZZFIXR03",
                      note="group location")
        self.file(c, False, "voice_grp", "audio/aac", "voice_group.aac", 1, thumb_mime=None, sender="ZZFIXR02",
                  note="group voice message")
        self.text(c, True, "Outgoing with legacy group acks (g_msg_states)",
                       gstates={"ZZFIXR01": "USERACK", "ZZFIXR02": "USERACK", "ZZFIXR03": "USERDEC"},
                       note="g_msg_states on outgoing")
        self.text(c, False, "Incoming acked by me (g_msg_states)", sender="ZZFIXR02",
                  gstates={OWN: "USERACK", "ZZFIXR03": "USERACK"}, note="g_msg_states on incoming")
        gr = self.text(c, True, "Group message with reactions", note="group reactions (group_reactions.csv)")
        self.greact(G, gr, "ZZFIXR01", "❤️")
        self.greact(G, gr, "ZZFIXR02", "👍")
        self.greact(G, gr, OWN, "😮")
        self.greact(G, gi, OWN, "🔥")
        self.text(c, False, "Hey @[ZZFIXN01] and @[@@@@@@@@] – newest group message", sender="ZZFIXR01",
                  note="group mention (newest)")

        G = "Own Group"
        c = self.chat("group", G, G, BASE + 12 * day)
        self.gstatus(c, 0, note="status CREATED (own group)")
        self.gstatus(c, 3, identity="ZZFIXR02", step=3_000, note="MEMBER_ADDED")
        self.gstatus(c, 3, identity="ZZFIXR04", step=3_000, note="MEMBER_ADDED")
        self.text(c, True, "My own group: first message", note="own group text out")
        self.text(c, False, "Thanks for adding me", sender="ZZFIXR04", note="own group text in")
        self.file(c, True, "jpeg_owngroup", "image/jpeg", "IMG_own.jpg", 1, note="own group image out")
        self.file(c, False, "voice_out", "audio/aac", "voice_own.aac", 1, thumb_mime=None, sender="ZZFIXR02",
                  note="own group voice in")
        self.text(c, True, "Newest message in my own group", note="")

        G = "Left Group"
        c = self.chat("group", G, G, BASE + 13 * day)
        self.gstatus(c, 0, note="CREATED")
        self.text(c, False, "Message before I left", sender="ZZFIXR06", note="")
        self.text(c, True, "Bye, leaving this group", note="")
        self.gstatus(c, 4, identity=OWN, note="MEMBER_LEFT (me) -> user_state=2")

        # derive last_update
        for ident, cd in self.contacts.items():
            ch = self.chats.get(("contact", ident))
            cd["last_update"] = max(r["created_at"] for r in ch.rows) if ch and ch.rows else None
        for name, g in self.groups.items():
            ch = self.chats[("group", name)]
            g["last_update"] = max(r["created_at"] for r in ch.rows)

    # ---- zip writing -----------------------------------------------------------------------------------------
    def write_zip(self, path, *, with_media=True, cutoff=None, id_seed=0):
        rr = random.Random(id_seed)
        cids = {i: "%010d" % rr.randrange(0, 2**31 - 1) for i in self.contacts}
        guids = {n: ("%010d" % rr.randrange(0, 2**31 - 1)) if id_seed else g["group_uid"] for n, g in self.groups.items()}
        keep = (lambda r: r["created_at"] < cutoff) if cutoff else (lambda r: True)
        counts = {"entries": 0, "media": 0, "thumbs": 0, "rows": 0}
        tmp = path + ".tmp"
        with pyzipper.AESZipFile(tmp, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
            z.setpassword(PASSWORD.encode())
            z.setencryption(pyzipper.WZ_AES, nbits=256)

            def put(name, data, compress=True):
                zi = z.zipinfo_cls(name, date_time=(2026, 9, 28, 8, 0, 0))
                zi.compress_type = pyzipper.ZIP_DEFLATED
                zi.flag_bits |= 0x800
                z.writestr(zi, data, compresslevel=6 if compress else 0)
                counts["entries"] += 1

            def putfile(name, src):
                with open(src, "rb") as f:
                    put(name, f.read(), compress=False)

            put("settings", csv_doc(None, [["version", self.format_version]]))
            put("identity", identity_export(OWN, self.own_private, PASSWORD).encode("ascii"), compress=False)
            putfile("contact_avatar_me", self.M["avatar_me"][0])
            contact_rows = []
            for ident, cd in self.contacts.items():
                cid = cids[ident]
                if ident == "ZZFIXR01":
                    putfile("contact_avatar_" + cid, self.M["avatar_user_ZZFIXR01"][0])
                if ident in ("ZZFIXR02", "ZZFIXR04"):
                    putfile("contact_profile_pic_" + cid, self.M["profile_" + ident][0])
                ch = self.chats.get(("contact", ident))
                rows = [r for r in (ch.rows if ch else []) if keep(r)]
                if with_media:
                    for r in rows:
                        if r["_media"]:
                            putfile("message_media_" + r["uid"], r["_media"]); counts["media"] += 1
                        if r["_thumb"]:
                            putfile("message_thumbnail_" + r["uid"], r["_thumb"]); counts["thumbs"] += 1
                # contact files: g_msg_states never written -> unquoted empty
                crow = [{k: v for k, v in r.items() if k in MSG_HDR and k != "g_msg_states"} for r in rows]
                put("message_%s.csv" % cid, csv_doc(MSG_HDR, crow))
                counts["rows"] += len(rows)
                lu = max((r["created_at"] for r in rows), default=None)
                contact_rows.append({**{k: v for k, v in cd.items() if not k.startswith("_")},
                                     "last_update": lu, "identity_id": cid})
            put("contacts.csv", csv_doc(CONTACT_HDR, contact_rows))
            group_rows = []
            for name, g in self.groups.items():
                guid = guids[name]
                if g["_avatar"]:
                    putfile("group_avatar_" + guid, self.M[g["_avatar"]][0])
                ch = self.chats[("group", name)]
                rows = [r for r in ch.rows if keep(r)]
                if with_media:
                    for r in rows:
                        if r["_media"]:
                            putfile("group_message_media_" + r["uid"], r["_media"]); counts["media"] += 1
                        if r["_thumb"]:
                            putfile("group_message_thumbnail_" + r["uid"], r["_thumb"]); counts["thumbs"] += 1
                put("group_message_%s.csv" % guid,
                    csv_doc(GMSG_HDR, [{k: v for k, v in r.items() if k in GMSG_HDR} for r in rows]))
                counts["rows"] += len(rows)
                group_rows.append({**{k: v for k, v in g.items() if not k.startswith("_")},
                                   "group_uid": guid,
                                   "last_update": max((r["created_at"] for r in rows), default=g["created_at"])})
            put("groups.csv", csv_doc(GROUP_HDR, group_rows))
            put("distribution_list.csv", csv_doc(DL_HDR, []))
            polls = []
            for p in self.polls:
                q = {k: v for k, v in p.items() if not k.startswith("_")}
                if q["ref"] == "GroupBallotModel":
                    q["ref_id"] = guids[p["_chat"][1]]
                if cutoff and q["created_at"] >= cutoff:
                    continue
                polls.append(q)
            puids = {"%s-%s" % (p["aid"], p["creator"]) for p in polls}
            put("ballot.csv", csv_doc(POLL_HDR, polls))
            put("ballot_choice.csv", csv_doc(CHOICE_HDR, [x for x in self.choices if x["ballot"] in puids]))
            put("ballot_vote.csv", csv_doc(VOTE_HDR, [x for x in self.votes if x["ballot_uid"] in puids]))
            cr = [x for x in self.creactions if not cutoff or x["reacted_at"] < cutoff]
            gr = [x for x in self.greactions if not cutoff or x["reacted_at"] < cutoff]
            put("contact_reactions.csv", csv_doc(CREACT_HDR, cr))
            put("group_reactions.csv", csv_doc(GREACT_HDR, gr))
            put("reaction_counts.csv", csv_doc(["contactReactions", "groupReactions"], [[len(cr), len(gr)]]))
            csp = [hmac.new(OWN.encode(), rbytes(24), hashlib.sha256).hexdigest() for _ in range(40)]
            d2d = [hmac.new(OWN.encode(), rbytes(24), hashlib.sha256).hexdigest() for _ in range(12)]
            put("nonces.csv", csv_doc(["nonces"], [[x] for x in csp]))
            put("nonces_d2d.csv", csv_doc(["nonces"], [[x] for x in d2d]))
            put("nonce_counts.csv", csv_doc(["csp", "d2d"], [[len(csp), len(d2d)]]), compress=False)
        os.replace(tmp, path)
        return counts, cids, guids

    def manifest_json(self, cids, guids):
        chats = []
        for (kind, key), ch in self.chats.items():
            chats.append({
                "chat_kind": kind, "chat_key": key, "title": ch.title,
                "file_id": cids.get(key) if kind == "contact" else guids.get(key),
                "messages": [{"uid": r["uid"], "apiid": r["apiid"], "type": r["type"], "isoutbox": int(r["isoutbox"]),
                              "state": r["messagestae"], "created_at": r["created_at"],
                              "has_media": bool(r["_media"]), "has_thumb": bool(r["_thumb"]),
                              "feature": r["_note"]} for r in ch.rows],
            })
        return {
            "own_identity": OWN, "format_version": self.format_version,
            "contacts": [{k: v for k, v in c.items() if k in ("identity", "verification", "hidden", "archived")}
                         | {"feature": c["_feature"], "identity_id": cids[c["identity"]]} for c in self.contacts.values()],
            "groups": [{"name": n, "api_id": g["id"], "creator": g["creator"], "group_uid": guids[n],
                        "members": g["members"], "user_state": g["user_state"]} for n, g in self.groups.items()],
            "chats": chats,
            "polls": [{k: p[k] for k in ("id", "aid", "creator", "ref", "state", "assessment", "type")} for p in self.polls],
            "reactions": {"contact": len(self.creactions), "group": len(self.greactions)},
            "split_cutoff_ms": SPLIT_CUTOFF,
        }


# ----------------------------------------------------------------------------------------------------------------
# verification: read back with the project's reader conventions
# ----------------------------------------------------------------------------------------------------------------
def verify(path, expect_media, expect_format_27=True):
    if CORE not in sys.path:
        sys.path.insert(0, CORE)
    from tmcore.lib.validate_android import read_csv   # noqa: E402
    z = pyzipper.AESZipFile(path)
    z.setpassword(PASSWORD.encode())
    names = z.namelist()
    bad_enc = [i.filename for i in z.infolist() if not (i.flag_bits & 1) or b"\x01\x99" not in i.extra
               or i.compress_type != 8]
    assert not bad_enc, "unencrypted/non-AES entries: %s" % bad_enc[:3]
    assert z.read("settings") == b'"version","27"\n' or not expect_format_27
    ident, pk, ok = identity_import_check(z.read("identity").decode(), PASSWORD)
    assert ident == OWN and ok, "identity entry round trip failed"
    hdr, contacts, _ = read_csv(z, "contacts.csv")
    assert hdr == CONTACT_HDR
    n = {"contact_rows": 0, "group_rows": 0, "file_rows": 0, "media_entries": 0, "thumb_entries": 0}
    for c in contacts:
        h, rows, raw = read_csv(z, "message_%s.csv" % c[10])
        assert h == MSG_HDR, h
        for r in rows:
            assert len(r) == 20
            if r[9] != "VOIP_STATUS":
                assert len(r[0]) == 16
            if r[9] == "FILE" and r[10]:
                b = json.loads(r[10]); assert len(b) == 10 and b[7] == (r[12] or None)
                n["file_rows"] += 1
        n["contact_rows"] += len(rows)
        # g_msg_states is the only unquoted-empty column in contact files
        assert not rows or raw.count('",,"') >= len(rows)
    hdr, groups, _ = read_csv(z, "groups.csv")
    assert hdr == GROUP_HDR
    for g in groups:
        h, rows, _ = read_csv(z, "group_message_%s.csv" % g[9])
        assert h == GMSG_HDR
        n["group_rows"] += len(rows)
        for r in rows:
            assert len(r) == 21
            if r[10] == "FILE" and r[11]:
                b = json.loads(r[11]); assert len(b) == 10 and b[7] == (r[13] or None)
                n["file_rows"] += 1
    n["media_entries"] = sum(1 for x in names if "_media_" in x)
    n["thumb_entries"] = sum(1 for x in names if "_thumbnail_" in x)
    if not expect_media:
        assert n["media_entries"] == 0 and n["thumb_entries"] == 0
    for x in ("ballot.csv", "ballot_choice.csv", "ballot_vote.csv", "contact_reactions.csv", "group_reactions.csv",
              "reaction_counts.csv", "nonces.csv", "nonces_d2d.csv", "nonce_counts.csv", "distribution_list.csv"):
        assert x in names, x
    n["entries"] = len(names)
    return n


def generate(out, *, split=False, format_version=FORMAT_VERSION, rebuild_media=False):
    """Write the fixture backup(s) to `out` and return the verification counts (no password file is written)."""
    os.makedirs(out, exist_ok=True)
    src = os.path.join(out, "src-media")
    if rebuild_media and os.path.isdir(src):
        shutil.rmtree(src)
    media = build_media(src)
    w = World(media)
    w.format_version = str(format_version)
    w.build()
    target = os.path.join(out, "fixture-backup.zip")
    counts, cids, guids = w.write_zip(target)
    rep = {"fixture-backup.zip": verify(target, True, str(format_version) == "27")}
    with open(os.path.join(out, "fixture-manifest.json"), "w") as f:
        json.dump(w.manifest_json(cids, guids), f, indent=1, ensure_ascii=False)
    if split:
        t = os.path.join(out, "fixture-text-backup.zip")
        m = os.path.join(out, "fixture-media-backup.zip")
        w.write_zip(t, with_media=False, id_seed=11)
        w.write_zip(m, with_media=True, cutoff=SPLIT_CUTOFF, id_seed=22)
        rep["fixture-text-backup.zip"] = verify(t, False, str(format_version) == "27")
        rep["fixture-media-backup.zip"] = verify(m, True, str(format_version) == "27")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--split", action="store_true")
    ap.add_argument("--format-version", default=FORMAT_VERSION)
    ap.add_argument("--rebuild-media", action="store_true")
    a = ap.parse_args()
    print(json.dumps(generate(a.out, split=a.split, format_version=a.format_version,
                              rebuild_media=a.rebuild_media), indent=1))


if __name__ == "__main__":
    main()
