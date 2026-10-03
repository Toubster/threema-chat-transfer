# SPDX-License-Identifier: AGPL-3.0-or-later
"""
pytest for tmcore.lib.android_normalize with small synthetic WinZip-AES (AE-2, AES-256) backups that follow the
Android backup format v27. No real data is used. Passwords are handed over in-process (no password files).
"""
import hashlib
import json
import os
import sqlite3

import pyzipper
import pytest

from tmcore.lib import android_normalize as an  # noqa: E402

PW = "fixture-pass-1234"
WRONG_PW = "wrong-" + "pass-1234"
OWN = "ZZOWN345"
ALICE, BOB, GW, CAROL, DAVE = "ZZALI001", "ZZBOB002", "*ZZGATAY", "ZZCAR003", "ZZDAV004"
T0 = 1_700_000_000_000
MEDIA_TS = 1_780_000_000_000
TEXT_TS = 1_790_000_000_000

CONTACT_HDR = ["apiid", "uid", "isoutbox", "isread", "issaved", "messagestae", "posted_at", "created_at",
               "modified_at", "type", "body", "isstatusmessage", "caption", "quoted_message_apiid", "delivered_at",
               "read_at", "g_msg_states", "display_tags", "edited_at", "deleted_at"]
GROUP_HDR = CONTACT_HDR[:2] + ["identity"] + CONTACT_HDR[2:]
DLIST_HDR = ["apiid", "uid", "identity", "isoutbox", "isread", "issaved", "messagestae", "posted_at", "created_at",
             "modified_at", "type", "body", "isstatusmessage", "caption", "quoted_message_apiid", "delivered_at",
             "read_at"]

TRICKY = 'back\\slash "quoted", comma\nnew line \\\\ double and \\" mix'


def acsv(header, rows):
    """Emulate BackupService: CSVRow.escape doubles backslashes, opencsv quotes every cell and doubles quotes,
    a never-filled (Java null) cell is written unquoted and empty."""
    def cell(v):
        if v is None:
            return ""
        s = str(v).replace("\\", "\\\\")
        return '"' + s.replace('"', '""') + '"'
    lines = [",".join('"%s"' % h for h in header)]
    for r in rows:
        lines.append(",".join(cell(v) for v in r))
    return ("\n".join(lines) + "\n").encode("utf-8")


def make_zip(path, entries, pw=PW):
    with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
        z.setpassword(pw.encode())
        z.setencryption(pyzipper.WZ_AES, nbits=256)
        for name, data in entries.items():
            z.writestr(name, data)


def uid(n):
    return f"00000000-0000-4000-8000-{n:012d}"


def api(n):
    return f"{n:016x}"


def crow(apiid, u, out, state, created, typ, body, **kw):
    d = {"apiid": apiid, "uid": u, "isoutbox": "1" if out else "0", "isread": "1", "issaved": "1",
         "messagestae": state, "posted_at": created - 1000, "created_at": created, "modified_at": created + 5,
         "type": typ, "body": body, "isstatusmessage": "0", "caption": "", "quoted_message_apiid": "",
         "delivered_at": "", "read_at": "", "g_msg_states": None, "display_tags": "0", "edited_at": "",
         "deleted_at": ""}
    d.update(kw)
    return d


def rows_for(header, dicts):
    return [[d.get(h, "") for h in header] for d in dicts]


KEY64 = "ab" * 32
BLOB32 = "cd" * 16
MEDIA = {  # uid -> bytes
    uid(6): b"\xff\xd8\xff" + os.urandom(3000),
    uid(7): b"AAC" + os.urandom(500),
    uid(12): b"\xff\xd8\xff" + b"legacy" * 50,
    uid(105): b"%PDF" + os.urandom(200_000),
}
THUMBS = {uid(6): b"\xff\xd8\xffthumb6", uid(106): b"\x89PNGthumb-only"}


def file_body(blob, key, mime, size, name, render, dl, caption, tmime, meta):
    return json.dumps([blob, key, mime, size, name, render, dl, caption, tmime, meta])


def build_text_backup(path, version="27"):
    contacts = acsv(
        ["identity", "publickey", "verification", "acid", "firstname", "lastname", "nick_name", "last_update",
         "hidden", "archived", "identity_id"],
        [[ALICE, "11" * 32, "FULLY_VERIFIED", "", "Alice", "A", "ali", str(T0 + 99_000), "0", "0", "0000000001"],
         [BOB, "22" * 32, "SERVER_VERIFIED", "", "", "", "bob", "", "0", "1", "0000000002"],
         [GW, "33" * 32, "UNVERIFIED", "", "Gateway", "", "", "", "0", "0", "0000000003"],
         [CAROL, "44" * 32, "UNVERIFIED", "", "", "", "", "", "1", "0", "0000000004"]])
    groups = acsv(
        ["id", "creator", "groupname", "created_at", "last_update", "members", "archived", "groupDesc",
         "groupDescTimestamp", "group_uid", "user_state"],
        [["0102030405060708", ALICE, "G1", str(T0), str(T0 + 50_000), f"{ALICE};{OWN};{BOB};{CAROL}", "0",
          "desc", str(T0 + 1), "1111111111", "0"],
         ["a1a2a3a4a5a6a7a8", OWN, "Mine", "-5", str(T0), f"{OWN};{BOB}", "0", "", "", "2222222222", "0"],
         ["b1b2b3b4b5b6b7b8", DAVE, "Orphan", str(T0), str(T0), f"{OWN};{BOB}", "0", "", "", "3333333333", "0"]])
    c = [
        crow(api(1), uid(1), False, "", T0 + 1000, "TEXT", TRICKY, read_at=str(T0 + 2000)),
        crow(api(2), uid(2), True, "USERACK", T0 + 3000, "TEXT", "acked", read_at=str(T0 + 3500),
             delivered_at=str(T0 + 3100)),
        crow(api(3), uid(3), False, "USERDEC", T0 + 4000, "TEXT", "declined"),
        crow(api(4), uid(4), True, "READ", T0 + 5000, "TEXT", "quoting", quoted_message_apiid=api(1),
             edited_at=str(T0 + 5500), display_tags="1"),
        crow(api(5), uid(5), True, "READ", T0 + 6000, "TEXT", "", deleted_at=str(T0 + 6500)),
        crow(api(6), uid(6), True, "READ", T0 + 7000, "FILE",
             file_body(None, None, "image/jpeg", 999, "img.jpg", 1, True, "cap6", "image/jpeg", {"w": 10, "h": 20}),
             caption="cap6"),
        crow(api(7), uid(7), False, "", T0 + 8000, "FILE",
             file_body("", "", "audio/aac", len(MEDIA[uid(7)]), "voice.aac", 1, True, None, None, {"d": 3.5})),
        crow(api(8), uid(8), False, "", 1_785_000_000_000, "FILE",
             file_body(BLOB32, KEY64, "image/png", 10, "late.png", 1, True, None, "image/jpeg", {})),
        crow(api(9), uid(9), False, "", T0 + 9000, "LOCATION", json.dumps([47.1, 8.5, 10.0, "Street 1", "Cafe"]),
             caption="*Cafe*\nStreet 1"),
        crow("", uid(10), True, "", T0 + 10000, "VOIP_STATUS",
             json.dumps([1, {"status": 2, "callId": 123, "duration": 65}])),
        crow(api(11), uid(11), True, "READ", T0 + 11000, "BALLOT", json.dumps([1, 7])),
        crow(api(12), uid(12), False, "", T0 + 12000, "IMAGE", json.dumps([True, KEY64, BLOB32, "ee" * 24])),
        crow(api(13), uid(13), False, "", T0 + 13000, "VIDEO", json.dumps([12, False, KEY64, BLOB32, 5000])),
        crow(api(14), uid(14), False, "", T0 + 14000, "VOICEMESSAGE", json.dumps([4, True, KEY64, BLOB32])),
        crow(api(15), uid(15), False, "", T0 + 15000, "CONTACT", "ZZBOB002"),
        crow(api(16), uid(16), False, "", T0 + 16000, "STATUS", "some status"),
        crow(api(17), uid(17), False, "", T0 + 17000, "TEXT", f"> {ALICE}: hi\nreply"),
        crow("", uid(18), False, "", T0 + 18000, "VOIP_STATUS", json.dumps([1, {"status": 1}])),
    ]
    g = [
        dict(crow(api(101), uid(101), False, "", T0 + 101000, "TEXT", "g hello", read_at=str(T0 + 101500),
                  g_msg_states=json.dumps({OWN: "USERACK", ALICE: "USERDEC"})), identity=BOB),
        dict(crow(api(102), uid(102), True, "SENT", T0 + 102000, "TEXT", "g mine"), identity=""),
        dict(crow("", uid(103), False, "", T0 + 103000, "GROUP_STATUS", json.dumps([4, {"status": 3,
             "identity": CAROL}]), isstatusmessage="1"), identity=""),
        dict(crow("", uid(104), False, "", T0 + 104000, "TEXT", "Alice renamed the group", isstatusmessage="1"),
             identity=""),
        dict(crow(api(105), uid(105), False, "", T0 + 105000, "FILE",
                  file_body(BLOB32, KEY64, "application/pdf", 7, "doc.pdf", 0, True, None, None, {})), identity=ALICE),
        dict(crow(api(106), uid(106), False, "", T0 + 106000, "FILE",
                  file_body(BLOB32, KEY64, "image/jpeg", 5, "x.jpg", 1, False, None, "image/png", {})),
             identity=ALICE),
        dict(crow(api(107), uid(107), True, "SENT", T0 + 107000, "BALLOT", json.dumps([3, 8])), identity=""),
        dict(crow("", uid(108), False, "", T0 + 108000, "GROUP_CALL_STATUS",
                  json.dumps([2, {"status": 1, "callId": "abc", "callerIdentity": BOB}])), identity=""),
        dict(crow(api(109), uid(109), False, "", T0 + 109000, "TEXT", "no sender"), identity=""),
    ]
    entries = {
        "settings": b'"version","%s"\n' % version.encode(),
        "identity": b"AAAA-" * 19 + b"AAAA",
        "contact_avatar_me": b"\xff\xd8\xffme",
        "contact_avatar_0000000001": b"\xff\xd8\xffalice-user",
        "contact_profile_pic_0000000002": b"\xff\xd8\xffbob-profile",
        "contact_profile_pic_0000000003": b"\xff\xd8\xffgw",
        "message_0000000001.csv": acsv(CONTACT_HDR, rows_for(CONTACT_HDR, c)),
        "message_0000000002.csv": acsv(CONTACT_HDR, []),
        "message_0000000003.csv": acsv(CONTACT_HDR, []),
        "message_0000000004.csv": acsv(CONTACT_HDR, []),
        "contacts.csv": contacts,
        "group_avatar_1111111111": b"\xff\xd8\xffgroup",
        "group_message_1111111111.csv": acsv(GROUP_HDR, rows_for(GROUP_HDR, g)),
        "group_message_2222222222.csv": acsv(GROUP_HDR, []),
        "group_message_3333333333.csv": acsv(GROUP_HDR, []),
        "groups.csv": groups,
        "distribution_list_message_5.csv": acsv(DLIST_HDR, [[api(200), uid(200), BOB, "1", "1", "1", "SENT",
                                                             str(T0), str(T0), "", "TEXT", "dl", "0", "", "", "",
                                                             ""]]),
        "distribution_list.csv": acsv(["id", "distribution_list_name", "created_at", "last_update",
                                       "distribution_members", "archived"], [["5", "DL", str(T0), "", BOB, "0"]]),
        "ballot.csv": acsv(["id", "aid", "creator", "ref", "ref_id", "name", "state", "assessment", "type",
                            "choice_type", "last_viewed_at", "created_at", "modified_at"],
                           [["7", "0000000000000abc", ALICE, "IdentityBallotModel", ALICE, "Poll A", "OPEN",
                             "SINGLE_CHOICE", "INTERMEDIATE", "TEXT", "", str(T0), str(T0 + 1)],
                            ["8", "0000000000000def", OWN, "GroupBallotModel", "1111111111", "Poll G", "CLOSED",
                             "MULTIPLE_CHOICE", "RESULT_ON_CLOSE", "TEXT", "", str(T0), str(T0 + 2)]]),
        "ballot_choice.csv": acsv(["id", "ballot", "aid", "type", "name", "vote_count", "order", "created_at",
                                   "modified_at"],
                                  [["1", f"0000000000000abc-{ALICE}", "0", "Text", "Yes", "1", "0", str(T0), ""],
                                   ["2", f"0000000000000abc-{ALICE}", "1", "Text", "No", "0", "1", str(T0), ""],
                                   ["3", f"0000000000000def-{OWN}", "0", "Text", "X", "2", "0", str(T0), ""]]),
        "ballot_vote.csv": acsv(["id", "ballot_uid", "choice_uid", "identity", "choice", "created_at", "modified_at"],
                                [["1", f"0000000000000abc-{ALICE}", "0", OWN, "1", str(T0), ""],
                                 ["2", f"0000000000000def-{OWN}", "0", BOB, "1", str(T0), ""],
                                 ["3", "ffffffffffffffff-ZZZZZZZZ", "0", BOB, "1", str(T0), ""]]),
        "contact_reactions.csv": acsv(["identity", "api_message_id", "sender_identity", "emoji_sequence",
                                       "reacted_at"],
                                      [[ALICE, api(1), OWN, "❤️", str(T0 + 2100)],
                                       [ALICE, api(4), ALICE, "\U0001F602", str(T0 + 5100)],
                                       [ALICE, api(2), ALICE, "\U0001F44D", str(T0 + 3200)],
                                       [ALICE, api(999), ALICE, "\U0001F44D", str(T0 + 3200)]]),
        "group_reactions.csv": acsv(["api_group_id", "group_creator_identity", "api_message_id", "sender_identity",
                                     "emoji_sequence", "reacted_at"],
                                    [["0102030405060708", ALICE, api(101), BOB, "\U0001F44D", str(T0 + 101600)]]),
        "reaction_counts.csv": acsv(["contactReactions", "groupReactions"], [["4", "1"]]),
        "nonces.csv": acsv(["nonces"], [["aa" * 32], ["bb" * 32]]),
        "nonces_d2d.csv": acsv(["nonces"], [["cc" * 32]]),
        "nonce_counts.csv": acsv(["csp", "d2d"], [["2", "1"]]),
    }
    make_zip(path, entries)
    return entries


def build_media_backup(path):
    entries = {"settings": b'"version","27"\n'}
    for u, b in MEDIA.items():
        entries[("group_message_media_" if int(u[-12:]) >= 100 else "message_media_") + u] = b
    for u, b in THUMBS.items():
        entries[("group_message_thumbnail_" if int(u[-12:]) >= 100 else "message_thumbnail_") + u] = b
    entries["message_media_" + uid(777)] = b"unreferenced"
    make_zip(path, entries)


@pytest.fixture()
def fx(tmp_path):
    text = tmp_path / f"threema-backup_{TEXT_TS}_1"
    media = tmp_path / f"threema-backup_{MEDIA_TS}_1"
    build_text_backup(str(text))
    build_media_backup(str(media))
    out = tmp_path / "out"
    return {"text": str(text), "media": str(media), "pw": PW, "out": str(out), "tmp": tmp_path}


def run(fx, *extra):
    argv = ["--text-backup", fx["text"], "--media-backup", fx["media"], "--out-dir", fx["out"], *extra]
    assert an.main(argv, password=fx["pw"]) == 0
    db = sqlite3.connect(os.path.join(fx["out"], "normalized.sqlite"))
    db.row_factory = sqlite3.Row
    rep = json.load(open(os.path.join(fx["out"], "normalize-report.json")))
    return db, rep


def msg(db, n):
    return db.execute("SELECT * FROM messages WHERE uid=?", (uid(n),)).fetchone()


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


# ---------------------------------------------------------------------------------------------------------------

def test_unescape_roundtrip():
    raw = acsv(["body"], [[TRICKY], ["plain"], [None]])
    h, rows = an.parse_csv_bytes(raw)
    assert h == ["body"]
    assert rows[0] == [TRICKY]
    assert rows[1] == ["plain"]
    assert rows[2] == []  # unquoted null in a 1-column CSV = empty line


def test_schema_matches_contract(fx):
    db, _ = run(fx, "--no-media")
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == set(CONTRACT)
    for t, cols in CONTRACT.items():
        got = [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')]
        assert got == cols, t
    meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    for k in ("own_identity", "format_version", "text_backup", "media_backup", "generated_at"):
        assert k in meta


def test_own_identity_detection(fx):
    db, rep = run(fx, "--no-media")
    meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    assert meta["own_identity"] == OWN
    d = rep["own_identity_detection"]
    assert d["detected"] and d["signal_conflicts"] == 0
    assert d["method"].startswith("contact_reactions")
    assert d["signals"]["groups.members_or_creator_not_contact"]["contains_detected"] is True
    assert d["signals"]["g_msg_states.key_not_contact"]["contains_detected"] is True
    # own identity must not leak into the report
    assert OWN not in json.dumps(rep)


def test_own_identity_override(fx):
    _, rep = run(fx, "--no-media", "--own-identity", "ZZZZZZZZ")
    assert rep["own_identity_detection"]["override_matches_detected"] is False
    assert "DISAGREES" in rep["own_identity_detection"]["method"]


def test_contacts_and_groups(fx):
    db, rep = run(fx, "--no-media")
    a = db.execute("SELECT * FROM contacts WHERE identity=?", (ALICE,)).fetchone()
    assert a["public_key"] == bytes.fromhex("11" * 32) and a["verification"] == 2
    assert a["first_name"] == "Alice" and a["nickname"] == "ali" and a["last_update_ms"] == T0 + 99_000
    assert a["avatar_user_path"] == "avatars/contact_avatar_ZZALI001" and a["avatar_contact_path"] is None
    b = db.execute("SELECT * FROM contacts WHERE identity=?", (BOB,)).fetchone()
    assert b["verification"] == 1 and b["archived"] == 1 and b["first_name"] is None
    assert b["avatar_contact_path"] == "avatars/contact_profile_pic_ZZBOB002"
    gw = db.execute("SELECT * FROM contacts WHERE identity=?", (GW,)).fetchone()
    assert gw["verification"] == 0 and gw["avatar_contact_path"] == "avatars/contact_profile_pic__ZZGATAY"
    assert db.execute("SELECT hidden FROM contacts WHERE identity=?", (CAROL,)).fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM contacts WHERE identity=?", (OWN,)).fetchone()[0] == 0

    g1 = db.execute("SELECT * FROM groups WHERE group_key=?", (f"0102030405060708-{ALICE}",)).fetchone()
    assert g1["group_id"] == bytes.fromhex("0102030405060708") and g1["creator"] == ALICE and g1["is_mine"] == 0
    assert json.loads(g1["members_json"]) == [ALICE, BOB, CAROL]
    assert g1["avatar_path"] == f"avatars/group_avatar_0102030405060708-{ALICE}"
    assert g1["description"] == "desc" and g1["description_ms"] == T0 + 1 and g1["user_state"] == 0
    g2 = db.execute("SELECT * FROM groups WHERE creator=?", (OWN,)).fetchone()
    assert g2["is_mine"] == 1 and json.loads(g2["members_json"]) == [BOB] and g2["created_ms"] == 0
    g3 = db.execute("SELECT * FROM groups WHERE creator=?", (DAVE,)).fetchone()
    assert g3["user_state"] == 1  # orphaned -> KICKED like RestoreService
    assert json.loads(g3["members_json"]) == [BOB, DAVE]
    assert rep["group_members_without_contacts_csv_entry"] == 1
    meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    assert meta["own_avatar_path"] == "avatars/contact_avatar_me"
    assert open(os.path.join(fx["out"], "avatars/contact_avatar_me"), "rb").read() == b"\xff\xd8\xffme"


def test_text_messages(fx):
    db, rep = run(fx, "--no-media")
    m1 = msg(db, 1)
    assert m1["text"] == TRICKY  # CSV unescape incl. backslashes, quotes, newline
    assert m1["chat_kind"] == "contact" and m1["chat_key"] == ALICE
    assert m1["api_id"] == bytes.fromhex(api(1)) and m1["msg_id"] == m1["api_id"]
    assert m1["is_own"] == 0 and m1["sender"] == ALICE and m1["kind"] == "text" and m1["state"] is None
    assert m1["posted_ms"] == T0 + 0 and m1["created_ms"] == T0 + 1000 and m1["read_ms"] == T0 + 2000
    m2 = msg(db, 2)
    assert m2["is_own"] == 1 and m2["sender"] is None and m2["state"] == "READ"  # USERACK + read_at
    assert msg(db, 3)["state"] == "DELIVERED"  # USERDEC without read_at
    m4 = msg(db, 4)
    assert m4["quoted_api_id"] == bytes.fromhex(api(1)) and m4["edited_ms"] == T0 + 5500 and m4["starred"] == 1
    m5 = msg(db, 5)
    assert m5["deleted_ms"] == T0 + 6500 and m5["text"] is None and m5["kind"] == "text"
    assert msg(db, 15)["kind"] == "text" and msg(db, 15)["raw_type"] == "CONTACT"
    assert msg(db, 16)["kind"] == "legacy_status" and msg(db, 16)["text"] == "some status"
    assert msg(db, 17)["text"].startswith("> ZZALI001: ")
    assert rep["quote_v1_in_text_body (kept verbatim)"] == 1
    assert rep["quotes"] == {"contact:resolved": 1}
    assert rep["messages_edited"] == 1 and rep["messages_deleted"] == 1 and rep["messages_starred"] == 1


def test_file_location_ballot_voip_group(fx):
    db, rep = run(fx, "--no-media")
    m6 = msg(db, 6)
    assert m6["kind"] == "file" and m6["file_mime"] == "image/jpeg" and m6["file_name"] == "img.jpg"
    assert m6["file_size"] == 999 and m6["file_render"] == 1 and m6["file_caption"] == "cap6"
    assert m6["file_blob_id"] is None and m6["file_key"] is None and m6["file_thumb_mime"] == "image/jpeg"
    assert json.loads(m6["file_meta_json"]) == {"h": 20, "w": 10} and m6["media_path"] is None
    m7 = msg(db, 7)
    assert m7["file_blob_id"] is None and m7["file_key"] is None  # "" -> NULL
    assert json.loads(m7["file_meta_json"]) == {"d": 3.5} and m7["file_caption"] is None
    m8 = msg(db, 8)
    assert m8["file_blob_id"] == bytes.fromhex(BLOB32) and m8["file_key"] == bytes.fromhex(KEY64)
    m9 = msg(db, 9)
    assert (m9["loc_lat"], m9["loc_lon"], m9["loc_acc"]) == (47.1, 8.5, 10.0)
    assert m9["loc_address"] == "Street 1" and m9["loc_name"] == "Cafe"  # address BEFORE name
    m10 = msg(db, 10)
    assert m10["kind"] == "call" and m10["api_id"] is None
    assert m10["msg_id"] == hashlib.sha256(uid(10).encode()).digest()[:8]
    assert (m10["call_status"], m10["call_duration_s"], m10["call_id"], m10["call_reason"]) == (2, 65, 123, None)
    assert msg(db, 18)["call_status"] == 1 and msg(db, 18)["call_id"] is None
    m11 = msg(db, 11)
    assert m11["kind"] == "ballot" and m11["ballot_ref"] == 7 and m11["ballot_data_type"] == 1
    # legacy conversions
    m12, m13, m14 = msg(db, 12), msg(db, 13), msg(db, 14)
    assert m12["kind"] == "file" and m12["file_mime"] == "image/jpeg" and m12["file_render"] == 1
    assert m12["file_blob_id"] == bytes.fromhex(BLOB32) and m12["file_key"] == bytes.fromhex(KEY64)
    assert json.loads(m12["file_meta_json"]) == {}  # _legacy_nonce dropped
    assert m12["raw_type"] == "IMAGE" and json.loads(m12["raw_body"])[3] == "ee" * 24
    assert m13["file_mime"] == "video/mpeg" and m13["file_size"] == 5000 and json.loads(m13["file_meta_json"]) == {"d": 12}
    assert m14["file_mime"] == "audio/aac" and json.loads(m14["file_meta_json"]) == {"d": 4}
    # group
    gk = f"0102030405060708-{ALICE}"
    g1 = msg(db, 101)
    assert g1["chat_kind"] == "group" and g1["chat_key"] == gk and g1["sender"] == BOB and g1["is_own"] == 0
    g2 = msg(db, 102)
    assert g2["is_own"] == 1 and g2["sender"] is None and g2["state"] == "SENT"
    g3 = msg(db, 103)
    assert g3["kind"] == "group_status" and g3["gstatus_type"] == 3 and g3["gstatus_identity"] == CAROL
    assert msg(db, 104)["kind"] == "legacy_status" and msg(db, 104)["text"] == "Alice renamed the group"
    assert msg(db, 107)["ballot_ref"] == 8 and msg(db, 107)["ballot_data_type"] == 3
    g8 = msg(db, 108)
    assert g8["kind"] == "call" and g8["raw_type"] == "GROUP_CALL_STATUS" and g8["call_status"] == 1
    assert rep["anomalies"]["group_incoming_message_without_sender"] == 1
    assert rep["skipped_rows"]["distribution_list_message (no iOS target)"] == 1
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 18 + 9
    assert rep["messages_by_kind"]["file"] == 8


def test_reactions_and_legacy_acks(fx):
    db, rep = run(fx, "--no-media")
    rs = db.execute("SELECT * FROM reactions ORDER BY source, chat_kind, reacted_ms").fetchall()
    got = {(r["chat_kind"], r["target_msg_id"].hex(), r["sender"], r["emoji"], r["source"]) for r in rs}
    assert ("contact", api(1), None, "❤️", "csv") in got           # own reaction -> sender NULL
    assert ("contact", api(4), ALICE, "\U0001F602", "csv") in got
    assert ("contact", api(2), ALICE, "\U0001F44D", "csv") in got           # csv wins over identical legacy ack
    assert ("contact", api(3), None, "\U0001F44E", "legacy_ack") in got     # incoming USERDEC -> reactor = me
    assert ("group", api(101), BOB, "\U0001F44D", "csv") in got
    assert ("group", api(101), None, "\U0001F44D", "legacy_ack") in got     # g_msg_states own
    assert ("group", api(101), ALICE, "\U0001F44E", "legacy_ack") in got
    assert not any(t == ("contact", api(2), ALICE, "\U0001F44D", "legacy_ack") for t in got)
    assert rep["skipped_rows"]["legacy_ack_duplicate_of_existing_reaction"] == 1
    assert rep["reactions"]["csv:contact:unresolved"] == 1  # api(999)
    legacy3 = [r for r in rs if r["source"] == "legacy_ack" and r["target_msg_id"].hex() == api(3)][0]
    assert legacy3["reacted_ms"] == T0 + 4000 + 5  # modified_at first (RestoreService.createReactionForStateName)


def test_ballots_and_nonces(fx):
    db, rep = run(fx, "--no-media")
    b7 = db.execute("SELECT * FROM ballots WHERE ref_id=7").fetchone()
    assert b7["chat_kind"] == "contact" and b7["chat_key"] == ALICE and b7["api_id"] == bytes.fromhex("0000000000000abc")
    assert b7["state"] == "OPEN" and b7["assessment"] == "SINGLE_CHOICE" and b7["btype"] == "INTERMEDIATE"
    b8 = db.execute("SELECT * FROM ballots WHERE ref_id=8").fetchone()
    assert b8["chat_kind"] == "group" and b8["chat_key"] == f"0102030405060708-{ALICE}" and b8["creator"] == OWN
    ch = db.execute("SELECT ballot_ref, choice_id, name, order_pos, vote_count FROM ballot_choices ORDER BY 1,2").fetchall()
    assert [tuple(r) for r in ch] == [(7, 0, "Yes", 0, 1), (7, 1, "No", 1, 0), (8, 0, "X", 0, 2)]
    v = db.execute("SELECT ballot_ref, choice_id, identity, choice FROM ballot_votes ORDER BY 1").fetchall()
    assert [tuple(r) for r in v] == [(7, 0, OWN, 1), (8, 0, BOB, 1)]
    assert rep["skipped_rows"]["ballot_vote_without_ballot"] == 1
    assert rep["ballots"]["ballot_message_resolved"] == 2
    n = db.execute("SELECT kind, hash FROM nonces ORDER BY kind, hash").fetchall()
    assert [(r[0], r[1].hex()) for r in n] == [("csp", "aa" * 32), ("csp", "bb" * 32), ("d2d", "cc" * 32)]
    assert rep["nonces"]["csp"] == 2 and rep["nonces"]["nonce_counts.csv"] == {"csp": 2, "d2d": 1}


def test_media_extraction(fx):
    db, rep = run(fx)
    for u, n in ((uid(6), 6), (uid(7), 7), (uid(12), 12), (uid(105), 105)):
        m = msg(db, n)
        assert m["media_path"] == f"media/{u}"
        data = open(os.path.join(fx["out"], m["media_path"]), "rb").read()
        assert data == MEDIA[u]
        assert m["media_sha256"] == hashlib.sha256(data).hexdigest()
        assert m["file_size"] == len(data)  # entry size wins over body fileSize (999 for uid 6)
    assert msg(db, 6)["thumb_path"] == f"thumbs/{uid(6)}"
    assert open(os.path.join(fx["out"], "thumbs", uid(6)), "rb").read() == THUMBS[uid(6)]
    m106 = msg(db, 106)
    assert m106["media_path"] is None and m106["thumb_path"] == f"thumbs/{uid(106)}"
    assert msg(db, 8)["media_path"] is None
    cov = rep["media"]["coverage"]
    assert cov["file_messages"] == 8 and cov["with_media"] == 4 and cov["with_thumb"] == 2
    assert cov["without_media:created_after_media_backup"] == 1
    assert rep["media"]["media_size_differs_from_body_fileSize"] == 3  # uid6 (999), legacy 12 (0), pdf 105 (7)
    assert rep["media_entries_unreferenced_by_text_backup"] == 1
    assert not any(f.endswith(".part") for f in os.listdir(os.path.join(fx["out"], "media")))


def test_media_resume(fx):
    run(fx)
    p6 = os.path.join(fx["out"], "media", uid(6))
    p7 = os.path.join(fx["out"], "media", uid(7))
    mt6 = os.stat(p6).st_mtime_ns
    with open(p7, "wb") as f:  # truncated file must be re-extracted
        f.write(b"x")
    with open(p7 + ".part", "wb") as f:  # stale .part from an interrupted run
        f.write(b"junk")
    db, rep = run(fx)
    assert os.stat(p6).st_mtime_ns == mt6
    assert open(p7, "rb").read() == MEDIA[uid(7)]
    assert not os.path.exists(p7 + ".part")
    res = rep["media"]["results"]
    assert res["media:extracted"] == 1 and res["media:skipped_existing"] == 3
    assert msg(db, 7)["media_sha256"] == hashlib.sha256(MEDIA[uid(7)]).hexdigest()
    assert msg(db, 6)["media_sha256"] == hashlib.sha256(MEDIA[uid(6)]).hexdigest()


def test_parallel_jobs_same_result(fx):
    db, rep = run(fx, "--jobs", "3")
    assert rep["media"]["errors"] == {}
    assert msg(db, 105)["media_sha256"] == hashlib.sha256(MEDIA[uid(105)]).hexdigest()


def test_no_media(fx):
    db, rep = run(fx, "--no-media")
    assert db.execute("SELECT count(*) FROM messages WHERE media_path IS NOT NULL OR thumb_path IS NOT NULL").fetchone()[0] == 0
    assert not os.path.exists(os.path.join(fx["out"], "media"))
    assert rep["media"] == {"skipped": "--no-media"}


def test_wrong_password(fx):
    with pytest.raises(SystemExit) as e:
        an.main(["--text-backup", fx["text"], "--out-dir", fx["out"], "--no-media"], password=WRONG_PW)
    assert WRONG_PW not in str(e.value)
    assert isinstance(e.value, an.NormalizeError) and e.value.kind == "password"


def test_password_from_stdin_line(fx, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    assert an.main(["--text-backup", fx["text"], "--out-dir", fx["out"], "--no-media"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(an.NormalizeError) as e:
        an.main(["--text-backup", fx["text"], "--out-dir", fx["out"], "--no-media"])
    assert e.value.kind == "password"


def test_no_password_file_option():
    with pytest.raises(SystemExit) as e:       # argparse: unknown option (DESIGN §9: no password files)
        an.main(["--text-backup", "x", "--out-dir", "y", "--password-file", "z"], password=PW)
    assert e.value.code == 2


def test_per_file_passwords(fx, tmp_path):
    """Text and media backup with DIFFERENT passwords (each file has its own, DESIGN §5.1 android_passwords)."""
    media2 = tmp_path / f"threema-backup_{MEDIA_TS}_2"
    entries = {"settings": b'"version","27"\n'}
    for u, b in MEDIA.items():
        entries[("group_message_media_" if int(u[-12:]) >= 100 else "message_media_") + u] = b
    make_zip(str(media2), entries, pw="other-media-pass")
    out = tmp_path / "out2"
    rep = an.normalize(fx["text"], [str(media2)], passwords={fx["text"]: PW, str(media2): "other-media-pass"},
                       out_dir=str(out))
    assert rep["media"]["results"].get("media:extracted") == 4 and rep["media"]["errors"] == {}
    with pytest.raises(an.NormalizeError) as e:
        an.normalize(fx["text"], [str(media2)], passwords={fx["text"]: PW, str(media2): PW}, out_dir=str(out))
    assert e.value.kind == "password"


def test_format_version_and_newer_format(fx, tmp_path):
    assert an.read_format_version(fx["text"], [PW]) == 27
    newer = tmp_path / f"threema-backup_{TEXT_TS}_9"
    build_text_backup(str(newer), version="28")
    with pytest.raises(an.NormalizeError) as e:
        an.normalize(str(newer), passwords=PW, out_dir=str(tmp_path / "o"), no_media=True)
    assert e.value.kind == "format_new" and e.value.info["format_version"] == 28


def test_progress_callback(fx):
    seen = []
    an.normalize(fx["text"], [fx["media"]], passwords=PW, out_dir=fx["out"], progress=lambda d, t: seen.append((d, t)))
    assert seen and seen[-1][0] == seen[-1][1]


def test_password_never_written(fx, capsys):
    run(fx)
    out = capsys.readouterr()
    assert PW not in out.out and PW not in out.err
    for root, _, files in os.walk(fx["out"]):
        for fn in files:
            assert PW.encode() not in open(os.path.join(root, fn), "rb").read(), fn


def test_pre_v19_naming(tmp_path):
    """Before format v19: message files keyed by identity, group files by '<idhex>-<creator>',
    no identity_id / group_uid / user_state columns."""
    text = tmp_path / "threema-backup_1600000000000_1"
    hdr = CONTACT_HDR[:16]
    ghdr = GROUP_HDR[:17]
    entries = {
        "settings": b'"version","18"\n',
        "contacts.csv": acsv(["identity", "publickey", "verification", "firstname", "lastname", "nick_name"],
                             [[ALICE, "11" * 32, "UNVERIFIED", "", "", ""]]),
        "groups.csv": acsv(["id", "creator", "groupname", "created_at", "members", "archived"],
                           [["0102030405060708", ALICE, "G", str(T0), f"{ALICE};{OWN}", "0"]]),
        f"message_{ALICE}.csv": acsv(hdr, rows_for(hdr, [crow(api(1), uid(1), False, "", T0, "TEXT", "old")])),
        f"group_message_0102030405060708-{ALICE}.csv": acsv(ghdr, rows_for(ghdr, [
            dict(crow(api(2), uid(2), False, "", T0, "TEXT", "g old"), identity=ALICE)])),
        f"contact_avatar_{OWN}": b"\xff\xd8\xffme-old",
    }
    make_zip(str(text), entries)
    out = tmp_path / "out"
    assert an.main(["--text-backup", str(text), "--out-dir", str(out), "--no-media", "--own-identity", OWN],
                   password=PW) == 0
    db = sqlite3.connect(str(out / "normalized.sqlite"))
    assert db.execute("SELECT chat_key FROM messages WHERE uid=?", (uid(1),)).fetchone()[0] == ALICE
    assert db.execute("SELECT chat_key FROM messages WHERE uid=?", (uid(2),)).fetchone()[0] == f"0102030405060708-{ALICE}"
    assert db.execute("SELECT user_state FROM groups").fetchone()[0] == 0
    meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    assert meta["own_avatar_path"] == "avatars/contact_avatar_me"


def test_supplementary_contacts(fx):
    """review F2: a sender without contacts.csv entry can be added as hidden contact from an explicit key file."""
    db0, _ = run(fx, "--no-media")
    senders = {r[0] for r in db0.execute("SELECT DISTINCT sender FROM messages WHERE sender IS NOT NULL")}
    known = {r[0] for r in db0.execute("SELECT identity FROM contacts")}
    unknown = sorted(senders - known)
    db0.close()
    sup = fx["tmp"] / "sup.json"
    items = [{"identity": ALICE, "public_key_hex": "22" * 32},        # existing contact -> rejected (existing wins)
             {"identity": "QQQQQQQQ", "public_key_hex": "33" * 32},   # not referenced -> rejected
             {"identity": "BADID", "public_key_hex": "44" * 32}]      # invalid -> rejected
    if unknown:
        items.append({"identity": unknown[0], "public_key_hex": "55" * 32})
    sup.write_text(json.dumps(items))
    db, rep = run(fx, "--no-media", "--supplementary-contacts", str(sup))
    s = rep["supplementary_contacts"]
    assert s["rejected_existing_or_own"] == 1 and s["rejected_not_referenced"] == 1 and s["rejected_invalid"] == 1
    assert db.execute("SELECT public_key FROM contacts WHERE identity=?", (ALICE,)).fetchone()[0] == bytes.fromhex("11" * 32)
    if unknown:
        r = db.execute("SELECT hidden, verification, public_key FROM contacts WHERE identity=?", (unknown[0],)).fetchone()
        assert s["added_hidden"] == 1 and r[0] == 1 and r[1] == 0 and r[2] == bytes.fromhex("55" * 32)


def test_supplementary_contacts_positive_path(tmp_path):
    """review F2: the accepted path (sender occurs in messages, not in contacts.csv) adds a hidden, unverified contact."""
    data = {"own": OWN, "contacts": {ALICE: {"identity": ALICE}},
            "messages": {"u1": {"sender": "ZZSENX01"}, "u2": {"sender": ALICE}}, "reactions": [{"sender": None}]}
    p = tmp_path / "sup.json"
    p.write_text(json.dumps([{"identity": "zzsenx01", "public_key_hex": "AB" * 32}]))

    class R:
        data = {}
    an.add_supplementary_contacts(data, str(p), R)
    c = data["contacts"]["ZZSENX01"]
    assert R.data["supplementary_contacts"] == {"added_hidden": 1}
    assert c["hidden"] == 1 and c["verification"] == 0 and c["public_key"] == bytes.fromhex("ab" * 32)
