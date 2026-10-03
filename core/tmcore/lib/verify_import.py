#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""verify_import.py - independent check of an imported Threema iOS store against normalized.sqlite.

    python3 -m tmcore.lib.verify_import --normalized <session>/android/normalized.sqlite --work-dir <session>/android \
        --store <session>/work/store_out [--store-in <session>/work/extract/store] [--momd PATH] [--importer PATH] \
        [--own-identity ID] [--report out.json] [--no-hash] [--no-coredata]
    In-process (tmcore prepare/postcheck): rc = main([...], quiet=True)
    Defaults: --momd = the bundled V56 model, --importer = the bundled threema-import (TMCORE_IMPORTER overrides).

It does NOT reuse importer code. It reads the output store with sqlite3 (read-only), derives the expected result of every
normalized row from the documented mapping (docs/import-mapping.md), and compares:
  * per chat: message count (= baseline count from --store-in + expected imported), every msg_id exactly once
  * per message: entity, direction, date / remoteSentDate (ms), sent/sendFailed/read/delivered, sender, text, quote id,
    edited/deleted dates, star marker, file mime/name/size/type/caption, FileData sha256 (inline 0x01 and external 0x02
    blob refs resolved), blob-state safety, location values, system message type, ballot link
  * reactions (set equality incl. creator and date), ballots/choices/results, contacts, groups, nonces
  * store health: PRAGMA integrity_check, Z_PRIMARYKEY.Z_MAX >= max(Z_PK) per root entity, no -wal left,
    no conversation with messages but NULL lastUpdate / lastMessage, unreadMessageCount == 0 for touched chats,
    Core Data re-open without migration (`threema-import verify`, which also checks the model hashes).
Column names of single-table-inheritance subclasses (ZDATAAVAILABLE1, ZTYPE1, ...) are detected from the data pattern
instead of assumed, because on-device migrated stores may use other suffixes.

Output: JSON report (counts and at most 20 examples per check, examples use message uids / chat hashes only - no
texts, names or identities). Exit code 0 = everything matches, 1 = at least one mismatch, 2 = usage / cannot open.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

APPLE_EPOCH = 978307200
MAX_EXAMPLES = 20


def _resources():
    env = os.environ.get("TMCORE_RESOURCES")
    return env if env else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def default_momd(model_id="V56"):
    """Bundle: Resources/models/<id>/ThreemaData.momd; repository: model/<id>/ThreemaData.momd."""
    r = _resources()
    for sub in ("models", "model"):
        p = os.path.join(r, sub, model_id, "ThreemaData.momd")
        if os.path.isdir(p):
            return p
    return os.path.join(r, "models", model_id, "ThreemaData.momd")


def default_importer():
    """TMCORE_IMPORTER (tests, development builds) or the bundled Resources/bin/threema-import."""
    return os.environ.get("TMCORE_IMPORTER") or os.path.join(_resources(), "bin", "threema-import")

# SystemMessageEntity.SystemMessageEntityType excluded as lastMessage (SystemMessageEntity.swift:49-64)
EXCLUDED_LAST = {20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 36, 37}


class Checks:
    def __init__(self):
        self.fail = collections.Counter()
        self.ok = collections.Counter()
        self.examples = collections.defaultdict(list)

    def check(self, name, cond, example=None):
        if cond:
            self.ok[name] += 1
        else:
            self.fail[name] += 1
            if example is not None and len(self.examples[name]) < MAX_EXAMPLES:
                self.examples[name].append(example)
        return cond


def ms_of(v):
    return None if v is None else int(round((v + APPLE_EPOCH) * 1000))


def chat_hash(kind, key):
    return hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:10]


def sha256_file(path, bufsize=8 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def ro_connect(path):
    # immutable=1 would ignore a -wal; the importer output has none, the baseline may have one -> plain mode=ro
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ---------------------------------------------------------------------------------------------------------- expected

def expected_call_type(status, reason, is_own):
    """Android VoipStatusDataModel -> iOS SystemMessage.type (docs/import-mapping.md 'Calls')."""
    if status == 2 or status == 4:
        return 11
    if status == 1:
        return 7
    if status == 3:
        return {1: 9, 2: 10 if is_own else 7, 3: 8, 4: 12, 5: 15}.get(reason, 13 if is_own else 7)
    return None


def expected_gstatus_type(t, ident_is_me, has_ident, creator_is_me):
    """Android GroupStatusDataModel -> iOS SystemMessage.type; None = not importable."""
    if t == 0:
        return None if creator_is_me else 5
    if t == 1:
        return 1
    if t == 2:
        return 32
    if t in (3, 4, 5):
        if ident_is_me:
            return {3: 5, 4: 16, 5: 6}[t]
        if not has_ident:
            return None
        return {3: 3, 4: 2, 5: 4}[t]
    return {6: 17, 7: 18, 8: 20, 9: 30, 10: 20, 13: 19}.get(t)


# ------------------------------------------------------------------------------------------------------------- store

class Store:
    def __init__(self, store_dir):
        self.dir = store_dir
        self.path = os.path.join(store_dir, "ThreemaData.sqlite")
        if not os.path.isfile(self.path):
            raise SystemExit(f"no ThreemaData.sqlite in {store_dir}")
        self.db = ro_connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.ent = {name: e for e, name in self.db.execute("SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY")}
        self.msg_cols = [r[1] for r in self.db.execute("PRAGMA table_info(ZMESSAGE)")]

    def q(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    def owner_column(self, family, entity, default, checks):
        """Among ZMESSAGE columns <family>, <family>1, ... find the one that holds `entity`'s attribute:
        the column with non-NULL values in that entity's rows. Every other family column must be NULL there."""
        ent = self.ent[entity]
        cols = [c for c in self.msg_cols if re.fullmatch(re.escape(family) + r"\d*", c)]
        used = [c for c in cols if self.q(f"SELECT 1 FROM ZMESSAGE WHERE Z_ENT=? AND {c} IS NOT NULL LIMIT 1", ent)]
        if len(used) > 1:
            checks.check("schema_column_detection", False, {"family": family, "entity": entity, "columns": used})
        col = used[0] if used else default
        checks.check("schema_column_detection", col in cols, {"family": family, "entity": entity, "column": col})
        return col

    def external_blob(self, blob):
        """Core Data external-storage attribute: 0x01||bytes inline, 0x02||UUID||0x00 -> _EXTERNAL_DATA/<UUID>."""
        if blob is None:
            return None, None
        if not isinstance(blob, (bytes, bytearray, memoryview)):
            return "unknown", None
        b = bytes(blob)
        if not b:
            return None, None
        if b[0] == 1:
            return "inline", b[1:]
        if b[0] == 2:
            uuid = b[1:].split(b"\x00", 1)[0].decode("ascii", "replace")
            return "external", os.path.join(self.dir, ".ThreemaData_SUPPORT", "_EXTERNAL_DATA", uuid)
        return "unknown", None


def load_store_messages(st, checks, cols):
    """(conv_pk, id) -> list of row dicts, plus per-conv counts."""
    want = ["Z_PK", "Z_ENT", "ZCONVERSATION", "ZID", "ZISOWN", "ZDATE", "ZREMOTESENTDATE", "ZSENT", "ZSENDFAILED",
            "ZREAD", "ZREADDATE", "ZDELIVERED", "ZUSERACK", "ZSENDER", "ZTEXT", "ZQUOTEDMESSAGEID", "ZLASTEDITEDAT",
            "ZDELETEDAT", "ZMESSAGEMARKERS", "ZMIMETYPE", "ZFILENAME", "ZFILESIZE", "ZCAPTION", "ZJSON", "ZBLOBID",
            "ZBLOBTHUMBNAILID", "ZDATA", "ZCONSUMED", "ZLATITUDE", "ZLONGITUDE", "ZACCURACY", "ZPOINAME", "ZPOIADDRESS",
            "ZBALLOT", "ZBALLOTSTATE", "ZARG", "ZFLAGS"]
    sel = ", ".join(want + [f"{cols[k]} AS {k}" for k in ("FDA", "FKEY", "FTYPE", "STYPE", "FTHUMB")])
    by_key = collections.defaultdict(list)
    per_conv = collections.Counter()
    for r in st.db.execute(f"SELECT {sel} FROM ZMESSAGE"):
        d = dict(r)
        by_key[(d["ZCONVERSATION"], bytes(d["ZID"]) if d["ZID"] is not None else None)].append(d)
        per_conv[d["ZCONVERSATION"]] += 1
    return by_key, per_conv


def conversation_keys(st, own):
    """conv Z_PK -> ('contact', identity) | ('group', '<idhex>-<creator|ME>')"""
    ident = {pk: i for pk, i in st.q("SELECT Z_PK, ZIDENTITY FROM ZCONTACT")}
    out = {}
    for pk, gid, cpk in st.q("SELECT Z_PK, ZGROUPID, ZCONTACT FROM ZCONVERSATION"):
        if gid is not None:
            creator = ident.get(cpk)
            out[pk] = ("group", bytes(gid).hex() + "-" + ("ME" if creator is None or creator == own else creator))
        elif cpk is not None:
            out[pk] = ("contact", ident.get(cpk))
    return out, ident


def canon_group_key(norm_key, own_ids):
    idhex, _, creator = norm_key.partition("-")
    return idhex.lower() + "-" + ("ME" if creator in own_ids else creator)


# -------------------------------------------------------------------------------------------------------------- main

def main(argv=None, *, quiet=False):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--normalized", required=True)
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--store", required=True, help="importer output store dir")
    ap.add_argument("--store-in", default=None, help="importer input store dir (baseline for pre-existing rows)")
    ap.add_argument("--momd", default=None, help="default: the bundled V56 model")
    ap.add_argument("--importer", default=None, help="default: TMCORE_IMPORTER or the bundled threema-import")
    ap.add_argument("--own-identity", default=None, help="identity the import ran with (if overridden)")
    ap.add_argument("--report", default=None)
    ap.add_argument("--no-hash", action="store_true", help="skip sha256 of media (size check only)")
    ap.add_argument("--no-coredata", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="with --post-launch: the run drove the EXPLICIT syncBlobs path for every file message "
                         "(THREEMA_SIM_OPEN=probe); also tolerate sendFailed=1 on own file rows without blob id/data")
    ap.add_argument("--post-launch", action="store_true",
                    help="store copied back from a device/simulator AFTER the app ran on it: tolerate what the app "
                         "itself writes offline (review appsafety m6: BlobManager sets sendFailed=1 on displayed incoming "
                         "placeholders, BlobManager.swift:424,920-930); every other invariant stays strict")
    a = ap.parse_args(argv)
    a.momd = a.momd or default_momd()
    a.importer = a.importer or default_importer()
    t0 = time.time()
    C = Checks()
    info = collections.OrderedDict()

    nd = sqlite3.connect(f"file:{a.normalized}?mode=ro", uri=True)
    nd.row_factory = sqlite3.Row
    meta = {k: v for k, v in nd.execute("SELECT key, value FROM meta")}
    meta_own = meta.get("own_identity")
    own = a.own_identity or meta_own
    own_ids = {own, meta_own} - {None}
    overridden = own != meta_own

    st = Store(a.store)

    # ---- store health -----------------------------------------------------------------------------------------
    ic = st.q("PRAGMA integrity_check")
    C.check("sqlite_integrity_check", [r[0] for r in ic] == ["ok"], [r[0] for r in ic][:5])
    C.check("no_wal_left", not (os.path.exists(st.path + "-wal") and os.path.getsize(st.path + "-wal") > 0))
    jm = st.q("PRAGMA journal_mode")[0][0]
    info["journal_mode"] = jm
    for ent, name, sup, zmax in st.q("SELECT Z_ENT, Z_NAME, Z_SUPER, Z_MAX FROM Z_PRIMARYKEY"):
        if sup != 0:
            continue
        table = "Z" + name.upper()
        try:
            mx = st.q(f"SELECT COALESCE(MAX(Z_PK),0) FROM {table}")[0][0]
        except sqlite3.OperationalError:
            continue
        C.check("z_primarykey_max_ge_max_pk", zmax >= mx, {"entity": name, "z_max": zmax, "max_pk": mx})
    # sub-entity rows must carry a valid Z_ENT
    sub_ents = {e for n, e in st.ent.items() if n in ("AudioMessage", "BallotMessage", "FileMessage", "ImageMessage",
                                                        "LocationMessage", "SystemMessage", "TextMessage", "VideoMessage")}
    bad_ent = st.q(f"SELECT COUNT(*) FROM ZMESSAGE WHERE Z_ENT NOT IN ({','.join(map(str, sub_ents))})")[0][0]
    C.check("message_z_ent_valid", bad_ent == 0, {"rows": bad_ent})

    cols = {
        "FDA": st.owner_column("ZDATAAVAILABLE", "FileMessage", "ZDATAAVAILABLE1", C),
        "FKEY": st.owner_column("ZENCRYPTIONKEY", "FileMessage", "ZENCRYPTIONKEY1", C),
        "FTYPE": st.owner_column("ZTYPE", "FileMessage", "ZTYPE", C),
        "STYPE": st.owner_column("ZTYPE", "SystemMessage", "ZTYPE1", C),
        "FTHUMB": st.owner_column("ZTHUMBNAIL", "FileMessage", "ZTHUMBNAIL", C),
    }
    info["detected_columns"] = cols
    E = {v: k for k, v in st.ent.items()}  # Z_ENT -> entity name

    conv_key, contact_ident = conversation_keys(st, own)
    key_conv = {}
    for pk, k in conv_key.items():
        C.check("conversation_key_unique", k not in key_conv, {"chat": chat_hash(*k)})
        key_conv.setdefault(k, pk)
    contact_pk = {i: pk for pk, i in contact_ident.items()}
    by_key, per_conv = load_store_messages(st, C, cols)

    # ---- baseline (input store) ---------------------------------------------------------------------------------
    base_ids = collections.defaultdict(set)   # chat key -> set(msg ids)
    base_count = collections.Counter()
    base_contacts, base_groups = set(), set()
    if a.store_in:
        bs = Store(a.store_in)
        bkeys, bident = conversation_keys(bs, own)
        base_contacts = set(bident.values())
        for cpk, mid in bs.q("SELECT ZCONVERSATION, ZID FROM ZMESSAGE"):
            k = bkeys.get(cpk)
            if k:
                base_ids[k].add(bytes(mid))
                base_count[k] += 1
        base_groups = {k for k in bkeys.values() if k[0] == "group"}
        base_conv_state = {bkeys[pk]: (lu, lm, un) for pk, lu, lm, un in
                           bs.q("SELECT Z_PK, ZLASTUPDATE, ZLASTMESSAGE, ZUNREADMESSAGECOUNT FROM ZCONVERSATION") if pk in bkeys}
        info["baseline"] = {"messages": sum(base_count.values()), "contacts": len(base_contacts),
                            "conversations": len(bkeys)}
        bs.db.close()
    else:
        base_conv_state = {}

    # ---- contacts -------------------------------------------------------------------------------------------------
    ncontacts = {r["identity"]: r for r in nd.execute("SELECT * FROM contacts")}
    store_contact_rows = {r["ZIDENTITY"]: r for r in st.db.execute(
        "SELECT Z_PK, ZIDENTITY, ZPUBLICKEY, ZHIDDEN, ZVERIFICATIONLEVEL FROM ZCONTACT")}
    for ident, c in ncontacts.items():
        if ident in own_ids:
            C.check("own_identity_not_a_contact", ident not in store_contact_rows)
            continue
        valid = len(ident) == 8 and c["public_key"] is not None and len(c["public_key"]) == 32
        if not valid:
            continue
        r = store_contact_rows.get(ident)
        if not C.check("contact_present", r is not None, {"contact": chat_hash("contact", ident)}):
            continue
        if ident in base_contacts:
            continue  # existing iPhone data wins, not compared
        C.check("contact_public_key", bytes(r["ZPUBLICKEY"]) == bytes(c["public_key"]), {"contact": chat_hash("contact", ident)})
        C.check("contact_hidden", (r["ZHIDDEN"] or 0) == (1 if c["hidden"] else 0), {"contact": chat_hash("contact", ident)})
        C.check("contact_verification", r["ZVERIFICATIONLEVEL"] == max(0, min(2, c["verification"] or 0)),
                {"contact": chat_hash("contact", ident)})
    info["contacts_in_store"] = len(store_contact_rows)

    # ---- groups ---------------------------------------------------------------------------------------------------
    ngroups = {}
    store_groups = {(bytes(g).hex(), cr) for g, cr in st.q("SELECT ZGROUPID, ZGROUPCREATOR FROM ZGROUP")}
    conv_my = {pk: my for pk, my in st.q("SELECT Z_PK, ZGROUPMYIDENTITY FROM ZCONVERSATION WHERE ZGROUPID IS NOT NULL")}
    mtab = [r[0] for r in st.q("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z\\_%GROUPCONVERSATIONS' ESCAPE '\\'")]
    conv_members = collections.defaultdict(set)
    if mtab:
        cols_m = [r[1] for r in st.q(f"PRAGMA table_info({mtab[0]})")]
        c_member = next(c for c in cols_m if c.endswith("MEMBERS"))
        c_conv = next(c for c in cols_m if c.endswith("GROUPCONVERSATIONS"))
        for cpk, vpk in st.q(f"SELECT {c_member}, {c_conv} FROM {mtab[0]}"):
            conv_members[vpk].add(contact_ident.get(cpk))
    for g in nd.execute("SELECT * FROM groups"):
        ck = canon_group_key(g["group_key"], own_ids)
        ngroups[g["group_key"]] = (ck, g)
        idhex = bytes(g["group_id"]).hex()
        creator = None if ck.endswith("-ME") else g["creator"]
        creator_known = creator is None or creator in contact_pk
        pk = key_conv.get(("group", ck))
        if creator_known or ("group", ck) in base_groups:
            C.check("group_entity_present", (idhex, creator) in store_groups, {"chat": chat_hash("group", g["group_key"])})
        elif ("group", ck) not in base_groups:
            # review m2: no GroupEntity without a conversation when the creator contact is missing
            C.check("no_orphan_group_entity", (idhex, creator) not in store_groups, {"chat": chat_hash("group", g["group_key"])})
        if creator_known:
            if C.check("group_conversation_present", pk is not None, {"chat": chat_hash("group", g["group_key"])}):
                if ("group", ck) not in base_groups:
                    C.check("group_my_identity", conv_my.get(pk) == own, {"chat": chat_hash("group", g["group_key"])})
                    # review m3: members == members_json (minus me, only identities that exist as contacts)
                    want_m = {m for m in json.loads(g["members_json"] or "[]") if m not in own_ids and m in contact_pk}
                    C.check("group_members_equal_members_json", conv_members.get(pk, set()) == want_m,
                            {"chat": chat_hash("group", g["group_key"])})

    # ---- ballots --------------------------------------------------------------------------------------------------
    nballots = {r["ref_id"]: r for r in nd.execute("SELECT * FROM ballots")}
    store_ballots = {bytes(i): pk for pk, i in st.q("SELECT Z_PK, ZID FROM ZBALLOT")}
    ballot_pk_by_ref = {}
    base_ballot_ids = set()
    if a.store_in:
        bs2 = Store(a.store_in)
        base_ballot_ids = {bytes(i) for (i,) in bs2.q("SELECT ZID FROM ZBALLOT")}
        bs2.db.close()
    store_ballot_creator = {pk: cr for pk, cr in st.q("SELECT Z_PK, ZCREATORID FROM ZBALLOT")}
    for ref, b in nballots.items():
        if b["api_id"] is None or len(b["api_id"]) != 8:
            continue
        pk = store_ballots.get(bytes(b["api_id"]))
        ballot_pk_by_ref[ref] = pk
        if pk is not None and bytes(b["api_id"]) not in base_ballot_ids:
            # review m1: own polls carry the identity the import ran with (BallotEntity+Extension.swift:82)
            want_cr = own if b["creator"] in own_ids else b["creator"]
            C.check("ballot_creator", store_ballot_creator.get(pk) == want_cr, {"ballot_ref": ref})
    n_choices = collections.Counter(r[0] for r in nd.execute("SELECT ballot_ref FROM ballot_choices"))
    n_votes = collections.Counter(r[0] for r in nd.execute("SELECT ballot_ref FROM ballot_votes"))
    s_choices = collections.Counter(r[0] for r in st.q("SELECT ZBALLOT FROM ZBALLOTCHOICE"))
    s_votes = collections.Counter(r[0] for r in st.q(
        "SELECT c.ZBALLOT FROM ZBALLOTRESULT r JOIN ZBALLOTCHOICE c ON r.ZBALLOTCHOICE = c.Z_PK"))

    # ---- messages -------------------------------------------------------------------------------------------------
    marker_star = {pk: s for pk, s in st.q("SELECT Z_PK, ZSTAR FROM ZMESSAGEMARKERS")}
    filedata = {pk: blob for pk, blob in st.q("SELECT Z_PK, ZDATA FROM ZFILEDATA")}
    imagedata = {pk: (w, h) for pk, w, h in st.q("SELECT Z_PK, ZWIDTH, ZHEIGHT FROM ZIMAGEDATA")}
    E_TEXT, E_FILE, E_LOC = st.ent["TextMessage"], st.ent["FileMessage"], st.ent["LocationMessage"]
    E_SYS, E_BALLOT = st.ent["SystemMessage"], st.ent["BallotMessage"]
    kind_ent = {"text": E_TEXT, "file": E_FILE, "location": E_LOC, "ballot": E_BALLOT, "call": E_SYS, "group_status": E_SYS}

    exp_per_chat = collections.Counter()
    not_imported = collections.Counter()
    imported = {}   # (chat_key, msg_id) -> (row, normalized)
    seen_input = set()
    media_bytes = 0
    hashed = 0
    blob_refs = collections.Counter()
    touched_chats = set()

    group_creator_me = {ck for ck, _ in ngroups.values() if ck.endswith("-ME")}
    post_launch_tolerated = collections.Counter()

    for m in nd.execute("SELECT * FROM messages ORDER BY chat_kind, chat_key, created_ms, uid"):
        kind = m["kind"]
        uid = m["uid"]
        if m["chat_kind"] == "contact":
            if m["chat_key"] in own_ids:
                not_imported["chat_with_self"] += 1
                continue
            ck = ("contact", m["chat_key"])
            if m["chat_key"] not in contact_pk:
                not_imported["contact_missing"] += 1
                continue
        elif m["chat_kind"] == "group":
            ck = ("group", canon_group_key(m["chat_key"], own_ids))
            if ck not in key_conv:
                not_imported["group_conversation_missing"] += 1
                continue
        else:
            not_imported["unsupported_chat_kind"] += 1
            continue
        is_group = ck[0] == "group"
        mid = bytes(m["msg_id"])
        if len(mid) != 8:
            not_imported["bad_msg_id"] += 1
            continue
        if mid in base_ids.get(ck, ()):
            not_imported["duplicate_existing"] += 1
            continue
        if (ck, mid) in seen_input:
            not_imported["duplicate_in_input"] += 1
            continue
        # skip rules (docs/import-mapping.md)
        if kind == "legacy_status":
            not_imported["legacy_status"] += 1
            continue
        if kind not in kind_ent:
            not_imported["unsupported_kind"] += 1
            continue
        is_own = bool(m["is_own"])
        is_system = kind in ("call", "group_status")
        sender = None
        if is_group and not is_own and not is_system:
            s = m["sender"]
            if not s:
                not_imported["sender_unknown"] += 1
                continue
            if s in own_ids:
                is_own = True
            elif s not in contact_pk:
                not_imported["sender_unknown"] += 1
                continue
            else:
                sender = s
        date_ms = m["created_ms"] if m["created_ms"] is not None else (
            m["posted_ms"] if m["posted_ms"] is not None else m["modified_ms"])
        if date_ms is None:
            not_imported["no_date"] += 1
            continue
        deleted = m["deleted_ms"] is not None
        exp_sys = None
        if kind == "location" and not deleted and (m["loc_lat"] is None or m["loc_lon"] is None):
            not_imported["location_without_coordinates"] += 1
            continue
        if kind == "ballot" and ballot_pk_by_ref.get(m["ballot_ref"]) is None:
            not_imported["ballot_missing"] += 1
            continue
        if kind == "call":
            if is_group:
                not_imported["call_in_group"] += 1
                continue
            exp_sys = expected_call_type(m["call_status"], m["call_reason"], is_own)
            if exp_sys is None:
                not_imported["call_status_unknown"] += 1
                continue
        if kind == "group_status":
            if not is_group:
                not_imported["group_status_outside_group"] += 1
                continue
            gi = m["gstatus_identity"] or None
            exp_sys = expected_gstatus_type(m["gstatus_type"], gi in own_ids if gi else False, gi is not None,
                                            ck[1] in group_creator_me)
            if exp_sys is None:
                not_imported["group_status_no_ios_equivalent"] += 1
                continue
            is_own = True  # iOS creates group status system messages with isOwn = YES
        seen_input.add((ck, mid))
        exp_per_chat[ck] += 1
        touched_chats.add(ck)

        conv_pk = key_conv[ck]
        rows = by_key.get((conv_pk, mid), [])
        ex = {"uid": uid, "chat": chat_hash(*ck)}
        if not C.check("message_present_exactly_once", len(rows) == 1, dict(ex, found=len(rows))):
            continue
        r = rows[0]
        imported[(ck, mid)] = (r, m)
        C.check("entity", r["Z_ENT"] == kind_ent[kind], dict(ex, want=kind, got=E.get(r["Z_ENT"])))
        C.check("direction_isOwn", bool(r["ZISOWN"]) == is_own, ex)
        C.check("date_ms", ms_of(r["ZDATE"]) == date_ms, ex)
        want_rsd = m["posted_ms"] if m["posted_ms"] is not None else date_ms
        C.check("remoteSentDate_ms", ms_of(r["ZREMOTESENTDATE"]) == want_rsd, ex)
        C.check("sent_1", r["ZSENT"] == 1, ex)
        if a.post_launch and r["ZSENDFAILED"] and kind == "file" and r["ZBLOBID"] is None and not r["FDA"] \
                and (not is_own or a.probe):
            post_launch_tolerated["own_placeholder_blobError_probe" if is_own else "incoming_placeholder_blobError"] += 1
        else:
            C.check("sendFailed_0", not r["ZSENDFAILED"], ex)
        C.check("userack_0", not r["ZUSERACK"], ex)
        if not is_own:
            C.check("incoming_read_1", r["ZREAD"] == 1, ex)
            C.check("incoming_delivered_1", r["ZDELIVERED"] == 1, ex)
            want_rd = m["read_ms"] if m["read_ms"] is not None else date_ms
            C.check("incoming_readDate_ms", ms_of(r["ZREADDATE"]) == want_rd, ex)
        else:
            stt = (m["state"] or "").upper()
            if stt in ("READ", "CONSUMED") or m["read_ms"] is not None:
                C.check("own_read_state", r["ZREAD"] == 1, ex)
            if stt in ("DELIVERED", "READ", "CONSUMED", "USERACK", "USERDEC") or m["delivered_ms"] is not None:
                C.check("own_delivered_state", r["ZDELIVERED"] == 1, ex)
        want_sender_pk = contact_pk.get(sender) if sender else None
        C.check("sender", r["ZSENDER"] == want_sender_pk, ex)
        C.check("lastEditedAt_ms", ms_of(r["ZLASTEDITEDAT"]) == m["edited_ms"], ex)
        C.check("deletedAt_ms", ms_of(r["ZDELETEDAT"]) == m["deleted_ms"], ex)
        star = marker_star.get(r["ZMESSAGEMARKERS"]) if r["ZMESSAGEMARKERS"] is not None else None
        C.check("starred", bool(star) == bool(m["starred"]), ex)

        if kind == "text":
            C.check("text_equal", r["ZTEXT"] == ("" if deleted else (m["text"] or "")), ex)
            q = m["quoted_api_id"]
            want_q = bytes(q) if q is not None and len(q) == 8 else None
            got_q = bytes(r["ZQUOTEDMESSAGEID"]) if r["ZQUOTEDMESSAGEID"] is not None else None
            C.check("quote_id", got_q == want_q, ex)
        elif kind == "location":
            if deleted:
                C.check("location_deleted_cleared", r["ZLATITUDE"] == 0 and r["ZLONGITUDE"] == 0 and r["ZPOINAME"] is None, ex)
            else:
                C.check("location_lat_lon", r["ZLATITUDE"] == m["loc_lat"] and r["ZLONGITUDE"] == m["loc_lon"], ex)
                C.check("location_accuracy", r["ZACCURACY"] == (m["loc_acc"] if m["loc_acc"] is not None else 0), ex)
                C.check("location_poi", r["ZPOINAME"] == m["loc_name"] and r["ZPOIADDRESS"] == m["loc_address"], ex)
        elif kind in ("call", "group_status"):
            C.check("system_type", r["STYPE"] == exp_sys, dict(ex, want=exp_sys, got=r["STYPE"]))
            if kind == "call":
                try:
                    arg = json.loads(bytes(r["ZARG"]).decode())
                    ok = arg.get("CallInitiator") == bool(m["is_own"]) and "DateString" in arg
                    if m["call_status"] == 2 and (m["call_duration_s"] or 0) > 0:
                        ok = ok and "CallTime" in arg
                except Exception:
                    ok = False
                C.check("call_arg_json", ok, ex)
        elif kind == "ballot":
            C.check("ballot_link", r["ZBALLOT"] == ballot_pk_by_ref.get(m["ballot_ref"]), ex)
            C.check("ballot_state", (r["ZBALLOTSTATE"] or 0) == (1 if m["ballot_data_type"] == 3 else 0), ex)
        elif kind == "file":
            fda = r["FDA"]
            if deleted:
                C.check("file_deleted_cleared", fda == 0 and r["ZDATA"] is None and r["ZBLOBID"] is None
                        and r["FKEY"] is None and (r["ZMIMETYPE"] or "") == "", ex)
                continue
            want_mime = (m["file_mime"] or "application/octet-stream").lower() or "application/octet-stream"
            C.check("file_mime", r["ZMIMETYPE"] == want_mime, ex)
            C.check("file_name", r["ZFILENAME"] == m["file_name"], ex)
            C.check("file_type_render", r["FTYPE"] == max(0, min(2, m["file_render"] or 0)), ex)
            C.check("file_caption", (r["ZCAPTION"] or None) == (m["file_caption"] or None), ex)
            C.check("file_key_32", r["FKEY"] is not None and len(r["FKEY"]) == 32, ex)
            media = os.path.join(a.work_dir, m["media_path"]) if m["media_path"] else None
            has_media = bool(media and os.path.isfile(media))
            if has_media and not fda and r["ZDATA"] is None and m["media_sha256"] and \
                    sha256_file(media) != m["media_sha256"].lower():
                # importer rule: a media file whose sha256 differs from normalized is not imported (placeholder)
                has_media = False
                not_imported["media_sha256_mismatch_placeholder"] += 1
            if has_media:
                size = os.path.getsize(media)
                C.check("file_size", r["ZFILESIZE"] == min(size, 2**31 - 1), dict(ex, want=size, got=r["ZFILESIZE"]))
                C.check("file_dataAvailable_1", fda == 1, ex)
                C.check("file_blobId_set", r["ZBLOBID"] is not None and len(r["ZBLOBID"]) == 16, ex)
                blob = filedata.get(r["ZDATA"])
                kind_ref, ref = st.external_blob(blob)
                blob_refs[kind_ref or "none"] += 1
                if C.check("filedata_present", kind_ref in ("inline", "external"), ex):
                    if kind_ref == "external":
                        if not C.check("filedata_external_file_exists", os.path.isfile(ref), ex):
                            continue
                        got_size = os.path.getsize(ref)
                    else:
                        got_size = len(ref)
                    C.check("filedata_size", got_size == size, ex)
                    media_bytes += got_size
                    if not a.no_hash:
                        got = sha256_file(ref) if kind_ref == "external" else hashlib.sha256(ref).hexdigest()
                        hashed += 1
                        C.check("filedata_sha256_vs_normalized", got == (m["media_sha256"] or "").lower(), ex)
            else:
                want_size = m["file_size"]
                C.check("file_size", r["ZFILESIZE"] == (None if want_size is None else min(want_size, 2**31 - 1)),
                        dict(ex, want=want_size, got=r["ZFILESIZE"]))
                C.check("placeholder_dataAvailable_0", fda == 0 and r["ZDATA"] is None, ex)
                C.check("placeholder_blobId_nil", r["ZBLOBID"] is None, ex)
            if m["thumb_path"] and os.path.isfile(os.path.join(a.work_dir, m["thumb_path"])):
                C.check("thumbnail_from_android_present", r["FTHUMB"] is not None, ex)
            if r["FTHUMB"] is not None:
                w, h = imagedata.get(r["FTHUMB"], (0, 0))
                C.check("thumbnail_dimensions", (w or 0) > 0 and (h or 0) > 0, ex)
            if is_own:
                C.check("own_thumb_blob_state", (r["FTHUMB"] is None) == (r["ZBLOBTHUMBNAILID"] is None), ex)
            try:
                j = json.loads(r["ZJSON"] or "")
                x = j.get("x") or {}
                ok = all(isinstance(x.get(k), int) for k in ("w", "h") if k in x) and \
                    (("d" not in x) or isinstance(x["d"], (int, float)))
                ok = ok and j.get("m") == want_mime
            except Exception:
                ok = False
            C.check("file_json_decodable_like_ios", ok, ex)

    info["not_imported_by_rule"] = dict(not_imported)
    if a.post_launch:
        info["post_launch_tolerated"] = dict(post_launch_tolerated)
    info["expected_imported_messages"] = sum(exp_per_chat.values())
    info["media"] = {"bytes_checked": media_bytes, "sha256_checked": hashed, "blob_refs": dict(blob_refs)}

    # ---- per-chat counts ------------------------------------------------------------------------------------------
    for ck in set(exp_per_chat) | set(base_count):
        pk = key_conv.get(ck)
        got = per_conv.get(pk, 0) if pk is not None else 0
        want = exp_per_chat.get(ck, 0) + base_count.get(ck, 0)
        C.check("per_chat_message_count", got == want, {"chat": chat_hash(*ck), "want": want, "got": got})
    extra_convs = [pk for pk in per_conv if pk not in conv_key or conv_key[pk] not in (set(exp_per_chat) | set(base_count))]
    C.check("no_unexpected_messages", not extra_convs, {"conversations": len(extra_convs)})
    info["chats_with_imported_messages"] = len(exp_per_chat)

    # ---- ballots (only ballots referenced by imported chats) -----------------------------------------------------
    for ref, pk in ballot_pk_by_ref.items():
        b = nballots[ref]
        if pk is None:
            continue
        C.check("ballot_choice_count", s_choices.get(pk, 0) == n_choices.get(ref, 0), {"ballot_ref": ref})
        C.check("ballot_vote_count", s_votes.get(pk, 0) == n_votes.get(ref, 0), {"ballot_ref": ref})

    # ---- reactions ------------------------------------------------------------------------------------------------
    msg_pk_to_key = {}
    for (ck, mid), (r, m) in imported.items():
        msg_pk_to_key[r["Z_PK"]] = (ck, mid)
    want_rx = {}
    rx_skip = collections.Counter()
    for rx in nd.execute("SELECT * FROM reactions"):
        if rx["chat_kind"] == "contact":
            ck = ("contact", rx["chat_key"])
        else:
            ck = ("group", canon_group_key(rx["chat_key"], own_ids))
        tgt = bytes(rx["target_msg_id"]) if rx["target_msg_id"] is not None else None
        hit = imported.get((ck, tgt))
        if hit is None:
            rx_skip["target_not_imported_in_this_run"] += 1
            continue
        r, m = hit
        if r["Z_ENT"] == E_SYS or r["ZDELETEDAT"] is not None:
            rx_skip["target_not_reactable"] += 1
            continue
        s = rx["sender"] or None
        creator = None if (s is None or s in own_ids) else s
        if creator is not None and creator not in contact_pk:
            rx_skip["reactor_unknown"] += 1
            continue
        if creator is not None and ck[0] == "contact" and creator != ck[1]:
            rx_skip["reactor_not_in_chat"] += 1
            continue
        if not rx["emoji"]:
            rx_skip["empty_emoji"] += 1
            continue
        k = (ck, tgt, creator, rx["emoji"])
        if k in want_rx:
            rx_skip["duplicate"] += 1
            continue
        want_rx[k] = rx["reacted_ms"] if rx["reacted_ms"] is not None else ms_of(r["ZDATE"])
    got_rx = {}
    for mpk, cpk, emoji, d in st.q("SELECT ZMESSAGE, ZCREATOR, ZREACTION, ZDATE FROM ZMESSAGEREACTION"):
        key = msg_pk_to_key.get(mpk)
        if key is None:
            continue  # reaction on a pre-existing message: not ours
        k = (key[0], key[1], contact_ident.get(cpk) if cpk is not None else None, emoji)
        C.check("reaction_unique", k not in got_rx, {"chat": chat_hash(*key[0])})
        got_rx[k] = ms_of(d)
    missing = set(want_rx) - set(got_rx)
    extra = set(got_rx) - set(want_rx)
    C.check("reactions_missing", not missing, {"count": len(missing)})
    C.check("reactions_unexpected", not extra, {"count": len(extra)})
    for k in set(want_rx) & set(got_rx):
        C.check("reaction_date_ms", want_rx[k] == got_rx[k], {"chat": chat_hash(*k[0])})
    info["reactions"] = {"expected": len(want_rx), "found": len(got_rx), "not_imported_by_rule": dict(rx_skip)}

    # ---- conversations --------------------------------------------------------------------------------------------
    touched_pks = {key_conv[ck] for ck in touched_chats}
    lm_conv = {pk: c for pk, c in st.q("SELECT Z_PK, ZCONVERSATION FROM ZMESSAGE")}
    listed = 0
    for pk, lu, lm, unread, vis in st.q("SELECT Z_PK, ZLASTUPDATE, ZLASTMESSAGE, ZUNREADMESSAGECOUNT, ZVISIBILITY FROM ZCONVERSATION"):
        k = conv_key.get(pk, ("?", str(pk)))
        ex = {"chat": chat_hash(*k)}
        n = per_conv.get(pk, 0)
        if n > 0:
            C.check("conversation_with_messages_has_lastUpdate", lu is not None, ex)
            C.check("conversation_with_messages_has_lastMessage", lm is not None, ex)
        if lm is not None:
            C.check("lastMessage_in_conversation", lm_conv.get(lm) == pk, ex)
        if lu is not None:
            listed += 1
        if pk in touched_pks:
            b = base_conv_state.get(k)
            # imported messages never add unread; a pre-existing chat keeps its own (iPhone) unread count
            want_un = (b[2] or 0) if b else 0
            # after a launch the user may have READ pre-existing unread messages (count can only go down)
            C.check("unreadMessageCount_0", (unread or 0) <= want_un if a.post_launch else unread == want_un, ex)
            if b and b[0] is not None and lu is not None:
                C.check("existing_lastUpdate_not_lowered", lu >= b[0], ex)
    info["conversations"] = {"total": len(conv_key), "listed": listed, "touched": len(touched_pks)}
    unread_in = st.q("SELECT COUNT(*) FROM ZMESSAGE WHERE ZISOWN = 0 AND ZREAD = 0")[0][0]
    info["incoming_unread_total"] = unread_in
    own_unsent = st.q("SELECT COUNT(*) FROM ZMESSAGE WHERE ZISOWN = 1 AND ZSENT = 0")[0][0]
    baseline_has_messages = sum(base_count.values()) > 0
    C.check("no_own_unsent_messages", own_unsent == 0 or baseline_has_messages, {"count": own_unsent})

    # ---- nonces ---------------------------------------------------------------------------------------------------
    if not overridden:
        store_nonces = {bytes(n) for (n,) in st.q("SELECT ZNONCE FROM ZNONCE")}
        nn = [bytes(h) for (h,) in nd.execute("SELECT hash FROM nonces") if h is not None and len(h) == 32]
        miss = sum(1 for h in nn if h not in store_nonces)
        C.check("nonces_present", miss == 0, {"missing": miss})
        info["nonces"] = {"normalized": len(nn), "store": len(store_nonces)}

    # ---- Core Data re-open without migration -----------------------------------------------------------------------
    if not a.no_coredata:
        p = subprocess.run([a.importer, "verify", "--store", a.store, "--momd", a.momd], capture_output=True, text=True,
                           timeout=600)
        ok = p.returncode == 0
        C.check("coredata_reopen_without_migration", ok, {"rc": p.returncode, "stderr": p.stderr[-300:]})
        if ok:
            v = json.loads(p.stdout)
            C.check("coredata_model_compatible", v.get("model_compatible") is True)
            inv = v.get("invariants", {})
            for k2, n in inv.items():
                if k2 in ("conversation_listed",):
                    continue
                if baseline_has_messages and k2 in ("own_not_sent", "send_failed", "incoming_unread", "incoming_not_delivered",
                                         "conversation_unread_nonzero", "conversation_group_without_myIdentity"):
                    continue  # pre-existing iPhone state may legitimately have these
                C.check(f"coredata_invariant_{k2}", n == 0, {"count": n})
            info["coredata_counts"] = v.get("counts")
            info["coredata_version_identifiers"] = v.get("store_version_identifiers")

    # ---- report ---------------------------------------------------------------------------------------------------
    total_fail = sum(C.fail.values())
    rep = collections.OrderedDict()
    rep["result"] = "PASS" if total_fail == 0 else "FAIL"
    rep["store"] = os.path.abspath(a.store)
    rep["duration_s"] = round(time.time() - t0, 1)
    rep["own_identity_overridden"] = overridden
    rep["failures"] = dict(C.fail)
    rep["checks_passed"] = dict(sorted(C.ok.items()))
    rep["examples"] = {k: v for k, v in C.examples.items() if k in C.fail}
    rep.update(info)
    out = json.dumps(rep, indent=1, default=str)
    if a.report:
        with open(a.report, "w") as f:
            f.write(out + "\n")
    if not quiet:
        print(json.dumps({"result": rep["result"], "failures": rep["failures"], "checks": sum(C.ok.values()),
                          "expected_imported_messages": info["expected_imported_messages"],
                          "not_imported_by_rule": info["not_imported_by_rule"], "duration_s": rep["duration_s"]},
                         indent=1))
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
