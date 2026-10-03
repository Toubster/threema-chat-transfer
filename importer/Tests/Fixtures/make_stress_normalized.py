#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SYNTHETIC scale fixture (real-data-sized) to prove bounded memory / runtime of the importer.

    .venv/bin/python tools/importer/tests/make_stress_normalized.py work/stress [n_text=80000] [n_files=2000] [file_mb=1.5]

Media files are hard links of a few random blobs (disk-cheap on input; the importer still writes every one into
_EXTERNAL_DATA, which is what is measured).
"""
import hashlib
import json
import os
import random
import shutil
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from make_mini_normalized import SCHEMA, MSG_COLS  # noqa: E402

WORK = os.path.abspath(sys.argv[1])
N_TEXT = int(sys.argv[2]) if len(sys.argv) > 2 else 80000
N_FILES = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
FILE_MB = float(sys.argv[4]) if len(sys.argv) > 4 else 1.5
rng = random.Random(1)
OWN = "ZZSTRS00"

shutil.rmtree(WORK, ignore_errors=True)
for s in ("media", "thumbs", "avatars", "_blobs"):
    os.makedirs(os.path.join(WORK, s))
db = sqlite3.connect(os.path.join(WORK, "normalized.sqlite"))
db.executescript(SCHEMA)
contacts = ["STR%05d" % i for i in range(40)]
for c in contacts:
    db.execute("INSERT INTO contacts (identity, public_key, verification, hidden, archived) VALUES (?,?,?,?,?)",
               (c, os.urandom(32), 1, 0, 0))
groups = []
for g in range(4):
    gid = os.urandom(8)
    creator = contacts[g]
    key = f"{gid.hex()}-{creator}"
    groups.append(key)
    db.execute("INSERT INTO groups (group_key, group_id, creator, is_mine, name, created_ms, archived, user_state, members_json) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (key, gid, creator, 0, f"G{g}", 1600000000000, 0, 0, json.dumps(contacts[:10])))
blobs = []
for i in range(8):
    p = os.path.join(WORK, "_blobs", str(i))
    data = os.urandom(int(FILE_MB * 1024 * 1024))
    with open(p, "wb") as f:
        f.write(data)
    blobs.append((p, hashlib.sha256(data).hexdigest(), len(data)))
t = 1600000000000
rows = []
target_ids = []
for i in range(N_TEXT + N_FILES):
    t += rng.randint(1000, 60000)
    uid = str(uuid.uuid4())
    in_group = rng.random() < 0.1
    chat_kind = "group" if in_group else "contact"
    chat_key = rng.choice(groups) if in_group else rng.choice(contacts)
    own = rng.random() < 0.45
    api = os.urandom(8)
    row = {c: None for c in MSG_COLS}
    row.update(uid=uid, chat_kind=chat_kind, chat_key=chat_key, api_id=api, msg_id=api, is_own=int(own),
               sender=None if own or not in_group else rng.choice(contacts[:10]), created_ms=t, posted_ms=t - 500,
               state="READ" if own else None, is_read=1, starred=0)
    if i < N_FILES:
        bp, bh, bl = blobs[i % len(blobs)]
        rel = os.path.join("media", uid)
        os.link(bp, os.path.join(WORK, rel))
        row.update(kind="file", file_mime="application/octet-stream", file_name=f"f{i}.bin", file_size=bl, file_render=0,
                   file_blob_id=os.urandom(16), file_key=os.urandom(32), file_meta_json="{}", media_path=rel, media_sha256=bh)
    else:
        row.update(kind="text", text="synthetic text %d " % i + "x" * rng.randint(0, 200))
    rows.append([row[c] for c in MSG_COLS])
    if rng.random() < 0.04:
        target_ids.append((chat_kind, chat_key, api, row["sender"] if in_group else (None if rng.random() < .5 else chat_key)))
db.executemany("INSERT INTO messages (%s) VALUES (%s)" % (",".join(MSG_COLS), ",".join("?" * len(MSG_COLS))), rows)
db.executemany("INSERT INTO reactions VALUES (?,?,?,?,?,?,?)",
               [(k, ck, tid, s, "\U0001F44D", 1600000000000, "csv") for (k, ck, tid, s) in target_ids])
db.executemany("INSERT INTO nonces VALUES (?,?)", [("csp", os.urandom(32)) for _ in range(170000)])
for k, v in {"own_identity": OWN, "format_version": "1"}.items():
    db.execute("INSERT INTO meta VALUES (?,?)", (k, v))
db.commit()
print(json.dumps({"messages": len(rows), "files": N_FILES, "media_bytes": sum(blobs[i % len(blobs)][2] for i in range(N_FILES)),
                  "reactions": len(target_ids)}))
