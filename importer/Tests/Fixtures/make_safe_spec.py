#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""make_safe_spec.py - spec for safe_seed.swift: what a real Threema Safe restore on iOS would create from the same
Android data (review appsafety M2 / fidelity F3).

  .venv/bin/python tools/importer/tests/make_safe_spec.py --normalized N --out spec.json
        [--private-contact ID] [--post-restore]

Android Safe export (ref/threema-android ThreemaSafeServiceImpl.java):
  * contacts: every contact except removed ones and me (getContacts, :1294-1307), with hidden = acquaintance level
    GROUP_OR_DELETED, lastUpdate (when the contact has a conversation), private flag. NO pictures.
  * groups: all groups incl. left ones (getGroups includeLeftGroups=true, :1362-1390); members include me only when
    I am still a member (getGroup, :1330-1338), lastUpdate, private.
--post-restore adds 3 synthetic messages that "arrived on the iPhone after the Safe restore": an unread incoming
text in the largest 1:1 chat (newer than everything Android has), an incoming text that re-uses the msg_id of an
Android text in the same chat (dedupe: existing wins), and an own text in the largest group.
Writes identities/keys into the spec file only; prints counts.
"""
import argparse
import json
import sqlite3
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--normalized", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--private-contact", default=None)
    ap.add_argument("--post-restore", action="store_true")
    a = ap.parse_args()
    db = sqlite3.connect(f"file:{a.normalized}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    own = db.execute("SELECT value FROM meta WHERE key='own_identity'").fetchone()[0]
    contacts = []
    for c in db.execute("SELECT * FROM contacts ORDER BY identity"):
        if c["identity"] == own or c["public_key"] is None or len(c["public_key"]) != 32:
            continue
        contacts.append({"identity": c["identity"], "publicKey": bytes(c["public_key"]).hex(),
                         "verification": max(0, min(2, c["verification"] or 0)),
                         "firstName": c["first_name"] or None, "lastName": c["last_name"] or None,
                         "nickname": c["nickname"] or None, "hidden": bool(c["hidden"]),
                         "createdAtMs": None, "lastUpdateMs": c["last_update_ms"],
                         "private": c["identity"] == a.private_contact, "featureMask": 255, "workVerified": False})
    groups = []
    for g in db.execute('SELECT * FROM "groups" ORDER BY group_key'):
        members = json.loads(g["members_json"] or "[]")
        if (g["user_state"] or 0) == 0:
            members = [own] + members
        groups.append({"groupId": bytes(g["group_id"]).hex(), "creator": g["creator"], "name": g["name"],
                       "lastUpdateMs": g["last_update_ms"], "private": False, "members": members})
    post = []
    if a.post_restore:
        now_ms = int(time.time() * 1000)
        big = db.execute("SELECT chat_key, count(*) n FROM messages WHERE chat_kind='contact' GROUP BY chat_key "
                         "ORDER BY n DESC LIMIT 1").fetchone()
        if big:
            post.append({"chat": f"contact:{big['chat_key']}", "id": "fe" * 7 + "01", "text": "post-restore test (unread)",
                         "dateMs": now_ms, "isOwn": False, "read": False})
            dup = db.execute("SELECT msg_id, created_ms FROM messages WHERE chat_kind='contact' AND chat_key=? AND "
                             "kind='text' AND is_own=0 ORDER BY created_ms DESC LIMIT 1", (big["chat_key"],)).fetchone()
            if dup:
                post.append({"chat": f"contact:{big['chat_key']}", "id": bytes(dup["msg_id"]).hex(),
                             "text": "post-restore duplicate id", "dateMs": dup["created_ms"], "isOwn": False, "read": True})
        bg = db.execute("SELECT chat_key, count(*) n FROM messages WHERE chat_kind='group' GROUP BY chat_key "
                        "ORDER BY n DESC LIMIT 1").fetchone()
        if bg:
            gid, creator = bg["chat_key"].split("-", 1)
            post.append({"chat": f"group:{gid.lower()}:{creator}", "id": "fe" * 7 + "02", "text": "post-restore own group msg",
                         "dateMs": now_ms + 1000, "isOwn": True, "read": True})
    spec = {"own": own, "contacts": contacts, "groups": groups, "postRestore": post}
    with open(a.out, "w") as f:
        json.dump(spec, f)
    print(json.dumps({"contacts": len(contacts), "contacts_with_lastUpdate": sum(1 for c in contacts if c["lastUpdateMs"]),
                      "hidden": sum(1 for c in contacts if c["hidden"]), "groups": len(groups),
                      "groups_member": sum(1 for g in groups if own in g["members"]), "post_restore": len(post)}))


if __name__ == "__main__":
    main()
