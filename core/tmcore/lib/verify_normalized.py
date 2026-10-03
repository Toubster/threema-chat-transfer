#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
verify_normalized.py - independent checks of <work-dir>/normalized.sqlite against the NORMALIZED CONTRACT.

  In-process (tmcore android-normalize):  res = verify(work_dir, rehash=False)  -> {"result", "checks", "fails"}
  Maintainer CLI:  python3 -m tmcore.lib.verify_normalized --work-dir DIR [--rehash]

Checks: exact table/column set, blob lengths, enum domains, referential integrity (messages -> chats,
reactions/quotes -> messages in the same chat, ballots), media files exist with the recorded size, optional full
sha256 re-hash, optional re-read of N random media entries straight from the zip (in-process password only).
Prints counts only (no texts/names/identities). The proof of concept's comparison with the maintainer's real
counts (--expect-real) is gone: those counts stay private (DESIGN §10.3).
"""
import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys

try:
    from . import android_normalize as an
except ImportError:  # pragma: no cover -- run as a plain script
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import android_normalize as an  # type: ignore[no-redef]  # noqa: E402

CONTRACT = {
    "meta": ["key", "value"],
    "contacts": ["identity", "public_key", "verification", "first_name", "last_name", "nickname", "hidden",
                 "archived", "last_update_ms", "avatar_user_path", "avatar_contact_path"],
    "groups": ["group_key", "group_id", "creator", "is_mine", "name", "created_ms", "last_update_ms", "archived",
               "user_state", "members_json", "avatar_path", "description", "description_ms"],
    "messages": an.MESSAGE_COLUMNS,
    "reactions": ["chat_kind", "chat_key", "target_msg_id", "sender", "emoji", "reacted_ms", "source"],
    "ballots": ["ref_id", "api_id", "creator", "chat_kind", "chat_key", "title", "state", "assessment", "btype",
                "choice_type", "created_ms", "modified_ms"],
    "ballot_choices": ["ballot_ref", "choice_id", "name", "order_pos", "vote_count", "created_ms", "modified_ms"],
    "ballot_votes": ["ballot_ref", "choice_id", "identity", "choice", "created_ms", "modified_ms"],
    "nonces": ["kind", "hash"],
}

def verify(work_dir, *, rehash=False, zip_sample=0, media_backup=None, password=None, out=None):
    """Run every check. out: callable for the OK/FAIL lines (None = silent). Returns
    {"result": "PASS"|"FAIL", "checks": n, "fails": [check names]} -- names only, never values."""
    wd = os.path.abspath(work_dir)
    db = sqlite3.connect(f"file:{os.path.join(wd, 'normalized.sqlite')}?mode=ro", uri=True)
    fails = []
    n_checks = [0]
    say = out or (lambda _line: None)

    def check(cond, what):
        n_checks[0] += 1
        say(("OK   " if cond else "FAIL ") + what)
        if not cond:
            fails.append(what)

    q = lambda sql, *p: db.execute(sql, p).fetchone()[0]  # noqa: E731
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check(tables == set(CONTRACT), f"table set == contract ({len(tables)} tables)")
    for t, cols in CONTRACT.items():
        got = [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')]
        check(got == cols, f"columns of {t} == contract ({len(got)})")
    meta = dict(db.execute("SELECT key, value FROM meta"))
    check(all(k in meta for k in ("own_identity", "format_version", "text_backup", "media_backup", "generated_at")),
          "meta has required keys")
    own = meta["own_identity"]
    check(q("SELECT count(*) FROM contacts WHERE identity=?", own) == 0, "own identity not in contacts")
    check(q("SELECT count(*) FROM contacts WHERE length(public_key)!=32") == 0, "contacts.public_key 32 bytes")
    check(q("SELECT count(*) FROM contacts WHERE verification NOT IN (0,1,2)") == 0, "verification in 0/1/2")
    check(q('SELECT count(*) FROM "groups" WHERE length(group_id)!=8') == 0, "groups.group_id 8 bytes")
    check(q('SELECT count(*) FROM "groups" WHERE group_key != lower(hex(group_id)) || \'-\' || creator') == 0,
          "group_key == <idhex>-<creator>")
    bad_members = 0
    for (mj, creator) in db.execute('SELECT members_json, creator FROM "groups"'):
        mem = json.loads(mj)
        if own in mem or (creator != own and creator not in mem):
            bad_members += 1
    check(bad_members == 0, "members_json excludes own, includes creator if not me")
    check(q("SELECT count(*) FROM messages WHERE length(msg_id)!=8") == 0, "msg_id 8 bytes")
    check(q("SELECT count(*) FROM messages WHERE api_id IS NOT NULL AND (length(api_id)!=8 OR api_id!=msg_id)") == 0,
          "api_id 8 bytes and == msg_id when present")
    check(q("SELECT count(*) FROM messages WHERE file_blob_id IS NOT NULL AND length(file_blob_id)!=16") == 0,
          "file_blob_id 16 bytes")
    check(q("SELECT count(*) FROM messages WHERE file_key IS NOT NULL AND length(file_key)!=32") == 0, "file_key 32 bytes")
    check(q("SELECT count(*) FROM messages WHERE kind NOT IN "
            "('text','file','location','ballot','call','group_status','legacy_status')") == 0, "kind domain")
    check(q("SELECT count(*) FROM messages WHERE chat_kind NOT IN ('contact','group')") == 0, "chat_kind domain")
    check(q("SELECT count(*) FROM messages m WHERE chat_kind='contact' AND NOT EXISTS "
            "(SELECT 1 FROM contacts c WHERE c.identity=m.chat_key)") == 0, "contact messages -> contacts")
    check(q("SELECT count(*) FROM messages m WHERE chat_kind='group' AND NOT EXISTS "
            "(SELECT 1 FROM \"groups\" g WHERE g.group_key=m.chat_key)") == 0, "group messages -> groups")
    check(q("SELECT count(*) FROM messages WHERE is_own=1 AND sender IS NOT NULL") == 0, "own messages have sender NULL")
    check(q("SELECT count(*) FROM messages WHERE created_ms IS NULL") == 0, "created_ms set")
    check(q("SELECT count(*) FROM messages WHERE created_ms < 1000000000000 OR created_ms > 4102444800000") == 0,
          "created_ms looks like epoch ms")
    check(q("SELECT count(*) FROM messages WHERE kind='location' AND deleted_ms IS NULL AND (loc_lat IS NULL OR loc_lon IS NULL)") == 0,
          "non-deleted locations have lat/lon")
    check(q("SELECT count(*) FROM messages WHERE kind='file' AND deleted_ms IS NULL AND file_mime IS NULL") == 0,
          "non-deleted files have a mime type")
    check(q("SELECT count(*) FROM messages WHERE (kind='text' OR kind='legacy_status') AND deleted_ms IS NULL AND text IS NULL") == 0,
          "non-deleted text rows have text")
    check(q("SELECT count(*) FROM (SELECT chat_kind, chat_key, msg_id FROM messages GROUP BY 1,2,3 HAVING count(*)>1)") == 0,
          "msg_id unique per chat")
    nq = q("SELECT count(*) FROM messages WHERE quoted_api_id IS NOT NULL")
    nqr = q("SELECT count(*) FROM messages m WHERE quoted_api_id IS NOT NULL AND EXISTS (SELECT 1 FROM messages t "
            "WHERE t.chat_kind=m.chat_kind AND t.chat_key=m.chat_key AND t.msg_id=m.quoted_api_id AND t.api_id IS NOT NULL)")
    say(f"     quotes {nq}, resolved in same chat {nqr}")
    nr = q("SELECT count(*) FROM reactions")
    nrr = q("SELECT count(*) FROM reactions r WHERE EXISTS (SELECT 1 FROM messages t WHERE t.chat_kind=r.chat_kind "
            "AND t.chat_key=r.chat_key AND t.msg_id=r.target_msg_id)")
    say(f"     reactions {nr}, resolved {nrr}")
    check(q("SELECT count(*) FROM (SELECT 1 FROM reactions GROUP BY chat_kind, chat_key, target_msg_id, "
            "ifnull(sender,''), emoji HAVING count(*)>1)") == 0, "reactions unique (chat,target,sender,emoji)")
    check(q("SELECT count(*) FROM reactions WHERE sender=?", own) == 0, "own reactions use sender NULL")
    check(q("SELECT count(*) FROM messages WHERE kind='ballot' AND ballot_ref NOT IN (SELECT ref_id FROM ballots)") == 0,
          "ballot messages -> ballots")
    check(q("SELECT count(*) FROM nonces WHERE length(hash)!=32 OR kind NOT IN ('csp','d2d')") == 0, "nonces 32 bytes")

    # media files
    missing = size_bad = 0
    total_bytes = 0
    rows = db.execute("SELECT uid, media_path, media_sha256, file_size, thumb_path FROM messages "
                      "WHERE media_path IS NOT NULL OR thumb_path IS NOT NULL").fetchall()
    for uid, mp, sha, size, tp in rows:
        for p, sz in ((mp, size), (tp, None)):
            if p is None:
                continue
            full = os.path.join(wd, p)
            if not os.path.isfile(full):
                missing += 1
                continue
            if sz is not None and os.path.getsize(full) != sz:
                size_bad += 1
            total_bytes += os.path.getsize(full)
    check(missing == 0, f"all referenced media/thumb files exist ({len(rows)} rows, {total_bytes / 1e9:.2f} GB)")
    check(size_bad == 0, "media file size == file_size")
    check(q("SELECT count(*) FROM messages WHERE media_path IS NOT NULL AND (media_sha256 IS NULL OR length(media_sha256)!=64)") == 0,
          "media_sha256 present")
    for col in ("avatar_user_path", "avatar_contact_path"):
        for (p,) in db.execute(f"SELECT {col} FROM contacts WHERE {col} IS NOT NULL"):
            check(os.path.isfile(os.path.join(wd, p)), f"contacts.{col} exists")
    for (p,) in db.execute('SELECT avatar_path FROM "groups" WHERE avatar_path IS NOT NULL'):
        check(os.path.isfile(os.path.join(wd, p)), "groups.avatar_path exists")
    part = [f for d in ("media", "thumbs", "avatars") if os.path.isdir(os.path.join(wd, d))
            for f in os.listdir(os.path.join(wd, d)) if f.endswith(".part")]
    check(not part, "no leftover .part files")
    magic_bad = 0
    for (tp,) in db.execute("SELECT thumb_path FROM messages WHERE thumb_path IS NOT NULL"):
        with open(os.path.join(wd, tp), "rb") as f:
            h = f.read(8)
        if not (h[:3] == b"\xff\xd8\xff" or h[:4] == b"\x89PNG"):
            magic_bad += 1
    say(f"     thumbnails that are not JPEG/PNG: {magic_bad}")

    if rehash:
        bad = 0
        n = 0
        for uid, mp, sha in db.execute("SELECT uid, media_path, media_sha256 FROM messages WHERE media_path IS NOT NULL"):
            n += 1
            if an.sha256_file(os.path.join(wd, mp)) != sha:
                bad += 1
        check(bad == 0, f"full sha256 re-hash matches ({n} media files)")

    if zip_sample:
        z, _ = an.open_zip(media_backup, an.password_candidates(password))
        names = set(z.namelist())
        cand = db.execute("SELECT uid, chat_kind, media_path, media_sha256 FROM messages WHERE media_path IS NOT NULL").fetchall()
        random.seed(42)
        bad = 0
        sample = random.sample(cand, min(zip_sample, len(cand)))
        for uid, ck, mp, sha in sample:
            name = an.MEDIA_PREFIX[ck][0] + uid
            if name not in names:
                bad += 1
                continue
            h = hashlib.sha256()
            with z.open(name) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            bad += h.hexdigest() != sha
        check(bad == 0, f"{len(sample)} random media entries re-read from zip match sha256")

        z.close()
    db.close()
    say("RESULT: " + ("PASS" if not fails else f"FAIL ({len(fails)})"))
    return {"result": "PASS" if not fails else "FAIL", "checks": n_checks[0], "fails": fails}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--rehash", action="store_true")
    a = ap.parse_args(argv)
    res = verify(a.work_dir, rehash=a.rehash, out=print)
    return 0 if res["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
