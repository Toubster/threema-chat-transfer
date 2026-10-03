#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Create a SYNTHETIC normalized.sqlite (NORMALIZED CONTRACT) covering every message kind, for the Swift importer.

    .venv/bin/python tools/importer/tests/make_mini_normalized.py [build/importer-mini]

No real data: fake identities (TST*), fake texts, generated media (Pillow + ffmpeg if available).
Writes <workdir>/normalized.sqlite, <workdir>/media/<uid>, <workdir>/thumbs/<uid>, <workdir>/avatars/<name>
and <workdir>/expected.json (the numbers the test script asserts on).
"""
import hashlib
import io
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import uuid

from PIL import Image, ImageDraw

WORK = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "../../../build/importer-mini"))
RNG = random.Random(4711)

OWN = "ZZTSTN01"
A, B, C, D, GW = "ZZTSTAA1", "ZZTSTBB2", "ZZTSTCC3", "ZZTSTDD4", "*ZZTST01"
UNKNOWN_SENDER = "ZZTSTZZ9"      # not in contacts -> group message skipped
UNKNOWN_CHAT = "ZZTSTYY8"        # 1:1 chat without contact row -> skipped

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE contacts(identity TEXT PRIMARY KEY, public_key BLOB NOT NULL, verification INTEGER, first_name TEXT, last_name TEXT,
  nickname TEXT, hidden INTEGER, archived INTEGER, last_update_ms INTEGER, avatar_user_path TEXT, avatar_contact_path TEXT);
CREATE TABLE groups(group_key TEXT PRIMARY KEY, group_id BLOB NOT NULL, creator TEXT NOT NULL, is_mine INTEGER, name TEXT,
  created_ms INTEGER, last_update_ms INTEGER, archived INTEGER, user_state INTEGER, members_json TEXT, avatar_path TEXT,
  description TEXT, description_ms INTEGER);
CREATE TABLE messages(uid TEXT PRIMARY KEY, chat_kind TEXT, chat_key TEXT, api_id BLOB, msg_id BLOB NOT NULL, is_own INTEGER,
  sender TEXT, kind TEXT, created_ms INTEGER, posted_ms INTEGER, delivered_ms INTEGER, read_ms INTEGER, modified_ms INTEGER,
  edited_ms INTEGER, deleted_ms INTEGER, state TEXT, is_read INTEGER, starred INTEGER, text TEXT, quoted_api_id BLOB,
  file_mime TEXT, file_name TEXT, file_size INTEGER, file_render INTEGER, file_caption TEXT, file_blob_id BLOB, file_key BLOB,
  file_thumb_mime TEXT, file_meta_json TEXT, media_path TEXT, media_sha256 TEXT, thumb_path TEXT,
  loc_lat REAL, loc_lon REAL, loc_acc REAL, loc_name TEXT, loc_address TEXT,
  ballot_ref INTEGER, ballot_data_type INTEGER,
  call_status INTEGER, call_reason INTEGER, call_duration_s INTEGER, call_id INTEGER,
  gstatus_type INTEGER, gstatus_identity TEXT, gstatus_name TEXT,
  raw_type TEXT, raw_body TEXT);
CREATE TABLE reactions(chat_kind TEXT, chat_key TEXT, target_msg_id BLOB, sender TEXT, emoji TEXT, reacted_ms INTEGER, source TEXT);
CREATE TABLE ballots(ref_id INTEGER PRIMARY KEY, api_id BLOB, creator TEXT, chat_kind TEXT, chat_key TEXT, title TEXT, state TEXT,
  assessment TEXT, btype TEXT, choice_type TEXT, created_ms INTEGER, modified_ms INTEGER);
CREATE TABLE ballot_choices(ballot_ref INTEGER, choice_id INTEGER, name TEXT, order_pos INTEGER, vote_count INTEGER, created_ms INTEGER, modified_ms INTEGER);
CREATE TABLE ballot_votes(ballot_ref INTEGER, choice_id INTEGER, identity TEXT, choice INTEGER, created_ms INTEGER, modified_ms INTEGER);
CREATE TABLE nonces(kind TEXT, hash BLOB);
"""

MSG_COLS = ["uid", "chat_kind", "chat_key", "api_id", "msg_id", "is_own", "sender", "kind", "created_ms", "posted_ms",
            "delivered_ms", "read_ms", "modified_ms", "edited_ms", "deleted_ms", "state", "is_read", "starred", "text",
            "quoted_api_id", "file_mime", "file_name", "file_size", "file_render", "file_caption", "file_blob_id",
            "file_key", "file_thumb_mime", "file_meta_json", "media_path", "media_sha256", "thumb_path", "loc_lat",
            "loc_lon", "loc_acc", "loc_name", "loc_address", "ballot_ref", "ballot_data_type", "call_status",
            "call_reason", "call_duration_s", "call_id", "gstatus_type", "gstatus_identity", "gstatus_name",
            "raw_type", "raw_body"]


def rbytes(n):
    return bytes(RNG.getrandbits(8) for _ in range(n))


def msgid_from_uid(uid):
    return hashlib.sha256(uid.encode()).digest()[:8]


def new_uid():
    return str(uuid.UUID(int=RNG.getrandbits(128), version=4))


T0 = 1_700_000_000_000  # 2023-11-14
_t = [T0]


def ts(step_s=60):
    _t[0] += step_s * 1000
    return _t[0]


def jpeg_bytes(w, h, color, noise=False):
    im = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(im)
    d.rectangle([w // 4, h // 4, 3 * w // 4, 3 * h // 4], outline=(255, 255, 255), width=3)
    if noise:
        px = im.load()
        for y in range(h):
            for x in range(w):
                px[x, y] = (RNG.randrange(256), RNG.randrange(256), RNG.randrange(256))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    return b.getvalue()


def png_rgba_bytes(w, h):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([4, 4, w - 4, h - 4], fill=(250, 180, 0, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def gif_bytes(w, h):
    frames = [Image.new("P", (w, h), i * 40) for i in range(3)]
    b = io.BytesIO()
    frames[0].save(b, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return b.getvalue()


def ffmpeg_media(kind, out_path):
    if not shutil.which("ffmpeg"):
        return False
    if kind == "audio":
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2.5",
               "-c:a", "aac", "-b:a", "32k", "-f", "mp4", out_path]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=3",
               "-pix_fmt", "yuv420p", "-c:v", "libx264", "-f", "mp4", out_path]
    return subprocess.run(cmd).returncode == 0 and os.path.getsize(out_path) > 0


class Fixture:
    def __init__(self):
        self.msgs = []
        self.reactions = []
        self.expected = {"skip": {}}
        self.media = {}   # uid -> bytes or path marker

    def put_media(self, uid, data, sub="media"):
        path = os.path.join(sub, uid)
        full = os.path.join(WORK, path)
        with open(full, "wb") as f:
            f.write(data)
        return path

    def add(self, **kw):
        row = {c: None for c in MSG_COLS}
        row.update(kw)
        if row["uid"] is None:
            row["uid"] = new_uid()
        if row["msg_id"] is None:
            row["msg_id"] = row["api_id"] if row["api_id"] is not None else msgid_from_uid(row["uid"])
        if row["created_ms"] is None:
            row["created_ms"] = ts()
        if row["posted_ms"] is None:
            row["posted_ms"] = row["created_ms"] - (0 if row["is_own"] else 5000)
        if row["is_read"] is None:
            row["is_read"] = 1
        if row["starred"] is None:
            row["starred"] = 0
        self.msgs.append(row)
        return row


def main():
    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    for sub in ("media", "thumbs", "avatars"):
        os.makedirs(os.path.join(WORK, sub))
    db_path = os.path.join(WORK, "normalized.sqlite")
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    fx = Fixture()

    # ---------------- contacts ----------------
    with open(os.path.join(WORK, "avatars", "contact_avatar_A"), "wb") as f:
        f.write(jpeg_bytes(96, 96, (10, 120, 200)))
    with open(os.path.join(WORK, "avatars", "contact_profile_pic_A"), "wb") as f:
        f.write(jpeg_bytes(80, 64, (200, 20, 20)))
    with open(os.path.join(WORK, "avatars", "group_avatar_G1"), "wb") as f:
        f.write(jpeg_bytes(128, 128, (20, 200, 20)))
    contacts = [
        # identity, verification, first, last, nick, hidden, archived, last_update, avatar_user, avatar_contact
        (A, 2, "Alice", "Fixture", "ali", 0, 0, None, "avatars/contact_avatar_A", "avatars/contact_profile_pic_A"),
        (B, 1, None, None, "bob", 0, 1, None, None, None),          # archived 1:1 chat
        (C, 0, None, None, None, 1, 0, None, None, None),           # group-only (hidden)
        (D, 0, "Dora", None, None, 0, 0, None, None, None),         # no messages -> no conversation
        (GW, 1, None, None, "Gateway", 0, 0, None, None, None),
    ]
    for c in contacts:
        db.execute("INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (c[0], rbytes(32), c[1], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9]))

    # ---------------- groups ----------------
    g1_id, g2_id, g3_id = rbytes(8), rbytes(8), rbytes(8)
    G1 = f"{g1_id.hex()}-{A}"
    G2 = f"{g2_id.hex()}-{OWN}"
    G3 = f"{g3_id.hex()}-{B}"
    groups = [
        (G1, g1_id, A, 0, "Fixture Group One", T0, None, 0, 0, json.dumps([A, B, C]), "avatars/group_avatar_G1", None, None),
        (G2, g2_id, OWN, 1, "My Own Group", T0 + 1000, None, 1, 0, json.dumps([B]), None, "desc", T0 + 2000),
        (G3, g3_id, B, 0, "Left Group", T0 + 3000, T0 + 4000, 0, 2, json.dumps([B, A]), None, None, None),  # no msgs
    ]
    for g in groups:
        db.execute("INSERT INTO groups VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", g)

    # ---------------- ballots ----------------
    b1_api, b2_api = rbytes(8), rbytes(8)
    db.execute("INSERT INTO ballots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (101, b1_api, OWN, "contact", A, "Fixture poll 1:1", "CLOSED", "SINGLE_CHOICE", "RESULT_ON_CLOSE", "TEXT",
                T0 + 10, T0 + 20))
    db.execute("INSERT INTO ballots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (102, b2_api, A, "group", G1, "Fixture poll group", "OPEN", "MULTIPLE_CHOICE", "INTERMEDIATE", "TEXT",
                T0 + 30, T0 + 40))
    for (bref, cid, name, pos) in [(101, 1, "Yes", 0), (101, 2, "No", 1), (102, 10, "Mon", 0), (102, 11, "Tue", 1),
                                   (102, 12, "Wed", 2)]:
        db.execute("INSERT INTO ballot_choices VALUES (?,?,?,?,?,?,?)", (bref, cid, name, pos, None, T0, T0))
    votes = [(101, 1, OWN, 1), (101, 2, OWN, 0), (101, 1, A, 0), (101, 2, A, 1),
             (102, 10, A, 1), (102, 11, A, 1), (102, 12, A, 0), (102, 10, OWN, 0), (102, 11, OWN, 1), (102, 12, OWN, 0)]
    for v in votes:
        db.execute("INSERT INTO ballot_votes VALUES (?,?,?,?,?,?)", (*v, T0, T0))

    # ---------------- media ----------------
    img_in = jpeg_bytes(640, 480, (30, 60, 90))
    img_big = jpeg_bytes(700, 700, (0, 0, 0), noise=True)       # > 100 KB -> external storage
    sticker = png_rgba_bytes(128, 128)
    gif = gif_bytes(64, 48)
    pdf = b"%PDF-1.4\n% fixture\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    thumb_android = jpeg_bytes(160, 120, (90, 60, 30))
    tmp_audio = os.path.join(WORK, "media", "_tmp_audio")
    tmp_video = os.path.join(WORK, "media", "_tmp_video")
    have_audio = ffmpeg_media("audio", tmp_audio)
    have_video = ffmpeg_media("video", tmp_video)

    def sha(b):
        return hashlib.sha256(b).hexdigest()

    exp_file_with_data = 0
    exp_generated_thumbs = 0

    # ---------------- 1:1 chat with A ----------------
    t_first = fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="text", text="Hello from A")
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="text", text="Reply with quote",
           quoted_api_id=t_first["api_id"], state="READ", delivered_ms=ts(0) + 1000, read_ms=ts(0) + 2000)
    edited = fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="text", text="edited text",
                    state="DELIVERED", edited_ms=ts(0) + 500)
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="text", text=None, deleted_ms=ts(0) + 100)
    starred = fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="text", text="starred", starred=1)
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="text", text="failed on android",
           state="SENDFAILED")
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="text", text="pending on android",
           state="PENDING")
    # duplicate msg id within input (second copy must be skipped)
    dup_api = rbytes(8)
    fx.add(chat_kind="contact", chat_key=A, api_id=dup_api, is_own=0, kind="text", text="dup original")
    fx.add(chat_kind="contact", chat_key=A, api_id=dup_api, is_own=0, kind="text", text="dup copy")
    fx.expected["skip"]["duplicate_in_input"] = 1

    # image incoming with media + android thumbnail
    u = new_uid()
    fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="file", file_mime="image/jpeg",
           file_name="img.jpg", file_size=len(img_in) + 7, file_render=1, file_caption="caption in",
           file_blob_id=rbytes(16), file_key=rbytes(32), file_thumb_mime="image/jpeg",
           file_meta_json=json.dumps({"w": 640.0, "h": 480}), media_path=fx.put_media(u, img_in),
           media_sha256=sha(img_in), thumb_path=fx.put_media(u, thumb_android, "thumbs"))
    exp_file_with_data += 1
    # big image outgoing, no blob id/key (android local-only outgoing), no thumbnail -> generated
    u = new_uid()
    fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="file", file_mime="image/jpeg",
           file_name=None, file_size=len(img_big), file_render=1, state="READ", read_ms=ts(0) + 3000,
           file_meta_json=json.dumps({"w": 700, "h": 700}), media_path=fx.put_media(u, img_big), media_sha256=sha(img_big))
    exp_file_with_data += 1
    exp_generated_thumbs += 1
    # voice message incoming (consumed)
    if have_audio:
        u = new_uid()
        data = open(tmp_audio, "rb").read()
        fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="file", file_mime="audio/aac",
               file_name="voice.aac", file_size=len(data), file_render=1, state="CONSUMED", modified_ms=ts(0) + 100,
               file_blob_id=rbytes(16), file_key=rbytes(32), file_meta_json=json.dumps({"d": 2.5}),
               media_path=fx.put_media(u, data), media_sha256=sha(data))
        exp_file_with_data += 1
        # voice outgoing without duration in meta -> computed via AVFoundation
        u = new_uid()
        fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="file", file_mime="audio/aac",
               file_name=None, file_size=len(data), file_render=1, state="DELIVERED", file_meta_json="{}",
               media_path=fx.put_media(u, data), media_sha256=sha(data))
        exp_file_with_data += 1
    # video incoming without thumbnail -> generated from video
    if have_video:
        u = new_uid()
        data = open(tmp_video, "rb").read()
        fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="file", file_mime="video/mp4",
               file_name="clip.mp4", file_size=len(data), file_render=1, file_blob_id=rbytes(16), file_key=rbytes(32),
               file_meta_json=json.dumps({"d": 3}), media_path=fx.put_media(u, data), media_sha256=sha(data))
        exp_file_with_data += 1
        exp_generated_thumbs += 1
    # sticker (png, render 2)
    u = new_uid()
    fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="file", file_mime="image/png",
           file_size=len(sticker), file_render=2, file_blob_id=rbytes(16), file_key=rbytes(32),
           file_meta_json=json.dumps({"w": 128, "h": 128}), media_path=fx.put_media(u, sticker), media_sha256=sha(sticker))
    exp_file_with_data += 1
    exp_generated_thumbs += 1
    # gif (render 1, animated)
    u = new_uid()
    fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="file", file_mime="image/gif",
           file_size=len(gif), file_render=1, state="READ", file_meta_json=json.dumps({"a": True, "w": 64, "h": 48}),
           media_path=fx.put_media(u, gif), media_sha256=sha(gif))
    exp_file_with_data += 1
    exp_generated_thumbs += 1
    # pdf file (render 0), no thumbnail expected
    u = new_uid()
    fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="file", file_mime="application/pdf",
           file_name="doc.pdf", file_size=len(pdf), file_render=0, file_caption="a pdf", file_blob_id=rbytes(16),
           file_key=rbytes(32), file_meta_json="{}", media_path=fx.put_media(u, pdf), media_sha256=sha(pdf))
    exp_file_with_data += 1
    # media hash mismatch -> placeholder (no data)
    u = new_uid()
    fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="file", file_mime="image/jpeg",
           file_size=len(img_in), file_render=1, file_blob_id=rbytes(16), file_key=rbytes(32), file_meta_json="{}",
           media_path=fx.put_media(u, img_in), media_sha256="00" * 32)
    # missing media incoming (newer than media backup) with android thumbnail only
    u = new_uid()
    fx.add(uid=u, chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="file", file_mime="image/jpeg",
           file_name="missing.jpg", file_size=12345, file_render=1, file_blob_id=rbytes(16), file_key=rbytes(32),
           file_meta_json=json.dumps({"w": 10, "h": 10}), thumb_path=fx.put_media(u, thumb_android, "thumbs"))
    # missing media outgoing, nothing at all
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="file", file_mime="video/mp4",
           file_name="gone.mp4", file_size=999999, file_render=1, state="READ", file_meta_json=json.dumps({"d": 12.5}))
    # media_path given but file absent on disk -> placeholder
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="file", file_mime="application/pdf",
           file_size=10, file_render=0, file_blob_id=rbytes(16), file_key=rbytes(32), media_path="media/does-not-exist")
    # deleted file message
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="file", deleted_ms=ts(0) + 10, state="READ")
    # locations
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="location", loc_lat=47.3769, loc_lon=8.5417,
           loc_acc=12.0, loc_name="Somewhere", loc_address="Street 1\n8000 City")
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="location", loc_lat=47.5, loc_lon=9.3,
           state="READ")
    # ballot messages in 1:1 (created + closed)
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="ballot", ballot_ref=101, ballot_data_type=1,
           state="READ")
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="ballot", ballot_ref=101, ballot_data_type=3,
           state="READ")
    fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="ballot", ballot_ref=999, ballot_data_type=1)
    fx.expected["skip"]["ballot_missing"] = 1
    # calls (api_id NULL -> msg_id from uid)
    calls = [(1, 2, None, 75), (0, 1, None, None), (1, 3, 1, None), (0, 3, 2, None), (1, 4, None, None),
             (0, 3, 0, None), (1, 3, 4, None), (1, 3, 5, None), (0, 2, None, 3700), (1, 2, None, 0)]
    for own, st, reason, dur in calls:
        fx.add(chat_kind="contact", chat_key=A, api_id=None, is_own=own, kind="call", call_status=st, call_reason=reason,
               call_duration_s=dur, call_id=RNG.getrandbits(31), state="READ" if own else None)
    # legacy USERACK target
    ack_target = fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=1, kind="text", text="ack me",
                        state="READ", read_ms=ts(0) + 500)
    last_a = fx.add(chat_kind="contact", chat_key=A, api_id=rbytes(8), is_own=0, kind="text", text="newest in A")

    # ---------------- 1:1 chat with B (archived) ----------------
    b_msg = fx.add(chat_kind="contact", chat_key=B, api_id=rbytes(8), is_own=0, kind="text", text="hi from B")
    fx.add(chat_kind="contact", chat_key=B, api_id=rbytes(8), is_own=1, kind="text", text="hi B", state="SENT")
    # gateway chat
    fx.add(chat_kind="contact", chat_key=GW, api_id=rbytes(8), is_own=0, kind="text", text="gateway msg")
    # chat with unknown contact -> skipped
    fx.add(chat_kind="contact", chat_key=UNKNOWN_CHAT, api_id=rbytes(8), is_own=0, kind="text", text="nobody")
    fx.expected["skip"]["contact_missing"] = 1
    # unknown kind -> skipped
    fx.add(chat_kind="contact", chat_key=B, api_id=rbytes(8), is_own=0, kind="teleport", raw_type="FOO", raw_body="x")
    fx.expected["skip"]["unsupported_kind"] = 1
    # call in group -> skipped
    # ---------------- group G1 (creator A) ----------------
    g_first = fx.add(chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=0, sender=A, kind="text", text="group hello")
    fx.add(chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=0, sender=B, kind="text", text="from B")
    fx.add(chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=0, sender=C, kind="text", text="from hidden C")
    g_own = fx.add(chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=1, kind="text", text="mine in group",
                   state="SENT", quoted_api_id=g_first["api_id"])
    fx.add(chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=0, sender=UNKNOWN_SENDER, kind="text", text="ghost")
    fx.expected["skip"]["sender_unknown"] = 1
    u = new_uid()
    fx.add(uid=u, chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=0, sender=B, kind="file",
           file_mime="image/jpeg", file_size=len(img_in), file_render=1, file_blob_id=rbytes(16), file_key=rbytes(32),
           file_meta_json=json.dumps({"w": 640, "h": 480}), media_path=fx.put_media(u, img_in), media_sha256=sha(img_in),
           thumb_path=fx.put_media(u, thumb_android, "thumbs"))
    exp_file_with_data += 1
    fx.add(chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=0, sender=A, kind="location", loc_lat=1.5, loc_lon=2.5)
    fx.add(chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=0, sender=A, kind="ballot", ballot_ref=102,
           ballot_data_type=1)
    fx.add(chat_kind="group", chat_key=G1, api_id=None, is_own=0, sender=A, kind="call", call_status=2)
    fx.expected["skip"]["call_in_group"] = 1
    gstatus = [(0, None, None), (1, None, "Renamed Group"), (2, None, None), (3, B, None), (3, OWN, None), (4, C, None),
               (4, OWN, None), (5, B, None), (5, OWN, None), (6, None, None), (7, None, None), (8, A, "Fixture poll group"),
               (9, A, "Fixture poll group"), (10, None, "Fixture poll group"), (11, None, "Fixture poll group"),
               (12, None, None), (13, None, None), (4, A, None)]
    for (t, ident, name) in gstatus:
        fx.add(chat_kind="group", chat_key=G1, api_id=None, is_own=0, kind="group_status", gstatus_type=t,
               gstatus_identity=ident, gstatus_name=name)
    fx.expected["skip"]["group_status_no_ios_equivalent"] = 2   # 11 VOTES_COMPLETE, 12 DESCRIPTION_CHANGED
    fx.add(chat_kind="group", chat_key=G1, api_id=None, is_own=0, kind="legacy_status", text="Legacy status line")
    fx.expected["skip"]["legacy_status_no_ios_equivalent"] = 1
    g_last = fx.add(chat_kind="group", chat_key=G1, api_id=rbytes(8), is_own=0, sender=A, kind="text", text="group newest")
    # ---------------- group G2 (own, archived) ----------------
    fx.add(chat_kind="group", chat_key=G2, api_id=rbytes(8), is_own=1, kind="text", text="own group msg", state="SENT")
    fx.add(chat_kind="group", chat_key=G2, api_id=rbytes(8), is_own=0, sender=B, kind="text", text="B in own group")
    # message for group not in groups table
    fx.add(chat_kind="group", chat_key="00112233aabbccdd-ZZTSTXX7", api_id=rbytes(8), is_own=0, sender=A, kind="text",
           text="no such group")
    fx.expected["skip"]["group_missing"] = 1

    # ---------------- reactions ----------------
    R = fx.reactions
    R.append(("contact", A, t_first["msg_id"], None, "❤️", ts(0), "csv"))          # own
    R.append(("contact", A, t_first["msg_id"], A, "\U0001F602", ts(0), "csv"))              # partner
    R.append(("contact", A, t_first["msg_id"], A, "\U0001F602", ts(0), "csv"))              # duplicate
    R.append(("contact", A, ack_target["msg_id"], A, "\U0001F44D", ts(0), "legacy_ack"))    # legacy ack by partner
    R.append(("contact", A, starred["msg_id"], None, "\U0001F44E", ts(0), "legacy_ack"))     # legacy dec by me
    R.append(("contact", A, rbytes(8), A, "\U0001F44D", ts(0), "csv"))                      # target missing
    R.append(("contact", A, t_first["msg_id"], B, "\U0001F44D", ts(0), "csv"))              # reactor not in chat
    R.append(("group", G1, g_first["msg_id"], B, "\U0001F44D", ts(0), "legacy_ack"))
    R.append(("group", G1, g_first["msg_id"], None, "\U0001F389", ts(0), "csv"))
    R.append(("group", G1, g_own["msg_id"], UNKNOWN_SENDER, "\U0001F44D", ts(0), "csv"))   # reactor unknown
    R.append(("contact", B, b_msg["msg_id"], None, "\U0001F44D", ts(0), "csv"))
    fx.expected["reactions_inserted"] = 7
    fx.expected["skip_reactions"] = {"duplicate": 1, "target_missing": 1, "reactor_not_in_chat": 1, "reactor_unknown": 1}

    # ---------------- nonces ----------------
    for i in range(20):
        db.execute("INSERT INTO nonces VALUES (?,?)", ("csp" if i % 2 else "d2d", hashlib.sha256(b"n%d" % i).digest()))
    db.execute("INSERT INTO nonces VALUES (?,?)", ("csp", hashlib.sha256(b"n1").digest()))   # duplicate
    fx.expected["nonces_inserted"] = 20

    # write messages
    for m in fx.msgs:
        db.execute("INSERT INTO messages (%s) VALUES (%s)" % (",".join(MSG_COLS), ",".join("?" * len(MSG_COLS))),
                   [m[c] for c in MSG_COLS])
    for r in fx.reactions:
        db.execute("INSERT INTO reactions VALUES (?,?,?,?,?,?,?)", r)
    meta = {"own_identity": OWN, "format_version": "1", "text_backup": "fixture", "media_backup": "fixture",
            "generated_at": "fixture"}
    for k, v in meta.items():
        db.execute("INSERT INTO meta VALUES (?,?)", (k, v))
    db.commit()
    db.close()
    for p in (tmp_audio, tmp_video):
        if os.path.exists(p):
            os.remove(p)

    total = len(fx.msgs)
    skipped = sum(fx.expected["skip"].values())
    fx.expected.update({
        "own_identity": OWN,
        "messages_total": total,
        "messages_inserted": total - skipped,
        "file_with_data": exp_file_with_data,
        "have_audio": have_audio,
        "have_video": have_video,
        "contacts_inserted": 5,
        "groups_inserted": 3,
        "conversations_inserted": 3 + 3,   # 1:1 A, B, GW + groups G1, G2, G3
        "ballots_inserted": 2,
        "ballot_choices_inserted": 5,
        "ballot_results_inserted": 10,
        "newest_uid": {"A": last_a["uid"], "G1": g_last["uid"]},
        "newest_msg_id_hex": {"A": last_a["msg_id"].hex(), "G1": g_last["msg_id"].hex()},
        "edited_msg_id_hex": edited["msg_id"].hex(),
    })
    with open(os.path.join(WORK, "expected.json"), "w") as f:
        json.dump(fx.expected, f, indent=1, sort_keys=True)
    print(json.dumps({"workdir": WORK, "messages": total, "expected_inserted": total - skipped,
                      "audio": have_audio, "video": have_video}))


if __name__ == "__main__":
    main()
