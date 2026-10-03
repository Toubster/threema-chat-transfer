#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Threema-Android data backup files: classification for `android-inspect` (no password) and the maintainer's
format-spec validator.

classify(path) -> {"kind": full|text|media|incomplete|unknown, "bytes", "has_media", "has_text", "encrypted",
                   "created_at_ms", "entries"}
    Reads only the zip central directory (file names and sizes are not encrypted in WinZip-AES archives), so it
    needs no password and never decrypts content. The format version lives in the encrypted 'settings' entry and is
    checked by android-normalize (android_normalize.read_format_version) once the password is known.

Format-spec validator (maintainer; prints ONLY structural/aggregate information -- column headers, row counts,
enum distributions, JSON key sets, naming-consistency checks; never bodies, names, identities or the password):
  python3 -m tmcore.lib.validate_android --backup PATH [--media PATH] [--json OUT] < password-line
The password is one line on stdin, never argv, never a file (DESIGN §9).
"""
import argparse
import collections
import csv
import datetime as _dt
import io
import json
import os
import re
import sys
import zipfile

import pyzipper

csv.field_size_limit(sys.maxsize)

DEFAULT_BACKUP = None
DEFAULT_MEDIA = None
STDIN_PASSWORDS: list = []   # filled by main() from one stdin line; never from a file (DESIGN §9)

HEX16 = re.compile(r"^[0-9a-f]{16}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ID10 = re.compile(r"^\d{10}$")
IDENTITY_RE = re.compile(r"^[A-Z0-9*][A-Z0-9]{7}$")
MILLIS = re.compile(r"^-?\d{1,14}$")
QUOTE_V1 = re.compile(r"(?s)\A> ([A-Z0-9*]{8}): ")
QUOTE_V2 = re.compile(r"(?s)\A> quote #([0-9a-f]{16})\n\n")
IDBACKUP_RE = re.compile(r"^([A-Z2-7]{4}-){19}[A-Z2-7]{4}$")


# ----------------------------------------------------------------------------------------------------------------
# classification for android-inspect (no password, central directory only)

TEXT_ENTRIES = ("contacts.csv", "groups.csv")
TEXT_PREFIXES = ("message_", "group_message_", "distribution_list_message_")
MEDIA_PREFIXES = ("message_media_", "message_thumbnail_", "group_message_media_", "group_message_thumbnail_",
                  "distribution_list_message_media_", "distribution_list_thumbnail_")
BACKUP_TS = re.compile(r"threema-backup_(\d{10,14})_")
INCOMPLETE_PREFIX = "INCOMPLETE-"


def _is_text_entry(name):
    return name in TEXT_ENTRIES or (name.startswith(TEXT_PREFIXES) and name.endswith(".csv"))


def _is_media_entry(name):
    return name.startswith(MEDIA_PREFIXES) and not name.endswith(".csv")


def classify(path):
    """Classify one user-chosen file without its password. Never raises for bad input: unreadable or truncated
    files are 'incomplete' (zip signature present) or 'unknown' (not a zip at all)."""
    out = {"kind": "unknown", "bytes": 0, "has_media": False, "has_text": False, "encrypted": False,
           "created_at_ms": None, "entries": 0}
    try:
        st = os.stat(path)
    except OSError:
        return out
    out["bytes"] = st.st_size
    base = os.path.basename(path)
    m = BACKUP_TS.search(base)
    if m:
        ts = int(m.group(1))
        out["created_at_ms"] = ts if ts > 10 ** 12 else ts * 1000
    if base.startswith(INCOMPLETE_PREFIX) or st.st_size == 0:
        out["kind"] = "incomplete"
        return out
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return out
    if magic[:2] != b"PK":
        return out                                   # not a zip at all
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        out["kind"] = "incomplete"                   # zip header but no readable central directory: cut off
        return out
    names = [i.filename for i in infos]
    out["entries"] = len(names)
    out["encrypted"] = bool(infos) and all((i.flag_bits & 1) for i in infos if not i.is_dir())
    out["has_text"] = any(_is_text_entry(n) for n in names)
    out["has_media"] = any(_is_media_entry(n) for n in names)
    threema_like = "settings" in names or "identity" in names or out["has_text"] or out["has_media"]
    if not threema_like:
        return out
    if out["created_at_ms"] is None:
        newest = max((i.date_time for i in infos), default=None)
        if newest:
            try:
                local = _dt.datetime(*newest).astimezone()      # DOS time = local time of the phone/Mac
                out["created_at_ms"] = int(local.timestamp() * 1000)
            except (ValueError, OverflowError):
                pass
    if out["has_text"]:
        out["kind"] = "full" if out["has_media"] else "text"
    elif out["has_media"]:
        out["kind"] = "media"
    else:
        out["kind"] = "incomplete" if "settings" in names else "unknown"
    return out


def plan(files):
    """files: [(ref, classify-result)] -> (plan, text_ref, media_refs) (DESIGN §5.4 / S05).
    Chats come from the NEWEST file with chats; media from that file if it has media, otherwise from every other
    file with media (newest first): 'text_plus_media'. No file with chats -> ('none', None, [])."""
    def age(item):
        return item[1].get("created_at_ms") or 0
    usable = [(r, c) for r, c in files if c["kind"] in ("full", "text", "media")]
    texts = sorted([x for x in usable if x[1]["has_text"]], key=age, reverse=True)
    if not texts:
        return "none", None, []
    text_ref, text_c = texts[0]
    if text_c["has_media"]:
        return "single", text_ref, [text_ref]
    media = [r for r, c in sorted(usable, key=age, reverse=True) if c["has_media"] and r != text_ref]
    return ("text_plus_media" if media else "single"), text_ref, media


# ----------------------------------------------------------------------------------------------------------------
# format-spec validator (maintainer)

def load_passwords():
    return [("stdin", p) for p in STDIN_PASSWORDS]


def open_zip(path):
    z = pyzipper.AESZipFile(path)
    probe = next(i for i in z.infolist() if i.filename == "settings")
    for src, pw in load_passwords():
        try:
            z.setpassword(pw.encode("utf-8"))
            z.read(probe)
            return z, src
        except RuntimeError:
            continue
        except Exception:
            continue
    raise SystemExit("no password candidate decrypted the backup")


def unescape(v):
    # CSVRow.escape() doubles backslashes; opencsv reader with '\\' escape char undoes it.
    return v.replace("\\\\", "\\")


def read_csv(z, name, header=True):
    raw = z.read(name).decode("utf-8")
    rows = list(csv.reader(io.StringIO(raw, newline="")))
    rows = [[unescape(c) for c in r] for r in rows]
    if not header:
        return None, rows, raw
    return (rows[0] if rows else []), rows[1:], raw


def dist(counter, top=None):
    items = counter.most_common(top)
    return {str(k): v for k, v in items}


def ts_range(values):
    vals = [int(v) for v in values if v and MILLIS.match(v)]
    if not vals:
        return None
    import datetime as dt
    f = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m")
    return {"n": len(vals), "min_month": f(min(vals)), "max_month": f(max(vals)),
            "all_13_digits": all(len(str(abs(v))) == 13 for v in vals)}


def jtype(x):
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "bool"
    if isinstance(x, int):
        return "int"
    if isinstance(x, float):
        return "float"
    if isinstance(x, str):
        return "str"
    if isinstance(x, list):
        return "array"
    if isinstance(x, dict):
        return "object"
    return type(x).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", default=DEFAULT_BACKUP)
    ap.add_argument("--media", default=DEFAULT_MEDIA)
    ap.add_argument("--json", default=None)
    ap.add_argument("--magic-samples", type=int, default=40, help="per-prefix media entries whose first 12 bytes are sniffed")
    args = ap.parse_args()
    line = sys.stdin.readline().rstrip("\r\n")
    if line:
        STDIN_PASSWORDS[:] = [line]

    out = collections.OrderedDict()
    z, pwsrc = open_zip(args.backup)
    out["backup"] = os.path.basename(args.backup)
    out["password_source"] = pwsrc
    infos = z.infolist()
    names = [i.filename for i in infos]
    out["entry_count"] = len(names)
    out["entry_patterns"] = dist(collections.Counter(re.sub(r"\d{6,}", "<N>", n) for n in names))
    out["zip_meta"] = dist(collections.Counter(
        f"compress_type={i.compress_type} encrypted={bool(i.flag_bits & 1)} extra_has_AES={b'\x01\x99' in i.extra}"
        for i in infos))

    # settings
    _, srows, sraw = read_csv(z, "settings", header=False)
    out["settings"] = {"rows": srows, "raw_repr_len": len(sraw), "ends_with_newline": sraw.endswith("\n")}

    # identity
    ident = z.read("identity").decode("ascii", "replace")
    out["identity_entry"] = {"length": len(ident), "matches_80char_base32_grouped": bool(IDBACKUP_RE.match(ident.strip())),
                             "trailing_newline": ident.endswith("\n")}

    # contacts
    ch, crows, _ = read_csv(z, "contacts.csv")
    cidx = {c: i for i, c in enumerate(ch)}
    idmap = {}
    cinfo = {"header": ch, "rows": len(crows)}
    cinfo["verification"] = dist(collections.Counter(r[cidx["verification"]] for r in crows))
    cinfo["hidden"] = dist(collections.Counter(r[cidx["hidden"]] for r in crows))
    cinfo["archived"] = dist(collections.Counter(r[cidx["archived"]] for r in crows))
    cinfo["acid_nonempty"] = sum(1 for r in crows if r[cidx["acid"]])
    cinfo["identity_format_ok"] = sum(1 for r in crows if IDENTITY_RE.match(r[cidx["identity"]]))
    cinfo["publickey_hex64"] = sum(1 for r in crows if HEX64.match(r[cidx["publickey"]]))
    cinfo["identity_id_10digits"] = sum(1 for r in crows if ID10.match(r[cidx["identity_id"]]))
    cinfo["firstname_nonempty"] = sum(1 for r in crows if r[cidx["firstname"]])
    cinfo["lastname_nonempty"] = sum(1 for r in crows if r[cidx["lastname"]])
    cinfo["nick_name_nonempty"] = sum(1 for r in crows if r[cidx["nick_name"]])
    cinfo["last_update"] = ts_range([r[cidx["last_update"]] for r in crows])
    cinfo["last_update_empty"] = sum(1 for r in crows if not r[cidx["last_update"]])
    cinfo["gateway_ids(*)"] = sum(1 for r in crows if r[cidx["identity"]].startswith("*"))
    for r in crows:
        idmap[r[cidx["identity_id"]]] = r[cidx["identity"]]
    out["contacts.csv"] = cinfo

    # groups
    gh, grows, _ = read_csv(z, "groups.csv")
    gidx = {c: i for i, c in enumerate(gh)}
    ginfo = {"header": gh, "rows": len(grows)}
    ginfo["id_hex16"] = sum(1 for r in grows if HEX16.match(r[gidx["id"]]))
    ginfo["group_uid_10digits"] = sum(1 for r in grows if ID10.match(r[gidx["group_uid"]]))
    ginfo["user_state"] = dist(collections.Counter(r[gidx["user_state"]] for r in grows))
    ginfo["archived"] = dist(collections.Counter(r[gidx["archived"]] for r in grows))
    ginfo["groupDesc_nonempty"] = sum(1 for r in grows if r[gidx["groupDesc"]])
    ginfo["member_count_dist"] = dist(collections.Counter(len([m for m in r[gidx["members"]].split(";") if m]) for r in grows))
    ginfo["created_at"] = ts_range([r[gidx["created_at"]] for r in grows])
    ginfo["creator_in_members"] = sum(1 for r in grows if r[gidx["creator"]] in r[gidx["members"]].split(";"))
    ginfo["creator_is_contact"] = sum(1 for r in grows if r[gidx["creator"]] in set(idmap.values()))
    groupuid = {r[gidx["group_uid"]]: (r[gidx["id"]], r[gidx["creator"]]) for r in grows}
    groupkey_to_uid = {(r[gidx["id"]], r[gidx["creator"]]): r[gidx["group_uid"]] for r in grows}
    out["groups.csv"] = ginfo

    # distribution lists
    dh, drows, _ = read_csv(z, "distribution_list.csv")
    out["distribution_list.csv"] = {"header": dh, "rows": len(drows)}

    # naming consistency
    msg_files = [n for n in names if n.startswith("message_") and n.endswith(".csv")]
    gmsg_files = [n for n in names if n.startswith("group_message_") and n.endswith(".csv")]
    dmsg_files = [n for n in names if n.startswith("distribution_list_message_") and n.endswith(".csv")]
    naming = {}
    naming["message_files"] = len(msg_files)
    naming["message_file_ids_in_contacts_identity_id"] = sum(1 for n in msg_files if n[len("message_"):-4] in idmap)
    naming["contacts_without_message_file"] = sum(1 for k in idmap if f"message_{k}.csv" not in names)
    naming["group_message_files"] = len(gmsg_files)
    naming["group_message_file_ids_in_groups_group_uid"] = sum(1 for n in gmsg_files if n[len("group_message_"):-4] in groupuid)
    naming["group_message_file_id_equals_api_group_id"] = sum(1 for n in gmsg_files if n[len("group_message_"):-4] in {v[0] for v in groupuid.values()})
    naming["distribution_list_message_files"] = len(dmsg_files)
    av = [n for n in names if n.startswith("contact_avatar_")]
    pp = [n for n in names if n.startswith("contact_profile_pic_")]
    ga = [n for n in names if n.startswith("group_avatar_")]
    naming["contact_avatar_entries"] = len(av)
    naming["contact_avatar_suffix_me"] = sum(1 for n in av if n == "contact_avatar_me")
    naming["contact_avatar_ids_in_contacts"] = sum(1 for n in av if n[len("contact_avatar_"):] in idmap)
    naming["contact_profile_pic_entries"] = len(pp)
    naming["contact_profile_pic_ids_in_contacts"] = sum(1 for n in pp if n[len("contact_profile_pic_"):] in idmap)
    naming["group_avatar_entries"] = len(ga)
    naming["group_avatar_ids_in_groups"] = sum(1 for n in ga if n[len("group_avatar_"):] in groupuid)
    # image magic of avatars
    magic = collections.Counter()
    for n in av + pp + ga:
        head = z.read(n)[:4]
        magic["jpeg" if head[:3] == b"\xff\xd8\xff" else "png" if head[:4] == b"\x89PNG" else "webp" if head[:4] == b"RIFF" else "other"] += 1
    naming["avatar_image_magic"] = dict(magic)
    out["naming"] = naming

    # messages
    agg = {
        "contact": collections.defaultdict(collections.Counter),
        "group": collections.defaultdict(collections.Counter),
        "distribution_list": collections.defaultdict(collections.Counter),
    }
    headers = {}
    file_array_len = collections.Counter()
    file_idx_types = collections.defaultdict(collections.Counter)
    file_mime = collections.Counter()
    file_thumb_mime = collections.Counter()
    file_render = collections.Counter()
    file_downloaded = collections.Counter()
    file_meta_keys = collections.Counter()
    file_meta_value_types = collections.defaultdict(collections.Counter)
    file_blob_len = collections.Counter()
    file_key_len = collections.Counter()
    file_caption_eq_col = collections.Counter()
    file_name_present = collections.Counter()
    file_json_kind = collections.Counter()
    file_blob_by_outbox = collections.Counter()
    file_mime_render = collections.Counter()
    loc_shape = collections.Counter()
    ballot_body = collections.Counter()
    ballot_pollid_known = collections.Counter()
    status_shape = collections.Counter()
    voip_keys = collections.Counter()
    voip_status = collections.Counter()
    gstatus_status = collections.Counter()
    gstatus_keys = collections.Counter()
    gcall_keys = collections.Counter()
    text_quote = collections.Counter()
    quoted_resolved = collections.Counter()
    gms_states = collections.Counter()
    gms_json_kind = collections.Counter()
    field_count_mismatch = 0
    uid_format = collections.Counter()
    apiid_format = collections.Counter()
    backslash_fields = 0
    multiline_bodies = 0
    group_identity_for_outbox = collections.Counter()
    ts_cols = collections.defaultdict(list)
    all_uids = {}  # uid -> (chat kind, type, mime, isDownloaded)
    msg_apiids_by_file = {}
    edited_by_type = collections.Counter()
    deleted_by_type = collections.Counter()
    deleted_body_empty = collections.Counter()
    empty_caption_col_by_type = collections.Counter()

    # ballot ids
    bh, brows, _ = read_csv(z, "ballot.csv")
    bidx = {c: i for i, c in enumerate(bh)}
    ballot_ids = {r[bidx["id"]] for r in brows}

    def process(kind, fname, prefix):
        nonlocal field_count_mismatch, backslash_fields, multiline_bodies
        h, rows, raw = read_csv(z, fname)
        headers.setdefault(kind, h)
        if h != headers[kind]:
            headers[kind + "_variant"] = h
        idx = {c: i for i, c in enumerate(h)}
        backslash_fields += raw.count("\\\\")
        apiids = set(r[idx["apiid"]] for r in rows if len(r) == len(h))
        msg_apiids_by_file[fname] = apiids
        a = agg[kind]
        a["_files"]["n"] += 1
        a["_rows"]["n"] += len(rows)
        for r in rows:
            if len(r) != len(h):
                field_count_mismatch += 1
                continue
            g = lambda c: r[idx[c]] if c in idx else None
            t = g("type")
            a["type"][t] += 1
            a["messagestae"][g("messagestae")] += 1
            a["messagestae_by_outbox"][f"{g('isoutbox')}:{g('messagestae')}"] += 1
            a["isoutbox"][g("isoutbox")] += 1
            a["isread"][g("isread")] += 1
            a["issaved"][g("issaved")] += 1
            a["isstatusmessage"][g("isstatusmessage")] += 1
            a["isstatusmessage_by_type"][f"{t}:{g('isstatusmessage')}"] += 1
            if "display_tags" in idx:
                a["display_tags"][g("display_tags")] += 1
            for c in ("edited_at", "deleted_at", "quoted_message_apiid", "caption", "delivered_at", "read_at", "modified_at", "posted_at", "created_at", "g_msg_states", "apiid"):
                if c in idx:
                    a[c + "_nonempty"]["yes" if g(c) else "no"] += 1
            for c in ("posted_at", "created_at", "modified_at", "delivered_at", "read_at", "edited_at", "deleted_at"):
                if c in idx and g(c):
                    ts_cols[c].append(g(c))
            if g("edited_at"):
                edited_by_type[t] += 1
            if g("deleted_at"):
                deleted_by_type[t] += 1
                deleted_body_empty["body_empty" if not g("body") else "body_present"] += 1
            if g("caption"):
                empty_caption_col_by_type[t] += 1
            uid = g("uid")
            uid_format["uuid" if UUID_RE.match(uid or "") else ("empty" if not uid else "other")] += 1
            api = g("apiid")
            apiid_format["hex16" if HEX16.match(api or "") else ("empty" if not api else "other")] += 1
            if kind == "group" and g("isoutbox") == "1":
                group_identity_for_outbox["empty" if not g("identity") else ("identity_format" if IDENTITY_RE.match(g("identity")) else "other")] += 1
            body = g("body") or ""
            if "\n" in body:
                multiline_bodies += 1
            q = g("quoted_message_apiid")
            if q:
                quoted_resolved[f"{kind}:{'same_chat' if q in apiids else 'unresolved'}"] += 1
            if g("g_msg_states"):
                try:
                    j = json.loads(g("g_msg_states"))
                    gms_json_kind[jtype(j)] += 1
                    if isinstance(j, dict):
                        for v in j.values():
                            gms_states[v] += 1
                except Exception:
                    gms_json_kind["invalid"] += 1
            info = None
            if t == "FILE":
                try:
                    j = json.loads(body) if body else None
                except Exception:
                    j = "invalid"
                file_json_kind[jtype(j) if j != "invalid" else "invalid"] += 1
                if isinstance(j, list):
                    file_array_len[len(j)] += 1
                    for i2, v in enumerate(j):
                        file_idx_types[i2][jtype(v)] += 1
                    blob, key, mime, size, name, rtype, dl, cap, tmime = (j + [None] * 9)[:9]
                    meta = j[9] if len(j) > 9 else None
                    file_mime[mime] += 1
                    file_thumb_mime[tmime] += 1
                    file_render[rtype] += 1
                    file_downloaded[dl] += 1
                    file_blob_len[len(blob) if isinstance(blob, str) else None] += 1
                    file_key_len[len(key) if isinstance(key, str) else None] += 1
                    file_name_present["yes" if name else "no"] += 1
                    file_caption_eq_col["eq" if (cap or "") == (g("caption") or "") else "differs"] += 1
                    if isinstance(meta, dict):
                        file_meta_keys[",".join(sorted(meta.keys())) or "{}"] += 1
                        for mk, mv in meta.items():
                            file_meta_value_types[mk][jtype(mv)] += 1
                    file_blob_by_outbox[f"outbox={g('isoutbox')} blob={'null' if blob is None else ('empty' if blob == '' else 'hex')}"] += 1
                    file_mime_render[f"{(mime or '?').split('/')[0]}:{rtype}"] += 1
                    info = (kind, t, mime, dl, rtype, size)
                elif isinstance(j, dict):
                    file_meta_keys["OBJECT:" + ",".join(sorted(j.keys()))] += 1
            elif t == "LOCATION":
                try:
                    j = json.loads(body)
                    loc_shape["array:" + ",".join(jtype(x) for x in j) if isinstance(j, list) else jtype(j)] += 1
                except Exception:
                    loc_shape["invalid"] += 1
            elif t == "BALLOT":
                try:
                    j = json.loads(body)
                    ballot_body[f"array_len={len(j)} type={j[0]}"] += 1
                    ballot_pollid_known["pollId_in_ballot.csv_id" if str(j[1]) in ballot_ids else "unknown"] += 1
                except Exception:
                    ballot_body["invalid"] += 1
            elif t in ("VOIP_STATUS", "GROUP_STATUS", "GROUP_CALL_STATUS"):
                try:
                    j = json.loads(body)
                    tgt = {"VOIP_STATUS": voip_keys, "GROUP_STATUS": gstatus_keys, "GROUP_CALL_STATUS": gcall_keys}[t]
                    tgt[f"[{j[0]}, {{{','.join(sorted(j[1].keys()))}}}]"] += 1
                    if t == "VOIP_STATUS":
                        voip_status[j[1].get("status")] += 1
                    if t == "GROUP_STATUS":
                        gstatus_status[j[1].get("status")] += 1
                except Exception:
                    status_shape[f"{t}:invalid"] += 1
            elif t == "STATUS":
                status_shape["json" if body.startswith("[") or body.startswith("{") else ("empty" if not body else "plaintext")] += 1
            elif t == "TEXT":
                text_quote["quote_v2_in_body" if QUOTE_V2.match(body) else ("quote_v1_in_body" if QUOTE_V1.match(body) else "plain")] += 1
            all_uids[uid] = info or (kind, t, None, None, None, None)

    for n in msg_files:
        process("contact", n, "message_")
    for n in gmsg_files:
        process("group", n, "group_message_")
    for n in dmsg_files:
        process("distribution_list", n, "distribution_list_message_")

    msgs = {}
    for kind, a in agg.items():
        msgs[kind] = {k: dist(v) for k, v in a.items()}
    out["message_headers"] = headers
    out["messages"] = msgs
    out["message_checks"] = {
        "field_count_mismatch_rows": field_count_mismatch,
        "uid_format": dist(uid_format),
        "apiid_format": dist(apiid_format),
        "escaped_double_backslash_occurrences": backslash_fields,
        "multiline_bodies": multiline_bodies,
        "group_outbox_identity_column": dist(group_identity_for_outbox),
        "quoted_message_apiid_resolution": dist(quoted_resolved),
        "text_body_quote_format": dist(text_quote),
        "g_msg_states_json_kind": dist(gms_json_kind),
        "g_msg_states_values": dist(gms_states),
        "edited_by_type": dist(edited_by_type),
        "deleted_by_type": dist(deleted_by_type),
        "deleted_body": dist(deleted_body_empty),
        "caption_column_nonempty_by_type": dist(empty_caption_col_by_type),
        "timestamps": {c: ts_range(v) for c, v in ts_cols.items()},
    }
    out["FILE_body"] = {
        "json_kind": dist(file_json_kind),
        "array_len": dist(file_array_len),
        "types_by_index": {i: dist(c) for i, c in sorted(file_idx_types.items())},
        "mime": dist(file_mime),
        "thumbnail_mime": dist(file_thumb_mime),
        "rendering_type": dist(file_render),
        "isDownloaded": dist(file_downloaded),
        "blobId_hexlen": dist(file_blob_len),
        "encryptionKey_hexlen": dist(file_key_len),
        "filename_present": dist(file_name_present),
        "caption_vs_caption_column": dist(file_caption_eq_col),
        "blob_presence_by_outbox": dist(file_blob_by_outbox),
        "mime_major:rendering_type": dist(file_mime_render),
        "metadata_keysets": dist(file_meta_keys),
        "metadata_value_types": {k: dist(v) for k, v in file_meta_value_types.items()},
    }
    out["other_bodies"] = {
        "LOCATION_shape": dist(loc_shape),
        "BALLOT_shape": dist(ballot_body),
        "BALLOT_pollId": dist(ballot_pollid_known),
        "VOIP_STATUS_shape": dist(voip_keys),
        "VOIP_STATUS_status": dist(voip_status),
        "GROUP_STATUS_shape": dist(gstatus_keys),
        "GROUP_STATUS_status": dist(gstatus_status),
        "GROUP_CALL_STATUS_shape": dist(gcall_keys),
        "STATUS_shape_and_invalid": dist(status_shape),
    }

    # ballots
    binfo = {"header": bh, "rows": len(brows)}
    for c in ("ref", "state", "assessment", "type", "choice_type"):
        binfo[c] = dist(collections.Counter(r[bidx[c]] for r in brows))
    binfo["ref_id_is_group_uid"] = sum(1 for r in brows if r[bidx["ref_id"]] in groupuid)
    binfo["ref_id_is_identity"] = sum(1 for r in brows if r[bidx["ref_id"]] in set(idmap.values()))
    binfo["aid_hex16"] = sum(1 for r in brows if HEX16.match(r[bidx["aid"]]))
    out["ballot.csv"] = binfo
    bch, bcrows, _ = read_csv(z, "ballot_choice.csv")
    bcidx = {c: i for i, c in enumerate(bch)}
    ballot_uids = {f"{r[bidx['aid']]}-{r[bidx['creator']]}" for r in brows}
    out["ballot_choice.csv"] = {"header": bch, "rows": len(bcrows),
                                "type": dist(collections.Counter(r[bcidx["type"]] for r in bcrows)),
                                "ballot_col_matches_aid-creator": sum(1 for r in bcrows if r[bcidx["ballot"]] in ballot_uids)}
    bvh, bvrows, _ = read_csv(z, "ballot_vote.csv")
    bvidx = {c: i for i, c in enumerate(bvh)}
    out["ballot_vote.csv"] = {"header": bvh, "rows": len(bvrows),
                              "choice": dist(collections.Counter(r[bvidx["choice"]] for r in bvrows)),
                              "ballot_uid_matches_aid-creator": sum(1 for r in bvrows if r[bvidx["ballot_uid"]] in ballot_uids)}

    # reactions
    rh, rrows, _ = read_csv(z, "contact_reactions.csv")
    ridx = {c: i for i, c in enumerate(rh)}
    ident_to_file = {v: f"message_{k}.csv" for k, v in idmap.items()}
    res = collections.Counter()
    for r in rrows:
        f = ident_to_file.get(r[ridx["identity"]])
        res["resolved" if f and r[ridx["api_message_id"]] in msg_apiids_by_file.get(f, ()) else "unresolved"] += 1
    out["contact_reactions.csv"] = {"header": rh, "rows": len(rrows), "api_message_id_resolves_in_chat": dist(res),
                                    "distinct_emoji_sequences": len({r[ridx["emoji_sequence"]] for r in rrows}),
                                    "sender_is_chat_partner": sum(1 for r in rrows if r[ridx["sender_identity"]] == r[ridx["identity"]]),
                                    "reacted_at": ts_range([r[ridx["reacted_at"]] for r in rrows])}
    grh, grrows, _ = read_csv(z, "group_reactions.csv")
    gridx = {c: i for i, c in enumerate(grh)}
    res = collections.Counter()
    for r in grrows:
        u = groupkey_to_uid.get((r[gridx["api_group_id"]], r[gridx["group_creator_identity"]]))
        f = f"group_message_{u}.csv" if u else None
        res["resolved" if f and r[gridx["api_message_id"]] in msg_apiids_by_file.get(f, ()) else "unresolved"] += 1
    out["group_reactions.csv"] = {"header": grh, "rows": len(grrows), "api_message_id_resolves_in_group": dist(res),
                                  "distinct_emoji_sequences": len({r[gridx["emoji_sequence"]] for r in grrows})}
    rch, rcrows, _ = read_csv(z, "reaction_counts.csv")
    out["reaction_counts.csv"] = {"header": rch, "rows": rcrows}

    # nonces
    nch, ncrows, _ = read_csv(z, "nonce_counts.csv")
    out["nonce_counts.csv"] = {"header": nch, "rows": ncrows}
    for nf in ("nonces.csv", "nonces_d2d.csv"):
        h, rows, _ = read_csv(z, nf)
        out[nf] = {"header": h, "rows": len(rows), "hex64": sum(1 for r in rows if r and HEX64.match(r[0]))}

    # media backup: names only (plus the tiny 'settings' entry)
    media = collections.OrderedDict()
    if args.media and os.path.exists(args.media):
        mz = pyzipper.AESZipFile(args.media)
        minfos = mz.infolist()
        mnames = [i.filename for i in minfos]
        media["backup"] = os.path.basename(args.media)
        media["entry_count"] = len(mnames)
        media["entry_patterns"] = dist(collections.Counter(
            re.sub(r"\d{6,}", "<N>", re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<UUID>", n)) for n in mnames))
        prefixes = [("message_media_", "contact"), ("message_thumbnail_", "contact"),
                    ("group_message_media_", "group"), ("group_message_thumbnail_", "group"),
                    ("distribution_list_message_media_", "distribution_list"), ("distribution_list_thumbnail_", "distribution_list")]
        m_by = collections.Counter()
        m_bytes = collections.Counter()
        mime_of_media = collections.Counter()
        match_kind = collections.Counter()
        size_cmp = collections.Counter()
        media_uids = set()
        for i in minfos:
            n = i.filename
            # longest prefix first
            for p, k in sorted(prefixes, key=lambda x: -len(x[0])):
                if n.startswith(p):
                    uid = n[len(p):]
                    m_by[p] += 1
                    m_bytes[p] += i.file_size
                    if "media" in p:
                        media_uids.add(uid)
                    info = all_uids.get(uid)
                    if info is None:
                        match_kind[f"{p}:uid_not_in_newest_backup"] += 1
                    else:
                        match_kind[f"{p}:in_newest:{info[0]}:{info[1]}{'' if info[0]==k else ':KIND_MISMATCH'}"] += 1
                        if "media" in p:
                            mime_of_media[info[2]] += 1
                            size_cmp["media_size==body_fileSize" if info[5] == i.file_size else "media_size!=body_fileSize"] += 1
                    break
        media["media_and_thumbnail_counts"] = dict(m_by)
        media["uncompressed_bytes"] = dict(m_bytes)
        media["uid_matching_vs_newest_backup"] = dist(match_kind)
        media["mime_of_matched_media"] = dist(mime_of_media, 25)
        media["media_entry_size_vs_body_fileSize"] = dist(size_cmp)
        newest_file_uids = {u for u, inf in all_uids.items() if inf[1] == "FILE"}
        media["newest_FILE_messages_total"] = len(newest_file_uids)
        media["newest_FILE_messages_with_media_in_media_backup"] = len(newest_file_uids & media_uids)
        media["newest_FILE_isDownloaded_true_without_media_in_media_backup"] = sum(
            1 for u in newest_file_uids - media_uids if all_uids[u][3] is True)
        try:
            mz2, _ = open_zip(args.media)
            _, ms, _ = read_csv(mz2, "settings", header=False)
            media["settings"] = ms
            # sample magic bytes (first 12 bytes only) of a few media/thumbnail entries
            magic = collections.Counter()
            per = collections.Counter()
            for i in minfos:
                n = i.filename
                pref = next((p for p, _ in sorted(prefixes, key=lambda x: -len(x[0])) if n.startswith(p)), None)
                if pref is None or per[pref] >= args.magic_samples:
                    continue
                per[pref] += 1
                with mz2.open(i) as fh:
                    head = fh.read(12)
                kindm = ("jpeg" if head[:3] == b"\xff\xd8\xff" else "png" if head[:4] == b"\x89PNG" else
                         "gif" if head[:3] == b"GIF" else "mp4/m4a(ftyp)" if head[4:8] == b"ftyp" else
                         "pdf" if head[:4] == b"%PDF" else "aac(adts)" if head[:2] in (b"\xff\xf1", b"\xff\xf9") else
                         "webp" if head[:4] == b"RIFF" else "zip" if head[:2] == b"PK" else "other")
                magic[f"{pref}:{kindm}"] += 1
            media["magic_sample"] = dist(magic)
        except SystemExit:
            media["settings"] = "password_failed"
        # other csv names present in media backup (names only)
        media["csv_entries"] = sum(1 for n in mnames if n.endswith(".csv"))
    out["media_backup"] = media

    s = json.dumps(out, indent=1, ensure_ascii=False, default=str)
    print(s)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(s)


if __name__ == "__main__":
    main()
