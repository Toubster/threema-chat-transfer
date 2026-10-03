# SPDX-License-Identifier: AGPL-3.0-or-later
"""
A small SYNTHETIC Threema Android data backup (format v27) that carries the canary values of fixtures/canaries.json
(owner: coreA). Used by the fixture E2E to prove that names, IDs, message texts, e-mail addresses, phone numbers and
passwords never reach events, reports, logs or engine.json.

    build(path)  ->  {"own": ..., "missing": [...]}     AES-256 zip, password = the canary password

Content: own identity ZZCANOWN; contacts ZZCANARY (canary first/last name) and ZZFIXR02; a 1:1 chat with ZZCANARY
(canary message text, the canary e-mail address and phone number in texts); a group with the canary group name whose
member ZZCANMIS is not a contact (a "missing key sender": it must appear ONLY in android/missing-senders.json).
"""
from __future__ import annotations

import pyzipper

from . import canaries

OWN = "ZZCANOWN"
FRIEND = "ZZFIXR02"
MISSING = "ZZCANMIS"
T0 = 1_788_000_000_000

CONTACT_HDR = ["apiid", "uid", "isoutbox", "isread", "issaved", "messagestae", "posted_at", "created_at",
               "modified_at", "type", "body", "isstatusmessage", "caption", "quoted_message_apiid", "delivered_at",
               "read_at", "g_msg_states", "display_tags", "edited_at", "deleted_at"]
GROUP_HDR = CONTACT_HDR[:2] + ["identity"] + CONTACT_HDR[2:]


def _csv(header, rows) -> bytes:
    """Threema's CSVWriter: every cell quoted, quotes doubled, backslashes doubled (CSVRow.escape)."""
    def cell(v):
        if v is None:
            return ""
        return '"' + str(v).replace("\\", "\\\\").replace('"', '""') + '"'
    lines = [",".join(f'"{h}"' for h in header)] + [",".join(cell(v) for v in r) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _uid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _row(header, n, out, created, body, **kw):
    d = {"apiid": f"{0x5c000000 + n:016x}", "uid": _uid(n), "isoutbox": "1" if out else "0", "isread": "1",
         "issaved": "1", "messagestae": "READ" if out else "", "posted_at": created - 1000, "created_at": created,
         "modified_at": created + 5, "type": "TEXT", "body": body, "isstatusmessage": "0", "caption": "",
         "quoted_message_apiid": "", "delivered_at": "", "read_at": "", "g_msg_states": None, "display_tags": "0",
         "edited_at": "", "deleted_at": ""}
    d.update(kw)
    return [d.get(h, "") for h in header]


def build(path, *, password: str | None = None) -> dict:
    c = canaries()
    pw = password or c["password"]
    canary_id = c["threema_id"]
    contacts = _csv(["identity", "publickey", "verification", "acid", "firstname", "lastname", "nick_name",
                     "last_update", "hidden", "archived", "identity_id"],
                    [[canary_id, "71" * 32, "FULLY_VERIFIED", "", c["contact_first_name"], c["contact_last_name"],
                      c["contact_first_name"].lower(), str(T0), "0", "0", "0000000001"],
                     [FRIEND, "72" * 32, "SERVER_VERIFIED", "", "Fixture", "Friend", "", "", "0", "0", "0000000002"]])
    groups = _csv(["id", "creator", "groupname", "created_at", "last_update", "members", "archived", "groupDesc",
                   "groupDescTimestamp", "group_uid", "user_state"],
                  [["5c5c5c5c5c5c5c01", canary_id, c["group_name"], str(T0), str(T0 + 9000),
                    f"{canary_id};{OWN};{MISSING};{FRIEND}", "0", c["message_text"], str(T0 + 1), "4444444444", "0"]])
    one = [
        _row(CONTACT_HDR, 1, False, T0 + 1000, c["message_text"]),
        _row(CONTACT_HDR, 2, True, T0 + 2000, f"mail {c['email']} phone {c['phone']}", messagestae="USERACK"),
        _row(CONTACT_HDR, 3, False, T0 + 3000, f"{c['device_name']} {c['serial']}"),
    ]
    grp = [
        _row(GROUP_HDR, 101, False, T0 + 4000, c["message_text"], identity=canary_id),
        _row(GROUP_HDR, 102, False, T0 + 5000, "from a person who is not a contact", identity=MISSING),
        _row(GROUP_HDR, 103, True, T0 + 6000, "mine", messagestae="SENT", identity=""),
    ]
    entries = {
        "settings": b'"version","27"\n',
        "identity": b"AAAA-" * 19 + b"AAAA",
        "contacts.csv": contacts,
        "message_0000000001.csv": _csv(CONTACT_HDR, one),
        "message_0000000002.csv": _csv(CONTACT_HDR, []),
        "groups.csv": groups,
        "group_message_4444444444.csv": _csv(GROUP_HDR, grp),
        "contact_reactions.csv": _csv(["identity", "api_message_id", "sender_identity", "emoji_sequence",
                                       "reacted_at"], [[canary_id, f"{0x5c000001:016x}", OWN, "❤️",
                                                        str(T0 + 1500)]]),
        "group_reactions.csv": _csv(["api_group_id", "group_creator_identity", "api_message_id", "sender_identity",
                                     "emoji_sequence", "reacted_at"], []),
        "nonces.csv": _csv(["nonces"], [["7c" * 32]]),
    }
    with pyzipper.AESZipFile(path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
        z.setpassword(pw.encode("utf-8"))
        z.setencryption(pyzipper.WZ_AES, nbits=256)
        for name, data in entries.items():
            z.writestr(name, data)
    return {"own": OWN, "missing": [MISSING]}
