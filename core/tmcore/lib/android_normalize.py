#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
android_normalize.py - Threema Android data backup (format v27, AES zip) -> normalized.sqlite

Reads an Android *text* backup (contacts, groups, chats, reactions, polls, nonces, avatars) and
optionally one or more *media* backups (media + thumbnails keyed by message uid), and writes

  <out-dir>/normalized.sqlite       exactly the NORMALIZED CONTRACT (see SCHEMA below)
  <out-dir>/media/<uid>             plaintext media file of a FILE message
  <out-dir>/thumbs/<uid>            thumbnail (JPEG/PNG) of a FILE message
  <out-dir>/avatars/<name>          contact / group / own avatars (from the text backup)
  <out-dir>/normalize-report.json   counts, unresolved references, own-identity detection, media coverage,
                                    skipped rows with reason. Contains NO bodies, names or identities
                                    (chats are labelled by a short sha256 of "<kind>:<key>").

Spec: the Threema for Android backup format v27 (BackupService.java / RestoreService.java of the AGPL Android
sources, implemented independently; no code copied).

In-process API (tmcore step android-normalize):
  report = normalize(text_backup, media_backups, passwords={path: pw, ...}, out_dir=DIR, jobs=N,
                     log=fn, progress=fn(done, total))
Maintainer CLI (the password is ONE line on stdin -- never argv, never a file):
  python3 -m tmcore.lib.android_normalize --text-backup PATH [--media-backup PATH ...] --out-dir DIR \
      [--no-media] [--own-identity ID] [--jobs N] < password-line

Secrets: passwords are only ever held in memory; every backup file may have its own password. They are never
printed, logged or written. Progress output contains counts only. Errors are NormalizeError (a SystemExit
subclass, so the CLI behaves as before) with a machine-readable `kind`.

Media extraction is streamed per entry (never extracts the whole zip), written to <file>.part and
renamed, and is resumable: an existing file with the right size is not extracted again (its sha256 is
taken from <out-dir>/.extract-manifest.jsonl when size+mtime match, otherwise re-hashed).
"""
import argparse
import collections
import concurrent.futures
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time

import pyzipper

csv.field_size_limit(sys.maxsize)

NORMALIZED_FORMAT_VERSION = "1"
MAX_SUPPORTED_BACKUP_VERSION = 27

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE contacts(identity TEXT PRIMARY KEY, public_key BLOB NOT NULL, verification INTEGER, first_name TEXT,
  last_name TEXT, nickname TEXT, hidden INTEGER, archived INTEGER, last_update_ms INTEGER, avatar_user_path TEXT,
  avatar_contact_path TEXT);
CREATE TABLE "groups"(group_key TEXT PRIMARY KEY, group_id BLOB NOT NULL, creator TEXT NOT NULL, is_mine INTEGER,
  name TEXT, created_ms INTEGER, last_update_ms INTEGER, archived INTEGER, user_state INTEGER, members_json TEXT,
  avatar_path TEXT, description TEXT, description_ms INTEGER);
CREATE TABLE messages(uid TEXT PRIMARY KEY, chat_kind TEXT, chat_key TEXT, api_id BLOB, msg_id BLOB NOT NULL,
  is_own INTEGER, sender TEXT, kind TEXT, created_ms INTEGER, posted_ms INTEGER, delivered_ms INTEGER,
  read_ms INTEGER, modified_ms INTEGER, edited_ms INTEGER, deleted_ms INTEGER, state TEXT, is_read INTEGER,
  starred INTEGER, text TEXT, quoted_api_id BLOB,
  file_mime TEXT, file_name TEXT, file_size INTEGER, file_render INTEGER, file_caption TEXT, file_blob_id BLOB,
  file_key BLOB, file_thumb_mime TEXT, file_meta_json TEXT, media_path TEXT, media_sha256 TEXT, thumb_path TEXT,
  loc_lat REAL, loc_lon REAL, loc_acc REAL, loc_name TEXT, loc_address TEXT,
  ballot_ref INTEGER, ballot_data_type INTEGER,
  call_status INTEGER, call_reason INTEGER, call_duration_s INTEGER, call_id INTEGER,
  gstatus_type INTEGER, gstatus_identity TEXT, gstatus_name TEXT,
  raw_type TEXT, raw_body TEXT);
CREATE TABLE reactions(chat_kind TEXT, chat_key TEXT, target_msg_id BLOB, sender TEXT, emoji TEXT,
  reacted_ms INTEGER, source TEXT);
CREATE TABLE ballots(ref_id INTEGER PRIMARY KEY, api_id BLOB, creator TEXT, chat_kind TEXT, chat_key TEXT,
  title TEXT, state TEXT, assessment TEXT, btype TEXT, choice_type TEXT, created_ms INTEGER, modified_ms INTEGER);
CREATE TABLE ballot_choices(ballot_ref INTEGER, choice_id INTEGER, name TEXT, order_pos INTEGER, vote_count INTEGER,
  created_ms INTEGER, modified_ms INTEGER);
CREATE TABLE ballot_votes(ballot_ref INTEGER, choice_id INTEGER, identity TEXT, choice INTEGER, created_ms INTEGER,
  modified_ms INTEGER);
CREATE TABLE nonces(kind TEXT, hash BLOB);
CREATE INDEX idx_messages_chat ON messages(chat_kind, chat_key, created_ms);
CREATE INDEX idx_messages_msgid ON messages(chat_kind, chat_key, msg_id);
CREATE INDEX idx_reactions_target ON reactions(chat_kind, chat_key, target_msg_id);
"""

MESSAGE_COLUMNS = [
    "uid", "chat_kind", "chat_key", "api_id", "msg_id", "is_own", "sender", "kind", "created_ms", "posted_ms",
    "delivered_ms", "read_ms", "modified_ms", "edited_ms", "deleted_ms", "state", "is_read", "starred", "text",
    "quoted_api_id", "file_mime", "file_name", "file_size", "file_render", "file_caption", "file_blob_id",
    "file_key", "file_thumb_mime", "file_meta_json", "media_path", "media_sha256", "thumb_path",
    "loc_lat", "loc_lon", "loc_acc", "loc_name", "loc_address", "ballot_ref", "ballot_data_type",
    "call_status", "call_reason", "call_duration_s", "call_id", "gstatus_type", "gstatus_identity",
    "gstatus_name", "raw_type", "raw_body",
]

IDENTITY_RE = re.compile(r"^[A-Z0-9*][A-Z0-9]{7}$")
HEX_RE = re.compile(r"^(?:[0-9a-fA-F]{2})*$")
BACKUP_TS_RE = re.compile(r"threema-backup_(\d{10,14})_")
QUOTE_V1_RE = re.compile(r"(?s)\A> ([A-Z0-9*]{8}): ")

THUMBS_UP = "\U0001F44D"    # EmojiUtil.THUMBS_UP_SEQUENCE  (Android app/.../emojis/EmojiUtil.java:19)
THUMBS_DOWN = "\U0001F44E"  # EmojiUtil.THUMBS_DOWN_SEQUENCE (EmojiUtil.java:20)
LEGACY_ACK = {"USERACK": THUMBS_UP, "USERDEC": THUMBS_DOWN}

VERIFICATION = {"UNVERIFIED": 0, "SERVER_VERIFIED": 1, "FULLY_VERIFIED": 2}

# message file prefix, media prefix, thumbnail prefix per chat kind (Tags.java)
MEDIA_PREFIX = {"contact": ("message_media_", "message_thumbnail_"),
                "group": ("group_message_media_", "group_message_thumbnail_"),
                "dlist": ("distribution_list_message_media_", "distribution_list_thumbnail_")}


# ----------------------------------------------------------------------------------------------------------------
# small helpers

def unescape(v):
    """CSVRow.escape() doubles every backslash before opencsv quoting; opencsv's reader (escape char '\\')
    undoes it. Python's csv module does not, so do it here (non-overlapping, left to right)."""
    return v.replace("\\\\", "\\")


def parse_csv_bytes(raw, header=True):
    text = raw.decode("utf-8")
    rows = [[unescape(c) for c in r] for r in csv.reader(io.StringIO(text, newline=""))]
    if not header:
        return None, rows
    if not rows:
        return [], []
    return rows[0], rows[1:]


def to_int(s):
    if s is None:
        return None
    if isinstance(s, bool):
        return int(s)
    if isinstance(s, (int, float)):
        return int(s)
    s = s.strip()
    if s == "":
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def to_float(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def hex_bytes(s, n=None):
    if not isinstance(s, str) or s == "" or not HEX_RE.match(s):
        return None
    b = bytes.fromhex(s)
    if n is not None and len(b) != n:
        return None
    return b


def blank_to_none(s):
    if s is None:
        return None
    if isinstance(s, str) and s.strip() == "":
        return None
    return s


def chat_ref(kind, key):
    return kind + ":" + hashlib.sha256(f"{kind}:{key}".encode()).hexdigest()[:10]


def safe_name(s):
    # identities are [A-Z0-9*]; '*' (gateway IDs) is replaced so file names stay shell-friendly
    return s.replace("*", "_").replace("/", "_")


def backup_ts_ms(path):
    m = BACKUP_TS_RE.search(os.path.basename(path))
    return int(m.group(1)) if m else None


class NormalizeError(SystemExit):
    """A refusal with a machine-readable kind: password, no_settings, format_new, own_identity, no_media_password.
    The message never contains a password (it may name the backup FILE, so callers must not log it)."""

    def __init__(self, kind, message, **info):
        super().__init__(message)
        self.kind = kind
        self.info = info


def password_candidates(pw):
    """In-memory password -> candidates (as typed, and without surrounding whitespace)."""
    cands = []
    if isinstance(pw, str):
        for c in (pw.rstrip("\r\n"), pw.strip()):
            if c and c not in cands:
                cands.append(c)
    return cands


def _candidates_for(pw_candidates, path):
    """pw_candidates: a list (same candidates for every zip) or a dict {zip path: password or [candidates]}."""
    if isinstance(pw_candidates, dict):
        v = pw_candidates.get(path)
        return password_candidates(v) if isinstance(v, str) else list(v or [])
    return list(pw_candidates or [])


def open_zip(path, pw_candidates):
    """Open an AES zip and return (zipfile, password_bytes). Verifies the password on the smallest
    encrypted entry. Never prints the password."""
    z = pyzipper.AESZipFile(path)
    infos = [i for i in z.infolist() if not i.is_dir()]
    if not infos:
        return z, None
    probe = next((i for i in infos if i.filename == "settings"), None) or min(infos, key=lambda i: i.file_size)
    for pw in _candidates_for(pw_candidates, path):
        pwb = pw.encode("utf-8")
        try:
            z.setpassword(pwb)
            z.read(probe)
            return z, pwb
        except Exception:
            continue
    z.close()
    raise NormalizeError("password", f"password does not decrypt {os.path.basename(path)}", path=path)


def read_format_version(path, pw_candidates):
    """Format version from the 'settings' entry of a backup (needs the password). None if there is none."""
    z, _ = open_zip(path, pw_candidates)
    try:
        if "settings" not in z.namelist():
            return None
        _, rows = parse_csv_bytes(z.read("settings"), header=False)
        for r in rows:
            if len(r) >= 2 and r[0] == "version":
                return to_int(r[1])
        return None
    finally:
        z.close()


class Report:
    def __init__(self):
        self.data = collections.OrderedDict()
        self.skipped = collections.Counter()
        self.anomalies = collections.Counter()

    def skip(self, reason, n=1):
        self.skipped[reason] += n

    def anomaly(self, what, n=1):
        self.anomalies[what] += n


# ----------------------------------------------------------------------------------------------------------------
# text backup parsing

class TextBackup:
    def __init__(self, z):
        self.z = z
        self.names = [i.filename for i in z.infolist()]
        self.nameset = set(self.names)

    def has(self, name):
        return name in self.nameset

    def read(self, name):
        return self.z.read(name)

    def csv_dicts(self, name, rep):
        """Rows as dicts keyed by header name (Android looks columns up by name, so do we)."""
        if not self.has(name):
            return None, []
        header, rows = parse_csv_bytes(self.read(name))
        out = []
        for r in rows:
            if not r or (len(r) == 1 and r[0] == "" and len(header) > 1):
                continue  # blank line (a single-column Java-null row is written as an empty line)
            if len(r) != len(header):
                rep.skip(f"csv_field_count_mismatch:{name.split('_')[0]}")
                continue
            out.append(dict(zip(header, r)))
        return header, out


def legacy_to_file_array(t, body):
    """LegacyMessageBodyTransformer.kt: IMAGE / VIDEO / VOICEMESSAGE bodies -> FILE 10-element array."""
    arr = json.loads(body)
    if t == "IMAGE":
        # [isDownloaded, keyHex, blobIdHex, nonceHex]
        dl, key, blob = bool(arr[0]), arr[1], arr[2]
        nonce = arr[3] if len(arr) > 3 else None
        meta = {"_legacy_nonce": nonce} if nonce else {}
        return [blob, key, "image/jpeg", 0, None, 1, dl, None, None, meta]
    if t == "VIDEO":
        # [durationSec, isDownloaded, keyHex, blobIdHex, size?]
        dur, dl, key, blob = int(arr[0]), bool(arr[1]), arr[2], arr[3]
        size = int(arr[4]) if len(arr) > 4 and arr[4] is not None else 0
        return [blob, key, "video/mpeg", size, None, 1, dl, None, None, {"d": dur}]
    if t == "VOICEMESSAGE":
        # [durationSec, isDownloaded, keyHex, blobIdHex]
        dur, dl, key, blob = int(arr[0]), bool(arr[1]), arr[2], arr[3]
        return [blob, key, "audio/aac", 0, None, 1, dl, None, None, {"d": dur}]
    raise ValueError(t)


def parse_file_array(arr):
    """FileDataModelSerializer.deserializeFileDataBody: [blobId, key, mime, size, name, render, isDownloaded,
    caption, thumbMime, meta]. Very old bodies lack render type / thumb mime / meta."""
    if not isinstance(arr, list) or len(arr) < 5:
        raise ValueError("file body is not an array of >=5 elements")
    a = list(arr)
    if len(a) > 5 and isinstance(a[5], bool):
        a.insert(5, 0)  # very old model without rendering type
    a += [None] * (10 - len(a))
    meta = a[9] if isinstance(a[9], dict) else {}
    return {
        "blob": a[0], "key": a[1], "mime": a[2], "size": to_int(a[3]) if a[3] is not None else None,
        "name": a[4], "render": to_int(a[5]) if a[5] is not None else 0, "downloaded": bool(a[6]),
        "caption": a[7], "thumb_mime": a[8], "meta": meta,
    }


def detect_own_identity(contacts_ids, contact_reactions, group_reactions, groups_rows, ballots, votes,
                        gmsg_states_keys, override, rep):
    """Own identity without decrypting the `identity` entry. Every signal = identities that appear in a
    user-related position but are not in contacts.csv (BackupService skips the user's own identity)."""
    signals = collections.OrderedDict()
    s = collections.Counter()
    for r in contact_reactions:
        snd, partner = r.get("sender_identity") or "", r.get("identity") or ""
        if snd and snd != partner:
            s[snd] += 1
    signals["contact_reactions.sender_not_partner"] = s
    s = collections.Counter()
    for r in group_reactions:
        snd = r.get("sender_identity") or ""
        if snd and snd not in contacts_ids:
            s[snd] += 1
    signals["group_reactions.sender_not_contact"] = s
    s = collections.Counter()
    for g in groups_rows:
        for m in set([x for x in (g.get("members") or "").split(";") if x] + [g.get("creator") or ""]):
            if m and m not in contacts_ids:
                s[m] += 1
    signals["groups.members_or_creator_not_contact"] = s
    s = collections.Counter()
    for b in ballots:
        c = b.get("creator") or ""
        if c and c not in contacts_ids:
            s[c] += 1
    for v in votes:
        c = v.get("identity") or ""
        if c and c not in contacts_ids:
            s[c] += 1
    signals["ballots.creator_or_voter_not_contact"] = s
    s = collections.Counter()
    for k in gmsg_states_keys:
        if k and k not in contacts_ids:
            s[k] += 1
    signals["g_msg_states.key_not_contact"] = s

    summary = collections.OrderedDict()
    candidate, method = None, None
    primary = signals["contact_reactions.sender_not_partner"]
    if len(primary) == 1:
        candidate = next(iter(primary))
        method = "contact_reactions.sender_identity != chat partner (unique)"
    else:
        # fall back: identity present in the most signals, then highest total count
        score = collections.Counter()
        for name, c in signals.items():
            for ident in c:
                score[ident] += 1
        if score:
            best = sorted(score.items(), key=lambda kv: (-kv[1], -sum(c[kv[0]] for c in signals.values())))
            if len(best) == 1 or best[0][1] > best[1][1]:
                candidate = best[0][0]
                method = "most signals agree (contact_reactions not unique: %d candidates)" % len(primary)
    conflicts = 0
    for name, c in signals.items():
        entry = {"rows": sum(c.values()), "distinct_candidates": len(c)}
        if candidate is not None and c:
            entry["contains_detected"] = candidate in c
            entry["other_candidates"] = len([k for k in c if k != candidate])
            if candidate not in c:
                conflicts += 1
        summary[name] = entry
    detected = candidate
    result = {"signals": summary, "detected": detected is not None, "method": method,
              "detected_in_contacts_csv": bool(detected and detected in contacts_ids),
              "signal_conflicts": conflicts}
    if override:
        result["override_used"] = True
        result["override_matches_detected"] = (override == detected) if detected else None
        own = override
        result["method"] = "--own-identity override" + (" (agrees with detection)" if override == detected else
                                                        " (DISAGREES with detection)" if detected else
                                                        " (nothing detected)")
    else:
        own = detected
    if own is not None and not IDENTITY_RE.match(own):
        raise NormalizeError("own_identity", "own identity has an invalid format")
    if own is None:
        raise NormalizeError("own_identity", "could not detect own identity; pass --own-identity")
    if own in contacts_ids:
        rep.anomaly("own_identity_listed_in_contacts_csv")
    return own, result


def normalize_text_backup(tb, own_override, rep, media_cutoff_ms):
    """Parse the text backup into plain python structures (contract rows)."""
    # settings
    _, srows = parse_csv_bytes(tb.read("settings"), header=False) if tb.has("settings") else (None, [])
    version = None
    for r in srows:
        if len(r) >= 2 and r[0] == "version":
            version = to_int(r[1])
    if version is None:
        raise NormalizeError("no_settings", "backup has no settings/version")
    if version > MAX_SUPPORTED_BACKUP_VERSION:
        raise NormalizeError("format_new", f"backup format version {version} > {MAX_SUPPORTED_BACKUP_VERSION} "
                             "(unsupported)", format_version=version)
    rep.data["android_backup_version"] = version

    # --- contacts
    _, crows = tb.csv_dicts("contacts.csv", rep)
    # --- groups
    _, grows = tb.csv_dicts("groups.csv", rep)
    # --- reactions / ballots (needed for own-identity detection)
    _, contact_reactions = tb.csv_dicts("contact_reactions.csv", rep)
    _, group_reactions = tb.csv_dicts("group_reactions.csv", rep)
    _, ballots_rows = tb.csv_dicts("ballot.csv", rep)
    _, choice_rows = tb.csv_dicts("ballot_choice.csv", rep)
    _, vote_rows = tb.csv_dicts("ballot_vote.csv", rep)

    # --- message files (read now; g_msg_states keys are an own-identity signal)
    msg_files = []  # (chat_kind, file_key, name)
    for n in tb.names:
        if not n.endswith(".csv"):
            continue
        if n.startswith("distribution_list_message_"):
            msg_files.append(("dlist", n[len("distribution_list_message_"):-4], n))
        elif n.startswith("group_message_"):
            msg_files.append(("group", n[len("group_message_"):-4], n))
        elif n.startswith("message_"):
            msg_files.append(("contact", n[len("message_"):-4], n))
    parsed_files = []
    gms_keys = []
    for kind, fkey, n in msg_files:
        header, rows = tb.csv_dicts(n, rep)
        parsed_files.append((kind, fkey, n, header, rows))
        if kind == "group":
            for r in rows:
                v = r.get("g_msg_states")
                if v:
                    try:
                        j = json.loads(v)
                        if isinstance(j, dict):
                            gms_keys.extend(j.keys())
                    except ValueError:
                        pass

    contact_ids_all = {r.get("identity") for r in crows}
    own, own_info = detect_own_identity(contact_ids_all, contact_reactions, group_reactions, grows, ballots_rows,
                                        vote_rows, gms_keys, own_override, rep)
    rep.data["own_identity_detection"] = own_info

    # --- contacts -> contract rows
    contacts = collections.OrderedDict()
    cid_to_identity = {}
    for r in crows:
        ident = r.get("identity") or ""
        if not IDENTITY_RE.match(ident):
            rep.skip("contact_invalid_identity")
            continue
        cid = r.get("identity_id") if version >= 19 and r.get("identity_id") else ident
        cid_to_identity[cid] = ident
        if ident == own:
            rep.skip("contact_is_own_identity")
            continue
        pk = hex_bytes(r.get("publickey"), 32)
        if pk is None:
            rep.skip("contact_invalid_public_key")
            continue
        v = r.get("verification") or ""
        if v not in VERIFICATION:
            rep.anomaly(f"contact_unknown_verification:{v or 'empty'}")
        contacts[ident] = {
            "identity": ident, "public_key": pk, "verification": VERIFICATION.get(v, 0),
            "first_name": blank_to_none(r.get("firstname")), "last_name": blank_to_none(r.get("lastname")),
            "nickname": blank_to_none(r.get("nick_name")), "hidden": 1 if r.get("hidden") == "1" else 0,
            "archived": 1 if r.get("archived") == "1" else 0, "last_update_ms": to_int(r.get("last_update")),
            "avatar_user_path": None, "avatar_contact_path": None, "_cid": cid,
        }

    # --- groups -> contract rows
    groups = collections.OrderedDict()
    guid_to_key = {}
    for r in grows:
        if r.get("deleted") == "1":
            rep.skip("group_deleted_flag")
            continue
        gid = hex_bytes(r.get("id"), 8)
        creator = r.get("creator") or ""
        if gid is None or not IDENTITY_RE.match(creator):
            rep.skip("group_invalid_id_or_creator")
            continue
        gkey = f"{r.get('id').lower()}-{creator}"
        guid = r.get("group_uid") if version >= 19 and r.get("group_uid") else gkey
        guid_to_key[guid] = gkey
        members_all = [m for m in (r.get("members") or "").split(";") if m]
        user_in = own in members_all
        creator_in = creator in members_all
        if version >= 25 and r.get("user_state") not in (None, ""):
            ustate = to_int(r.get("user_state"))
        else:
            ustate = 0 if user_in else 2
        # RestoreService.restoreGroupFile: member + not creator + creator not in member list -> KICKED (orphaned)
        if ustate == 0 and creator != own and not creator_in:
            ustate = 1
            rep.anomaly("group_orphaned_set_kicked")
        if ustate == 0 and not user_in:
            rep.anomaly("group_state_member_but_own_identity_not_in_members")
        members = []
        for m in members_all:
            if m == own or m in members:
                continue
            members.append(m)
        if creator != own and creator not in members:
            members.append(creator)
        created = to_int(r.get("created_at"))
        if created is not None and created < 0:
            created = 0
        lu = to_int(r.get("last_update"))
        if lu is not None and lu < 0:
            lu = 0
        dms = to_int(r.get("groupDescTimestamp"))
        if dms is not None and dms < 0:
            dms = 0
        groups[gkey] = {
            "group_key": gkey, "group_id": gid, "creator": creator, "is_mine": 1 if creator == own else 0,
            "name": r.get("groupname") or None, "created_ms": created, "last_update_ms": lu,
            "archived": 1 if r.get("archived") == "1" else 0, "user_state": ustate,
            "members_json": json.dumps(members), "avatar_path": None,
            "description": blank_to_none(r.get("groupDesc")), "description_ms": dms, "_guid": guid,
        }
    rep.data["group_members_without_contacts_csv_entry"] = sum(
        1 for g in groups.values() for m in json.loads(g["members_json"]) if m not in contacts)

    # --- messages
    messages = collections.OrderedDict()
    per_chat = collections.defaultdict(collections.Counter)
    kinds = collections.Counter()
    raw_types = collections.Counter()
    states = collections.Counter()
    legacy_ack_rows = []  # (chat_kind, chat_key, msg_id, sender, emoji, reacted_ms)
    body_errors = collections.Counter()
    quote_v1 = 0
    for kind, fkey, n, header, rows in parsed_files:
        if kind == "dlist":
            if rows:
                rep.skip("distribution_list_message (no iOS target)", len(rows))
            continue
        if kind == "contact":
            ident = cid_to_identity.get(fkey)
            if ident is None or ident not in contacts:
                rep.skip("contact_message_file_without_contact" if ident is None else
                         "contact_message_file_contact_skipped", len(rows))
                continue
            chat_key = ident
        else:
            chat_key = guid_to_key.get(fkey)
            if chat_key is None:
                rep.skip("group_message_file_without_group", len(rows))
                continue
        for r in rows:
            uid = r.get("uid") or ""
            if not uid:
                rep.skip("message_without_uid")
                continue
            if uid in messages:
                rep.skip("message_duplicate_uid")
                continue
            t = r.get("type") or ""
            body = r.get("body")
            body = body if body != "" else None
            is_own = 1 if r.get("isoutbox") == "1" else 0
            is_status = r.get("isstatusmessage") == "1"
            created = to_int(r.get("created_at"))
            posted = to_int(r.get("posted_at"))
            if posted is None:
                posted = created
            read_ms = to_int(r.get("read_at"))
            deleted_ms = to_int(r.get("deleted_at"))
            raw_state = r.get("messagestae") or ""
            api_id = hex_bytes(r.get("apiid"), 8)
            if r.get("apiid") and api_id is None:
                rep.anomaly("message_invalid_apiid")
            msg_id = api_id or hashlib.sha256(uid.encode("utf-8")).digest()[:8]

            m = dict.fromkeys(MESSAGE_COLUMNS)
            m.update({
                "uid": uid, "chat_kind": kind, "chat_key": chat_key, "api_id": api_id, "msg_id": msg_id,
                "is_own": is_own, "created_ms": created, "posted_ms": posted,
                "delivered_ms": to_int(r.get("delivered_at")), "read_ms": read_ms,
                "modified_ms": to_int(r.get("modified_at")), "edited_ms": to_int(r.get("edited_at")),
                "deleted_ms": deleted_ms, "is_read": 1 if (r.get("isread") == "1" or is_own) else 0,
                "starred": 1 if (to_int(r.get("display_tags")) or 0) & 1 else 0,
                "quoted_api_id": hex_bytes(r.get("quoted_message_apiid"), 8), "raw_type": t,
            })
            if r.get("quoted_message_apiid") and m["quoted_api_id"] is None:
                rep.anomaly("message_invalid_quoted_apiid")
            # state: RestoreService.setMessageState - USERACK/USERDEC -> READ if read_at else DELIVERED
            if raw_state in LEGACY_ACK:
                m["state"] = "READ" if read_ms is not None else "DELIVERED"
            else:
                m["state"] = raw_state or None
            states[f"{'out' if is_own else 'in'}:{raw_state or 'null'}"] += 1
            # sender
            if kind == "contact":
                m["sender"] = None if is_own else chat_key
            else:
                s_ident = r.get("identity") or None
                if s_ident == own:
                    if not is_own:
                        rep.anomaly("group_incoming_row_with_own_identity_as_sender")
                    s_ident = None
                m["sender"] = None if is_own else s_ident

            # type / body
            t_eff, src_body = t, body
            try:
                if t in ("IMAGE", "VIDEO", "VOICEMESSAGE"):
                    arr = legacy_to_file_array(t, body) if body else None
                    t_eff = "FILE"
                    rep.anomaly(f"legacy_{t}_converted_to_FILE")
                else:
                    arr = None
                if t_eff in ("TEXT", "CONTACT", "STATUS"):
                    if t == "STATUS" or (t == "TEXT" and is_status):
                        m["kind"] = "legacy_status"
                    else:
                        m["kind"] = "text"
                        if t == "CONTACT":
                            rep.anomaly("legacy_CONTACT_converted_to_TEXT")
                    m["text"] = body if body is not None else (None if deleted_ms else "")
                    if m["kind"] == "text" and body and QUOTE_V1_RE.match(body):
                        quote_v1 += 1
                elif t_eff == "FILE":
                    m["kind"] = "file"
                    m["raw_body"] = src_body
                    if arr is None and body:
                        arr = json.loads(body)
                    if arr is not None:
                        f = parse_file_array(arr)
                        m["file_blob_id"] = hex_bytes(f["blob"], 16)
                        m["file_key"] = hex_bytes(f["key"], 32)
                        if f["blob"] and m["file_blob_id"] is None:
                            rep.anomaly("file_blob_id_not_16_bytes")
                        m["file_mime"] = f["mime"] or None
                        m["file_name"] = f["name"] or None
                        m["file_size"] = f["size"]
                        m["file_render"] = f["render"]
                        m["file_caption"] = f["caption"] if f["caption"] else (r.get("caption") or None)
                        m["file_thumb_mime"] = f["thumb_mime"] or None
                        meta = {k: v for k, v in (f["meta"] or {}).items() if k != "_legacy_nonce"}
                        m["file_meta_json"] = json.dumps(meta, sort_keys=True)
                        m["_downloaded"] = f["downloaded"]
                    elif not deleted_ms:
                        body_errors["FILE:empty_body"] += 1
                elif t_eff == "LOCATION":
                    m["kind"] = "location"
                    m["raw_body"] = src_body
                    if body:
                        arr = json.loads(body)
                        if not isinstance(arr, list) or len(arr) < 2:
                            raise ValueError("location array < 2")
                        m["loc_lat"] = float(arr[0])
                        m["loc_lon"] = float(arr[1])
                        m["loc_acc"] = to_float(arr[2]) if len(arr) > 2 else None
                        # NOTE: address (index 3) comes BEFORE name (index 4) - LocationDataModel.kt:46-50
                        m["loc_address"] = blank_to_none(arr[3]) if len(arr) > 3 else None
                        m["loc_name"] = blank_to_none(arr[4]) if len(arr) > 4 else None
                    elif not deleted_ms:
                        body_errors["LOCATION:empty_body"] += 1
                elif t_eff == "BALLOT":
                    m["kind"] = "ballot"
                    m["raw_body"] = src_body
                    if body:
                        arr = json.loads(body)
                        m["ballot_data_type"] = to_int(arr[0])
                        m["ballot_ref"] = to_int(arr[1])
                elif t_eff == "VOIP_STATUS":
                    m["kind"] = "call"
                    m["raw_body"] = src_body
                    if body:
                        arr = json.loads(body)
                        d = arr[1] if len(arr) > 1 and isinstance(arr[1], dict) else {}
                        m["call_status"] = to_int(d.get("status"))
                        m["call_reason"] = to_int(d.get("reason"))
                        m["call_duration_s"] = to_int(d.get("duration"))
                        cid_v = to_int(d.get("callId"))
                        m["call_id"] = cid_v if cid_v else None  # 0 = NO_CALL_ID
                elif t_eff == "GROUP_CALL_STATUS":
                    m["kind"] = "call"
                    m["raw_body"] = src_body
                    if body:
                        arr = json.loads(body)
                        d = arr[1] if len(arr) > 1 and isinstance(arr[1], dict) else {}
                        m["call_status"] = to_int(d.get("status"))
                        m["gstatus_identity"] = d.get("callerIdentity") or None
                elif t_eff == "GROUP_STATUS":
                    m["kind"] = "group_status"
                    m["raw_body"] = src_body
                    if body:
                        arr = json.loads(body)
                        d = arr[1] if len(arr) > 1 and isinstance(arr[1], dict) else {}
                        m["gstatus_type"] = to_int(d.get("status"))
                        m["gstatus_identity"] = d.get("identity") or None
                        m["gstatus_name"] = d.get("newGroupName") or d.get("ballotName") or None
                else:
                    rep.skip(f"unknown_message_type:{t or 'empty'}")
                    continue
            except (ValueError, TypeError, IndexError, KeyError, AttributeError) as e:
                body_errors[f"{t}:{type(e).__name__}"] += 1
                m["raw_body"] = src_body
                if m.get("kind") is None:
                    m["kind"] = {"FILE": "file", "LOCATION": "location", "BALLOT": "ballot", "VOIP_STATUS": "call",
                                 "GROUP_CALL_STATUS": "call", "GROUP_STATUS": "group_status"}.get(t_eff, "text")

            if m["sender"] is None and not is_own and kind == "group" and m["kind"] in ("text", "file", "location",
                                                                                        "ballot"):
                rep.anomaly("group_incoming_message_without_sender")

            # legacy acks -> reactions (RestoreService.tryMapContactAckDecToReaction / tryMapGroupAckDecToReactions)
            reacted = m["modified_ms"] if m["modified_ms"] is not None else created
            if kind == "contact" and raw_state in LEGACY_ACK:
                # reactor = partner if the message is outgoing, else the user (RestoreService.java:1700-1702)
                legacy_ack_rows.append((kind, chat_key, msg_id, chat_key if is_own else None, LEGACY_ACK[raw_state],
                                        reacted))
            if kind == "group" and r.get("g_msg_states"):
                try:
                    j = json.loads(r["g_msg_states"])
                    for ident, st in (j.items() if isinstance(j, dict) else []):
                        if st in LEGACY_ACK and ident:
                            legacy_ack_rows.append((kind, chat_key, msg_id, None if ident == own else ident,
                                                    LEGACY_ACK[st], reacted))
                except ValueError:
                    rep.anomaly("g_msg_states_invalid_json")
            if kind == "group" and raw_state in LEGACY_ACK:
                # Android keeps group acks in g_msg_states; restore ignores the row state (only maps it to
                # READ/DELIVERED). Check that the user's own ack is covered there.
                try:
                    own_gms = json.loads(r.get("g_msg_states") or "{}").get(own)
                except (ValueError, AttributeError):
                    own_gms = None
                if own_gms == raw_state:
                    rep.data.setdefault("group_row_ack_state_covered_by_g_msg_states", 0)
                    rep.data["group_row_ack_state_covered_by_g_msg_states"] += 1
                else:
                    rep.anomaly("group_row_ack_state_NOT_covered_by_g_msg_states (no reaction created)")

            messages[uid] = m
            kinds[m["kind"]] += 1
            raw_types[f"{kind}:{t}"] += 1
            pc = per_chat[chat_ref(kind, chat_key)]
            pc["messages"] += 1
            pc[f"kind:{m['kind']}"] += 1
            if created is not None and media_cutoff_ms is not None and m["kind"] == "file" and created > media_cutoff_ms:
                pc["file_after_media_backup"] += 1

    snd_missing = [m for m in messages.values() if m["sender"] is not None and m["sender"] not in contacts]
    rep.data["incoming_sender_without_contacts_csv_entry"] = {
        "rows": len(snd_missing), "distinct_senders": len({m["sender"] for m in snd_missing})}
    rep.data["message_body_parse_errors"] = dict(body_errors)
    rep.data["quote_v1_in_text_body (kept verbatim)"] = quote_v1

    # --- quote resolution (same chat)
    ids_by_chat = collections.defaultdict(set)
    for m in messages.values():
        if m["api_id"] is not None:
            ids_by_chat[(m["chat_kind"], m["chat_key"])].add(m["api_id"])
    q = collections.Counter()
    for m in messages.values():
        if m["quoted_api_id"] is not None:
            q[f"{m['chat_kind']}:{'resolved' if m['quoted_api_id'] in ids_by_chat[(m['chat_kind'], m['chat_key'])] else 'unresolved'}"] += 1
    rep.data["quotes"] = dict(q)

    # duplicate msg ids within a chat (importer dedupes by id)
    dup = collections.Counter()
    seen = collections.defaultdict(set)
    for m in messages.values():
        k = (m["chat_kind"], m["chat_key"])
        if m["msg_id"] in seen[k]:
            dup[m["chat_kind"]] += 1
        seen[k].add(m["msg_id"])
    rep.data["duplicate_msg_id_within_chat"] = dict(dup)

    # --- reactions
    all_ids_by_chat = collections.defaultdict(set)
    for m in messages.values():
        all_ids_by_chat[(m["chat_kind"], m["chat_key"])].add(m["msg_id"])
    reactions = []
    rkeys = set()
    rstat = collections.Counter()
    for r in contact_reactions:
        partner = r.get("identity") or ""
        target = hex_bytes(r.get("api_message_id"), 8)
        emoji = r.get("emoji_sequence") or ""
        snd = r.get("sender_identity") or ""
        if target is None or not emoji:
            rep.skip("contact_reaction_invalid_target_or_emoji")
            continue
        if snd not in (own, partner):
            rep.anomaly("contact_reaction_sender_is_third_party")
        sender = None if snd == own else snd
        key = ("contact", partner, target, sender, emoji)
        if key in rkeys:
            rep.skip("reaction_duplicate")
            continue
        rkeys.add(key)
        resolved = partner in contacts and target in all_ids_by_chat[("contact", partner)]
        rstat[f"csv:contact:{'resolved' if resolved else 'unresolved'}"] += 1
        reactions.append({"chat_kind": "contact", "chat_key": partner, "target_msg_id": target, "sender": sender,
                          "emoji": emoji, "reacted_ms": to_int(r.get("reacted_at")), "source": "csv"})
    for r in group_reactions:
        gidhex = (r.get("api_group_id") or "").lower()
        gkey = f"{gidhex}-{r.get('group_creator_identity') or ''}"
        target = hex_bytes(r.get("api_message_id"), 8)
        emoji = r.get("emoji_sequence") or ""
        snd = r.get("sender_identity") or ""
        if target is None or not emoji or not snd:
            rep.skip("group_reaction_invalid_target_emoji_or_sender")
            continue
        sender = None if snd == own else snd
        key = ("group", gkey, target, sender, emoji)
        if key in rkeys:
            rep.skip("reaction_duplicate")
            continue
        rkeys.add(key)
        resolved = gkey in groups and target in all_ids_by_chat[("group", gkey)]
        rstat[f"csv:group:{'resolved' if resolved else 'unresolved'}"] += 1
        reactions.append({"chat_kind": "group", "chat_key": gkey, "target_msg_id": target, "sender": sender,
                          "emoji": emoji, "reacted_ms": to_int(r.get("reacted_at")), "source": "csv"})
    for (ck, key_, target, sender, emoji, reacted) in legacy_ack_rows:
        key = (ck, key_, target, sender, emoji)
        if key in rkeys:
            rep.skip("legacy_ack_duplicate_of_existing_reaction")
            continue
        rkeys.add(key)
        rstat[f"legacy_ack:{ck}"] += 1
        reactions.append({"chat_kind": ck, "chat_key": key_, "target_msg_id": target, "sender": sender,
                          "emoji": emoji, "reacted_ms": reacted, "source": "legacy_ack"})
    rep.data["reactions"] = dict(rstat)
    rep.data["reaction_senders_without_contact"] = sum(
        1 for x in reactions if x["sender"] is not None and x["sender"] not in contacts)

    # --- ballots
    ballots = collections.OrderedDict()
    uid_to_ref = {}
    bstat = collections.Counter()
    for r in ballots_rows:
        ref = to_int(r.get("id"))
        if ref is None:
            rep.skip("ballot_invalid_id")
            continue
        api = hex_bytes(r.get("aid"), 8)
        creator = r.get("creator") or None
        refkind = r.get("ref") or ""
        ref_id = r.get("ref_id") or ""
        if refkind == "GroupBallotModel":
            ck, ckey = "group", guid_to_key.get(ref_id)
        elif refkind == "IdentityBallotModel":
            ck, ckey = "contact", ref_id if ref_id in contacts else None
        else:
            ck, ckey = None, None
        if ckey is None:
            bstat["ballot_chat_unresolved"] += 1
        uid_to_ref[f"{r.get('aid')}-{r.get('creator')}"] = ref
        ballots[ref] = {"ref_id": ref, "api_id": api, "creator": creator, "chat_kind": ck, "chat_key": ckey,
                        "title": r.get("name") or None, "state": r.get("state") or None,
                        "assessment": r.get("assessment") or None, "btype": r.get("type") or None,
                        "choice_type": r.get("choice_type") or None, "created_ms": to_int(r.get("created_at")),
                        "modified_ms": to_int(r.get("modified_at"))}
    choices = []
    for r in choice_rows:
        ref = uid_to_ref.get(r.get("ballot") or "")
        if ref is None:
            bstat["choice_ballot_unresolved"] += 1
            rep.skip("ballot_choice_without_ballot")
            continue
        choices.append({"ballot_ref": ref, "choice_id": to_int(r.get("aid")), "name": r.get("name") or None,
                        "order_pos": to_int(r.get("order")), "vote_count": to_int(r.get("vote_count")),
                        "created_ms": to_int(r.get("created_at")), "modified_ms": to_int(r.get("modified_at"))})
    votes = []
    for r in vote_rows:
        ref = uid_to_ref.get(r.get("ballot_uid") or "")
        if ref is None:
            bstat["vote_ballot_unresolved"] += 1
            rep.skip("ballot_vote_without_ballot")
            continue
        votes.append({"ballot_ref": ref, "choice_id": to_int(r.get("choice_uid")), "identity": r.get("identity") or None,
                      "choice": to_int(r.get("choice")), "created_ms": to_int(r.get("created_at")),
                      "modified_ms": to_int(r.get("modified_at"))})
    for m in messages.values():
        if m["kind"] == "ballot":
            if m["ballot_ref"] in ballots:
                bstat["ballot_message_resolved"] += 1
                b = ballots[m["ballot_ref"]]
                if b["chat_kind"] is not None and (b["chat_kind"], b["chat_key"]) != (m["chat_kind"], m["chat_key"]):
                    bstat["ballot_message_chat_mismatch"] += 1
            else:
                bstat["ballot_message_unresolved"] += 1
    rep.data["ballots"] = {"ballots": len(ballots), "choices": len(choices), "votes": len(votes), **bstat}

    # --- nonces
    nonces = []
    nstat = collections.Counter()
    for fname, nk in (("nonces.csv", "csp"), ("nonces_d2d.csv", "d2d")):
        if not tb.has(fname):
            continue
        _, rows = parse_csv_bytes(tb.read(fname))
        for r in rows:
            if not r or r == [""]:
                continue
            h = hex_bytes(r[0], 32)
            if h is None:
                rep.skip(f"nonce_invalid_hex:{nk}")
                continue
            nonces.append((nk, h))
            nstat[nk] += 1
    expected = {}
    if tb.has("nonce_counts.csv"):
        _, ncr = tb.csv_dicts("nonce_counts.csv", rep)
        if ncr:
            expected = {"csp": to_int(ncr[0].get("csp")), "d2d": to_int(ncr[0].get("d2d"))}
    rep.data["nonces"] = {"csp": nstat["csp"], "d2d": nstat["d2d"], "nonce_counts.csv": expected}
    if tb.has("reaction_counts.csv"):
        _, rcr = tb.csv_dicts("reaction_counts.csv", rep)
        if rcr:
            rep.data["reaction_counts.csv"] = {k: to_int(v) for k, v in rcr[0].items()}

    rep.data["contacts"] = len(contacts)
    rep.data["groups"] = len(groups)
    rep.data["messages_total"] = len(messages)
    rep.data["messages_by_kind"] = dict(kinds)
    rep.data["messages_by_raw_type"] = dict(raw_types)
    rep.data["message_states_raw"] = dict(states)
    rep.data["messages_edited"] = sum(1 for m in messages.values() if m["edited_ms"] is not None)
    rep.data["messages_deleted"] = sum(1 for m in messages.values() if m["deleted_ms"] is not None)
    rep.data["messages_starred"] = sum(1 for m in messages.values() if m["starred"])
    rep.data["per_chat"] = {k: dict(v) for k, v in sorted(per_chat.items())}
    return {
        "version": version, "own": own, "contacts": contacts, "groups": groups, "messages": messages,
        "reactions": reactions, "ballots": ballots, "choices": choices, "votes": votes, "nonces": nonces,
        "cid_to_identity": cid_to_identity, "guid_to_key": guid_to_key,
    }


def extract_avatars(tb, data, out_dir, rep):
    os.makedirs(os.path.join(out_dir, "avatars"), exist_ok=True)
    stat = collections.Counter()
    own_avatar = None

    def write(name, entry):
        rel = f"avatars/{name}"
        raw = tb.read(entry)
        dst = os.path.join(out_dir, rel)
        if not (os.path.exists(dst) and os.path.getsize(dst) == len(raw)):
            with open(dst + ".part", "wb") as f:
                f.write(raw)
            os.replace(dst + ".part", dst)
        return rel

    for n in tb.names:
        if n.startswith("contact_avatar_") or n.startswith("contact_profile_pic_"):
            user_set = n.startswith("contact_avatar_")
            cid = n[len("contact_avatar_"):] if user_set else n[len("contact_profile_pic_"):]
            if user_set and (cid == "me" or (data["version"] < 19 and cid == data["own"])):
                own_avatar = write("contact_avatar_me", n)
                stat["own"] += 1
                continue
            ident = data["cid_to_identity"].get(cid)
            c = data["contacts"].get(ident) if ident else None
            if c is None:
                rep.skip("avatar_without_contact")
                continue
            rel = write(("contact_avatar_" if user_set else "contact_profile_pic_") + safe_name(ident), n)
            c["avatar_user_path" if user_set else "avatar_contact_path"] = rel
            stat["contact_user_set" if user_set else "contact_profile_pic"] += 1
        elif n.startswith("group_avatar_"):
            gkey = data["guid_to_key"].get(n[len("group_avatar_"):])
            g = data["groups"].get(gkey) if gkey else None
            if g is None:
                rep.skip("group_avatar_without_group")
                continue
            g["avatar_path"] = write("group_avatar_" + safe_name(gkey), n)
            stat["group"] += 1
    rep.data["avatars"] = dict(stat)
    return own_avatar


# ----------------------------------------------------------------------------------------------------------------
# media extraction (streamed per entry, resumable)

_W = {"pw": None, "zips": {}}


def _init_worker(pw):
    """pw: bytes (one password for every zip) or {zip path: bytes}. Worker processes (spawn) also install the
    network guard of tmcore when it is importable: a media worker has no reason to open any socket."""
    _W["pw"] = pw
    _W["zips"] = {}
    try:
        from tmcore import netguard
        if not netguard.is_active():
            netguard.install(allow_unix=False)
    except ImportError:  # pragma: no cover -- standalone use outside tmcore
        pass


def _worker_zip(path):
    z = _W["zips"].get(path)
    if z is None:
        z = pyzipper.AESZipFile(path)
        pw = _W["pw"].get(path) if isinstance(_W["pw"], dict) else _W["pw"]
        z.setpassword(pw)
        _W["zips"][path] = z
    return z


def _extract_one(task):
    """task = (zip_path, entry_name, dst_abs, expected_size). Returns (dst_abs, size, sha256hex, error)."""
    zpath, entry, dst, size = task
    try:
        z = _worker_zip(zpath)
        h = hashlib.sha256()
        n = 0
        tmp = dst + ".part"
        with z.open(entry) as src, open(tmp, "wb") as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
                n += len(chunk)
        if n != size:
            os.unlink(tmp)
            return dst, n, None, f"size mismatch {n} != {size}"
        os.replace(tmp, dst)
        return dst, n, h.hexdigest(), None
    except Exception as e:  # noqa: BLE001 - reported, never includes the password
        try:
            os.unlink(dst + ".part")
        except OSError:
            pass
        return dst, 0, None, f"{type(e).__name__}"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Manifest:
    def __init__(self, out_dir):
        self.path = os.path.join(out_dir, ".extract-manifest.jsonl")
        self.entries = {}
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        self.entries[e["rel"]] = e
                    except (ValueError, KeyError):
                        continue
        self.fh = open(self.path, "a", encoding="utf-8")

    def lookup(self, rel, abs_path):
        e = self.entries.get(rel)
        if not e:
            return None
        st = os.stat(abs_path)
        if e.get("size") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns:
            return e.get("sha256")
        return None

    def add(self, rel, abs_path, sha):
        st = os.stat(abs_path)
        e = {"rel": rel, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha}
        self.entries[rel] = e
        self.fh.write(json.dumps(e) + "\n")

    def flush(self):
        self.fh.flush()

    def close(self):
        self.fh.close()


def extract_media(data, zip_paths, pw_candidates, out_dir, db, jobs, rep, media_cutoff_ms, limit=None, log=print,
                  progress_cb=None):
    os.makedirs(os.path.join(out_dir, "media"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "thumbs"), exist_ok=True)
    # index all given zips (text backup first, then media backups in the given order)
    index = {}  # entry name -> (zip_path, ZipInfo)
    pw_for_zip = {}
    all_media_entries = collections.Counter()
    for zp in zip_paths:
        z, pw = open_zip(zp, pw_candidates)
        pw_for_zip[zp] = pw
        for info in z.infolist():
            n = info.filename
            for kind, (mp, tp) in MEDIA_PREFIX.items():
                if n.startswith(mp) or n.startswith(tp):
                    all_media_entries[n] += 1
                    if n not in index:
                        index[n] = (zp, info)
                    break
        z.close()
    pws = set(pw_for_zip.values()) - {None}
    # one password for all zips (as before) or each zip with its own (verified) password
    pw = next(iter(pws)) if len(pws) == 1 else ({k: v for k, v in pw_for_zip.items() if v is not None} or None)

    manifest = Manifest(out_dir)
    tasks = []
    referenced = set()
    plan = []  # (uid, 'media'|'thumb', rel, abs, zip, entry, size)
    for uid, m in data["messages"].items():
        if m["kind"] != "file":
            continue
        mp, tp = MEDIA_PREFIX[m["chat_kind"]]
        for what, prefix, sub in (("media", mp, "media"), ("thumb", tp, "thumbs")):
            name = prefix + uid
            if name not in index:
                continue
            referenced.add(name)
            if m["deleted_ms"] is not None:
                rep.anomaly(f"{what}_present_for_deleted_message (not extracted)")
                continue
            zp, info = index[name]
            rel = f"{sub}/{uid}"
            plan.append((uid, what, rel, os.path.join(out_dir, rel), zp, info.filename, info.file_size,
                         info.header_offset))
    rep.data["media_entries_unreferenced_by_text_backup"] = len(set(index) - referenced)
    plan.sort(key=lambda p: (p[4], p[7]))
    if limit is not None:
        plan = plan[:limit]

    done = collections.Counter()
    errors = collections.Counter()
    bytes_done = 0
    results = {}  # (uid, what) -> (rel, size, sha)
    for p in plan:
        uid, what, rel, dst, zp, entry, size, _ = p
        if os.path.exists(dst) and os.path.getsize(dst) == size:
            sha = None
            if what == "media":
                sha = manifest.lookup(rel, dst)
                if sha is None:
                    sha = sha256_file(dst)
                    manifest.add(rel, dst, sha)
            results[(uid, what)] = (rel, size, sha)
            done[f"{what}:skipped_existing"] += 1
        else:
            tasks.append(p)
    manifest.flush()
    total_bytes = sum(p[6] for p in tasks)
    log(f"[media] planned={len(plan)} already_present={len(plan) - len(tasks)} to_extract={len(tasks)} "
        f"bytes={total_bytes}")

    def apply(p, size, sha, err):
        nonlocal bytes_done
        uid, what, rel, dst = p[0], p[1], p[2], p[3]
        if err:
            errors[f"{what}:{err.split(' ')[0]}"] += 1
            return
        if what == "media":
            manifest.add(rel, dst, sha)
        results[(uid, what)] = (rel, size, sha)
        done[f"{what}:extracted"] += 1
        bytes_done += size

    t0 = time.time()
    last = [t0]

    def progress(i):
        if progress_cb is not None:
            progress_cb(i, len(tasks))
        now = time.time()
        if now - last[0] >= 15 or i == len(tasks):
            last[0] = now
            rate = bytes_done / max(now - t0, 1e-6) / 1e6
            log(f"[media] {i}/{len(tasks)} files, {bytes_done / 1e9:.2f}/{total_bytes / 1e9:.2f} GB, "
                f"{rate:.1f} MB/s, errors={sum(errors.values())}")
            manifest.flush()

    if tasks and pw is None:
        raise NormalizeError("no_media_password", "no password for media zips")
    if jobs <= 1:
        _init_worker(pw)
        for i, p in enumerate(tasks, 1):
            _, size, sha, err = _extract_one((p[4], p[5], p[3], p[6]))
            apply(p, size, sha, err)
            progress(i)
    elif tasks:
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker,
                                                    initargs=(pw,)) as ex:
            futs = {ex.submit(_extract_one, (p[4], p[5], p[3], p[6])): p for p in tasks}
            for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
                p = futs[fut]
                _, size, sha, err = fut.result()
                apply(p, size, sha, err)
                progress(i)
    manifest.flush()
    manifest.close()

    # apply to messages
    size_mismatch = 0
    for (uid, what), (rel, size, sha) in results.items():
        m = data["messages"][uid]
        if what == "media":
            if m["file_size"] is not None and m["file_size"] != size:
                size_mismatch += 1
            m["media_path"], m["media_sha256"], m["file_size"] = rel, sha, size
        else:
            m["thumb_path"] = rel

    # per-chat media coverage (chat labels are hashed, see chat_ref)
    for m in data["messages"].values():
        if m["kind"] != "file":
            continue
        pc = rep.data["per_chat"].setdefault(chat_ref(m["chat_kind"], m["chat_key"]), {})
        for k, cond in (("file_with_media", m["media_path"] is not None),
                        ("file_with_thumb", m["thumb_path"] is not None),
                        ("file_without_media", m["media_path"] is None and m["deleted_ms"] is None)):
            if cond:
                pc[k] = pc.get(k, 0) + 1

    # coverage
    cov = collections.Counter()
    for m in data["messages"].values():
        if m["kind"] != "file":
            continue
        cov["file_messages"] += 1
        has_m, has_t = m["media_path"] is not None, m["thumb_path"] is not None
        cov["with_media"] += has_m
        cov["with_thumb"] += has_t
        cov["with_thumb_only"] += (has_t and not has_m)
        if m["deleted_ms"] is not None:
            cov["deleted (no content)"] += 1
            continue
        if not has_m:
            after = media_cutoff_ms is not None and (m["created_ms"] or 0) > media_cutoff_ms
            cov["without_media:" + ("created_after_media_backup" if after else "created_before_media_backup")] += 1
            if not after:
                cov["without_media_before_backup:isDownloaded=" + str(m.get("_downloaded"))] += 1
    rep.data["media"] = {
        "zips": [os.path.basename(z) for z in zip_paths], "media_backup_cutoff_ms": media_cutoff_ms,
        "entries_indexed": len(index), "duplicate_entries_across_zips": sum(1 for v in all_media_entries.values() if v > 1),
        "results": dict(done), "errors": dict(errors), "media_size_differs_from_body_fileSize": size_mismatch,
        "coverage": dict(cov), "limit": limit,
    }
    return results


# ----------------------------------------------------------------------------------------------------------------
# DB writing

def write_db(path, data, meta):
    if os.path.exists(path):
        os.unlink(path)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", list(meta.items()))
    ccols = ["identity", "public_key", "verification", "first_name", "last_name", "nickname", "hidden", "archived",
             "last_update_ms", "avatar_user_path", "avatar_contact_path"]
    db.executemany(f"INSERT INTO contacts({','.join(ccols)}) VALUES ({','.join('?' * len(ccols))})",
                   [[c[k] for k in ccols] for c in data["contacts"].values()])
    gcols = ["group_key", "group_id", "creator", "is_mine", "name", "created_ms", "last_update_ms", "archived",
             "user_state", "members_json", "avatar_path", "description", "description_ms"]
    db.executemany(f'INSERT INTO "groups"({",".join(gcols)}) VALUES ({",".join("?" * len(gcols))})',
                   [[g[k] for k in gcols] for g in data["groups"].values()])
    db.executemany(f"INSERT INTO messages({','.join(MESSAGE_COLUMNS)}) VALUES ({','.join('?' * len(MESSAGE_COLUMNS))})",
                   [[m[k] for k in MESSAGE_COLUMNS] for m in data["messages"].values()])
    rcols = ["chat_kind", "chat_key", "target_msg_id", "sender", "emoji", "reacted_ms", "source"]
    db.executemany(f"INSERT INTO reactions({','.join(rcols)}) VALUES ({','.join('?' * len(rcols))})",
                   [[r[k] for k in rcols] for r in data["reactions"]])
    bcols = ["ref_id", "api_id", "creator", "chat_kind", "chat_key", "title", "state", "assessment", "btype",
             "choice_type", "created_ms", "modified_ms"]
    db.executemany(f"INSERT INTO ballots({','.join(bcols)}) VALUES ({','.join('?' * len(bcols))})",
                   [[b[k] for k in bcols] for b in data["ballots"].values()])
    chcols = ["ballot_ref", "choice_id", "name", "order_pos", "vote_count", "created_ms", "modified_ms"]
    db.executemany(f"INSERT INTO ballot_choices({','.join(chcols)}) VALUES ({','.join('?' * len(chcols))})",
                   [[c[k] for k in chcols] for c in data["choices"]])
    vcols = ["ballot_ref", "choice_id", "identity", "choice", "created_ms", "modified_ms"]
    db.executemany(f"INSERT INTO ballot_votes({','.join(vcols)}) VALUES ({','.join('?' * len(vcols))})",
                   [[v[k] for k in vcols] for v in data["votes"]])
    db.executemany("INSERT INTO nonces(kind, hash) VALUES (?, ?)", data["nonces"])
    db.commit()
    return db


def add_supplementary_contacts(data, path, rep):
    """review F2: hidden contacts for senders that exist in no contacts.csv. Keys must come from a trusted source
    (Threema directory lookup decided by the lead); only identities that actually occur as a message sender or
    reaction author are accepted, never the own identity, never an existing contact (existing data wins)."""
    with open(path, "r", encoding="utf-8") as f:
        items = json.load(f)
    used = {m["sender"] for m in data["messages"].values() if m.get("sender")}
    used |= {r["sender"] for r in data["reactions"] if r.get("sender")}
    c = collections.Counter()
    for it in items:
        ident = (it.get("identity") or "").upper()
        pk = hex_bytes(it.get("public_key_hex"), 32)
        if not IDENTITY_RE.match(ident) or pk is None:
            c["rejected_invalid"] += 1
        elif ident == data["own"] or ident in data["contacts"]:
            c["rejected_existing_or_own"] += 1
        elif ident not in used:
            c["rejected_not_referenced"] += 1
        else:
            data["contacts"][ident] = {
                "identity": ident, "public_key": pk, "verification": 0, "first_name": None, "last_name": None,
                "nickname": None, "hidden": 1, "archived": 0, "last_update_ms": None,
                "avatar_user_path": None, "avatar_contact_path": None, "_cid": None,
            }
            c["added_hidden"] += 1
    rep.data["supplementary_contacts"] = dict(c)
    rep.data["contacts"] = len(data["contacts"])


def normalize(text_backup, media_backups=(), *, passwords, out_dir, no_media=False, own_identity=None, jobs=1,
              media_cutoff_ms=None, media_limit=None, supplementary_contacts=None, log=None, progress=None):
    """Text backup (+ media backups) -> <out_dir>/normalized.sqlite, media/, thumbs/, avatars/,
    normalize-report.json. passwords: one password string for every file, or {file path: password}.
    Returns the report dict (counts only). Raises NormalizeError on refusals."""
    log = log or (lambda _msg: None)
    media_backups = list(media_backups or [])
    pw_candidates = (password_candidates(passwords) if isinstance(passwords, str)
                     else {k: password_candidates(v) for k, v in dict(passwords).items()})
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    rep = Report()
    media_cutoff = media_cutoff_ms
    if media_cutoff is None:
        media_cutoff = max([t for t in (backup_ts_ms(p) for p in media_backups) if t is not None], default=None)

    log("[text] reading " + os.path.basename(text_backup))
    z, _pw = open_zip(text_backup, pw_candidates)
    tb = TextBackup(z)
    data = normalize_text_backup(tb, own_identity, rep, media_cutoff)
    if supplementary_contacts:
        add_supplementary_contacts(data, supplementary_contacts, rep)
    own_avatar = extract_avatars(tb, data, out_dir, rep)
    z.close()
    log(f"[text] contacts={len(data['contacts'])} groups={len(data['groups'])} messages={len(data['messages'])} "
        f"reactions={len(data['reactions'])} ballots={len(data['ballots'])} nonces={len(data['nonces'])}")

    if not no_media:
        zips = [text_backup] + [p for p in media_backups if p != text_backup]
        extract_media(data, zips, pw_candidates, out_dir, None, jobs, rep, media_cutoff,
                      limit=media_limit, log=log, progress_cb=progress)
    else:
        rep.data["media"] = {"skipped": "--no-media"}

    meta = collections.OrderedDict([
        ("own_identity", data["own"]),
        ("format_version", NORMALIZED_FORMAT_VERSION),
        ("text_backup", os.path.basename(text_backup)),
        ("media_backup", json.dumps([os.path.basename(p) for p in media_backups]) if not no_media else "[]"),
        ("generated_at", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("android_backup_version", str(data["version"])),
        ("own_identity_method", rep.data["own_identity_detection"]["method"] or ""),
        ("own_avatar_path", own_avatar or ""),
        ("media_backup_cutoff_ms", str(media_cutoff) if media_cutoff is not None and not no_media else ""),
    ])
    tmp = os.path.join(out_dir, "normalized.sqlite.tmp")
    db = write_db(tmp, data, meta)
    db.close()
    os.replace(tmp, os.path.join(out_dir, "normalized.sqlite"))

    rep.data["skipped_rows"] = dict(rep.skipped)
    rep.data["anomalies"] = dict(rep.anomalies)
    report = collections.OrderedDict([("generated_at", meta["generated_at"]),
                                      ("text_backup", meta["text_backup"])])
    report.update(rep.data)
    with open(os.path.join(out_dir, "normalize-report.json.part"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False, default=str)
    os.replace(os.path.join(out_dir, "normalize-report.json.part"), os.path.join(out_dir, "normalize-report.json"))
    log("[done] normalized.sqlite + normalize-report.json written")
    return report


def read_password_line(stream=None):
    """Maintainer CLI: the password is exactly one line on stdin (never argv, never a file)."""
    stream = stream if stream is not None else sys.stdin
    pw = stream.readline().rstrip("\r\n")
    if not pw:
        raise NormalizeError("password", "no password on stdin (one line expected)")
    return pw


def main(argv=None, *, password=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--text-backup", required=True)
    ap.add_argument("--media-backup", action="append", default=[])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--no-media", action="store_true", help="do not extract media/thumbnails")
    ap.add_argument("--own-identity", default=None, help="override own-identity detection")
    ap.add_argument("--jobs", type=int, default=1, help="parallel media extraction processes")
    ap.add_argument("--media-cutoff-ms", type=int, default=None,
                    help="creation time (epoch ms) of the newest media backup, for the coverage report; default: "
                         "parsed from the file name threema-backup_<ms>_1")
    ap.add_argument("--media-limit", type=int, default=None, help=argparse.SUPPRESS)  # testing only
    ap.add_argument("--supplementary-contacts", default=None,
                    help="JSON file [{\"identity\": ID, \"public_key_hex\": 64 hex}] of identities that are NOT in "
                         "contacts.csv; added as hidden, unverified contacts so their group messages can be imported "
                         "(review F2). Never used by the product (no directory lookup, DESIGN D4).")
    args = ap.parse_args(argv)

    def log(msg):
        print(time.strftime("%H:%M:%S ") + msg, flush=True)

    pw = password if password is not None else read_password_line()
    normalize(args.text_backup, args.media_backup, passwords=pw, out_dir=args.out_dir, no_media=args.no_media,
              own_identity=args.own_identity, jobs=args.jobs, media_cutoff_ms=args.media_cutoff_ms,
              media_limit=args.media_limit, supplementary_contacts=args.supplementary_contacts, log=log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
